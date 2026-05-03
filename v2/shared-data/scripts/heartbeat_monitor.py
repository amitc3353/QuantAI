
import logging
import sys
sys.path.insert(0, '/home/trader/QuantAI/v2/shared-data/scripts')
from _logger import setup as _logger_setup
_logger_setup('heartbeat_monitor')

#!/usr/bin/env python3
"""
QuantAI Heartbeat Monitor

Checks /tmp/quantai-heartbeats/pipeline.beat for staleness.
During market hours (9-16 ET Mon-Fri), alerts Discord if the beat is missing
or older than STALE_MIN. Outside market hours, no alerts.

Writes /var/dashboard/state/quantai-heartbeats.json each run.
Alert cooldown: one Discord message per COOLDOWN_MIN minutes max.

Run every 2 minutes via cron:
  */2 * * * *  python3 /home/trader/QuantAI/v2/shared-data/scripts/heartbeat_monitor.py >> /root/quantai-v2/shared-data/logs/heartbeat.log 2>&1
"""

import os
import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# Auto-load .env (same pattern as run_pipeline.py)
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
BEAT_DIR = Path("/tmp/quantai-heartbeats")
STATE_FILE = Path("/var/dashboard/state/quantai-heartbeats.json")
COOLDOWN_FILE = BEAT_DIR / "alert_cooldown.json"

STALE_MIN = 20       # pipeline beat older than this triggers alert
COOLDOWN_MIN = 30    # minimum minutes between Discord alerts per beat name
OFF_HOURS_LOG_INTERVAL_MIN = 60   # off-hours: log status line at most this often
LOG_THROTTLE_FILE = Path("/tmp/quantai-heartbeat-last-log.json")

IBKR_HOST = "127.0.0.1"
IBKR_PORT = 4002
IBKR_PROBE_COOLDOWN_MIN = 30
IBKR_PROBE_FAIL_FILE = Path("/tmp/quantai-heartbeats/ibkr_probe_fail.json")
IBKR_CONSECUTIVE_FAILS_ALERT = 2  # alert after ~4 min (2 consecutive 2-min ticks)


def is_market_hours():
    """NYSE equity options hours: 09:30 → 16:00 ET, Mon-Fri."""
    now = datetime.now(ET)
    if now.weekday() >= 5:
        return False
    h, m = now.hour, now.minute
    return (h == 9 and m >= 30) or (10 <= h < 16)


def read_beat(name):
    path = BEAT_DIR / f"{name}.beat"
    if not path.exists():
        return None
    try:
        dt = datetime.fromisoformat(path.read_text().strip())
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def cooldown_ok(name):
    if not COOLDOWN_FILE.exists():
        return True
    try:
        data = json.loads(COOLDOWN_FILE.read_text())
        last_str = data.get(name)
        if not last_str:
            return True
        last = datetime.fromisoformat(last_str)
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        elapsed = (datetime.now(timezone.utc) - last).total_seconds()
        return elapsed >= COOLDOWN_MIN * 60
    except Exception:
        return True


def record_cooldown(name):
    BEAT_DIR.mkdir(parents=True, exist_ok=True)
    data = {}
    if COOLDOWN_FILE.exists():
        try:
            data = json.loads(COOLDOWN_FILE.read_text())
        except Exception:
            pass
    data[name] = datetime.now(timezone.utc).isoformat()
    COOLDOWN_FILE.write_text(json.dumps(data))


def post_discord(msg):
    from _discord import post_to_channel
    ch = os.environ.get("DISCORD_CHANNEL_ALERTS", "")
    if not ch:
        logging.warning("DISCORD_CHANNEL_ALERTS not set; alert skipped")
        return
    if not post_to_channel(ch, msg):
        logging.warning("Discord post failed (heartbeat alert)")


def should_print_status(beat_status: str, market: bool) -> bool:
    """Decide whether to print the pipeline-beat status line this cron tick.
    During market hours: always print (every 2 min — no spam since the operator
    cares about that data). Off-hours: print only on status change or once per
    OFF_HOURS_LOG_INTERVAL_MIN.
    Fail-open: any IO error returns True so we never silently lose forensic info.
    """
    if market:
        return True
    try:
        if not LOG_THROTTLE_FILE.exists():
            return True
        data = json.loads(LOG_THROTTLE_FILE.read_text())
        last_status = data.get("last_status")
        last_iso = data.get("last_log_at")
        if last_status != beat_status:
            return True
        if not last_iso:
            return True
        last = datetime.fromisoformat(last_iso)
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        elapsed_min = (datetime.now(timezone.utc) - last).total_seconds() / 60.0
        return elapsed_min >= OFF_HOURS_LOG_INTERVAL_MIN
    except Exception:
        return True


def record_log(beat_status: str):
    """Persist the last-print timestamp + status. Failure is non-fatal."""
    try:
        LOG_THROTTLE_FILE.write_text(json.dumps({
            "last_log_at": datetime.now(timezone.utc).isoformat(),
            "last_status": beat_status,
        }))
    except Exception:
        pass


def probe_ibkr_port() -> bool:
    """Return True if IBKR Gateway port 4002 is accepting connections.

    Catches all exceptions — this function must never crash the caller.
    OSError can be raised by connect_ex() on some platforms/network conditions.
    """
    import socket
    s = socket.socket()
    s.settimeout(2)
    try:
        return s.connect_ex((IBKR_HOST, IBKR_PORT)) == 0
    except OSError:
        return False
    finally:
        s.close()


