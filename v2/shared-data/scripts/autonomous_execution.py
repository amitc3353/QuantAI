
import logging
import sys
sys.path.insert(0, '/home/trader/QuantAI/v2/shared-data/scripts')
from _logger import setup as _logger_setup
_logger_setup('autonomous_execution')

#!/usr/bin/env python3
"""
QuantAI Autonomous Execution Engine
Executes approved trades from debate_chamber.py via Alpaca paper API.

Agent Alpha: Bull put spreads, bear call spreads, and other defined-risk strategies
Agent Beta:  Iron condors and butterflies — submitted as multi-leg orders

Key fixes (Apr 2):
- Iron condors submitted as single mleg order (fixes uncovered options error)
- Strike selection queries broker chain first, falls back to nearest available
- Spreads also submitted as mleg for cleaner execution

Usage:
  python3 autonomous_execution.py               # execute from debate_output.json
  python3 autonomous_execution.py --check-only  # dry run
  python3 autonomous_execution.py --monitor-only
"""

import json, os, sys, time, requests
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

# Unique IBKR clientId so concurrent cron jobs don't collide on clientId=1.
os.environ.setdefault("IBKR_CLIENT_ID", "12")
from broker import get_broker

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

ET          = ZoneInfo("America/New_York")
DRY_RUN     = "--check-only" in sys.argv
MONITOR_ONLY= "--monitor-only" in sys.argv

CACHE   = "/root/quantai-v2/shared-data/cache"
JOURNAL = "/root/quantai-v2/shared-data/journal/paper/trades.jsonl"
LOGS    = "/root/quantai-v2/shared-data/logs"
SCRIPTS = "/home/trader/QuantAI/v2/shared-data/scripts"

# Alert channel — post trade notifications here
DISCORD_BOT_TOKEN    = os.environ.get("DISCORD_BOT_TOKEN", "")
DISCORD_ALERTS_CH    = os.environ["DISCORD_CHANNEL_ALERTS"]

os.makedirs(LOGS, exist_ok=True)

# Guard constants
MAX_LOSS_PCT      = 2.0
MAX_OPEN          = 3
EARNINGS_BLACKOUT = 14
MIN_CREDIT        = 0.30
VIX_HALT          = 35

# Strategies agents can execute autonomously (defined-risk only, no shares needed)
ALLOWED_STRATEGIES = {
    "bull_put_spread", "bear_call_spread", "iron_condor", "iron_butterfly",
    "calendar_spread", "diagonal_spread", "jade_lizard", "put_spread", "call_spread",
}
MANUAL_ONLY = {"covered_call", "collar", "cash_secured_put", "covered_strangle"}

def log(msg):
    print(f"[{datetime.now(ET).strftime('%H:%M:%S')}] {msg}")

def post_discord(msg, channel_id=None):
    """Post a message to Discord via bot token. Falls back to webhook if set."""
    if DRY_RUN:
        return
    ch = channel_id or DISCORD_ALERTS_CH
    # Try bot token first (preferred — no webhook setup needed)
    if DISCORD_BOT_TOKEN and ch:
        try:
            requests.post(
                f"https://discord.com/api/v10/channels/{ch}/messages",
                headers={"Authorization": f"Bot {DISCORD_BOT_TOKEN}",
                         "Content-Type": "application/json"},
                json={"content": msg[:1900]},
                timeout=8
            )
            return
        except:
            pass

