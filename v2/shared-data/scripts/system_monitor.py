#!/usr/bin/env python3
"""system_monitor.py — Deterministic health checker (Sentinel's eyes).

Runs every 2 min via cron. No LLM. No Discord. Writes a single JSON report
to /var/dashboard/state/system-health-report.json that sentinel_agent.py
reads as its primary input.

13 checks, each returning {status: ok|warning|error|info, ...details}:

  1. ibkr_port           — port 4002 connectivity (consecutive-fail counter)
  2. litellm_4000        — LiteLLM port 4000 connectivity
  3. clawroute_18790     — ClawRoute port 18790 connectivity
  4. cron_freshness      — every cron in cron-status.json ran within 2x interval
  5. disk                — root partition fill % (warn 85, error 92)
  6. memory              — virtual memory % (warn 85, error 92)
  7. self_learning_sla   — diagnosis + review files appear within 10 min of CLOSED journal entry
  8. weekly_synthesis    — Friday weekly_reports/<date>.md present by Fri 22:00 UTC
  9. collector_staleness — every /var/dashboard/state/*.json updated within 10 min
 10. journal_schema      — last trades.jsonl line parses + has decision object
 11. test_results        — quantai-test-results.json fresh < 24h
 12. graphify            — graphify-out/graph.json mtime < 7 days
 13. open_positions      — count from quantai-positions.json (informational)

Top-level status = highest severity seen across checks (error > warning > info > ok).
"""
from __future__ import annotations

import json
import os
import re
import socket
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


ET = ZoneInfo("America/New_York")
UTC = timezone.utc

REPO = Path("/home/trader/QuantAI")
RUNTIME_ROOT = Path(os.environ.get("QUANTAI_RUNTIME_ROOT", "/root/quantai-v2/shared-data"))
DASHBOARD_STATE_DIR = Path("/var/dashboard/state")
HEALTH_REPORT_PATH = DASHBOARD_STATE_DIR / "system-health-report.json"
HEARTBEAT_DIR = Path("/tmp/quantai-heartbeats")

JOURNAL_PATH = RUNTIME_ROOT / "journal" / "paper" / "trades.jsonl"
CAPABILITY_DIR = RUNTIME_ROOT / "capability_requests"
TRADE_REVIEW_DIR = RUNTIME_ROOT / "trade_reviews"
WEEKLY_REPORTS_DIR = RUNTIME_ROOT / "weekly_reports"
GRAPHIFY_GRAPH = REPO / "graphify-out" / "graph.json"
TEST_RESULTS_PATH = DASHBOARD_STATE_DIR / "quantai-test-results.json"
POSITIONS_PATH = DASHBOARD_STATE_DIR / "quantai-positions.json"
CRON_STATUS_PATH = DASHBOARD_STATE_DIR / "cron-status.json"

# Probe state files (consecutive-fail counters survive process restarts)
IBKR_FAIL_FILE = HEARTBEAT_DIR / "ibkr_probe_fail.json"
LITELLM_FAIL_FILE = HEARTBEAT_DIR / "litellm_4000_fail.json"
CLAWROUTE_FAIL_FILE = HEARTBEAT_DIR / "clawroute_18790_fail.json"

# Thresholds
DISK_WARN_PCT = 85
DISK_ERROR_PCT = 92
MEM_WARN_PCT = 85
MEM_ERROR_PCT = 92
SELF_LEARN_SLA_MIN = 10
COLLECTOR_STALE_MIN_MARKET = 10        # during 9:30–16:00 ET weekday
COLLECTOR_STALE_MIN_OFFHOURS = 60      # off-market: more lenient (some collectors only update on change)
TEST_RESULTS_STALE_HOURS = 24
GRAPHIFY_STALE_DAYS = 7

# Files explicitly NOT tracked for staleness (retired / unused / event-driven / scheduled-sparse)
COLLECTOR_SKIP_FILES = {
    "equity_history.jsonl",            # not JSON envelope, append-only
    "system-health-report.json",       # written by us, would create reflexive warning
    "quantai-test-results.json",       # checked separately by check_test_results
    "quantai-auto-heal.json",          # auto_heal retired 2026-05-03
    "quantai-sentinel.json",           # Sentinel runs ~5×/day (8:30, 4:15, 9 PM ET + weekend 10 AM)
                                       # — write cadence < collector threshold by design
}