def get_ibkr_probe_state() -> dict:
    """Read persistent probe failure state (survives process restarts)."""
    if not IBKR_PROBE_FAIL_FILE.exists():
        return {"consecutive_fails": 0, "first_fail_at": None}
    try:
        return json.loads(IBKR_PROBE_FAIL_FILE.read_text())
    except Exception:
        return {"consecutive_fails": 0, "first_fail_at": None}


def update_ibkr_probe_state(connected: bool) -> dict:
    """Persist probe state; return updated state dict."""
    BEAT_DIR.mkdir(parents=True, exist_ok=True)
    if connected:
        state = {"consecutive_fails": 0, "first_fail_at": None}
    else:
        existing = get_ibkr_probe_state()
        fails = existing.get("consecutive_fails", 0) + 1
        first = existing.get("first_fail_at") or datetime.now(timezone.utc).isoformat()
        state = {"consecutive_fails": fails, "first_fail_at": first}
    IBKR_PROBE_FAIL_FILE.write_text(json.dumps(state))
    return state


def main():
    BEAT_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(ET)
    now_utc = datetime.now(timezone.utc)
    market = is_market_hours()

    # --- Evaluate pipeline beat ---
    beat = read_beat("pipeline")
    age_min = None
    beat_status = "unknown"

    if beat is None:
        beat_status = "missing"
    else:
        age_min = (now_utc - beat).total_seconds() / 60
        beat_status = "stale" if age_min > STALE_MIN else "ok"

    # Overnight-carryover gate: a beat <18h old reflects yesterday's last
    # pipeline run. Off-market that's expected (no cron during the gap);
    # in the first 30 min after market open it's the warm-up window before
    # the morning's first run lands. In both cases, demote "stale" → "ok-
    # overnight" so the dashboard error detector doesn't flag it critical
    # and Discord doesn't get paged.
    if beat_status == "stale" and age_min is not None and age_min < 18 * 60:
        market_open_min = (now.hour - 9) * 60 + (now.minute - 30) if now.weekday() < 5 else -1
        warmup = market and 0 <= market_open_min < 30
        if not market or warmup:
            beat_status = "ok-overnight"

    if should_print_status(beat_status, market):
        print(f"[{now.strftime('%H:%M ET')}] pipeline beat={beat_status}"
              + (f" age={age_min:.1f}m" if age_min is not None else "")
              + f" market={market}")
        record_log(beat_status)

    # --- Alert if stale/missing during market hours ---
    if market and beat_status in ("missing", "stale") and cooldown_ok("pipeline"):
        time_str = now.strftime("%H:%M ET")
        if beat_status == "missing":
            msg = (f"⚠️ **QuantAI Heartbeat MISSING** — `pipeline.beat` not found. "
                   f"Pipeline cron may be down. {time_str}")
        else:
            msg = (f"⚠️ **QuantAI Heartbeat STALE** — last beat {age_min:.0f}m ago "
                   f"(threshold: {STALE_MIN}m). Pipeline may be stuck or crashed. {time_str}")
        post_discord(msg)
        record_cooldown("pipeline")
        print(f"ALERT: sent Discord notification ({beat_status})")

    # --- Probe IBKR port 4002 (runs every tick, all hours including weekends) ---
    ibkr_connected = probe_ibkr_port()
    ibkr_probe_state = update_ibkr_probe_state(ibkr_connected)
    ibkr_status = "ok" if ibkr_connected else "refused"
    consecutive_fails = ibkr_probe_state.get("consecutive_fails", 0)
    first_fail_at = ibkr_probe_state.get("first_fail_at")

    print(f"[{now.strftime('%H:%M ET')}] ibkr_port={ibkr_status}"
          + (f" consecutive_fails={consecutive_fails}" if not ibkr_connected else ""))

    if not ibkr_connected and consecutive_fails >= IBKR_CONSECUTIVE_FAILS_ALERT:
        if cooldown_ok("ibkr_port"):
            outage_mins = ""
            if first_fail_at:
                try:
                    first_dt = datetime.fromisoformat(first_fail_at)
                    if first_dt.tzinfo is None:
                        first_dt = first_dt.replace(tzinfo=timezone.utc)
                    elapsed = (datetime.now(timezone.utc) - first_dt).total_seconds() / 60
                    outage_mins = f" (down ~{elapsed:.0f}m)"
                except Exception:
                    pass
            msg = (
                f"🔴 **IBKR Gateway port 4002 REFUSED**{outage_mins} — broker connection down. "
                f"IB Gateway process alive but not accepting API connections. "
                f"**Fix:** `sudo systemctl restart ibgateway` then wait 90s and verify."
            )
            post_discord(msg)
            record_cooldown("ibkr_port")
            print("ALERT: sent Discord notification (ibkr_port refused)")

    # --- Write dashboard state ---
    if beat_status in ("ok", "ok-overnight") and ibkr_connected:
        dash_status = "ok"
    elif not market:
        dash_status = "idle"
    else:
        dash_status = "error"

    state = {
        "last_updated": now.isoformat(),
        "status": dash_status,
        "data": {
            "market_hours": market,
            "pipeline": {
                "status": beat_status,
                "age_min": round(age_min, 1) if age_min is not None else None,
                "last_beat": beat.isoformat() if beat else None,
                "stale_threshold_min": STALE_MIN,
            },
            "ibkr_port": {
                "status": ibkr_status,
                "consecutive_fails": consecutive_fails,
                "first_fail_at": first_fail_at,
            },
        },
    }
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))
    print(f"State written: {STATE_FILE}")


if __name__ == "__main__":
    main()
