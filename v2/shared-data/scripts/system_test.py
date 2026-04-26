#!/usr/bin/env python3
"""
QuantAI System Test
Tests every component and reports pass/fail.
Run this anytime to verify the system is healthy.

Usage: python3 system_test.py
"""

import json, os, sys, subprocess, requests
from datetime import datetime
from zoneinfo import ZoneInfo

# Auto-load .env
import pathlib as _pl
for _ef in [_pl.Path("/home/trader/QuantAI/.env"), _pl.Path("/root/quantai-v2/.env")]:
    if _ef.exists():
        for _line in _ef.read_text().splitlines():
            if _line.strip() and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.strip().partition("=")
                if not os.environ.get(_k.strip()):
                    os.environ[_k.strip()] = _v.strip()
        break

ET = ZoneInfo("America/New_York")
SCRIPTS = "/home/trader/QuantAI/v2/shared-data/scripts"
CACHE   = "/root/quantai-v2/shared-data/cache"
LOGS    = "/root/quantai-v2/shared-data/logs"

results = []

def check(name, passed, detail=""):
    status = "✅ PASS" if passed else "❌ FAIL"
    results.append((name, passed, detail))
    print(f"  {status}  {name}" + (f" — {detail}" if detail else ""))

def run_script(script, *args, timeout=60):
    try:
        r = subprocess.run(
            ["python3", f"{SCRIPTS}/{script}"] + list(args),
            capture_output=True, text=True, timeout=timeout
        )
        return r.returncode == 0, r.stdout + r.stderr
    except subprocess.TimeoutExpired:
        return False, "Timed out"
    except Exception as e:
        return False, str(e)

print("\n" + "="*55)
print("  QuantAI System Test")
print(f"  {datetime.now(ET).strftime('%Y-%m-%d %H:%M ET')}")
print("="*55)

# ── 1. Environment ────────────────────────────────────────────────────
print("\n📋 Environment")
check("ANTHROPIC_API_KEY set",    bool(os.environ.get("ANTHROPIC_API_KEY")))
check("ALPACA_API_KEY set",       bool(os.environ.get("ALPACA_API_KEY")))
check("ALPACA_SECRET_KEY set",    bool(os.environ.get("ALPACA_SECRET_KEY")))
check("FINNHUB_API_KEY set",      bool(os.environ.get("FINNHUB_API_KEY")))
check("DISCORD_TOKEN_ORCHESTRATOR set", bool(os.environ.get("DISCORD_TOKEN_ORCHESTRATOR")))
check("GOOGLE_SHEET_ID set",      bool(os.environ.get("GOOGLE_SHEET_ID")))

# ── 2. Python dependencies ────────────────────────────────────────────
print("\n📦 Dependencies")
for pkg in ["anthropic", "yfinance", "aiohttp", "requests"]:
    try:
        __import__(pkg)
        check(f"{pkg} importable", True)
    except ImportError:
        check(f"{pkg} importable", False, "pip3 install " + pkg + " --break-system-packages")

try:
    from google.oauth2.service_account import Credentials
    check("google-auth importable", True)
except ImportError:
    check("google-auth importable", False, "pip3 install google-api-python-client google-auth --break-system-packages")

# ── 3. Files and paths ────────────────────────────────────────────────
print("\n📁 Files & Paths")
scripts = [
    "market_intelligence.py", "debate_chamber.py", "autonomous_execution.py",
    "run_pipeline.py", "scan_options.py", "self_evolution.py",
    "sheets_sync.py", "pattern_engine.py", "eod_summary.py", "fetch_sofi.py"
]
for s in scripts:
    path = f"{SCRIPTS}/{s}"
    check(f"scripts/{s}", os.path.exists(path))

workspaces = [
    "/root/quantai-v2/workspace-orchestrator/AGENTS.md",
    "/root/quantai-v2/workspace-orchestrator/SOUL.md",
    "/root/quantai-v2/workspace-research/AGENTS.md",
    "/root/quantai-v2/workspace-infra/AGENTS.md",
    "/root/quantai-v2/workspace-journal/AGENTS.md",
]
for w in workspaces:
    check(f"workspace {w.split('/')[-2]}/{w.split('/')[-1]}", os.path.exists(w))

check("sofi_collar.json exists",
      os.path.exists("/root/quantai-v2/shared-data/strategies/sofi_collar.json"))
check("google_service_account.json exists",
      os.path.exists("/root/quantai-v2/shared-data/google_service_account.json"))
check("Journal directory exists",
      os.path.exists("/root/quantai-v2/shared-data/journal/paper"))

# ── 4. Cron ───────────────────────────────────────────────────────────
print("\n⏰ Cron")
try:
    r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    cron = r.stdout
    check("Pipeline cron (*/15)",  "run_pipeline.py" in cron and "*/15" in cron)
    check("EOD cron (4:05 PM)",    "run_pipeline.py" in cron and "eod" in cron)
except:
    check("Crontab readable", False)

# ── 5. Alpaca connection ──────────────────────────────────────────────
print("\n🔌 Alpaca Connection")
try:
    hdrs = {
        "APCA-API-KEY-ID": os.environ.get("ALPACA_API_KEY",""),
        "APCA-API-SECRET-KEY": os.environ.get("ALPACA_SECRET_KEY",""),
    }
    r = requests.get("https://paper-api.alpaca.markets/v2/account",
                    headers=hdrs, timeout=10)
    if r.status_code == 200:
        acct = r.json()
        equity = float(acct.get("equity", 0))
        check("Alpaca paper API", True, f"Equity: ${equity:,.0f}")
    else:
        check("Alpaca paper API", False, f"HTTP {r.status_code}")
