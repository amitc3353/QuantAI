#!/usr/bin/env python3
"""
QuantAI Error Detector — Phase E1

Runs every 5 min via cron. Tails recent log lines, matches against the error
catalog (docs/error-catalog.json), and:

- KNOWN + auto_action in {retry, restart_service, skip} → execute, post #logs
- KNOWN + auto_action == "none" → post runbook link to #karna-approvals
- UNKNOWN (matches error tokens but no catalog pattern) → post #karna-alerts

Dedup: same pattern within 30 minutes = one Discord message. State at
/tmp/quantai-error-dedup.json.

Writes /var/dashboard/state/quantai-errors.json for the dashboard Errors tab.
Atomically updates docs/error-catalog.json (.tmp + os.replace, with a .bak).

Usage:
  python3 error_detector.py           # normal run (cron)
  python3 error_detector.py --dry-run # scan and classify, no Discord, no writes
  python3 error_detector.py --verbose # print per-line classification
"""

import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# --- Env loading (same pattern as run_pipeline.py) ---
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
RUNBOOKS_REL = "docs/runbooks"

LOGS = {
    "pipeline": Path("/root/quantai-v2/shared-data/logs/pipeline.log"),
    "heartbeat": Path("/root/quantai-v2/shared-data/logs/heartbeat.log"),
    "position_monitor": Path("/root/quantai-v2/shared-data/logs/position_monitor.log"),
    "error_detector": Path("/root/quantai-v2/shared-data/logs/error_detector.log"),
}

DASHBOARD_STATE = Path("/var/dashboard/state/quantai-errors.json")
DEDUP_FILE = Path("/tmp/quantai-error-dedup.json")
DEDUP_MINUTES = 30
TAIL_LINES = 500

DRY_RUN = "--dry-run" in sys.argv
VERBOSE = "--verbose" in sys.argv or "-v" in sys.argv

# Heuristic tokens that mark a log line as "looks like an error".
# A line matching one of these but matching NO catalog pattern is UNKNOWN.
ERROR_TOKENS = (
    "traceback",
    "exception",
    "error:",
    "error -",
    "failed",
    "failure",
    "rejected",
    "connectionerror",
    "timeout",
    "refused",
    "404",
    "500",
    "panic",
    "unhandled",
    "aborting",
    "fatal",
)

# Lines containing these are noise, not real errors (e.g. debate logging its own state).
ERROR_TOKEN_IGNORE = (
    "no errors",
    "error_count: 0",
    "0 errors",
)


def now_et():
    return datetime.now(ET)


def log(msg):
    print(f"[{now_et().strftime('%H:%M:%S')}] {msg}")


# --- Catalog I/O -----------------------------------------------------

def load_catalog():
    try:
        with open(CATALOG_PATH) as f:
            return json.load(f)
    except Exception as e:
        log(f"ERROR: cannot load catalog {CATALOG_PATH}: {e}")
        return {"schema_version": 1, "errors": []}


def save_catalog(catalog):
    """Atomic write with .bak backup. Skipped in DRY_RUN."""
    if DRY_RUN:
        return True
    try:
        tmp = Path(str(CATALOG_PATH) + ".tmp")
        bak = Path(str(CATALOG_PATH) + ".bak")
        catalog["last_updated"] = now_et().isoformat()
        tmp.write_text(json.dumps(catalog, indent=2))
        # backup current before replacing
        if CATALOG_PATH.exists():
            try:
                bak.write_text(CATALOG_PATH.read_text())
            except Exception:
                pass
        os.replace(tmp, CATALOG_PATH)
        return True
    except Exception as e:
        log(f"ERROR: save_catalog failed: {e}")
        return False


# --- Log tailing -----------------------------------------------------

def tail_lines(path, n):
    if not path.exists():
        return []
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            block = 65536
            data = b""
            while size > 0 and data.count(b"\n") <= n:
                read = min(block, size)
                size -= read
                f.seek(size)
                data = f.read(read) + data
            lines = data.decode("utf-8", errors="replace").splitlines()
            return lines[-n:]
    except Exception as e:
        log(f"WARN: tail failed for {path}: {e}")
        return []


# --- Matching --------------------------------------------------------

def line_matches_entry(line, entry):
    pat = entry.get("pattern", "")
    if not pat:
        return False
    if entry.get("is_regex"):
        try:
            return re.search(pat, line, flags=re.IGNORECASE) is not None
        except re.error:
            return False
    return pat.lower() in line.lower()


def looks_like_error(line):
    low = line.lower()
    if any(ig in low for ig in ERROR_TOKEN_IGNORE):
        return False
    return any(tok in low for tok in ERROR_TOKENS)


# --- Dedup -----------------------------------------------------------

def load_dedup():
    if not DEDUP_FILE.exists():
        return {}
    try:
        return json.loads(DEDUP_FILE.read_text())
    except Exception:
        return {}


def save_dedup(d):
    if DRY_RUN:
        return
    try:
        DEDUP_FILE.write_text(json.dumps(d))
    except Exception as e:
        log(f"WARN: save_dedup failed: {e}")