def post_trade_alert(entry, trade, agent_name):
    """Post a trade execution alert to #alerts channel."""
    strategy = trade.get("strategy", "").replace("_", " ").upper()
    symbol   = trade.get("symbol", "?")
    credit   = trade.get("estimated_credit", 0)
    max_loss = trade.get("max_loss_pct", 0)
    thesis   = trade.get("thesis", "")[:120]
    legs     = trade.get("legs", [])

    legs_str = ""
    for l in legs:
        action = l.get("action", "?").upper()
        ltype  = l.get("type", "").upper()
        strike = l.get("strike", "?")
        expiry = l.get("expiry", "")
        legs_str += f"\n  {action} {ltype} ${strike} {expiry}"

    credit_label = f"Debit: ${abs(credit):.2f}" if credit < 0 else f"Credit: ${credit:.2f}"
    agent_label  = "🔵 Agent Alpha" if "alpha" in agent_name else "🟠 Agent Beta"

    msg = (
        f"🤖 **TRADE EXECUTED — {agent_label}**\n"
        f"**{strategy}** on **{symbol}** | {credit_label} | Max loss: {max_loss:.1f}%\n"
        f"```{legs_str.strip()}```\n"
        f"📝 {thesis}\n"
        f"🆔 {entry.get('id','?')} | 📅 {datetime.now(ET).strftime('%b %d %H:%M ET')}"
    )
    post_discord(msg, channel_id=DISCORD_ALERTS_CH)

def is_market_open():
    now = datetime.now(ET)
    if now.weekday() >= 5: return False
    h, m = now.hour, now.minute
    return not (h < 9 or (h == 9 and m < 30) or h >= 16)

def count_open_agent_trades():
    if not os.path.exists(JOURNAL): return 0
    trades = [json.loads(l) for l in open(JOURNAL) if l.strip()]
    return len([t for t in trades if t.get("status") == "OPEN"
                and t.get("source", "").startswith("agent")])

# ── Option symbol builder ─────────────────────────────────────────────
def build_occ_symbol(symbol, expiry, option_type, strike):
    """OCC format: SYMBOL + YYMMDD + C/P + 8-digit strike (strike*1000, zero-padded)"""
    try:
        exp_str = datetime.strptime(expiry, "%Y-%m-%d").strftime("%y%m%d")
    except:
        exp_str = expiry.replace("-", "")[2:]
    type_char = "C" if option_type.lower() in ("call", "c") else "P"
    strike_padded = f"{int(float(strike) * 1000):08d}"
    return f"{symbol}{exp_str}{type_char}{strike_padded}"

def resolve_expiry(expiry_str):
    """Convert '7DTE', '0DTE' etc to YYYY-MM-DD. Never returns today."""
    today = datetime.now(ET).date()

    def next_weekday(d, min_days=1):
        """Advance d by at least min_days, then skip weekends."""
        d = d + timedelta(days=min_days)
        while d.weekday() >= 5:
            d += timedelta(days=1)
        return d

    if expiry_str and len(str(expiry_str)) == 10 and "-" in str(expiry_str):
        d = datetime.strptime(str(expiry_str), "%Y-%m-%d").date()
        # If the date is today or past, push to next Friday
        if d <= today:
            days_to_friday = (4 - today.weekday()) % 7 or 7
            d = today + timedelta(days=days_to_friday)
        return d.strftime("%Y-%m-%d")

    try:
        days = int(str(expiry_str).upper().replace("DTE","").strip())
        # 0DTE: only valid before market open, otherwise use next Friday
        # Minimum is always today+2 to avoid same/next-day 404s from Alpaca
        if days == 0:
            now = datetime.now(ET)
            if now.hour < 9 or (now.hour == 9 and now.minute < 30):
                target = today  # genuine pre-market 0DTE
            else:
                # Push to next Friday — same-day and next-day chains often return 404
                days_to_friday = (4 - today.weekday()) % 7 or 7
                target = today + timedelta(days=days_to_friday)
        else:
            target = today + timedelta(days=max(days, 2))  # minimum 2 days out
        while target.weekday() >= 5:
            target += timedelta(days=1)
        return target.strftime("%Y-%m-%d")
    except:
        # Default to next Friday
        days_to_friday = (4 - today.weekday()) % 7 or 7
        return (today + timedelta(days=days_to_friday)).strftime("%Y-%m-%d")

