#!/usr/bin/env python3
"""Nightly reconciler — retry failed reflection writes.

Scans all agent JSONL files for records with reflection_status != "complete".
For each stub: increment retry_count, attempt LLM call again.
After 3 calendar days of failed retries: set status="manual_review", Discord alert.

Cron: 0 22 * * * (22:00 UTC = 18:00 ET, after market close)
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from _paths import _ROOT, JOURNAL

MEMORY_DIR = _ROOT / "memory"
MAX_RETRY_DAYS = 3

from _journal_update import update_trade_entry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [reconciler] %(message)s",
)


def _discord_alert(msg: str) -> None:
    try:
        ch = os.environ.get("DISCORD_CHANNEL_ALERTS", "")
        if ch:
            from _discord import post_to_channel
            post_to_channel(ch, msg)
    except Exception:
        pass


def _attempt_reflection(record: dict) -> str | None:
    """Re-attempt LLM reflection for a failed record. Returns text or None."""
    agent = record.get("agent", "")
    if agent == "agent_gamma":
        return None

    from _memory import _build_reflection_prompt
    from _journal_update import find_trade

    trade = find_trade(record["trade_id"])
    if trade is None:
        return None

    prompt = _build_reflection_prompt(trade)
    system = (
        "You are a post-trade analyst for an options trading system. "
        "Write a 2-4 sentence reflection on this closed trade. "
        "Focus on: was the thesis right or wrong? What signal was missed? "
        "What should change next time? Be specific and actionable."
    )
    try:
        from _llm_call import call_llm_text
        return call_llm_text(
            model="claude-haiku-4-5-20251001",
            system=system,
            user=prompt,
            max_tokens=300,
            timeout=20,
            caller="reflection_reconciler",
        )
    except Exception as e:
        logging.warning("Reconciler LLM call failed for %s: %s", record["trade_id"], e)
        return None


def reconcile_file(jsonl_path: Path) -> tuple[int, int, int]:
    """Process one agent's JSONL. Returns (retried, completed, escalated)."""
    if not jsonl_path.exists():
        return 0, 0, 0

    lines = []
    retried = 0
    completed = 0
    escalated = 0

    try:
        with open(jsonl_path) as f:
            lines = [l.strip() for l in f if l.strip()]
    except Exception as e:
        logging.error("Failed to read %s: %s", jsonl_path, e)
        return 0, 0, 0

    updated = []
    for raw in lines:
        try:
            rec = json.loads(raw)
        except Exception:
            updated.append(raw)
            continue

        status = rec.get("reflection_status", "complete")
        if status == "complete" or status == "manual_review":
            updated.append(json.dumps(rec))
            continue

        first_ts = rec.get("first_attempt_ts", "")
        days_since = 0
        if first_ts:
            try:
                first = datetime.fromisoformat(first_ts)
                days_since = (datetime.now(timezone.utc) - first).days
            except Exception:
                pass

        if days_since >= MAX_RETRY_DAYS:
            rec["reflection_status"] = "manual_review"
            escalated += 1
            logging.info("Escalated %s to manual_review (days=%d)", rec["trade_id"], days_since)
            updated.append(json.dumps(rec))
            continue

        retried += 1
        text = _attempt_reflection(rec)
        if text:
            rec["reflection_text"] = text
            rec["reflection_status"] = "complete"
            rec["reflection_error"] = None
            completed += 1
            logging.info("Reconciled %s — reflection complete", rec["trade_id"])
        else:
            rec["retry_count"] = rec.get("retry_count", 0) + 1
            logging.info("Retry failed for %s (attempt #%d)", rec["trade_id"], rec["retry_count"])

        updated.append(json.dumps(rec))

    if retried > 0 or escalated > 0:
        tmp = jsonl_path.with_suffix(".tmp")
        try:
            tmp.write_text("\n".join(updated) + "\n")
            tmp.replace(jsonl_path)
        except Exception as e:
            logging.error("Failed to write updated %s: %s", jsonl_path, e)
            if tmp.exists():
                tmp.unlink()

    return retried, completed, escalated


def sweep_expired_entries() -> tuple[int, int]:
    """Scan OPEN/PENDING journal entries for expired option legs.

    If ALL legs have expiry < today, mark the entry as EXPIRED (for OPEN)
    or PHANTOM_NEVER_FILLED (for PENDING).

    Returns (scanned, expired_count).
    """
    from datetime import date

    today = date.today()
    scanned = 0
    expired = 0

    # Read all OPEN/PENDING entries from journal
    entries = []
    try:
        with open(JOURNAL) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    t = json.loads(line)
                    if t.get("status") in ("OPEN", "PENDING"):
                        entries.append(t)
                except Exception:
                    continue
    except FileNotFoundError:
        return 0, 0

    for t in entries:
        scanned += 1
        tid = t.get("trade_id") or t.get("id")
        legs = t.get("legs", [])
        if not legs:
            continue

        # Check if ALL legs have expired
        all_expired = True
        latest_expiry = None
        for leg in legs:
            expiry_str = leg.get("expiry", "")
            if not expiry_str:
                all_expired = False
                break
            try:
                exp_date = datetime.strptime(expiry_str, "%Y-%m-%d").date()
                if latest_expiry is None or exp_date > latest_expiry:
                    latest_expiry = exp_date
                if exp_date >= today:
                    all_expired = False
                    break
            except ValueError:
                all_expired = False
                break

        if all_expired and latest_expiry is not None:
            days_past = (today - latest_expiry).days
            # PENDING + expired = phantom; OPEN + expired = expired
            if t.get("status") == "PENDING":
                new_status = "PHANTOM_NEVER_FILLED"
                close_reason = f"pending_and_all_legs_expired_{days_past}d_ago"
            else:
                new_status = "EXPIRED"
                close_reason = f"all_legs_expired_{days_past}d_ago"

            ok = update_trade_entry(tid, {
                "status": new_status,
                "close_reason": close_reason,
                "close_timestamp": datetime.now(timezone.utc).isoformat(),
                "expired_detected_at": datetime.now(timezone.utc).isoformat(),
            }, journal_path=str(JOURNAL))
            if ok:
                expired += 1
                logging.info(
                    "Expired: %s -> %s (latest=%s, %dd ago)",
                    tid, new_status, latest_expiry, days_past,
                )

    return scanned, expired


def main():
    total_retried = 0
    total_completed = 0
    total_escalated = 0

    for fname in ["alpha_reflections.jsonl", "beta_reflections.jsonl", "gamma_reflections.jsonl"]:
        path = MEMORY_DIR / fname
        r, c, e = reconcile_file(path)
        total_retried += r
        total_completed += c
        total_escalated += e

    logging.info(
        "Reconciler done: retried=%d completed=%d escalated=%d",
        total_retried, total_completed, total_escalated,
    )

    if total_escalated > 0:
        _discord_alert(
            f"⚠️ Reflection reconciler: {total_escalated} trade(s) escalated to manual_review "
            f"(failed after {MAX_RETRY_DAYS} days of retries). Check memory/ JSONL files."
        )

    # Nightly expired-options sweep
    scanned, expired_count = sweep_expired_entries()
    logging.info("Expiry sweep: scanned=%d expired=%d", scanned, expired_count)
    if expired_count > 0:
        _discord_alert(
            f"📅 Nightly expiry sweep: {expired_count} trade(s) auto-marked EXPIRED "
            f"(all option legs past expiry date). Check journal."
        )


if __name__ == "__main__":
    main()