def should_alert(key, dedup):
    """Return True if we haven't alerted on this key within DEDUP_MINUTES."""
    last_iso = dedup.get(key)
    if not last_iso:
        return True
    try:
        last = datetime.fromisoformat(last_iso)
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        age_min = (datetime.now(timezone.utc) - last).total_seconds() / 60
        return age_min >= DEDUP_MINUTES
    except Exception:
        return True


def mark_alerted(key, dedup):
    dedup[key] = datetime.now(timezone.utc).isoformat()


# --- Discord ---------------------------------------------------------

# Two webhooks:
#   DISCORD_WEBHOOK_CHAT   → informational (known + auto-fixed errors)
#   DISCORD_WEBHOOK_ALERTS → unknown + critical errors (#alerts channel)
# If DISCORD_WEBHOOK_ALERTS is unset, alerts fall back to DISCORD_WEBHOOK_CHAT.

def post_discord(level, msg):
    """level is 'chat' (informational) or 'alert' (unknown/critical)."""
    chat_url = os.environ.get("DISCORD_WEBHOOK_CHAT", "")
    alert_url = os.environ.get("DISCORD_WEBHOOK_ALERTS", "") or chat_url
    url = alert_url if level == "alert" else chat_url
    if not url:
        log("WARN: no Discord webhook configured (DISCORD_WEBHOOK_CHAT unset); skipping post")
        return False
    if DRY_RUN:
        log(f"[DRY] would post [{level}]: {msg[:120]}")
        return True
    prefix = "🚨" if level == "alert" else "📋"
    payload = json.dumps({"content": f"{prefix} {msg}"}).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json", "User-Agent": "QuantAI-ErrorDetector/1.0"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status in (200, 204)
    except Exception as e:
        log(f"WARN: Discord POST failed ({level}): {e}")
        return False


# --- Auto-fix actions ------------------------------------------------

def action_retry(entry, line):
    """Retry the command specified in entry.retry_command after 60s delay.
    If no retry_command, treat as 'wait for next cron cycle'."""
    cmd = entry.get("retry_command")
    if not cmd:
        return ("deferred", "no retry_command in catalog entry — next cron cycle acts as retry")
    log(f"retry: sleeping 60s before re-running: {cmd}")
    time.sleep(60)
    try:
        proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=600)
        ok = proc.returncode == 0
        summary = f"exit={proc.returncode}"
        if proc.stderr:
            summary += f" stderr={proc.stderr[:200]!r}"
        return ("ok" if ok else "failed", summary)
    except subprocess.TimeoutExpired:
        return ("failed", "retry itself timed out after 600s")
    except Exception as e:
        return ("failed", f"retry exception: {e}")


def action_restart_service(entry, line):
    """systemctl restart the service named in entry.restart_target."""
    svc = entry.get("restart_target")
    if not svc:
        return ("skipped", "no restart_target in catalog entry")
    try:
        proc = subprocess.run(["sudo", "-n", "systemctl", "restart", svc], capture_output=True, text=True, timeout=30)
        return (("ok" if proc.returncode == 0 else "failed"), f"exit={proc.returncode}")
    except Exception as e:
        return ("failed", f"restart exception: {e}")


def action_skip(entry, line):
    return ("ok", "logged and skipped; next cron cycle handles retry")


AUTO_ACTIONS = {
    "retry": action_retry,
    "restart_service": action_restart_service,
    "skip": action_skip,
}


# --- Core detection --------------------------------------------------

def classify_lines():
    """Scan all log files, return per-entry and unknown aggregations."""
    catalog = load_catalog()
    entries = catalog.get("errors", [])

    known_hits = {}   # entry_id -> {"entry": entry, "count": int, "latest_line": str, "latest_source": str}
    unknown_hits = {} # signature -> {"line": str, "count": int, "source": str}

    for source, path in LOGS.items():
        if source == "error_detector":
            continue  # avoid feedback loop
        lines = tail_lines(path, TAIL_LINES)
        for line in lines:
            if not line.strip():
                continue
            matched = False
            for e in entries:
                if line_matches_entry(line, e):
                    eid = e["id"]
                    if eid not in known_hits:
                        known_hits[eid] = {"entry": e, "count": 0, "latest_line": line, "latest_source": source}
                    known_hits[eid]["count"] += 1
                    known_hits[eid]["latest_line"] = line
                    known_hits[eid]["latest_source"] = source
                    matched = True
                    # A single line can match multiple catalog entries (e.g. generic + specific).
                    # Keep counting all, but Discord dedup is per-pattern so we won't spam.
            if not matched and looks_like_error(line):
                # signature: strip timestamps / ids / hex for stability
                sig = re.sub(r"\d+", "#", line)[:200]
                if sig not in unknown_hits:
                    unknown_hits[sig] = {"line": line, "count": 0, "source": source}
                unknown_hits[sig]["count"] += 1

    return catalog, entries, known_hits, unknown_hits