# Files that ONLY update during market hours by design — skip when market closed
COLLECTOR_MARKET_HOURS_ONLY = {
    "quantai-positions.json",          # position_monitor cron is 13-20 UTC weekday only
    "agent-beta-state.json",           # collect_beta runs every minute but state may not change
    "agent-gamma-state.json",          # same
}

STATUS_RANK = {"ok": 0, "info": 1, "warning": 2, "error": 3}


# ── Probe primitive (duplicated from heartbeat_monitor.probe_ibkr_port for low coupling) ──

def _probe_port(host: str, port: int, timeout: float = 2.0) -> bool:
    """Return True if (host, port) accepts a TCP connection within timeout."""
    s = socket.socket()
    s.settimeout(timeout)
    try:
        return s.connect_ex((host, port)) == 0
    except OSError:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


def _read_fail_state(path: Path) -> dict:
    if not path.exists():
        return {"consecutive_fails": 0, "first_fail_at": None}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {"consecutive_fails": 0, "first_fail_at": None}


def _update_fail_state(path: Path, connected: bool) -> dict:
    HEARTBEAT_DIR.mkdir(parents=True, exist_ok=True)
    if connected:
        state = {"consecutive_fails": 0, "first_fail_at": None}
    else:
        existing = _read_fail_state(path)
        fails = existing.get("consecutive_fails", 0) + 1
        first = existing.get("first_fail_at") or datetime.now(UTC).isoformat()
        state = {"consecutive_fails": fails, "first_fail_at": first}
    try:
        path.write_text(json.dumps(state))
    except Exception:
        pass
    return state


def _port_check(host: str, port: int, fail_file: Path, label: str) -> dict:
    """Generic port check — returns the standard check dict.
    consecutive_fails >= 3 → error, >= 2 → warning, else ok.
    """
    connected = _probe_port(host, port)
    state = _update_fail_state(fail_file, connected)
    fails = state.get("consecutive_fails", 0)
    if connected:
        status = "ok"
    elif fails >= 3:
        status = "error"
    elif fails >= 2:
        status = "warning"
    else:
        status = "info"  # one transient blip — not actionable yet
    return {
        "status": status,
        "connected": connected,
        "consecutive_fails": fails,
        "first_fail_at": state.get("first_fail_at"),
        "label": label,
    }


# ── Individual checks ──────────────────────────────────────────────────

def check_ibkr_port() -> dict:
    return _port_check("127.0.0.1", 4002, IBKR_FAIL_FILE, "IBKR Gateway")


def check_litellm_4000() -> dict:
    return _port_check("127.0.0.1", 4000, LITELLM_FAIL_FILE, "LiteLLM proxy")


def check_clawroute_18790() -> dict:
    return _port_check("127.0.0.1", 18790, CLAWROUTE_FAIL_FILE, "ClawRoute")


def check_disk() -> dict:
    try:
        stat = os.statvfs("/")
        used_pct = round((1 - stat.f_bavail / stat.f_blocks) * 100, 1)
    except Exception as e:
        return {"status": "warning", "error": f"statvfs failed: {e}"}
    status = "error" if used_pct >= DISK_ERROR_PCT else \
             "warning" if used_pct >= DISK_WARN_PCT else "ok"
    return {"status": status, "used_pct": used_pct}


def check_memory() -> dict:
    try:
        info = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            k, _, v = line.partition(":")
            v = v.strip().split()[0] if v.strip() else "0"
            info[k.strip()] = int(v) if v.isdigit() else 0
        total = info.get("MemTotal", 1)
        avail = info.get("MemAvailable", info.get("MemFree", 0))
        used_pct = round((1 - avail / total) * 100, 1) if total else 0
    except Exception as e:
        return {"status": "warning", "error": f"meminfo failed: {e}"}
    status = "error" if used_pct >= MEM_ERROR_PCT else \
             "warning" if used_pct >= MEM_WARN_PCT else "ok"
    return {"status": status, "used_pct": used_pct}