# ── Strike selection — query broker chain ────────────────────────────
def get_available_strikes(symbol, option_type, expiry):
    """Query the active broker for available strikes on a given expiry.
    Returns sorted list of strikes, or empty list on failure.
    """
    if DRY_RUN:
        return []
    try:
        d = datetime.strptime(expiry, "%Y-%m-%d").date()
        dte = (d - date.today()).days
        if dte < 0:
            return []
        right = "C" if option_type.lower().startswith("c") else "P"
        chain = get_broker().fetch_option_chain(symbol, dte_range=(dte, dte))
        strikes = sorted({float(c["strike"]) for c in chain
                          if c.get("right") == right and c.get("expiry") == expiry})
        return list(strikes)
    except Exception as e:
        log(f"  Chain query failed: {e}")
        return []

def nearest_strike(available_strikes, target):
    """Find the strike closest to target from available list."""
    if not available_strikes:
        return None
    return min(available_strikes, key=lambda s: abs(s - float(target)))

def find_spread_strikes(symbol, short_target, long_target, option_type, expiry):
    """
    Find two real strikes for a spread from Alpaca's chain.
    Returns (short_strike, long_strike) or (None, None).
    """
    strikes = get_available_strikes(symbol, option_type, expiry)
    if not strikes:
        log(f"  No {option_type} contracts found for {symbol} {expiry}")
        return None, None

    short_strike = nearest_strike(strikes, short_target)
    if short_strike is None:
        return None, None

    # For puts: long strike is below short. For calls: long strike is above short.
    if option_type.lower() == "put":
        long_candidates = [s for s in strikes if s < short_strike]
        long_strike = max(long_candidates) if long_candidates else None
    else:
        long_candidates = [s for s in strikes if s > short_strike]
        long_strike = min(long_candidates) if long_candidates else None

    if long_strike is None:
        log(f"  Could not find long {option_type} below/above ${short_strike}")
        return None, None

    log(f"  Found strikes: short ${short_strike}, long ${long_strike} (from broker chain)")
    return short_strike, long_strike

# ── Multi-leg order (mleg) ────────────────────────────────────────────
def place_mleg_order(symbol, legs_config, strategy_name):
    """
    Place a multi-leg options order as a single mleg order.
    This avoids the 'uncovered options' error for condors/spreads.

    legs_config: list of dicts with keys:
      ratio_qty, side, position_intent, symbol (OCC)

    Returns order dict or None.
    """
    if DRY_RUN:
        log(f"  [DRY RUN] Would place mleg {strategy_name} with {len(legs_config)} legs")
        for leg in legs_config:
            log(f"    {leg['side'].upper()} {leg['symbol']}")
        return {"id": "dry-run", "status": "simulated"}

    coid = f"{strategy_name[:30]}-{int(time.time())}"
    broker = get_broker()
    result = broker.place_mleg_order(legs_config, qty=1, client_order_id=coid)
    if result is None:
        reason = getattr(broker, "_last_order_error", None) or "unknown"
        # Partial-fill safeguard: the order packet may have reached the gateway
        # even if place_mleg_order returned None (connection drop after submit).
        # Query open orders before concluding failure.
        recovered_orders = []
        try:
            recovered_orders = broker.get_open_orders(client_order_id=coid)
        except Exception as _rec_err:
            log(f"  ⚠️  get_open_orders reconciliation failed: {_rec_err}")
        if recovered_orders:
            result = recovered_orders[0]
            log(f"  ⚠️  place_mleg_order returned None but order found in open orders "
                f"(coid={coid} orderId={result.get('order_id','?')}) — treating as submitted")
            logging.warning(
                "autonomous_execution: order recovered from open orders after None result "
                "(coid=%s orderId=%s reason=%s)", coid, result.get("order_id"), reason,
            )
        else:
            log(f"  ❌ mleg order failed — {reason}")
            return None
    order_id = (result.get("order_id") or "")[:8]
    log(f"  ✅ mleg order placed | ID: {order_id}")
    # Preserve the legacy {id, status} shape for downstream callers/journaling.
    return {"id": result.get("order_id", ""), "status": result.get("status", "submitted")}

