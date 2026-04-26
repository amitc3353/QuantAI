#!/usr/bin/env python3
"""
QuantAI Error Learner — Phase E2

Runs Friday 22:00 UTC (6 PM ET) via cron. Reviews the week's error history
(last 7 days of pipeline + heartbeat + position_monitor logs), correlates
with the catalog, and:

- Patterns seen 3+ times and NOT yet in catalog → auto-append as "recurring"
  with a stub runbook entry (the human writes the runbook later).
- Patterns seen once and NOT yet in catalog → append as "novel" with no
  runbook (flagged for manual review).
- Known-catalog entries → refresh last_seen, bump occurrence_count by the
  count observed this week.

Posts a weekly summary to Discord via the bot-token helper (_discord.post_to_channel).

Pure Python, no LLM calls. Atomic catalog writes with .bak backup.

Usage:
  python3 error_learner.py           # normal weekly run
  python3 error_learner.py --dry-run # analyze + report, no catalog write, no Discord
  python3 error_learner.py --verbose # per-pattern detail to stdout
"""

import hashlib
import json
import os
import re
import sys
import urllib.request
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# Env loading (matches run_pipeline.py)
for _ef in [Path("/home/trader/QuantAI/.env"), Path("/root/quantai-v2/.env")]:
    if _ef.exists():
        for _line in _ef.read_text().splitlines():
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.partition("=")
                if not os.environ.get(_k.strip()):
                    os.environ[_k.strip()] = _v.strip()
        break

ET = ZoneInfo("America/New_York")
REPO = Path("/home/trader/QuantAI")
CATALOG_PATH = REPO / "docs" / "error-catalog.json"

LOGS = [
    Path("/root/quantai-v2/shared-data/logs/pipeline.log"),
    Path("/root/quantai-v2/shared-data/logs/heartbeat.log"),
    Path("/root/quantai-v2/shared-data/logs/position_monitor.log"),
]

LOOKBACK_DAYS = 7
RECURRING_THRESHOLD = 3
MIN_NOVEL_THRESHOLD = 2  # don't catalog single-occurrence transients

DRY_RUN = "--dry-run" in sys.argv
VERBOSE = "--verbose" in sys.argv or "-v" in sys.argv

ERROR_TOKENS = (
    "traceback", "exception", "error:", "error -", "failed", "failure",
    "rejected", "connectionerror", "timeout", "refused", "404", "500",
    "panic", "unhandled", "aborting", "fatal",
)
ERROR_TOKEN_IGNORE = ("no errors", "error_count: 0", "0 errors")
# Skip lines that are clearly LLM debate/analysis prose rather than system
# errors, even if they happen to contain an error token like "rejected".
NOISE_PREFIXES = (
    # LLM debate / analysis prose
    "•", "[debate]", "- **", "**", "**✅", "**❌", "Diagonal:", "BUY ", "SELL ",
    "SENIOR TRADER", "JUDGE:", "BULL:", "BEAR:",
    # Routine data-format prints — never errors
    "[market_intelligence]", "[scan_options]", "[debate_chamber]",
    "[market_data]", "[autonomous_execution]", "[eod_summary]",
    "[heartbeat]", "[position_monitor]",
)

# Match a leading [HH:MM:SS] or [YYYY-MM-DD HH:MM:SS] prefix so we can strip
# it when producing a stable signature.
TS_PREFIX = re.compile(r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\]\s*|^\[\d{2}:\d{2}(:\d{2})?\]\s*|^\[\d{2}:\d{2} ET\]\s*")
# Strip variable tokens (numbers, quoted strings, paths, hex ids) to make
# "connection timeout to 1.2.3.4" and "connection timeout to 5.6.7.8" one
# signature.
NUMS = re.compile(r"\b\d+(?:\.\d+)*\b")
QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")
PATHS = re.compile(r"/[\w./\-]+")
HEX = re.compile(r"\b[0-9a-f]{8,}\b", re.IGNORECASE)


def now_et():
    return datetime.now(ET)


def log(msg):
    print(f"[{now_et().strftime('%H:%M:%S')}] {msg}", flush=True)


def load_catalog():
    if not CATALOG_PATH.exists():
        return {"schema_version": 1, "errors": []}
    return json.loads(CATALOG_PATH.read_text())


def save_catalog(cat):
    if DRY_RUN:
        log("[DRY] would write catalog")
        return
    cat["last_updated"] = now_et().isoformat(timespec="seconds")
    bak = CATALOG_PATH.with_suffix(".json.bak")
    if CATALOG_PATH.exists():
        bak.write_bytes(CATALOG_PATH.read_bytes())
    tmp = CATALOG_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cat, indent=2))
    os.replace(tmp, CATALOG_PATH)


