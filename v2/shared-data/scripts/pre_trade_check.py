#!/usr/bin/env python3
"""
QuantAI Pre-Trade Diagnostic
Runs at 9:30 AM ET (before pipeline starts) and posts a go/no-go report to #infra.
Catches every known failure mode before the market opens.

Usage:
  python3 pre_trade_check.py
"""

import os, sys, json, requests, subprocess
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
import pathlib as _pl

# Auto-load .env
for _ef in [_pl.Path("/home/trader/QuantAI/.env"), _pl.Path("/root/quantai-v2/.env")]:
    if _ef.exists():
        for _line in _ef.read_text().splitlines():
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.partition("=")
                if not os.environ.get(_k.strip()):
                    os.environ[_k.strip()] = _v.strip()
        break

ET       = ZoneInfo("America/New_York")
SCRIPTS  = "/home/trader/QuantAI/v2/shared-data/scripts"
REPO     = "/root/quantai-v2/v2/shared-data/scripts"
CACHE    = "/root/quantai-v2/shared-data/cache"
JOURNAL  = "/root/quantai-v2/shared-data/journal/paper/trades.jsonl"

ALPACA_KEY    = os.environ.get("ALPACA_API_KEY", "")
ALPACA_SECRET = os.environ.get("ALPACA_SECRET_KEY", "")
ALPACA_BASE   = "https://paper-api.alpaca.markets"
BOT_TOKEN     = os.environ.get("DISCORD_TOKEN_ORCHESTRATOR", "")
INFRA_CH      = os.environ.get("DISCORD_CHANNEL_INFRA", "")

now   = datetime.now(ET)
today = date.today()

checks_passed = []
checks_failed = []

def ok(msg):
    checks_passed.append(f"✅ {msg}")

def fail(msg):
    checks_failed.append(f"❌ {msg}")

def warn(msg):
    checks_passed.append(f"⚠️ {msg}")

# ── 1. Script sync check ──────────────────────────────────────────────
for script in ["autonomous_execution.py", "run_pipeline.py", "debate_chamber.py",
               "scan_options.py", "market_intelligence.py"]:
    runtime = f"{SCRIPTS}/{script}"
    repo    = f"{REPO}/{script}"
    if not os.path.exists(runtime):
        fail(f"{script} missing from runtime path")
    elif not os.path.exists(repo):
        warn(f"{script} not in repo path")
    else:
        import filecmp
        if filecmp.cmp(runtime, repo, shallow=False):
            ok(f"{script} in sync")
        else:
            fail(f"{script} OUT OF SYNC — run sync_workspaces.sh")

# ── 2. Syntax check all scripts ───────────────────────────────────────
for script in ["autonomous_execution.py", "run_pipeline.py", "debate_chamber.py",
               "scan_options.py", "market_intelligence.py"]:
    r = subprocess.run(
        ["python3", "-m", "py_compile", f"{SCRIPTS}/{script}"],
        capture_output=True
    )
    if r.returncode == 0:
        ok(f"{script} syntax valid")
    else:
        fail(f"{script} SYNTAX ERROR: {r.stderr.decode()[:80]}")

# ── 3. Alpaca API ─────────────────────────────────────────────────────
try:
    r = requests.get(f"{ALPACA_BASE}/v2/account",
                     headers={"APCA-API-KEY-ID": ALPACA_KEY,
                               "APCA-API-SECRET-KEY": ALPACA_SECRET},
                     timeout=10)
    if r.status_code == 200:
        acct = r.json()
        equity = float(acct.get("equity", 0))
        trading_blocked = acct.get("trading_blocked", False)
        options_level   = acct.get("options_approved_level", 0)
        ok(f"Alpaca connected — equity ${equity:,.0f} | options level {options_level}")
        if trading_blocked:
            fail("Alpaca trading is BLOCKED")
        if options_level < 2:
            fail(f"Alpaca options level {options_level} — need level 2+ for spreads")
    else:
        fail(f"Alpaca API error {r.status_code}: {r.text[:80]}")
except Exception as e:
    fail(f"Alpaca connection failed: {e}")

# ── 4. Daily state check ──────────────────────────────────────────────
state_path = f"{CACHE}/daily_state.json"
try:
    state = json.load(open(state_path))
    state_date = state.get("date", "")
    entries    = state.get("entries_today", 0)
    if state_date == today.isoformat():
        if entries >= 2:
            warn(f"Daily state: {entries}/2 entries used today — no more entries")
        else:
            ok(f"Daily state: {entries}/2 entries used today — {2-entries} remaining")
    else:
        ok(f"Daily state: fresh (date={state_date}, will reset for today)")