# ── Strategy execution builders ───────────────────────────────────────
def execute_bull_put_spread(symbol, trade, expiry):
    """
    Bull put spread: SELL higher put + BUY lower put.
    Both legs submitted as single mleg order.
    """
    legs = trade.get("legs", [])
    sell_leg = next((l for l in legs if l.get("action") == "sell"), None)
    buy_leg  = next((l for l in legs if l.get("action") == "buy"), None)

    short_target = float(sell_leg.get("strike", 0)) if sell_leg else 0
    long_target  = float(buy_leg.get("strike", 0))  if buy_leg  else 0

    if not short_target:
        log("  No short strike in proposal")
        return None

    # Get real strikes from Alpaca
    short_strike, long_strike = find_spread_strikes(
        symbol, short_target, long_target, "put", expiry
    )
    if not short_strike or not long_strike:
        return None

    short_sym = build_occ_symbol(symbol, expiry, "put", short_strike)
    long_sym  = build_occ_symbol(symbol, expiry, "put", long_strike)

    # Update journal-bound legs with snapped strikes + broker OCC.
    sell_leg["strike"], sell_leg["symbol"], sell_leg["expiry"] = short_strike, short_sym, expiry
    buy_leg["strike"], buy_leg["symbol"], buy_leg["expiry"] = long_strike, long_sym, expiry

    mleg_legs = [
        {"ratio_qty": "1", "side": "sell", "symbol": short_sym},
        {"ratio_qty": "1", "side": "buy",  "symbol": long_sym},
    ]
    return place_mleg_order(symbol, mleg_legs, "bull_put_spread")

def execute_bear_call_spread(symbol, trade, expiry):
    """Bear call spread: SELL lower call + BUY higher call."""
    legs = trade.get("legs", [])
    sell_leg = next((l for l in legs if l.get("action") == "sell"), None)
    buy_leg  = next((l for l in legs if l.get("action") == "buy"), None)

    short_target = float(sell_leg.get("strike", 0)) if sell_leg else 0
    long_target  = float(buy_leg.get("strike", 0))  if buy_leg  else 0

    short_strike, long_strike = find_spread_strikes(
        symbol, short_target, long_target, "call", expiry
    )
    if not short_strike or not long_strike:
        return None

    short_sym = build_occ_symbol(symbol, expiry, "call", short_strike)
    long_sym  = build_occ_symbol(symbol, expiry, "call", long_strike)

    # Update journal-bound legs with snapped strikes + broker OCC.
    sell_leg["strike"], sell_leg["symbol"], sell_leg["expiry"] = short_strike, short_sym, expiry
    buy_leg["strike"], buy_leg["symbol"], buy_leg["expiry"] = long_strike, long_sym, expiry

    mleg_legs = [
        {"ratio_qty": "1", "side": "sell", "symbol": short_sym},
        {"ratio_qty": "1", "side": "buy",  "symbol": long_sym},
    ]
    return place_mleg_order(symbol, mleg_legs, "bear_call_spread")

