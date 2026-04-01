#!/usr/bin/env python3
"""
QuantAI Autonomous Execution Engine
Places Alpaca paper orders for Agent Alpha and Agent Beta.
Called after debate_chamber.py selects approved trades.

Agent Alpha: Bull put spreads — any liquid ticker, dynamic
Agent Beta:  Iron condors on SPY/QQQ — when conditions support

Execution flow:
  1. Read debate_output.json
  2. For each approved trade, check all guard rules
  3. Place legs via Alpaca paper API
  4. Log fill to journal with source=agent_alpha or agent_beta
  5. Sync to Google Sheets
  6. Post execution summary to Discord

Usage:
  python3 autonomous_execution.py               # execute approved debate trades
  python3 autonomous_execution.py --check-only  # dry run, no orders placed
"""

import json, os, sys, time, requests
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
DRY_RUN = "--check-only" in sys.argv

ALPACA_KEY    = os.environ.get("ALPACA_API_KEY", "")
ALPACA_SECRET = os.environ.get("ALPACA_SECRET_KEY", "")
ALPACA_BASE   = "https://paper-api.alpaca.markets"
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK_CHAT", "")

CACHE      = "/root/quantai-v2/shared-data/cache"
JOURNAL    = "/root/quantai-v2/shared-data/journal/paper/trades.jsonl"
LOGS       = "/root/quantai-v2/shared-data/logs"
SCRIPTS    = "/home/trader/QuantAI/v2/shared-data/scripts"

os.makedirs(LOGS, exist_ok=True)

# ── Guard rules ───────────────────────────────────────────────────────
MAX_LOSS_PCT      = 2.0    # max % of account per trade
MAX_OPEN          = 3      # max simultaneous open positions
EARNINGS_BLACKOUT = 14     # days
MIN_CREDIT        = 0.30   # min credit to enter
VIX_HALT          = 35     # halt all trading above this
NO_TRADE_START    = (9, 30)
NO_TRADE_END      = (9, 45)
HARD_CLOSE        = (15, 30)

ACCOUNT_SIZE      = 20000  # paper account size

def log(msg):
    ts = datetime.now(ET).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


def post_discord(msg):
    if not DISCORD_WEBHOOK or DRY_RUN:
        return
    try:
        requests.post(DISCORD_WEBHOOK, json={"content": msg[:1900]}, timeout=8)
    except Exception as e:
        log(f"Discord post failed: {e}")


def alpaca_headers():
    return {
        "APCA-API-KEY-ID": ALPACA_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET,
        "Content-Type": "application/json",
    }


def get_account():
    r = requests.get(f"{ALPACA_BASE}/v2/account", headers=alpaca_headers(), timeout=10)
    return r.json()


def get_open_positions():
    r = requests.get(f"{ALPACA_BASE}/v2/positions", headers=alpaca_headers(), timeout=10)
    return r.json() if r.status_code == 200 else []


def get_open_orders():
    r = requests.get(f"{ALPACA_BASE}/v2/orders?status=open", headers=alpaca_headers(), timeout=10)
    return r.json() if r.status_code == 200 else []


def count_open_paper_trades():
    """Count open trades in our journal."""
    if not os.path.exists(JOURNAL):
        return 0
    trades = [json.loads(l) for l in open(JOURNAL) if l.strip()]
    return len([t for t in trades if t.get("status") == "OPEN"])


def check_time_window():
    """Returns (ok, reason). Checks no-trade windows."""
    now = datetime.now(ET)
    h, m = now.hour, now.minute
    # Pre-market or after close
    if h < 9 or (h == 9 and m < 30):
        return False, "Pre-market — market not open"
    if h > 16 or (h == 16 and m > 0):
        return False, "Market closed"
    # No-trade window at open
    if (h, m) >= NO_TRADE_START and (h, m) < NO_TRADE_END:
        return False, "No-trade window 9:30-9:45 AM ET"
    # Hard close window
    if (h, m) >= HARD_CLOSE:
        return False, "Past 3:30 PM — no new entries"
    return True, "Market open"


