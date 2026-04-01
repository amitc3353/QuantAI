#!/usr/bin/env python3
"""
QuantAI Autonomous Pipeline
Runs the full intelligence → debate → execution cycle on a schedule.

Called by cron. No human approval needed.
Agents Alpha and Beta execute within guardrails automatically.

Schedule (set in VPS crontab):
  9:50 AM ET Mon-Fri  — Entry 1 (morning session)
  1:30 PM ET Mon-Fri  — Entry 2 (afternoon session)
  3:30 PM ET Mon-Fri  — Position monitor + hard close check

Usage:
  python3 run_pipeline.py entry_1    # morning entry
  python3 run_pipeline.py entry_2    # afternoon entry
  python3 run_pipeline.py monitor    # position check
  python3 run_pipeline.py eod        # end of day scoring
"""

import os, sys, subprocess, json
from datetime import datetime
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
SCRIPTS = "/home/trader/QuantAI/v2/shared-data/scripts"
CACHE   = "/root/quantai-v2/shared-data/cache"
LOGS    = "/root/quantai-v2/shared-data/logs"
os.makedirs(LOGS, exist_ok=True)

mode = sys.argv[1] if len(sys.argv) > 1 else "entry_1"

def run(cmd, label):
    print(f"\n[pipeline] {label}")
    result = subprocess.run(
        ["python3"] + cmd,
        capture_output=False,
        timeout=300
    )
    return result.returncode == 0

def log_pipeline_event(event):
    entry = {"timestamp": datetime.now(ET).isoformat(), "mode": mode, **event}
    with open(f"{LOGS}/pipeline_log.jsonl", "a") as f:
        f.write(json.dumps(entry) + "\n")


# ── Entry 1 or Entry 2 (full pipeline) ───────────────────────────────
if mode in ("entry_1", "entry_2"):
    print(f"\n{'='*60}")
    print(f"QuantAI Autonomous Pipeline — {mode.upper()}")
    print(f"Time: {datetime.now(ET).strftime('%Y-%m-%d %H:%M ET')}")
    print(f"{'='*60}")

    # Step 1: Market intelligence (refresh if stale)
    ok = run([f"{SCRIPTS}/market_intelligence.py"], "Building market intelligence packet...")
    if not ok:
        print("[pipeline] Intelligence failed — aborting")
        log_pipeline_event({"step": "intelligence", "status": "failed"})
        sys.exit(1)

    # Check regime — abort if halt
    try:
        with open(f"{CACHE}/market_intelligence.json") as f:
            intel = json.load(f)
        regime = intel.get("market_regime", "normal")
        vix = intel.get("macro", {}).get("vix", 0)
        print(f"[pipeline] Regime: {regime.upper()} | VIX: {vix:.1f}")
        if regime == "halt":
            print("[pipeline] HALT regime — no trading today")
            log_pipeline_event({"step": "regime_check", "status": "halted", "vix": vix})
            sys.exit(0)
    except Exception as e:
        print(f"[pipeline] Could not read intel packet: {e}")

    # Step 2: Scan options
    run([f"{SCRIPTS}/scan_options.py", "both"], "Running options scanner...")

    # Step 3: Debate chamber
    ok = run([f"{SCRIPTS}/debate_chamber.py"], "Running debate chamber...")
    if not ok:
        print("[pipeline] Debate chamber failed — aborting")
        log_pipeline_event({"step": "debate", "status": "failed"})
        sys.exit(1)

    # Step 4: Autonomous execution
    run([f"{SCRIPTS}/autonomous_execution.py"], "Executing approved trades...")

    log_pipeline_event({"step": "complete", "status": "ok"})
    print(f"\n[pipeline] ✅ {mode.upper()} complete")


