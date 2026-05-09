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
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from _paths import _ROOT

MEMORY_DIR = _ROOT / "memory"
MAX_RETRY_DAYS = 3

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


if __name__ == "__main__":
    main()
