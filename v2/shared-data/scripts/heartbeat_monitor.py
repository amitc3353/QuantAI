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


def is_market_hours():
    now = datetime.now(ET)
    if now.weekday() >= 5:
        return False
    return 9 <= now.hour < 16


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
    url = os.environ.get("DISCORD_WEBHOOK_CHAT", "")
    if not url:
        print("WARN: DISCORD_WEBHOOK_CHAT not set, skipping alert")
        return
    try:
        import requests
        r = requests.post(url, json={"content": msg}, timeout=10)
        if r.status_code not in (200, 204):
            print(f"WARN: Discord returned {r.status_code}")
    except Exception as e:
        print(f"WARN: Discord post failed: {e}")


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

    print(f"[{now.strftime('%H:%M ET')}] pipeline beat={beat_status}"
          + (f" age={age_min:.1f}m" if age_min is not None else "")
          + f" market={market}")

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

    # --- Write dashboard state ---
    if beat_status == "ok":
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
        },
    }
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))
    print(f"State written: {STATE_FILE}")


if __name__ == "__main__":
    main()
