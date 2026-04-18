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

Posts a weekly summary to Discord #logs (via DISCORD_WEBHOOK_CHAT).

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
NOISE_PREFIXES = ("•", "[debate]", "- **", "**", "**✅", "**❌", "Diagonal:", "BUY ", "SELL ", "SENIOR TRADER", "JUDGE:", "BULL:", "BEAR:")

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
    url = os.environ.get("DISCORD_WEBHOOK_LOGS") or os.environ.get("DISCORD_WEBHOOK_CHAT")
    if not url:
        log("WARN: no Discord webhook env var set; skipping post")
        return
    if DRY_RUN:
        log(f"[DRY] would post to Discord: {msg[:140]}...")
        return
    body = json.dumps({"content": msg[:1900]}).encode("utf-8")
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status >= 300:
                log(f"Discord post failed: HTTP {resp.status}")
    except Exception as ex:
        log(f"Discord post error: {ex}")


def main():
    log(f"error_learner start {'[DRY-RUN]' if DRY_RUN else ''}")
    cutoff = now_et() - timedelta(days=LOOKBACK_DAYS)

    # 1. Collect candidate error lines across logs.
    lines = []
    for p in LOGS:
        lines.extend(tail_since(p, cutoff))
    error_lines = [ln for ln in lines if looks_like_error(ln)]
    log(f"candidate error lines in last {LOOKBACK_DAYS}d: {len(error_lines)}")

    # 2. Signature-count everything.
    sig_counts = Counter()
    sig_sample = {}
    for ln in error_lines:
        sig = line_signature(ln)
        if not sig:
            continue
        sig_counts[sig] += 1
        sig_sample.setdefault(sig, ln)

    # 3. Bucket against the catalog.
    cat = load_catalog()
    entries = cat.get("errors", [])
    by_id = {e["id"]: e for e in entries}

    known_bumps = Counter()       # id → week count
    new_recurring = []            # list of (sig, count, sample)
    new_novel = []                # list of (sig, count, sample)

    for sig, count in sig_counts.most_common():
        # Match catalog patterns against the raw sample (preserves exact
        # numbers/paths) rather than the mangled signature.
        hit = match_catalog(sig_sample[sig], entries)
        if hit:
            known_bumps[hit["id"]] += count
            if VERBOSE:
                log(f"known  {hit['id']:30s} +{count}")
        elif count >= RECURRING_THRESHOLD:
            new_recurring.append((sig, count, sig_sample[sig]))
            if VERBOSE:
                log(f"NEW recurring (x{count}): {sig[:120]}")
        else:
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
            "severity": "unknown",
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
            "severity": "unknown",
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
        f"Window: last {LOOKBACK_DAYS}d. Lines scanned: {len(error_lines)}. "
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