def tail_since(path, cutoff):
    """Return lines from `path` whose timestamp (if parseable) is >= cutoff.
    If a line has no timestamp prefix, include it (conservative).
    """
    if not path.exists():
        return []
    out = []
    cutoff_date = cutoff.date()
    try:
        with path.open("r", errors="replace") as f:
            for line in f:
                line = line.rstrip("\n")
                if not line.strip():
                    continue
                # Try to pull a date prefix [YYYY-MM-DD ...]
                m = re.match(r"^\[(\d{4}-\d{2}-\d{2})", line)
                if m:
                    try:
                        d = datetime.strptime(m.group(1), "%Y-%m-%d").date()
                        if d < cutoff_date:
                            continue
                    except ValueError:
                        pass
                out.append(line)
    except OSError:
        return []
    return out


def looks_like_error(line):
    stripped = line.lstrip()
    # Drop the optional timestamp prefix before applying noise-prefix check.
    no_ts = TS_PREFIX.sub("", stripped)
    if no_ts.startswith(NOISE_PREFIXES):
        return False
    low = line.lower()
    if any(i in low for i in ERROR_TOKEN_IGNORE):
        return False
    return any(tok in low for tok in ERROR_TOKENS)


def line_signature(line):
    """Stable signature from the stable parts of an error line."""
    s = TS_PREFIX.sub("", line)
    s = NUMS.sub("N", s)
    s = QUOTED.sub("'S'", s)
    s = PATHS.sub("P", s)
    s = HEX.sub("H", s)
    s = re.sub(r"\s+", " ", s).strip()
    # Cap length so one crazy-long exception doesn't dominate the signature.
    return s[:240]


def signature_id(sig):
    """Short id derived from signature — stable across runs."""
    h = hashlib.sha1(sig.encode("utf-8")).hexdigest()[:8]
    return f"novel-{h}"


def match_catalog(sig, catalog_entries):
    """Case-insensitive substring check of each catalog pattern against the
    raw signature. Returns the catalog entry or None.
    """
    sig_low = sig.lower()
    for e in catalog_entries:
        pat = e.get("pattern", "")
        if not pat:
            continue
        if e.get("is_regex"):
            try:
                if re.search(pat, sig, re.IGNORECASE):
                    return e
            except re.error:
                continue
        elif pat.lower() in sig_low:
            return e
    return None


def post_discord(msg):
    from _discord import post_to_channel
    ch = os.environ.get("DISCORD_CHANNEL_ALERTS", "")
    if not ch:
        log("WARN: DISCORD_CHANNEL_ALERTS not set; skipping post")
        return
    if DRY_RUN:
        log(f"[DRY] would post to Discord: {msg[:140]}...")
        return
    if not post_to_channel(ch, msg):
        log("Discord post failed")