except Exception as e:
    check("Alpaca paper API", False, str(e)[:60])

# ── 6. Market intelligence ────────────────────────────────────────────
print("\n🧠 Market Intelligence")
intel_path = f"{CACHE}/market_intelligence.json"
if os.path.exists(intel_path):
    try:
        d = json.load(open(intel_path))
        ts = datetime.fromisoformat(d.get("timestamp","2000-01-01"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=ET)
        age_min = (datetime.now(ET) - ts).total_seconds() / 60
        check("Intel packet exists", True,
              f"Age: {age_min:.0f} min | Regime: {d.get('market_regime','?')} | VIX: {d.get('macro',{}).get('vix','?')} | Quality: {d.get('data_quality','?')}/100")
        symbols_ok = len(d.get("symbols", {})) >= 8
        check("Intel has symbol data", symbols_ok,
              f"{len(d.get('symbols',{}))} symbols")
    except Exception as e:
        check("Intel packet readable", False, str(e))
else:
    check("Intel packet exists", False,
          "Run: python3 market_intelligence.py --force")

# ── 7. Run market intelligence fresh ─────────────────────────────────
print("\n🔄 Running market_intelligence.py --force")
ok, out = run_script("market_intelligence.py", "--force", timeout=120)
last_line = [l for l in out.strip().split("\n") if l.strip()][-1] if out.strip() else ""
check("market_intelligence.py runs", ok, last_line[-80:] if last_line else "")

# ── 8. Scan options ───────────────────────────────────────────────────
print("\n🔍 Running scan_options.py")
ok, out = run_script("scan_options.py", "credit_spreads", timeout=120)
check("scan_options.py runs", ok,
      f"Found opportunities" if ok else out[-100:])

# ── 9. Debate chamber (dry check — just verify it reads packet) ───────
print("\n⚖️  Debate Chamber")
debate_path = f"{CACHE}/debate_output.json"
if os.path.exists(debate_path):
    try:
        d = json.load(open(debate_path))
        check("Debate output exists", True,
              f"Status: {d.get('status','?')} | Approved: {d.get('approved_count','?')} | {d.get('timestamp','')[:16]}")
    except:
        check("Debate output readable", False)
else:
    check("Debate output exists", False, "Not run yet today — will run at next market open")

# ── 10. Autonomous execution dry run ─────────────────────────────────
print("\n🤖 Autonomous Execution (dry run)")
ok, out = run_script("autonomous_execution.py", "--check-only", timeout=30)
check("autonomous_execution.py --check-only", ok,
      [l for l in out.strip().split("\n") if l.strip()][-1][-80:] if out.strip() else "")

# ── 11. Journal ───────────────────────────────────────────────────────
print("\n📒 Journal")
journal_path = "/root/quantai-v2/shared-data/journal/paper/trades.jsonl"
if os.path.exists(journal_path):
    trades = [json.loads(l) for l in open(journal_path) if l.strip()]
    alpha  = [t for t in trades if t.get("source") == "agent_alpha"]
    beta   = [t for t in trades if t.get("source") == "agent_beta"]
    manual = [t for t in trades if t.get("source") == "manual"]
    open_t = [t for t in trades if t.get("status") == "OPEN"]
    check("Journal readable", True,
          f"Total: {len(trades)} | Alpha: {len(alpha)} | Beta: {len(beta)} | Manual: {len(manual)} | Open: {len(open_t)}")
else:
    check("Journal exists", False, "No trades logged yet — this is fine if day 1")

# ── 12. Google Sheets ─────────────────────────────────────────────────
print("\n📊 Google Sheets")
ok, out = run_script("sheets_sync.py", timeout=30)
check("sheets_sync.py runs", ok,
      [l for l in out.strip().split("\n") if l.strip()][-1][-80:] if out.strip() else "")

# ── 13. EOD summary ───────────────────────────────────────────────────
print("\n📋 EOD Summary")
ok, out = run_script("eod_summary.py", timeout=20)
check("eod_summary.py runs", ok)

# ── 14. Pipeline dry run ──────────────────────────────────────────────
print("\n🔁 Pipeline")
ok, out = run_script("run_pipeline.py", timeout=20)
check("run_pipeline.py runs", ok,
      [l for l in out.strip().split("\n") if l.strip()][-1][-60:] if out.strip() else "")

# ── 15. OpenClaw gateway ──────────────────────────────────────────────
print("\n🌐 OpenClaw")
try:
    r = subprocess.run(["ps", "aux"], capture_output=True, text=True)
    gateway_running = "openclaw-gateway" in r.stdout
    check("openclaw-gateway process", gateway_running)
except:
    check("openclaw-gateway process", False)

# ── Summary ───────────────────────────────────────────────────────────
passed = sum(1 for _, p, _ in results if p)
failed = sum(1 for _, p, _ in results if not p)
total  = len(results)

print("\n" + "="*55)
print(f"  Results: {passed}/{total} passed")
if failed:
    print(f"\n  ❌ Failed checks ({failed}):")
    for name, passed, detail in results:
        if not passed:
            print(f"     • {name}" + (f": {detail}" if detail else ""))
else:
    print("  🎉 All checks passed — system fully operational")
print("="*55 + "\n")
