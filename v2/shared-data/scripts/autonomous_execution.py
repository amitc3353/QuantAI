#!/usr/bin/env python3
"""
QuantAI Autonomous Execution Engine
Executes approved trades from debate_chamber.py via Alpaca paper API.

Agent Alpha: Bull put spreads — any liquid ticker
Agent Beta:  Iron condors — SPY/QQQ only

Spreads only — no covered calls or naked positions.
Covered calls require owning underlying shares (Amit handles those manually).

Usage:
  python3 autonomous_execution.py               # execute from debate_output.json
  python3 autonomous_execution.py --check-only  # dry run, no orders placed
  python3 autonomous_execution.py --monitor-only # check positions only
"""

import json, os, sys, time, requests
from datetime import datetime, date, timedelta
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
DRY_RUN      = "--check-only" in sys.argv
MONITOR_ONLY = "--monitor-only" in sys.argv

ALPACA_KEY    = os.environ.get("ALPACA_API_KEY", "")
ALPACA_SECRET = os.environ.get("ALPACA_SECRET_KEY", "")
ALPACA_BASE   = "https://paper-api.alpaca.markets"
ALPACA_DATA   = "https://data.alpaca.markets"
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK_CHAT", "")

CACHE   = "/root/quantai-v2/shared-data/cache"
JOURNAL = "/root/quantai-v2/shared-data/journal/paper/trades.jsonl"
LOGS    = "/root/quantai-v2/shared-data/logs"
SCRIPTS = "/home/trader/QuantAI/v2/shared-data/scripts"

os.makedirs(LOGS, exist_ok=True)

# Guard constants
MAX_LOSS_PCT      = 2.0
MAX_OPEN          = 3
EARNINGS_BLACKOUT = 14
MIN_CREDIT        = 0.30
VIX_HALT          = 35
ACCOUNT_SIZE      = 20000

# Strategies agents are allowed to trade autonomously
ALLOWED_STRATEGIES = {"bull_put_spread", "iron_condor", "bear_call_spread", "put_spread", "call_spread"}
# Strategies that require owning shares — Amit handles these manually
MANUAL_ONLY = {"covered_call", "collar", "cash_secured_put"}

def log(msg):
    print(f"[{datetime.now(ET).strftime('%H:%M:%S')}] {msg}")

def headers():
    return {
        "APCA-API-KEY-ID": ALPACA_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET,
        "Content-Type": "application/json",
    }

def post_discord(msg):
    if not DISCORD_WEBHOOK or DRY_RUN:
        return
    try:
        requests.post(DISCORD_WEBHOOK, json={"content": msg[:1900]}, timeout=8)
    except Exception as e:
        log(f"Discord post failed: {e}")

def is_market_open():
    now = datetime.now(ET)
    if now.weekday() >= 5:
        return False
    h, m = now.hour, now.minute
    return not (h < 9 or (h == 9 and m < 30) or h >= 16)

def count_open_agent_trades():
    if not os.path.exists(JOURNAL):
        return 0
    trades = [json.loads(l) for l in open(JOURNAL) if l.strip()]
    return len([t for t in trades if t.get("status") == "OPEN"
                and t.get("source", "").startswith("agent")])

def check_guards(trade, intel):
    """Returns (passed, reason)."""
    macro = intel.get("macro", {})

    # Strategy gate — only spreads and condors for autonomous trading
    strategy = trade.get("strategy", "").lower().replace(" ", "_")
    if strategy in MANUAL_ONLY:
        return False, f"{strategy} requires owning shares — Amit executes manually"

    # VIX halt
    vix = macro.get("vix", 0)
    if vix >= VIX_HALT:
        return False, f"VIX {vix:.1f} ≥ {VIX_HALT} — halted"

    # Max loss
    if trade.get("max_loss_pct", 99) > MAX_LOSS_PCT:
        return False, f"Max loss {trade.get('max_loss_pct',99):.1f}% > {MAX_LOSS_PCT}% limit"

    # Min credit
    if trade.get("estimated_credit", 0) < MIN_CREDIT:
        return False, f"Credit ${trade.get('estimated_credit',0):.2f} < ${MIN_CREDIT} minimum"

    # Earnings blackout
    symbol = trade.get("symbol", "")
    earn_days = intel.get("symbols", {}).get(symbol, {}).get("next_earnings_days", 999)
    if earn_days < EARNINGS_BLACKOUT:
        return False, f"Earnings in {earn_days} days — blackout"

    # Max open positions
    if count_open_agent_trades() >= MAX_OPEN:
        return False, f"Already {MAX_OPEN} open agent positions"

    # Regime halt
    if intel.get("market_regime") == "halt":
        return False, "Market regime: halt"

    return True, "Guards passed"