def execute_iron_condor(symbol, trade, expiry):
    """
    Iron condor: bull put spread + bear call spread as single 4-leg mleg order.
    This avoids the uncovered options rejection.
    """
    legs = trade.get("legs", [])

    # Separate put and call legs
    put_legs  = [l for l in legs if l.get("type", "").lower() == "put"]
    call_legs = [l for l in legs if l.get("type", "").lower() == "call"]

    put_sell  = next((l for l in put_legs  if l.get("action") == "sell"), None)
    put_buy   = next((l for l in put_legs  if l.get("action") == "buy"),  None)
    call_sell = next((l for l in call_legs if l.get("action") == "sell"), None)
    call_buy  = next((l for l in call_legs if l.get("action") == "buy"),  None)

    if not all([put_sell, put_buy, call_sell, call_buy]):
        log("  Iron condor proposal missing legs")
        return None

    # Get real put strikes
    put_short_s, put_long_s = find_spread_strikes(
        symbol,
        float(put_sell.get("strike", 0)),
        float(put_buy.get("strike", 0)),
        "put", expiry
    )
    # Get real call strikes
    call_short_s, call_long_s = find_spread_strikes(
        symbol,
        float(call_sell.get("strike", 0)),
        float(call_buy.get("strike", 0)),
        "call", expiry
    )

    if not all([put_short_s, put_long_s, call_short_s, call_long_s]):
        log("  Could not find all 4 condor strikes in broker chain")
        return None

    # Update journal-bound legs with the strikes actually used and broker OCC.
    # Without this, the journal records the LLM's proposed strikes which may
    # differ from what was placed (e.g. A011 INTC 89/86/100/105 → 89/88/100/101);
    # position_monitor's leg-matching then fails on the mismatched legs.
    put_sell["strike"] = put_short_s
    put_sell["symbol"] = build_occ_symbol(symbol, expiry, "put", put_short_s)
    put_sell["expiry"] = expiry
    put_buy["strike"] = put_long_s
    put_buy["symbol"] = build_occ_symbol(symbol, expiry, "put", put_long_s)
    put_buy["expiry"] = expiry
    call_sell["strike"] = call_short_s
    call_sell["symbol"] = build_occ_symbol(symbol, expiry, "call", call_short_s)
    call_sell["expiry"] = expiry
    call_buy["strike"] = call_long_s
    call_buy["symbol"] = build_occ_symbol(symbol, expiry, "call", call_long_s)
    call_buy["expiry"] = expiry

    mleg_legs = [
        {"ratio_qty": "1", "side": "sell",
         "symbol": build_occ_symbol(symbol, expiry, "put",  put_short_s)},
        {"ratio_qty": "1", "side": "buy",
         "symbol": build_occ_symbol(symbol, expiry, "put",  put_long_s)},
        {"ratio_qty": "1", "side": "sell",
         "symbol": build_occ_symbol(symbol, expiry, "call", call_short_s)},
        {"ratio_qty": "1", "side": "buy",
         "symbol": build_occ_symbol(symbol, expiry, "call", call_long_s)},
    ]
    return place_mleg_order(symbol, mleg_legs, "iron_condor")

def execute_generic_spread(symbol, trade, expiry):
    """Fallback for any other spread — detect put/call from legs and build mleg."""
    legs = trade.get("legs", [])
    if not legs:
        return None

    option_type = legs[0].get("type", "put").lower()
    sell_leg = next((l for l in legs if l.get("action") == "sell"), None)
    buy_leg  = next((l for l in legs if l.get("action") == "buy"),  None)
    if not sell_leg or not buy_leg:
        return None

    short_s, long_s = find_spread_strikes(
        symbol,
        float(sell_leg.get("strike", 0)),
        float(buy_leg.get("strike", 0)),
        option_type, expiry
    )
    if not short_s or not long_s:
        return None

    short_sym = build_occ_symbol(symbol, expiry, option_type, short_s)
    long_sym  = build_occ_symbol(symbol, expiry, option_type, long_s)

    # Update journal-bound legs with snapped strikes + broker OCC.
    sell_leg["strike"], sell_leg["symbol"], sell_leg["expiry"] = short_s, short_sym, expiry
    buy_leg["strike"], buy_leg["symbol"], buy_leg["expiry"] = long_s, long_sym, expiry

    mleg_legs = [
        {"ratio_qty": "1", "side": "sell", "symbol": short_sym},
        {"ratio_qty": "1", "side": "buy",  "symbol": long_sym},
    ]
    return place_mleg_order(symbol, mleg_legs, trade.get("strategy", "spread"))