def check_cron_freshness() -> dict:
    """Read cron-status.json and flag any cron whose last_run is > 2x its interval.

    collect_cron.py writes data.jobs (not data.crons) and uses field
    fresh: bool — we invert to find stale entries. Market-hours-only crons
    that are stale off-hours are NOT flagged (they're working as designed).
    """
    if not CRON_STATUS_PATH.exists():
        return {"status": "warning", "error": "cron-status.json missing"}
    try:
        doc = json.loads(CRON_STATUS_PATH.read_text())
        jobs = doc.get("data", {}).get("jobs", [])
    except Exception as e:
        return {"status": "warning", "error": f"parse failed: {e}"}
    if not jobs:
        return {"status": "warning", "error": "cron-status.json has empty jobs list"}

    now_et = datetime.now(ET)
    market = (now_et.weekday() < 5 and
              ((now_et.hour == 9 and now_et.minute >= 30) or 10 <= now_et.hour < 16))

    stale = []
    for c in jobs:
        if c.get("fresh", True):
            continue
        # Skip market-hours-only crons when we're off-market — staleness is expected
        sched_et = (c.get("schedule_et") or "").lower()
        if not market and ("9 am" in sched_et or "mon" in sched_et and "fri" in sched_et):
            # Heuristic: any market-window-bounded cron is expected to be
            # stale off-hours. Don't alarm.
            continue
        stale.append({
            "name": c.get("name"),
            "last_run": c.get("last_run"),
            "schedule": c.get("schedule"),
            "age_sec": c.get("last_run_age_sec"),
        })

    if not stale:
        return {"status": "ok", "stale_crons": [], "checked": len(jobs)}
    return {
        "status": "error" if market else "warning",
        "stale_crons": stale[:8],
        "checked": len(jobs),
    }