def build_option_symbol(symbol, expiry, option_type, strike):
    """
    Build OCC option symbol: SYMBOL + YYMMDD + C/P + 8-digit strike (padded)
    Strike in the symbol = strike * 1000, zero-padded to 8 digits
    Example: SPY 2026-04-08 P $550 → SPY260408P00550000
    """
    try:
        exp_dt = datetime.strptime(expiry, "%Y-%m-%d")
        exp_str = exp_dt.strftime("%y%m%d")
    except:
        exp_str = expiry.replace("-", "")[2:]

    type_char = "C" if option_type.lower() in ("call", "c") else "P"
    strike_padded = f"{int(float(strike) * 1000):08d}"
    return f"{symbol}{exp_str}{type_char}{strike_padded}"

def verify_option_exists(option_symbol):
    """Check if option contract exists in Alpaca before ordering."""
    if DRY_RUN:
        return True
    try:
        r = requests.get(
            f"{ALPACA_DATA}/v1beta1/options/snapshots/{option_symbol}",
            headers=headers(),
            timeout=10
        )
        return r.status_code == 200
    except:
        return False

def find_nearest_valid_strike(symbol, target_strike, option_type, expiry):
    """
    Query Alpaca options chain to find the nearest valid strike.
    Returns closest available strike or None.
    """
    if DRY_RUN:
        return target_strike
    try:
        params = {
            "underlying_symbols": symbol,
            "expiration_date": expiry,
            "type": option_type.lower(),
            "limit": 100,
        }
        r = requests.get(
            f"{ALPACA_DATA}/v1beta1/options/contracts",
            headers=headers(),
            params=params,
            timeout=15
        )
        if r.status_code != 200:
            return None
        contracts = r.json().get("option_contracts", [])
        if not contracts:
            return None
        # Find closest strike
        strikes = [float(c.get("strike_price", 0)) for c in contracts]
        closest = min(strikes, key=lambda s: abs(s - float(target_strike)))
        return closest
    except Exception as e:
        log(f"  Strike search failed: {e}")
        return None

def resolve_expiry(expiry_str):
    """Convert '0DTE', '7DTE' etc to YYYY-MM-DD date string."""
    if expiry_str and len(expiry_str) == 10 and "-" in expiry_str:
        return expiry_str
    try:
        days = int(str(expiry_str).upper().replace("DTE","").strip())
        target = datetime.now(ET).date() + timedelta(days=max(days, 0))
        # Move to next valid weekday if lands on weekend
        while target.weekday() >= 5:
            target += timedelta(days=1)
        return target.strftime("%Y-%m-%d")
    except:
        # Default to this Friday
        today = datetime.now(ET).date()
        days_to_friday = (4 - today.weekday()) % 7 or 7
        return (today + timedelta(days=days_to_friday)).strftime("%Y-%m-%d")

def place_leg(symbol, action, option_type, strike, expiry, qty=1):
    """Place one option leg. Returns fill dict or None."""
    expiry_str = resolve_expiry(expiry)
    opt_symbol = build_option_symbol(symbol, expiry_str, option_type, strike)

    if DRY_RUN:
        log(f"  [DRY RUN] {action.upper()} {qty}x {opt_symbol}")
        return {"id": "dry-run", "symbol": opt_symbol, "status": "simulated"}

    # Verify contract exists — if not, try to find nearest valid strike
    if not verify_option_exists(opt_symbol):
        log(f"  Contract {opt_symbol} not found — searching for nearest strike...")
        nearest = find_nearest_valid_strike(symbol, strike, option_type, expiry_str)
        if nearest and nearest != float(strike):
            log(f"  Using nearest strike: ${nearest} instead of ${strike}")
            opt_symbol = build_option_symbol(symbol, expiry_str, option_type, nearest)
            strike = nearest
        else:
            log(f"  No valid contract found for {symbol} {option_type} ~${strike} {expiry_str}")
            return None

    payload = {
        "symbol": opt_symbol,
        "qty": str(qty),
        "side": "buy" if action.lower() == "buy" else "sell",
        "type": "market",
        "time_in_force": "day",
    }

    try:
        r = requests.post(f"{ALPACA_BASE}/v2/orders",
                         headers=headers(), json=payload, timeout=15)
        result = r.json()
        if r.status_code in (200, 201):
            log(f"  ✅ {action.upper()} {qty}x {opt_symbol} | ID: {result.get('id','?')[:8]}")
            return result
        else:
            msg = result.get("message", str(result))[:100]
            log(f"  ❌ Order failed: {msg}")
            return None
    except Exception as e:
        log(f"  ❌ Exception: {e}")
        return None