def check_guards(trade, intel_packet):
    """Run all guard rules. Returns (passed, reason)."""
    macro = intel_packet.get("macro", {})

    # VIX halt
    vix = macro.get("vix", 0)
    if vix >= VIX_HALT:
        return False, f"VIX {vix:.1f} ≥ {VIX_HALT} — all trading halted"

    # Max loss pct
    max_loss_pct = trade.get("max_loss_pct", 99)
    if max_loss_pct > MAX_LOSS_PCT:
        return False, f"Max loss {max_loss_pct:.1f}% exceeds {MAX_LOSS_PCT}% limit"

    # Min credit
    credit = trade.get("estimated_credit", 0)
    if credit < MIN_CREDIT:
        return False, f"Credit ${credit:.2f} below minimum ${MIN_CREDIT}"

    # Earnings blackout
    symbol = trade.get("symbol", "")
    symbols = intel_packet.get("symbols", {})
    earn_days = symbols.get(symbol, {}).get("next_earnings_days", 999)
    if earn_days < EARNINGS_BLACKOUT:
        return False, f"{symbol} earnings in {earn_days} days — blackout period"

    # Max open positions
    open_count = count_open_paper_trades()
    if open_count >= MAX_OPEN:
        return False, f"Already {open_count} open positions — max {MAX_OPEN}"

    # Market regime
    regime = intel_packet.get("market_regime", "normal")
    if regime == "halt":
        return False, "Market regime: HALT — no new trades"

    return True, "All guards passed"


def place_option_order(symbol, action, option_type, strike, expiry, qty=1):
    """
    Place a single option leg via Alpaca.
    action: "buy" or "sell"
    option_type: "call" or "put"
    Returns order dict or None on failure.
    """
    if DRY_RUN:
        log(f"  [DRY RUN] Would {action} {qty}x {symbol} ${strike} {option_type} exp {expiry}")
        return {"id": "dry-run", "status": "simulated"}

    if not ALPACA_KEY:
        log("  ERROR: ALPACA_API_KEY not set")
        return None

    # Build option symbol: SYMBOL + YYMMDD + C/P + 8-digit strike (cents)
    try:
        from datetime import datetime as dt
        exp_dt = dt.strptime(expiry, "%Y-%m-%d")
        exp_str = exp_dt.strftime("%y%m%d")
    except:
        exp_str = expiry.replace("-", "")[2:]  # fallback

    opt_type_char = "C" if option_type.lower() == "call" else "P"
    strike_int = int(float(strike) * 1000)
    option_symbol = f"{symbol}{exp_str}{opt_type_char}{strike_int:08d}"

    order_side = "buy" if action.lower() == "buy" else "sell"

    payload = {
        "symbol": option_symbol,
        "qty": str(qty),
        "side": order_side,
        "type": "market",
        "time_in_force": "day",
    }

    try:
        r = requests.post(
            f"{ALPACA_BASE}/v2/orders",
            headers=alpaca_headers(),
            json=payload,
            timeout=15
        )
        result = r.json()
        if r.status_code in (200, 201):
            log(f"  ✅ Placed: {action} {qty}x {option_symbol} | Order ID: {result.get('id','?')}")
            return result
        else:
            log(f"  ❌ Order failed: {result.get('message', result)}")
            return None
    except Exception as e:
        log(f"  ❌ Order exception: {e}")
        return None


def log_trade_to_journal(trade, agent_name, fills, intel_packet):
    """Append executed trade to journal."""
    macro = intel_packet.get("macro", {})
    symbol = trade.get("symbol", "")
    symbols = intel_packet.get("symbols", {})
    underlying_price = symbols.get(symbol, {}).get("price", 0)

    existing = []
    if os.path.exists(JOURNAL):
        with open(JOURNAL) as f:
            existing = [json.loads(l) for l in f if l.strip()]

    entry = {
        "id": f"A{len(existing)+1:03d}",  # A prefix for agent trades
        "timestamp": datetime.now(ET).isoformat(),
        "mode": "paper",
        "source": agent_name,             # "agent_alpha" or "agent_beta"
        "symbol": symbol,
        "strategy": trade.get("strategy", ""),
        "action": trade.get("strategy", "").upper().replace(" ", "_"),
        "legs": trade.get("legs", []),
        "estimated_credit": trade.get("estimated_credit", 0),
        "max_loss_pct": trade.get("max_loss_pct", 0),
        "underlying_price": underlying_price,
        "vix_at_entry": macro.get("vix", 0),
        "regime_at_entry": intel_packet.get("market_regime", "normal"),
        "thesis": trade.get("thesis", ""),
        "invalidation": trade.get("invalidation", ""),
        "contracts": 1,
        "order_ids": [f.get("id") for f in fills if f],
        "status": "OPEN",
        "notes": f"Auto-executed by {agent_name}"
    }

    with open(JOURNAL, "a") as f:
        f.write(json.dumps(entry) + "\n")

    log(f"  📝 Logged as {entry['id']} (source: {agent_name})")
    return entry