def execute_diagonal_spread(symbol, trade, near_expiry, far_expiry):
    """
    Diagonal spread (poor man's covered call/put).
    BUY far-dated option + SELL near-dated option at same or nearby strike.
    Submitted as mleg order. Net debit position.
    """
    legs = trade.get("legs", [])
    sell_leg = next((l for l in legs if l.get("action") == "sell"), None)
    buy_leg  = next((l for l in legs if l.get("action") == "buy"), None)

    if not sell_leg or not buy_leg:
        log("  Diagonal spread missing sell or buy leg")
        return None

    option_type = sell_leg.get("type", "call").lower()
    sell_strike = float(sell_leg.get("strike", 0))
    buy_strike  = float(buy_leg.get("strike", sell_strike))  # often same strike

    # Get real strikes from broker chain
    near_strikes = get_available_strikes(symbol, option_type, near_expiry)
    far_strikes  = get_available_strikes(symbol, option_type, far_expiry)

    actual_sell_strike = nearest_strike(near_strikes, sell_strike) if near_strikes else sell_strike
    actual_buy_strike  = nearest_strike(far_strikes, buy_strike)   if far_strikes  else buy_strike

    if not actual_sell_strike or not actual_buy_strike:
        log(f"  Could not find diagonal strikes in broker chain")
        return None

    sell_sym = build_occ_symbol(symbol, near_expiry, option_type, actual_sell_strike)
    buy_sym  = build_occ_symbol(symbol, far_expiry,  option_type, actual_buy_strike)

    # Update journal-bound legs with snapped strikes + broker OCC.
    sell_leg["strike"], sell_leg["symbol"], sell_leg["expiry"] = actual_sell_strike, sell_sym, near_expiry
    buy_leg["strike"], buy_leg["symbol"], buy_leg["expiry"] = actual_buy_strike, buy_sym, far_expiry

    log(f"  Diagonal: BUY {buy_sym} + SELL {sell_sym}")

    mleg_legs = [
        {"ratio_qty": "1", "side": "buy",  "symbol": buy_sym},
        {"ratio_qty": "1", "side": "sell", "symbol": sell_sym},
    ]
    return place_mleg_order(symbol, mleg_legs, "diagonal_spread")


def execute_trade(trade, intel):
    """Route to correct executor based on strategy. Returns fill or None."""
    strategy = trade.get("strategy", "").lower().replace(" ", "_")
    symbol   = trade.get("symbol", "")
    legs     = trade.get("legs", [])

    log(f"  Executing {strategy.upper()} on {symbol}")

    if strategy == "diagonal_spread":
        # Diagonal needs two different expiries
        sell_leg = next((l for l in legs if l.get("action") == "sell"), None)
        buy_leg  = next((l for l in legs if l.get("action") == "buy"), None)
        near_expiry = resolve_expiry(sell_leg.get("expiry", "21DTE") if sell_leg else "21DTE")
        far_expiry  = resolve_expiry(buy_leg.get("expiry", "45DTE")  if buy_leg  else "45DTE")
        log(f"  Near expiry: {near_expiry} | Far expiry: {far_expiry}")
        return execute_diagonal_spread(symbol, trade, near_expiry, far_expiry)
    elif strategy == "iron_condor":
        expiry = resolve_expiry(legs[0].get("expiry", "14DTE") if legs else "14DTE")
        return execute_iron_condor(symbol, trade, expiry)
    elif strategy in ("bull_put_spread", "put_spread"):
        expiry = resolve_expiry(legs[0].get("expiry", "14DTE") if legs else "14DTE")
        return execute_bull_put_spread(symbol, trade, expiry)
    elif strategy in ("bear_call_spread", "call_spread"):
        expiry = resolve_expiry(legs[0].get("expiry", "14DTE") if legs else "14DTE")
        return execute_bear_call_spread(symbol, trade, expiry)
    else:
        expiry = resolve_expiry(legs[0].get("expiry", "14DTE") if legs else "14DTE")
        return execute_generic_spread(symbol, trade, expiry)