# ── Position monitor ──────────────────────────────────────────────────
elif mode == "monitor":
    print(f"\n[pipeline] Position Monitor — {datetime.now(ET).strftime('%H:%M ET')}")

    import requests as req
    ALPACA_KEY    = os.environ.get("ALPACA_API_KEY", "")
    ALPACA_SECRET = os.environ.get("ALPACA_SECRET_KEY", "")
    ALPACA_BASE   = "https://paper-api.alpaca.markets"
    DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK_CHAT", "")
    JOURNAL = "/root/quantai-v2/shared-data/journal/paper/trades.jsonl"

    if not os.path.exists(JOURNAL):
        print("[monitor] No journal yet")
        sys.exit(0)

    trades = [json.loads(l) for l in open(JOURNAL) if l.strip()]
    open_trades = [t for t in trades if t.get("status") == "OPEN"
                   and t.get("source", "").startswith("agent")]

    if not open_trades:
        print("[monitor] No open agent positions to monitor")
        sys.exit(0)

    now = datetime.now(ET)
    hard_close_time = now.replace(hour=15, minute=30, second=0)
    is_hard_close = now >= hard_close_time

    alerts = []
    for t in open_trades:
        symbol = t.get("symbol", "")
        trade_id = t.get("id", "")
        credit = t.get("estimated_credit", 0)
        agent = t.get("source", "agent")

        # Try to get current value from Alpaca
        try:
            headers = {
                "APCA-API-KEY-ID": ALPACA_KEY,
                "APCA-API-SECRET-KEY": ALPACA_SECRET
            }
            r = req.get(f"{ALPACA_BASE}/v2/positions", headers=headers, timeout=10)
            positions = r.json() if r.status_code == 200 else []

            # Find matching position
            for pos in positions:
                if symbol in pos.get("symbol", ""):
                    current_pl_pct = float(pos.get("unrealized_plpc", 0)) * 100
                    current_pl = float(pos.get("unrealized_pl", 0))

                    # 50% profit target
                    if current_pl_pct >= 50:
                        alerts.append({
                            "trade_id": trade_id,
                            "symbol": symbol,
                            "agent": agent,
                            "action": "CLOSE_PROFIT_TARGET",
                            "pl_pct": current_pl_pct,
                            "pl": current_pl,
                        })
                    # 2x stop loss
                    elif current_pl_pct <= -100:
                        alerts.append({
                            "trade_id": trade_id,
                            "symbol": symbol,
                            "agent": agent,
                            "action": "CLOSE_STOP_LOSS",
                            "pl_pct": current_pl_pct,
                            "pl": current_pl,
                        })
        except Exception as e:
            print(f"[monitor] Could not check {symbol}: {e}")

        # Hard close at 3:30 PM regardless
        if is_hard_close:
            alerts.append({
                "trade_id": trade_id,
                "symbol": symbol,
                "agent": agent,
                "action": "HARD_CLOSE_3:30PM",
                "pl_pct": 0,
                "pl": 0,
            })

    # Post alerts to Discord
    if alerts and DISCORD_WEBHOOK:
        msg = "🔔 **Position Monitor Alert**\n"
        for a in alerts:
            emoji = "✅" if "PROFIT" in a["action"] else "🛑"
            msg += f"{emoji} **{a['agent'].upper()}** {a['symbol']} → {a['action']}\n"
            if a.get("pl_pct"):
                msg += f"   P&L: {a['pl_pct']:+.1f}% (${a['pl']:+.2f})\n"
        try:
            req.post(DISCORD_WEBHOOK, json={"content": msg}, timeout=8)
        except:
            pass

    for a in alerts:
        print(f"[monitor] {a['agent']} {a['symbol']}: {a['action']} (P&L: {a.get('pl_pct',0):+.1f}%)")

    log_pipeline_event({"step": "monitor", "alerts": len(alerts)})


# ── EOD scoring ───────────────────────────────────────────────────────
elif mode == "eod":
    print(f"\n[pipeline] EOD — {datetime.now(ET).strftime('%H:%M ET')}")
    # EOD scoring is triggered by Amit saying "score today X/100" in Discord
    # This mode just posts a reminder to Discord
    DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK_CHAT", "")
    if DISCORD_WEBHOOK:
        import requests as req
        msg = (
            "📊 **Market Close** — Time to score today\n"
            "Tell me: `score today [0-100]/100`\n"
            "I'll run the evolution analysis on today's agent trades."
        )
        try:
            req.post(DISCORD_WEBHOOK, json={"content": msg}, timeout=8)
        except:
            pass
    print("[pipeline] EOD reminder posted")
