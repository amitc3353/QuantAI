#!/usr/bin/env python3
"""
QuantAI Autonomous Pipeline — Condition-Triggered

Runs every 15 minutes during market hours via cron.
Agents enter when conditions are right, not on a fixed clock.

Entry logic:
- Market must be open (9:45 AM – 3:30 PM ET)
- Intelligence packet must show regime != halt
- VIX must be in acceptable range
- No entry in first 15 min of open (9:30-9:45) — volatility too high
- Max 2 entries per day (tracked in daily_state.json)
- If Entry 1 is open and profitable, consider Entry 2
- Never enter after 3:00 PM ET (not enough time to manage)

Monitor runs every 15 min during market hours:
- Checks all open agent positions
- Closes at 50% profit, 2x stop, or 3:30 PM hard close

Usage (crontab):
  */15 * * * 1-5  python3 /home/trader/QuantAI/v2/shared-data/scripts/run_pipeline.py

Modes:
  python3 run_pipeline.py          # auto-detect what to do
  python3 run_pipeline.py monitor  # force position check only
  python3 run_pipeline.py eod      # end of day wrap-up
"""

import os, sys, subprocess, json
from datetime import datetime, date, timezone
from zoneinfo import ZoneInfo

# Auto-load .env
import pathlib as _pl
for _ef in [_pl.Path("/home/trader/QuantAI/.env"), _pl.Path("/root/quantai-v2/.env")]:
    if _ef.exists():
        for _line in _ef.read_text().splitlines():
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.partition("=")
                if not os.environ.get(_k.strip()):
                    os.environ[_k.strip()] = _v.strip()
        break

ET = ZoneInfo("America/New_York")
SCRIPTS  = "/home/trader/QuantAI/v2/shared-data/scripts"
CACHE    = "/root/quantai-v2/shared-data/cache"
LOGS     = "/root/quantai-v2/shared-data/logs"
JOURNAL  = "/root/quantai-v2/shared-data/journal/paper/trades.jsonl"
STATE    = f"{CACHE}/daily_state.json"
os.makedirs(LOGS, exist_ok=True)
os.makedirs(CACHE, exist_ok=True)

forced_mode = sys.argv[1] if len(sys.argv) > 1 else None

now = datetime.now(ET)
today = date.today().isoformat()

def log(msg):
    ts = now.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")