def check_self_learning_sla() -> dict:
    """For every CLOSED journal entry in the last 24h, both diagnosis JSON and
    review .md must exist within SELF_LEARN_SLA_MIN of close time.
    Tolerates missing journal (returns ok with note) so non-trading days don't alarm.
    """
    if not JOURNAL_PATH.exists():
        return {"status": "info", "note": "journal not present"}
    cutoff = datetime.now(UTC).timestamp() - 86400
    missing = []
    try:
        with open(JOURNAL_PATH, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            block, data = 65536, b""
            while size > 0 and data.count(b"\n") <= 200:
                read = min(block, size)
                size -= read
                f.seek(size)
                data = f.read(read) + data
        for line in data.decode("utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if rec.get("status") != "CLOSED":
                continue
            closed_at = rec.get("closed_at") or rec.get("close_ts")
            if not closed_at:
                continue
            try:
                closed_dt = datetime.fromisoformat(closed_at.replace("Z", "+00:00"))
            except Exception:
                continue
            if closed_dt.timestamp() < cutoff:
                continue
            tid = rec.get("trade_id") or rec.get("id")
            agent = rec.get("agent_source") or rec.get("agent") or "agent_alpha"
            if not tid:
                continue
            diag = CAPABILITY_DIR / agent / f"{tid}.json"
            review = TRADE_REVIEW_DIR / agent / f"{tid}.md"
            sla_deadline = closed_dt.timestamp() + SELF_LEARN_SLA_MIN * 60
            now_ts = datetime.now(UTC).timestamp()
            if now_ts < sla_deadline:
                continue  # still within grace window
            if not diag.exists() or not review.exists():
                missing.append({
                    "trade_id": tid, "agent": agent,
                    "diagnosis_present": diag.exists(),
                    "review_present": review.exists(),
                    "closed_at": closed_at,
                })
    except Exception as e:
        return {"status": "warning", "error": f"journal scan failed: {e}"}
    if missing:
        return {"status": "error", "missing": missing[:10]}
    return {"status": "ok", "checked_window_hours": 24}


def check_weekly_synthesis() -> dict:
    """Friday after 22:00 UTC, last week's report file must exist."""
    now_utc = datetime.now(UTC)
    # Only meaningful on Friday after 22:00 UTC, or any time Sat/Sun
    if now_utc.weekday() < 4 or (now_utc.weekday() == 4 and now_utc.hour < 22):
        return {"status": "info", "note": "outside synthesis check window"}
    if not WEEKLY_REPORTS_DIR.exists():
        return {"status": "warning", "error": "weekly_reports dir missing"}
    # Find most recent .md file
    reports = sorted(WEEKLY_REPORTS_DIR.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not reports:
        return {"status": "error", "error": "no weekly reports found"}
    latest = reports[0]
    age_hours = (now_utc.timestamp() - latest.stat().st_mtime) / 3600
    if age_hours > 24 * 8:  # >8 days = missed at least one Friday
        return {"status": "error", "latest": latest.name, "age_hours": round(age_hours, 1)}
    return {"status": "ok", "latest": latest.name, "age_hours": round(age_hours, 1)}


def check_collector_staleness() -> dict:
    """Every state file under /var/dashboard/state/ should have last_updated within
    threshold. Threshold is 10 min during market hours, 60 min off-hours (some
    collectors only update on change).
    """
    if not DASHBOARD_STATE_DIR.exists():
        return {"status": "error", "error": "dashboard state dir missing"}
    now_et = datetime.now(ET)
    market = (now_et.weekday() < 5 and
              ((now_et.hour == 9 and now_et.minute >= 30) or 10 <= now_et.hour < 16))
    threshold_min = COLLECTOR_STALE_MIN_MARKET if market else COLLECTOR_STALE_MIN_OFFHOURS
    cutoff_ts = datetime.now(UTC).timestamp() - threshold_min * 60
    stale = []
    for p in DASHBOARD_STATE_DIR.glob("*.json"):
        if p.name in COLLECTOR_SKIP_FILES:
            continue
        # Skip market-hours-only collectors when market is closed
        if not market and p.name in COLLECTOR_MARKET_HOURS_ONLY:
            continue
        try:
            doc = json.loads(p.read_text())
            lu = doc.get("last_updated")
            if not lu:
                continue
            dt = datetime.fromisoformat(lu.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            if dt.timestamp() < cutoff_ts:
                stale.append({"file": p.name, "last_updated": lu})
        except Exception:
            continue
    if not stale:
        return {"status": "ok", "stale_files": [], "threshold_min": threshold_min}
    # Stale during market hours = error; off-hours = warning
    status = "error" if market else "warning"
    return {"status": status, "stale_files": stale[:10], "threshold_min": threshold_min}


def check_journal_schema() -> dict:
    """Last journal line must parse and contain core required fields.
    Required: status + (trade_id or id) + legs.
    Note: 'decision' is on entry-time records only and may be absent from
    older or migrated CLOSED entries — not required by this check.
    """
    if not JOURNAL_PATH.exists():
        return {"status": "info", "note": "journal not present"}
    try:
        with open(JOURNAL_PATH, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            block, data = 4096, b""
            while size > 0 and b"\n" not in data[:-1]:
                read = min(block, size)
                size -= read
                f.seek(size)
                data = f.read(read) + data
        last_line = data.decode("utf-8", errors="replace").strip().splitlines()
        if not last_line:
            return {"status": "info", "note": "journal empty"}
        rec = json.loads(last_line[-1])
    except Exception as e:
        return {"status": "error", "error": f"last journal line invalid: {e}"}
    tid = rec.get("trade_id") or rec.get("id")
    missing = []
    if "status" not in rec:
        missing.append("status")
    if not tid:
        missing.append("trade_id|id")
    if "legs" not in rec:
        missing.append("legs")
    if missing:
        return {"status": "warning", "error": f"missing required fields: {missing}",
                "trade_id": tid}
    return {"status": "ok", "trade_id": tid, "trade_status": rec.get("status")}


def check_test_results() -> dict:
    if not TEST_RESULTS_PATH.exists():
        return {"status": "warning", "error": "no test results file"}
    try:
        doc = json.loads(TEST_RESULTS_PATH.read_text())
        lu = doc.get("last_updated")
        passed = doc.get("data", {}).get("passed")
        failed = doc.get("data", {}).get("failed", 0)
    except Exception as e:
        return {"status": "warning", "error": f"parse failed: {e}"}
    age_hours = None
    if lu:
        try:
            dt = datetime.fromisoformat(lu.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            age_hours = (datetime.now(UTC) - dt).total_seconds() / 3600
        except Exception:
            pass
    if failed and failed > 0:
        return {"status": "error", "passed": passed, "failed": failed, "age_hours": age_hours}
    if age_hours is not None and age_hours > TEST_RESULTS_STALE_HOURS:
        return {"status": "warning", "passed": passed, "failed": failed,
                "age_hours": round(age_hours, 1)}
    return {"status": "ok", "passed": passed, "failed": failed,
            "age_hours": round(age_hours, 1) if age_hours is not None else None}


def check_graphify() -> dict:
    if not GRAPHIFY_GRAPH.exists():
        return {"status": "info", "note": "graphify-out/graph.json not present"}
    age_days = (datetime.now(UTC).timestamp() - GRAPHIFY_GRAPH.stat().st_mtime) / 86400
    status = "info" if age_days > GRAPHIFY_STALE_DAYS else "ok"
    return {"status": status, "age_days": round(age_days, 1)}


def check_open_positions() -> dict:
    if not POSITIONS_PATH.exists():
        return {"status": "info", "count": 0, "note": "positions file missing"}
    try:
        doc = json.loads(POSITIONS_PATH.read_text())
        count = doc.get("data", {}).get("count", 0)
    except Exception as e:
        return {"status": "warning", "error": f"parse failed: {e}"}
    return {"status": "ok", "count": count}


def check_dashboard_html_size() -> dict:
    """Detect React SPA being overwritten by a tile-grid generator.

    The React SPA is ~93KB (88KB pre-2026-05-04, 93KB post-Sentinel-cards).
    The retired generate.py output was ~5KB. Anything <10KB means the SPA
    has been clobbered and must be restored from
    /home/trader/dashboard/index.html.

    Added 2026-05-04 after the second SPA-overwrite incident.
    """
    p = Path("/var/dashboard/index.html")
    if not p.exists():
        return {"status": "error", "error": "dashboard index.html missing"}
    try:
        size = p.stat().st_size
    except Exception as e:
        return {"status": "error", "error": f"stat failed: {e}"}
    if size < 10_000:
        return {
            "status": "error",
            "size_bytes": size,
            "hint": "React SPA appears overwritten — restore from "
                    "/home/trader/dashboard/index.html with sudo cp",
        }
    return {"status": "ok", "size_bytes": size}


# ── Aggregator ──────────────────────────────────────────────────

CHECKS = [
    ("ibkr_port", check_ibkr_port),
    ("litellm_4000", check_litellm_4000),
    ("clawroute_18790", check_clawroute_18790),
    ("cron_freshness", check_cron_freshness),
    ("disk", check_disk),
    ("memory", check_memory),
    ("self_learning_sla", check_self_learning_sla),
    ("weekly_synthesis", check_weekly_synthesis),
    ("collector_staleness", check_collector_staleness),
    ("journal_schema", check_journal_schema),
    ("test_results", check_test_results),
    ("dashboard_html_size", check_dashboard_html_size),
    ("graphify", check_graphify),
    ("open_positions", check_open_positions),
]


def run_all_checks() -> dict:
    results = {}
    for name, fn in CHECKS:
        try:
            results[name] = fn()
        except Exception as e:
            results[name] = {"status": "warning", "error": f"check raised: {type(e).__name__}: {e}"}
    return results


def aggregate_status(results: dict) -> str:
    worst = "ok"
    for v in results.values():
        s = v.get("status", "ok")
        if STATUS_RANK.get(s, 0) > STATUS_RANK.get(worst, 0):
            worst = s
    return worst


def write_report(results: dict) -> Path:
    DASHBOARD_STATE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "last_updated": datetime.now(UTC).isoformat(),
        "status": aggregate_status(results),
        "data": {"checks": results},
    }
    tmp = HEALTH_REPORT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    os.replace(tmp, HEALTH_REPORT_PATH)
    return HEALTH_REPORT_PATH


def main() -> int:
    results = run_all_checks()
    path = write_report(results)
    overall = aggregate_status(results)
    counts = {"ok": 0, "info": 0, "warning": 0, "error": 0}
    for v in results.values():
        counts[v.get("status", "ok")] = counts.get(v.get("status", "ok"), 0) + 1
    print(f"system_monitor: status={overall} "
          f"ok={counts['ok']} info={counts['info']} "
          f"warn={counts['warning']} err={counts['error']} "
          f"-> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