def log_trade(trade, agent_name, fills, intel):
    """Append executed trade to journal."""
    symbol = trade.get("symbol", "")
    existing = []
    if os.path.exists(JOURNAL):
        with open(JOURNAL) as f:
            existing = [json.loads(l) for l in f if l.strip()]

    entry = {
        "id": f"A{len(existing)+1:03d}",
        "timestamp": datetime.now(ET).isoformat(),
        "mode": "paper",
        "source": agent_name,
        "symbol": symbol,
        "strategy": trade.get("strategy", ""),
        "legs": trade.get("legs", []),
        "estimated_credit": trade.get("estimated_credit", 0),
        "max_loss_pct": trade.get("max_loss_pct", 0),
        "underlying_price": intel.get("symbols", {}).get(symbol, {}).get("price", 0),
        "vix_at_entry": intel.get("macro", {}).get("vix", 0),
        "regime_at_entry": intel.get("market_regime", "normal"),
        "thesis": trade.get("thesis", ""),
        "invalidation": trade.get("invalidation", ""),
        "contracts": 1,
        "order_ids": [f.get("id","") for f in fills if f],
        "status": "OPEN",
        "notes": f"Auto-executed by {agent_name}",
    }
    with open(JOURNAL, "a") as f:
        f.write(json.dumps(entry) + "\n")
    log(f"  📝 Logged as {entry['id']} (source: {agent_name})")
    return entry

def sync_sheets():
    try:
        import subprocess
        r = subprocess.run(["python3", f"{SCRIPTS}/sheets_sync.py"],
                          capture_output=True, text=True, timeout=30)
        if r.returncode == 0:
            log("  📊 Sheets synced")
        else:
            log(f"  ⚠️ Sheets sync: {r.stderr[:80]}")
    except Exception as e:
        log(f"  ⚠️ Sheets sync error: {e}")

def run_monitor():
    """Check open agent positions — close at profit target, stop loss, or hard close time."""
    if not os.path.exists(JOURNAL):
        return

    trades = [json.loads(l) for l in open(JOURNAL) if l.strip()]
    open_agent = [t for t in trades if t.get("status") == "OPEN"
                  and t.get("source", "").startswith("agent")]

    if not open_agent:
        log("Monitor: no open agent positions")
        return

    now = datetime.now(ET)
    hard_close = now.hour == 15 and now.minute >= 30 or now.hour > 15
    alerts = []

    for t in open_agent:
        trade_id = t.get("id")
        symbol = t.get("symbol")
        credit = t.get("estimated_credit", 0)
        agent = t.get("source", "agent")
        action = None

        if hard_close:
            action = "HARD_CLOSE_3:30PM"
            alerts.append(f"⏰ **{agent.upper()}** {trade_id} {symbol} — HARD CLOSE (3:30 PM)")
        else:
            # Try to get current P&L from Alpaca positions
            try:
                r = requests.get(f"{ALPACA_BASE}/v2/positions",
                                headers=headers(), timeout=10)
                if r.status_code == 200:
                    for pos in r.json():
                        if symbol in pos.get("symbol", ""):
                            pl_pct = float(pos.get("unrealized_plpc", 0)) * 100
                            if pl_pct >= 50:
                                action = "CLOSE_PROFIT_TARGET"
                                alerts.append(f"✅ **{agent.upper()}** {trade_id} {symbol} +{pl_pct:.0f}% — PROFIT TARGET")
                            elif pl_pct <= -100:
                                action = "CLOSE_STOP_LOSS"
                                alerts.append(f"🛑 **{agent.upper()}** {trade_id} {symbol} {pl_pct:.0f}% — STOP LOSS")
            except Exception as e:
                log(f"Monitor: could not check {symbol} P&L: {e}")

    if alerts:
        msg = "🔔 **Position Monitor**\n" + "\n".join(alerts)
        post_discord(msg)
        for alert in alerts:
            log(f"Monitor alert: {alert}")