def run_script(script, *args, label=""):
    if label:
        log(label)
    cmd = ["python3", f"{SCRIPTS}/{script}"] + list(args)
    # Capture stderr so debate_chamber tracebacks land in pipeline.log instead
    # of being silently dropped — otherwise "Debate failed" gives no diagnosis.
    result = subprocess.run(cmd, timeout=300, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0 and result.stderr:
        log(f"--- {script} stderr ---")
        for line in result.stderr.rstrip().splitlines()[-40:]:
            log(f"  {line}")
        log(f"--- end {script} stderr ---")
    return result.returncode == 0

def load_state():
    if os.path.exists(STATE):
        try:
            s = json.load(open(STATE))
            if s.get("date") == today:
                return s
        except:
            pass
    return {"date": today, "entries_today": 0, "last_entry_time": None}

def save_state(state):
    with open(STATE, "w") as f:
        json.dump(state, f)

def load_intel():
    p = f"{CACHE}/market_intelligence.json"
    if not os.path.exists(p):
        return None
    try:
        return json.load(open(p))
    except:
        return None

def count_open_agent_trades():
    if not os.path.exists(JOURNAL):
        return 0
    trades = [json.loads(l) for l in open(JOURNAL) if l.strip()]
    return len([t for t in trades
                if t.get("status") == "OPEN"
                and t.get("source", "").startswith("agent")])

def count_journal_entries_today() -> int:
    """Count journal entries timestamped today (ET date). Used to detect whether
    autonomous_execution.py actually wrote a new trade so the daily budget counter
    is only incremented on genuine fills — not on silent broker failures."""
    if not os.path.exists(JOURNAL):
        return 0
    try:
        count = 0
        for line in open(JOURNAL):
            line = line.strip()
            if not line:
                continue
            try:
                t = json.loads(line)
                if t.get("timestamp", "")[:10] == today:
                    count += 1
            except Exception:
                pass
        return count
    except Exception:
        return 0

def is_market_open():
    h, m = now.hour, now.minute
    # Market hours: 9:30 AM – 4:00 PM ET weekdays
    if now.weekday() >= 5:
        return False
    if h < 9 or (h == 9 and m < 30):
        return False
    if h >= 16:
        return False
    return True

def past_entry_cutoff():
    # No new entries after 3:00 PM — not enough time to manage
    return now.hour >= 15

def in_opening_volatility_window():
    # Avoid first 15 min of open
    return now.hour == 9 and now.minute < 45

def past_hard_close():
    return now.hour == 15 and now.minute >= 30 or now.hour > 15

def intel_is_fresh():
    p = f"{CACHE}/market_intelligence.json"
    if not os.path.exists(p):
        return False
    try:
        d = json.load(open(p))
        ts = datetime.fromisoformat(d.get("timestamp", "2000-01-01"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=ET)
        age_min = (now - ts).total_seconds() / 60
        return age_min < 30  # fresh if under 30 min
    except:
        return False

def write_heartbeat():
    """Write UTC timestamp to pipeline beat file so heartbeat_monitor can check liveness."""
    from pathlib import Path
    beat_dir = Path("/tmp/quantai-heartbeats")
    beat_dir.mkdir(parents=True, exist_ok=True)
    (beat_dir / "pipeline.beat").write_text(datetime.now(timezone.utc).isoformat())

# ── MONITOR mode ──────────────────────────────────────────────────────
def run_monitor():
    log("Running position monitor...")
    run_script("autonomous_execution.py", "--monitor-only",
               label="Checking open agent positions...")

# ── EOD mode ─────────────────────────────────────────────────────────
def run_eod():
    log("EOD wrap-up — posting daily summary")
    import subprocess
    subprocess.run(["python3", f"{SCRIPTS}/eod_summary.py"], timeout=60)
    # Reset daily state for next day
    save_state({"date": today, "entries_today": 0, "last_entry_time": None})

# ── ENTRY mode ────────────────────────────────────────────────────────
def run_entry(state):
    # Per-agent kill switch (added 2026-05-11). Default ON — see _agent_flags.py.
    # When ALPHA_ENABLED=0 in .env, skip the entry path entirely. position_monitor
    # runs independently and continues to exit existing positions.
    try:
        from _agent_flags import is_agent_enabled, notify_once_per_day_disabled
        if not is_agent_enabled("alpha"):
            log("[alpha] ALPHA_ENABLED=0 in .env — skipping entry path")

            def _post(msg: str) -> None:
                try:
                    from _discord import post_to_channel
                    ch = os.environ.get("DISCORD_CHANNEL_ALERTS", "")
                    if ch:
                        post_to_channel(ch, msg)
                except Exception:
                    pass

            notify_once_per_day_disabled("alpha", post_discord=_post)
            return state
    except Exception as e:
        log(f"[alpha] _agent_flags check failed: {e} (defaulting to enabled)")

    log(f"Considering entry — {state['entries_today']} entries today so far")

    # Refresh intelligence
    run_script("market_intelligence.py", label="Refreshing market intelligence...")
    intel = load_intel()
    if not intel:
        log("No intel — skipping")
        return state

    regime = intel.get("market_regime", "normal")
    vix = intel.get("macro", {}).get("vix", 0)
    log(f"Regime: {regime.upper()} | VIX: {vix:.1f}")

    # Regime gate
    if regime == "halt":
        log("HALT regime — no entry today")
        return state

    # VIX gate for condors (Agent Beta)
    # VIX gate for spreads (Agent Alpha) — wider range acceptable
    if vix > 35:
        log(f"VIX {vix:.1f} too high — no entry")
        return state

    # Entry 2 gate — only if Entry 1 exists and is doing well
    if state["entries_today"] >= 1:
        open_count = count_open_agent_trades()
        if open_count == 0:
            log("Entry 1 already closed — Entry 2 valid if conditions strong")
        elif open_count >= 3:
            log(f"Already {open_count} open positions — max reached, skipping entry")
            return state
        else:
            log(f"{open_count} position(s) still open — evaluating Entry 2")
            # Only enter second time if regime is PROCEED (not caution)
            if regime != "normal":
                log("Regime not normal — skipping Entry 2")
                return state

    if state["entries_today"] >= 2:
        log("Max 2 entries per day reached — done for today")
        return state

    # Run scan + debate + execute
    log("Conditions met — running full pipeline")
    run_script("scan_options.py", "all", label="Scanning options (spreads + diagonals + condors)...")
    ok = run_script("debate_chamber.py", label="Running debate chamber...")
    if not ok:
        log("Debate failed — skipping execution")
        return state

    journal_before = count_journal_entries_today()
    run_script("autonomous_execution.py", label="Executing approved trades...")
    journal_after = count_journal_entries_today()

    if journal_after > journal_before:
        state["entries_today"] += 1
        state["last_entry_time"] = now.isoformat()
        save_state(state)
        log(f"Entry {state['entries_today']} complete ({journal_after - journal_before} new journal entry/entries)")
    else:
        log("⚠️  Execution produced no journal entry — broker likely returned None. Not counting toward daily budget.")
    return state


# ── Main ──────────────────────────────────────────────────────────────
if __name__ == "__main__":

    if forced_mode == "eod":
        run_eod()
        sys.exit(0)

    if forced_mode == "monitor":
        run_monitor()
        sys.exit(0)

    if not is_market_open():
        log(f"Market closed at {now.strftime('%H:%M ET')} — nothing to do")
        sys.exit(0)

    state = load_state()

    # Always run monitor when market is open
    if past_hard_close():
        log("Past 3:30 PM — hard close check")
        run_monitor()
        write_heartbeat()
        sys.exit(0)

    if in_opening_volatility_window():
        log("Opening volatility window (9:30-9:45) — waiting for market to settle")
        write_heartbeat()
        sys.exit(0)

    if past_entry_cutoff():
        # After 3 PM — monitor only, no new entries
        run_monitor()
        write_heartbeat()
        sys.exit(0)

    # Normal market hours — run monitor + consider entry
    run_monitor()

    if not past_entry_cutoff():
        state = run_entry(state)

    write_heartbeat()