# ── Guard check ───────────────────────────────────────────────────────
def check_guards(trade, intel):
    macro    = intel.get("macro", {})
    strategy = trade.get("strategy", "").lower().replace(" ", "_")

    if strategy in MANUAL_ONLY:
        return False, f"{strategy} requires shares — Amit executes manually"
    if macro.get("vix", 0) >= VIX_HALT:
        return False, f"VIX {macro.get('vix',0):.1f} >= {VIX_HALT}"
    if trade.get("max_loss_pct", 99) > MAX_LOSS_PCT:
        return False, f"Max loss {trade.get('max_loss_pct',99):.1f}% > {MAX_LOSS_PCT}%"
    # Debit strategies (diagonal, calendar) have negative estimated_credit — skip credit floor check
    DEBIT_STRATEGIES = {"diagonal_spread", "calendar_spread"}
    if strategy not in DEBIT_STRATEGIES:
        if trade.get("estimated_credit", 0) < MIN_CREDIT:
            return False, f"Credit ${trade.get('estimated_credit',0):.2f} < ${MIN_CREDIT}"

    symbol    = trade.get("symbol", "")
    earn_days = intel.get("symbols", {}).get(symbol, {}).get("next_earnings_days", 999)
    if earn_days < EARNINGS_BLACKOUT:
        return False, f"Earnings in {earn_days} days"
    if count_open_agent_trades() >= MAX_OPEN:
        return False, f"Already {MAX_OPEN} open positions"
    if intel.get("market_regime") == "halt":
        return False, "Regime: halt"

    return True, "Guards passed"