def sync_sheets():
    """Sync journal to Google Sheets."""
    try:
        import subprocess
        result = subprocess.run(
            ["python3", f"{SCRIPTS}/sheets_sync.py"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            log("  📊 Google Sheets synced")
        else:
            log(f"  ⚠️ Sheets sync failed: {result.stderr[:100]}")
    except Exception as e:
        log(f"  ⚠️ Sheets sync error: {e}")


# ── Main execution ────────────────────────────────────────────────────

def run():
    log(f"Autonomous Execution Engine starting {'[DRY RUN]' if DRY_RUN else ''}")

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
        log(f"No approved trades from debate — {debate.get('status', 'unknown')}: {debate.get('reason', '')}")
        return

    # Time window check
    time_ok, time_reason = check_time_window()
    if not time_ok:
        log(f"Skipping execution: {time_reason}")
        post_discord(f"⏸️ Auto-execution skipped: {time_reason}")
        return

    log(f"Processing {len(approved)} approved trade(s)...")
    executed = []
    skipped = []

    for item in approved:
        trade = item.get("proposal", {})
        symbol = trade.get("symbol", "?")
        strategy = trade.get("strategy", "?")

        # Determine agent
        if strategy in ["iron_condor"]:
            agent_name = "agent_beta"
        else:
            agent_name = "agent_alpha"

        log(f"\n{'='*50}")
        log(f"Trade: {strategy.upper()} on {symbol} | Agent: {agent_name}")
        log(f"Credit: ${trade.get('estimated_credit',0):.2f} | Max loss: {trade.get('max_loss_pct',0):.1f}%")
        log(f"Thesis: {trade.get('thesis','')}")

        # Guard check
        guard_ok, guard_reason = check_guards(trade, intel)
        if not guard_ok:
            log(f"  ❌ GUARD REJECTED: {guard_reason}")
            skipped.append({"symbol": symbol, "reason": guard_reason})
            continue

        log(f"  ✅ Guards passed")

        # Place legs
        legs = trade.get("legs", [])
        fills = []
        failed = False

        for leg in legs:
            fill = place_option_order(
                symbol=symbol,
                action=leg.get("action", "buy"),
                option_type=leg.get("type", "put"),
                strike=leg.get("strike", 0),
                expiry=_resolve_expiry(leg.get("expiry", "0DTE")),
                qty=1
            )
            fills.append(fill)
            if not fill and not DRY_RUN:
                failed = True
                log(f"  ❌ Leg failed — rolling back")
                break
            time.sleep(0.5)  # small delay between legs

        if failed:
            skipped.append({"symbol": symbol, "reason": "Leg placement failed"})
            continue

        # Log the trade
        entry = log_trade_to_journal(trade, agent_name, fills, intel)
        executed.append({"entry": entry, "agent": agent_name, "trade": trade})
        time.sleep(1)

    # Sync sheets
    if executed:
        sync_sheets()

    # Discord summary
    summary = f"🤖 **Auto-Execution Complete** {'[DRY RUN] ' if DRY_RUN else ''}\n"
    summary += f"Executed: {len(executed)} | Skipped: {len(skipped)}\n\n"

    for e in executed:
        t = e["trade"]
        summary += f"✅ **{e['agent'].upper()}**: {t.get('strategy','').upper()} on {t.get('symbol','?')}\n"
        summary += f"   Credit: ${t.get('estimated_credit',0):.2f} | Max loss: {t.get('max_loss_pct',0):.1f}%\n"
        summary += f"   Entry: {e['entry']['id']} logged\n\n"

    for s in skipped:
        summary += f"❌ **SKIPPED** {s['symbol']}: {s['reason']}\n"

    post_discord(summary)
    log(f"\nDone — {len(executed)} executed, {len(skipped)} skipped")


def _resolve_expiry(expiry_str):
    """Convert '0DTE', '7DTE', or 'YYYY-MM-DD' to a date string."""
    from datetime import datetime, timedelta
    if expiry_str and "-" in expiry_str and len(expiry_str) == 10:
        return expiry_str  # already a date
    try:
        days = int(expiry_str.upper().replace("DTE", "").strip())
        target = datetime.now(ET).date() + timedelta(days=days)
        # Adjust to nearest Friday if options expiry
        while target.weekday() not in (4, 0, 1, 2, 3):  # any weekday
            target += timedelta(days=1)
        return target.strftime("%Y-%m-%d")
    except:
        # Default to this Friday
        today = datetime.now(ET).date()
        days_until_friday = (4 - today.weekday()) % 7
        if days_until_friday == 0:
            days_until_friday = 7
        return (today + timedelta(days=days_until_friday)).strftime("%Y-%m-%d")


if __name__ == "__main__":
    run()