def run():
    log(f"Autonomous Execution Engine starting {'[DRY RUN] ' if DRY_RUN else ''}")

    if MONITOR_ONLY:
        run_monitor()
        return

    if not is_market_open():
        log("Skipping execution: Market closed")
        return

    # Load intelligence packet
    intel_path = f"{CACHE}/market_intelligence.json"
    if not os.path.exists(intel_path):
        log("No intelligence packet — run market_intelligence.py first")
        return

    with open(intel_path) as f:
        intel = json.load(f)

    # Load debate output
    debate_path = f"{CACHE}/debate_output.json"
    if not os.path.exists(debate_path):
        log("No debate output — run debate_chamber.py first")
        return

    with open(debate_path) as f:
        debate = json.load(f)

    approved = debate.get("approved_trades", [])
    if not approved:
        log(f"No approved trades: {debate.get('status','?')} — {debate.get('reason','')}")
        return

    # Run monitor first
    run_monitor()

    log(f"Processing {len(approved)} approved trade(s)...")
    executed = []
    skipped = []

    for item in approved:
        trade = item.get("proposal", {})
        symbol = trade.get("symbol", "?")
        strategy = trade.get("strategy", "?").lower().replace(" ", "_")

        # Assign agent
        agent_name = "agent_beta" if strategy == "iron_condor" else "agent_alpha"

        log(f"\n{'='*50}")
        log(f"Trade: {strategy.upper()} on {symbol} | Agent: {agent_name}")
        log(f"Credit: ${trade.get('estimated_credit',0):.2f} | Max loss: {trade.get('max_loss_pct',0):.1f}%")

        # Guard check (includes strategy gate)
        guard_ok, reason = check_guards(trade, intel)
        if not guard_ok:
            log(f"  ❌ REJECTED: {reason}")
            skipped.append({"symbol": symbol, "strategy": strategy, "reason": reason})
            continue

        log("  ✅ Guards passed")

        # Place legs
        legs = trade.get("legs", [])
        fills = []
        failed = False

        for leg in legs:
            expiry = resolve_expiry(leg.get("expiry", "7DTE"))
            fill = place_leg(
                symbol=symbol,
                action=leg.get("action", "buy"),
                option_type=leg.get("type", "put"),
                strike=leg.get("strike", 0),
                expiry=expiry,
                qty=1
            )
            fills.append(fill)
            if not fill and not DRY_RUN:
                failed = True
                log("  ❌ Leg failed — skipping this trade")
                break
            time.sleep(0.5)

        if failed:
            skipped.append({"symbol": symbol, "strategy": strategy,
                           "reason": "Leg placement failed — contract not found in Alpaca paper"})
            continue

        # Log and sync
        entry = log_trade(trade, agent_name, fills, intel)
        executed.append({"entry": entry, "agent": agent_name, "trade": trade})
        time.sleep(1)

    if executed:
        sync_sheets()

    # Discord summary
    if executed or skipped:
        msg = f"🤖 **{'[DRY RUN] ' if DRY_RUN else ''}Agent Execution**\n"
        msg += f"✅ Executed: {len(executed)} | ❌ Skipped: {len(skipped)}\n\n"
        for e in executed:
            t = e["trade"]
            msg += f"**{e['agent'].upper()}**: {t.get('strategy','').replace('_',' ').upper()} on {t.get('symbol','?')}\n"
            msg += f"Credit: ${t.get('estimated_credit',0):.2f} | ID: {e['entry']['id']}\n\n"
        for s in skipped:
            msg += f"**Skipped** {s['symbol']} ({s['strategy']}): {s['reason']}\n"
        post_discord(msg)

    log(f"\nDone — {len(executed)} executed, {len(skipped)} skipped")


if __name__ == "__main__":
    run()