# ── Journal ───────────────────────────────────────────────────────────
def log_trade(trade, agent_name, fill, intel):
    symbol = trade.get("symbol", "")
    existing = []
    if os.path.exists(JOURNAL):
        with open(JOURNAL) as f:
            existing = [json.loads(l) for l in f if l.strip()]

    # Per-prefix counter so Alpha (A###) and Beta (B###) don't collide.
    # Use max-of-existing+1, NOT count, because gaps in the sequence (e.g. ids
    # skipped during the IBKR migration) caused count-based IDs to collide
    # with existing entries — duplicate A010 was the symptom on 2026-04-29.
    prefix = "B" if agent_name == "agent_beta" else "A"
    max_n = 0
    for t in existing:
        tid = (t.get("id") or "")
        if tid.startswith(prefix) and tid[1:].isdigit():
            max_n = max(max_n, int(tid[1:]))
    from _decision_helpers import age_of, alpha_conviction_from_judge
    macro = intel.get("macro", {}) or {}
    entry = {
        "id": f"{prefix}{max_n+1:03d}",
        "timestamp": datetime.now(ET).isoformat(),
        "mode": "paper",
        "source": agent_name,
        "symbol": symbol,
        "strategy": trade.get("strategy", ""),
        "legs": trade.get("legs", []),
        "estimated_credit": trade.get("estimated_credit", 0),
        "max_loss_pct": trade.get("max_loss_pct", 0),
        "underlying_price": intel.get("symbols", {}).get(symbol, {}).get("price", 0),
        "vix_at_entry": macro.get("vix", 0),
        "regime_at_entry": intel.get("market_regime", "normal"),
        "thesis": trade.get("thesis", ""),
        "invalidation": trade.get("invalidation", ""),
        "order_id": fill.get("id", "") if fill else "",
        "status": "OPEN",
        "notes": f"Auto-executed by {agent_name}",
        "decision": {
            "conviction_score": alpha_conviction_from_judge(trade.get("judge_score")),
            "thesis": trade.get("thesis", ""),
            "key_risk": trade.get("key_risk", ""),
            "invalidation": trade.get("invalidation", ""),
            "alternatives_considered": trade.get("alternatives_considered", []),
            "skills_consulted": trade.get("skills_consulted", []),
            "regime_at_entry": intel.get("market_regime", "normal"),
            "vix_at_entry": macro.get("vix", 0),
            "vix_data_age_seconds": age_of(macro.get("vix_timestamp") or macro.get("timestamp")),
            "chain_data_age_seconds": age_of(trade.get("chain_timestamp")),
            "market_intel_age_seconds": age_of(intel.get("timestamp")),
            "pipeline_stage_durations": trade.get("stage_durations", {}),
        },
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
        log("  📊 Sheets synced" if r.returncode == 0 else f"  ⚠️ Sheets: {r.stderr[:60]}")
    except Exception as e:
        log(f"  ⚠️ Sheets error: {e}")

# ── Monitor ───────────────────────────────────────────────────────────
def run_monitor():
    # Discord-alert stub only — actual position closing is handled by position_monitor.py cron.
    if not os.path.exists(JOURNAL): return
    trades = [json.loads(l) for l in open(JOURNAL) if l.strip()]
    open_agent = [t for t in trades if t.get("status") == "OPEN"
                  and t.get("source", "").startswith("agent")]
    if not open_agent:
        log("Monitor: no open agent positions")
        return

    now = datetime.now(ET)
    hard_close = now.hour > 15 or (now.hour == 15 and now.minute >= 30)
    alerts = []

    for t in open_agent:
        if hard_close:
            alerts.append(f"⏰ {t.get('source','?').upper()} {t['id']} {t['symbol']} — HARD CLOSE 3:30 PM")

    if alerts:
        msg = "🔔 **Position Monitor**\n" + "\n".join(alerts)
        post_discord(msg)
        for a in alerts:
            log(f"Monitor: {a}")

# ── Main ──────────────────────────────────────────────────────────────
def run():
    log(f"Autonomous Execution Engine {'[DRY RUN] ' if DRY_RUN else ''}starting")

    if MONITOR_ONLY:
        run_monitor()
        return

    if not is_market_open():
        log("Market closed")
        return

    intel_path  = f"{CACHE}/market_intelligence.json"
    debate_path = f"{CACHE}/debate_output.json"

    if not os.path.exists(intel_path):
        log("No intel packet — run market_intelligence.py first")
        return
    if not os.path.exists(debate_path):
        log("No debate output — run debate_chamber.py first")
        return

    intel  = json.load(open(intel_path))
    debate = json.load(open(debate_path))

    approved = debate.get("approved_trades", [])
    if not approved:
        log(f"No approved trades: {debate.get('status','?')} — {debate.get('reason','')}")
        return

    run_monitor()

    log(f"Processing {len(approved)} approved trade(s)...")
    executed, skipped = [], []

    for item in approved:
        trade    = item.get("proposal", {})
        symbol   = trade.get("symbol", "?")
        strategy = trade.get("strategy", "?").lower().replace(" ", "_")
        agent_name = "agent_alpha"

        log(f"\n{'='*50}")
        log(f"Trade: {strategy.upper()} on {symbol} | Agent: {agent_name}")
        log(f"Credit: ${trade.get('estimated_credit',0):.2f} | Max loss: {trade.get('max_loss_pct',0):.1f}%")

        guard_ok, reason = check_guards(trade, intel)
        if not guard_ok:
            log(f"  ❌ REJECTED: {reason}")
            logging.warning("Trade rejected: %s", reason)
            skipped.append({"symbol": symbol, "strategy": strategy, "reason": reason})
            continue

        log("  ✅ Guards passed")
        fill = execute_trade(trade, intel)

        if not fill and not DRY_RUN:
            skipped.append({"symbol": symbol, "strategy": strategy,
                           "reason": "Execution failed — no valid contracts found"})
            continue

        # Don't log in dry run mode
        if DRY_RUN:
            log(f"  [DRY RUN] Would log as agent trade — not writing to journal")
            executed.append({"entry": {"id": "dry-run"}, "agent": agent_name, "trade": trade})
            continue

        entry = log_trade(trade, agent_name, fill, intel)
        post_trade_alert(entry, trade, agent_name)
        executed.append({"entry": entry, "agent": agent_name, "trade": trade})
        time.sleep(1)

    if executed:
        sync_sheets()

    # Discord summary
    if executed or skipped:
        msg = f"🤖 **{'[DRY RUN] ' if DRY_RUN else ''}Agent Execution**\n"
        msg += f"✅ {len(executed)} executed | ❌ {len(skipped)} skipped\n\n"
        for e in executed:
            t = e["trade"]
            msg += f"**{e['agent'].upper()}**: {t.get('strategy','').replace('_',' ').upper()} {t.get('symbol','?')} | Credit: ${t.get('estimated_credit',0):.2f} | ID: {e['entry']['id']}\n"
        for s in skipped:
            msg += f"**Skipped** {s['symbol']}: {s['reason']}\n"
        post_discord(msg)

    log(f"\nDone — {len(executed)} executed, {len(skipped)} skipped")

if __name__ == "__main__":
    run()