def main():
    log(f"error_learner start {'[DRY-RUN]' if DRY_RUN else ''}")

    # Source data: /var/dashboard/errors.db, populated by collect_errors.py every
    # 2 minutes. We read all events from the last LOOKBACK_DAYS and group by
    # signature_hash. Pre-2026-04-26 this script tail-parsed text logs; we
    # migrated to the DB so the learner sees journalctl + docker + syslog +
    # pyapp_inbox events alongside text logs.
    import sqlite3
    DB_PATH = "/var/dashboard/errors.db"
    if not os.path.exists(DB_PATH):
        log(f"WARN: {DB_PATH} missing — collector hasn't run yet. Exiting.")
        return

    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        # Sum occurrences across all events with matching signature in the window.
        rows = conn.execute(
            """
            SELECT signature, signature_hash,
                   SUM(count) AS occurrences,
                   MAX(message) AS sample,
                   MAX(catalog_id) AS catalog_id
            FROM events
            WHERE last_seen >= datetime('now', ? )
            GROUP BY signature_hash
            ORDER BY occurrences DESC
            """,
            (f'-{LOOKBACK_DAYS} days',)
        ).fetchall()
    finally:
        conn.close()

    log(f"distinct signatures in last {LOOKBACK_DAYS}d (from DB): {len(rows)}")

    # Build a fake sig_counts/sig_sample so the rest of the code is untouched.
    sig_counts = Counter()
    sig_sample = {}
    sig_already_known = {}  # signature → bool (catalog_id present in DB row?)
    for r in rows:
        sig = r["signature"] or ""
        if not sig:
            continue
        sig_counts[sig] = int(r["occurrences"] or 0)
        sig_sample[sig] = r["sample"] or ""
        sig_already_known[sig] = bool(r["catalog_id"])

    # 3. Bucket against the catalog.
    cat = load_catalog()
    entries = cat.get("errors", [])
    by_id = {e["id"]: e for e in entries}

    known_bumps = Counter()       # id → week count
    new_recurring = []            # list of (sig, count, sample)
    new_novel = []                # list of (sig, count, sample)

    for sig, count in sig_counts.most_common():
        # If the collector already matched this signature against the catalog,
        # we trust that label — no need to re-scan patterns. Otherwise, try our
        # own match here (the catalog might have been updated since).
        if sig_already_known.get(sig):
            # Bump the most-recent matching entry. Fall through to match_catalog
            # so we know which entry id to bump.
            pass
        hit = match_catalog(sig_sample[sig], entries)
        if hit:
            known_bumps[hit["id"]] += count
            if VERBOSE:
                log(f"known  {hit['id']:30s} +{count}")
        elif count >= RECURRING_THRESHOLD:
            new_recurring.append((sig, count, sig_sample[sig]))
            if VERBOSE:
                log(f"NEW recurring (x{count}): {sig[:120]}")
        elif count >= MIN_NOVEL_THRESHOLD:
            new_novel.append((sig, count, sig_sample[sig]))
            if VERBOSE:
                log(f"NEW novel     (x{count}): {sig[:120]}")

    # 4. Update known entries.
    today = now_et().strftime("%Y-%m-%d")
    for eid, bump in known_bumps.items():
        e = by_id[eid]
        e["occurrence_count"] = int(e.get("occurrence_count", 0)) + bump
        e["last_seen"] = today

    # 5. Append new recurring + novel entries.
    for sig, count, sample in new_recurring:
        nid = signature_id(sig).replace("novel-", "recurring-")
        if nid in by_id:
            continue
        entry = {
            "id": nid,
            "pattern": sig[:120],
            "is_regex": False,
            "category": "recurring",
            "severity": "info",
            "auto_action": "none",
            "description": f"Auto-catalogued by error_learner (seen {count}x in 7d). Sample: {sample[:200]}",
            "runbook": "",
            "first_seen": today,
            "last_seen": today,
            "occurrence_count": count,
            "source": "learned",
        }
        entries.append(entry)
        by_id[nid] = entry

    for sig, count, sample in new_novel:
        nid = signature_id(sig)
        if nid in by_id:
            continue
        entry = {
            "id": nid,
            "pattern": sig[:120],
            "is_regex": False,
            "category": "novel",
            "severity": "info",
            "auto_action": "none",
            "description": f"Novel pattern (seen {count}x in 7d). Sample: {sample[:200]}",
            "runbook": "",
            "first_seen": today,
            "last_seen": today,
            "occurrence_count": count,
            "source": "learned",
        }
        entries.append(entry)
        by_id[nid] = entry

    cat["errors"] = entries
    save_catalog(cat)

    # 6. Build + post weekly summary.
    lines_out = []
    lines_out.append(f"**QuantAI weekly error digest — {today}**")
    lines_out.append(
        f"Window: last {LOOKBACK_DAYS}d. Events: {sum(sig_counts.values())}. "
        f"Signatures: {len(sig_counts)}."
    )
    lines_out.append(
        f"Known bumped: {len(known_bumps)}. New recurring: {len(new_recurring)}. "
        f"New novel: {len(new_novel)}. Catalog size: {len(entries)}."
    )
    if known_bumps:
        lines_out.append("\n__Top known recurrences this week:__")
        for eid, count in known_bumps.most_common(5):
            lines_out.append(f"• `{eid}` × {count}")
    if new_recurring:
        lines_out.append("\n__New recurring patterns (need runbook):__")
        for sig, count, _ in new_recurring[:5]:
            lines_out.append(f"• ×{count}: `{sig[:100]}`")
    if new_novel:
        lines_out.append(f"\n__{len(new_novel)} novel pattern(s) catalogued__ (awaiting review).")
    summary = "\n".join(lines_out)

    print(summary)
    post_discord(summary)

    log(
        f"error_learner done: known_bumped={len(known_bumps)} "
        f"new_recurring={len(new_recurring)} new_novel={len(new_novel)} "
        f"catalog_size={len(entries)}"
    )


if __name__ == "__main__":
    main()