except Exception as e:
    warn(f"Daily state unreadable ({e}) — will be created fresh")

# ── 5. Intel packet freshness ─────────────────────────────────────────
intel_path = f"{CACHE}/market_intelligence.json"
try:
    intel = json.load(open(intel_path))
    ts = datetime.fromisoformat(intel.get("timestamp","2000-01-01"))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=ET)
    age_h = (now - ts).total_seconds() / 3600
    if age_h < 18:
        ok(f"Intel packet {age_h:.0f}h old — will refresh at 9:45 AM")
    else:
        warn(f"Intel packet {age_h:.0f}h old — may be stale")
except Exception as e:
    warn(f"No intel packet yet ({e}) — will be created at 9:45 AM")

# ── 6. Debate output freshness ────────────────────────────────────────
debate_path = f"{CACHE}/debate_output.json"
try:
    debate = json.load(open(debate_path))
    ts = datetime.fromisoformat(debate.get("timestamp","2000-01-01"))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=ET)
    age_h = (now - ts).total_seconds() / 3600
    approved = len(debate.get("approved_trades",[]))
    if age_h > 20:
        ok(f"Debate output is {age_h:.0f}h old — fresh run pending at 9:45 AM")
    else:
        warn(f"Debate output only {age_h:.1f}h old ({approved} approved) — will re-run at 9:45 AM")
except:
    ok("No stale debate output — clean slate")

# ── 7. Journal state ──────────────────────────────────────────────────
try:
    trades = [json.loads(l) for l in open(JOURNAL) if l.strip()] if os.path.exists(JOURNAL) else []
    open_agent = [t for t in trades if t.get("status")=="OPEN" and t.get("source","").startswith("agent")]
    total_agent = [t for t in trades if t.get("source","").startswith("agent")]
    ok(f"Journal: {len(total_agent)} agent trades total | {len(open_agent)} open today")
except Exception as e:
    warn(f"Journal read error: {e}")

# ── 8. Cron check ─────────────────────────────────────────────────────
try:
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    cron = result.stdout
    if "*/15" in cron and "run_pipeline" in cron:
        ok("15-min pipeline cron installed")
    else:
        fail("Pipeline cron MISSING — agents won't run")
    if "eod" in cron:
        ok("EOD cron installed")
    else:
        warn("EOD cron missing")
except:
    fail("Could not check cron")

# ── 9. Discord alert channel check ────────────────────────────────────
alerts_ch = os.environ.get("DISCORD_CHANNEL_ALERTS", "")
if alerts_ch:
    ok(f"Alert channel configured: #{alerts_ch}")
else:
    fail("DISCORD_CHANNEL_ALERTS not set — trade alerts won't post")

# ── 10. VIX check (from last intel) ───────────────────────────────────
try:
    intel = json.load(open(intel_path))
    vix = intel.get("macro", {}).get("vix", 0)
    regime = intel.get("market_regime", "?")
    if vix >= 35:
        fail(f"VIX {vix:.1f} — HALT regime, no trades today")
    elif vix >= 25:
        warn(f"VIX {vix:.1f} — CAUTION regime ({regime}). Condors restricted, spreads OK")
    else:
        ok(f"VIX {vix:.1f} — {regime.upper()} regime. All strategies available")
except:
    warn("VIX unknown — intel packet will refresh at 9:45 AM")

# ── Report ────────────────────────────────────────────────────────────
total   = len(checks_passed) + len(checks_failed)
n_fail  = len(checks_failed)
n_ok    = len(checks_passed)

go_nogo = "🟢 GO" if n_fail == 0 else f"🔴 NO-GO ({n_fail} issue{'s' if n_fail>1 else ''})"

report  = f"**🔍 Pre-Trade Check — {now.strftime('%a %b %d %H:%M ET')}**\n"
report += f"**{go_nogo}** — {n_ok}/{total} checks passed\n\n"

if checks_failed:
    report += "**Issues to fix:**\n"
    for c in checks_failed:
        report += f"{c}\n"
    report += "\n"

report += "**All checks:**\n"
for c in checks_passed:
    report += f"{c}\n"
for c in checks_failed:
    report += f"{c}\n"

print(report)

# Post to #infra
if BOT_TOKEN and INFRA_CH:
    try:
        requests.post(
            f"https://discord.com/api/v10/channels/{INFRA_CH}/messages",
            headers={"Authorization": f"Bot {BOT_TOKEN}",
                     "Content-Type": "application/json"},
            json={"content": report[:1900]},
            timeout=8
        )
    except Exception as e:
        print(f"Discord post failed: {e}")

sys.exit(1 if n_fail > 0 else 0)