def handle_known(entry, info, catalog_map, dedup, dashboard_events):
    eid = entry["id"]
    action = entry.get("auto_action", "none")
    line = info["latest_line"]
    source = info["latest_source"]
    count_in_window = info["count"]

    action_result = None
    action_detail = ""
    severity = entry.get("severity", "?")
    if action in AUTO_ACTIONS:
        action_result, action_detail = AUTO_ACTIONS[action](entry, line)
        if should_alert(f"known:{eid}", dedup):
            post_discord("chat", f"auto-fixed known error `{eid}` ({action}): {action_detail}. Line: {line[:200]}")
            mark_alerted(f"known:{eid}", dedup)
    elif action == "none":
        # Critical known errors escalate to #alerts; lesser severities stay informational.
        level = "alert" if severity == "critical" else "chat"
        if should_alert(f"known:{eid}", dedup):
            runbook = entry.get("runbook", "")
            post_discord(level, f"known error `{eid}` (severity={severity}). Runbook: `{runbook}`. Line: {line[:200]}")
            mark_alerted(f"known:{eid}", dedup)

    # update catalog aggregates
    target = catalog_map.get(eid)
    if target is not None:
        target["occurrence_count"] = int(target.get("occurrence_count", 0)) + count_in_window
        target["last_seen"] = now_et().date().isoformat()

    dashboard_events.append({
        "id": eid,
        "classification": "known",
        "category": entry.get("category", "?"),
        "severity": entry.get("severity", "?"),
        "auto_action": action,
        "action_result": action_result,
        "action_detail": action_detail,
        "occurrences_this_window": count_in_window,
        "latest_line": line[:300],
        "source": source,
        "runbook": entry.get("runbook", ""),
        "timestamp": now_et().isoformat(),
    })


def handle_unknown(signature, info, dedup, dashboard_events):
    line = info["line"]
    source = info["source"]
    count = info["count"]
    key = f"unknown:{signature[:100]}"
    if should_alert(key, dedup):
        post_discord("alert", f"UNKNOWN error detected (source={source}, seen {count}x this window). Needs investigation: {line[:300]}")
        mark_alerted(key, dedup)

    dashboard_events.append({
        "id": None,
        "classification": "unknown",
        "category": "novel",
        "severity": "unknown",
        "auto_action": "none",
        "action_result": None,
        "action_detail": "",
        "occurrences_this_window": count,
        "latest_line": line[:300],
        "source": source,
        "runbook": "",
        "timestamp": now_et().isoformat(),
    })


def write_dashboard_state(dashboard_events, catalog):
    try:
        DASHBOARD_STATE.parent.mkdir(parents=True, exist_ok=True)
        auto_fixed = sum(1 for e in dashboard_events if e["classification"] == "known" and e["auto_action"] in AUTO_ACTIONS)
        known_manual = sum(1 for e in dashboard_events if e["classification"] == "known" and e["auto_action"] == "none")
        unknown = sum(1 for e in dashboard_events if e["classification"] == "unknown")
        status = "error" if unknown > 0 else ("warning" if known_manual > 0 else "ok")
        payload = {
            "last_updated": now_et().isoformat(),
            "status": status,
            "data": {
                "catalog_size": len(catalog.get("errors", [])),
                "window_events": len(dashboard_events),
                "summary": {
                    "auto_fixed": auto_fixed,
                    "known_manual": known_manual,
                    "unknown": unknown,
                },
                "events": dashboard_events[-50:],  # cap payload
            },
        }
        if DRY_RUN:
            log(f"[DRY] would write dashboard state: {json.dumps(payload['data']['summary'])}")
            return
        tmp = Path(str(DASHBOARD_STATE) + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        os.replace(tmp, DASHBOARD_STATE)
    except Exception as e:
        log(f"WARN: write_dashboard_state failed: {e}")


def main():
    log(f"error_detector start{' [DRY-RUN]' if DRY_RUN else ''}")

    catalog, entries, known_hits, unknown_hits = classify_lines()
    catalog_map = {e["id"]: e for e in catalog.get("errors", [])}
    dedup = load_dedup()
    dashboard_events = []

    log(f"known patterns seen: {len(known_hits)}, unknown signatures seen: {len(unknown_hits)}")

    for eid, info in known_hits.items():
        if VERBOSE:
            log(f"  KNOWN {eid} x{info['count']}: {info['latest_line'][:100]}")
        handle_known(info["entry"], info, catalog_map, dedup, dashboard_events)

    for sig, info in unknown_hits.items():
        if VERBOSE:
            log(f"  UNKNOWN x{info['count']}: {info['line'][:100]}")
        handle_unknown(sig, info, dedup, dashboard_events)

    save_dedup(dedup)
    if known_hits:
        save_catalog(catalog)
    write_dashboard_state(dashboard_events, catalog)

    log(f"error_detector done: catalog={len(entries)} events={len(dashboard_events)}")


if __name__ == "__main__":
    main()
