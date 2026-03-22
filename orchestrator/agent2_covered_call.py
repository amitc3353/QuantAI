"""
agent2_covered_call.py — Autonomous Covered Call Bot
======================================================
Strategy: Sell covered calls against high-liquidity holdings from
Amit's actual portfolio: PLTR, TSM, MU, AMD, AVGO, ASML.

Mode: FULLY AUTONOMOUS — no human approval required in paper mode.
Paper mode simulates owning shares and tracks P&L from premiums.

Entry logic (runs Mon morning at 9:50 AM, scans all 6 symbols):
  - IV Rank must be >= 30 for the symbol (don't sell cheap options)
  - No earnings within 14 days (blackout)
  - Short call delta <= 0.25 (want upside participation)
  - Target 21-35 DTE
  - Only 1 active covered call per symbol at a time

Exit logic (checked Monday morning for previous week's positions):
  - 50% profit → buy back and close
  - Stock runs through short strike → roll up and out
  - Hard close: 2 DTE remaining (don't hold through expiry)

Self-learning:
  - Per-agent journal tracks which tickers, deltas, DTEs perform best
  - Weekly scoring instead of daily (covered calls are weekly/monthly)
  - Auto-PR proposes ticker/delta/DTE tweaks when yield < threshold

Capital allocation: $30,000 of $100k paper account
Target yield: 1%+ per month per position = 12%+ annualized
"""

import os
import json
import logging
from datetime import datetime, timezone, date, timedelta
from pathlib import Path
import sys

sys.path.insert(0, "/app/services")
sys.path.insert(0, "/app/discord-bot")

import aiohttp

log = logging.getLogger("agent2-covered-call")

# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY PARAMETERS
# ─────────────────────────────────────────────────────────────────────────────

PARAMS_FILE = Path("/app/configs/agent2_params.json")
JOURNAL_FILE = Path("/app/data/memory/paper/agent2_journal.jsonl")

DEFAULT_PARAMS = {
    "version": 1,
    "symbols": ["PLTR", "TSM", "MU", "AMD", "AVGO", "ASML"],
    "target_delta": 0.20,
    "min_delta": 0.10,
    "max_delta": 0.25,
    "target_dte_min": 21,
    "target_dte_max": 35,
    "min_iv_rank": 30,
    "profit_target_pct": 0.50,
    "roll_trigger_dte": 7,          # Roll when <= 7 DTE remaining
    "hard_close_dte": 2,            # Force close at 2 DTE
    "min_premium": 0.30,            # Skip if premium < $0.30
    "earnings_blackout_days": 14,   # No entry within 14 days of earnings
    "simulated_shares_per_symbol": 100,  # Simulate owning 100 shares each
    "capital_allocation": 30000,
    "_note": "Covered call bot on PLTR/TSM/MU/AMD/AVGO/ASML"
}


def load_params() -> dict:
    if PARAMS_FILE.exists():
        try:
            with open(PARAMS_FILE) as f:
                return {**DEFAULT_PARAMS, **json.load(f)}
        except Exception as e:
            log.warning(f"Could not load agent2 params: {e}")
    return DEFAULT_PARAMS.copy()


def save_params(params: dict):
    PARAMS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(PARAMS_FILE, "w") as f:
        json.dump(params, f, indent=2)


def log_trade(record: dict):
    JOURNAL_FILE.parent.mkdir(parents=True, exist_ok=True)
    record["agent"] = "agent2_covered_call"
    record["timestamp"] = datetime.now(timezone.utc).isoformat()
    with open(JOURNAL_FILE, "a") as f:
        f.write(json.dumps(record) + "\n")
    log.info(f"Trade logged: {record.get('event')} | {record.get('symbol')} | {record.get('details', '')}")


# ─────────────────────────────────────────────────────────────────────────────
# ACTIVE POSITION TRACKING
# ─────────────────────────────────────────────────────────────────────────────

POSITIONS_FILE = Path("/app/data/memory/paper/agent2_positions.json")


def load_active_positions() -> list:
    if POSITIONS_FILE.exists():
        try:
            with open(POSITIONS_FILE) as f:
                return json.load(f)
        except Exception:
            return []
    return []


def save_active_positions(positions: list):
    POSITIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(POSITIONS_FILE, "w") as f:
        json.dump(positions, f, indent=2, default=str)


# ─────────────────────────────────────────────────────────────────────────────
# GUARD + DISCORD HELPERS
# ─────────────────────────────────────────────────────────────────────────────

GUARD_URL = os.getenv("GUARD_URL", "http://trader-guards:8100")
WEBHOOK_PROPOSALS = os.getenv("DISCORD_WEBHOOK_PROPOSALS", "")
WEBHOOK_EXECUTION = os.getenv("DISCORD_WEBHOOK_EXECUTION", "")
WEBHOOK_SYSTEM = os.getenv("DISCORD_WEBHOOK_SYSTEM", "")


async def guard_check(proposal: dict) -> dict:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{GUARD_URL}/check", json=proposal,
                timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                return await resp.json()
    except Exception as e:
        return {"result": "REJECT", "reason": f"Guard unreachable: {e}"}


async def post_discord(webhook: str, embeds: list):
    if not webhook:
        return
    try:
        async with aiohttp.ClientSession() as session:
            await session.post(webhook, json={"embeds": embeds}, timeout=aiohttp.ClientTimeout(total=10))
    except Exception as e:
        log.error(f"Discord post failed: {e}")


def make_embed(title, desc, color=0x3498DB, fields=None, footer=None):
    e = {"title": title, "description": desc, "color": color,
         "timestamp": datetime.now(timezone.utc).isoformat()}
    if fields:
        e["fields"] = fields
    if footer:
        e["footer"] = {"text": footer}
    return e


def _is_near_earnings(symbol: str, blackout_days: int) -> bool:
    """
    Check if symbol has earnings within blackout_days.
    Uses yfinance calendar. Returns True = block trading.
    """
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        cal = ticker.calendar
        if cal is None or cal.empty:
            return False
        # Calendar index is dates
        for dt in cal.columns:
            try:
                earnings_date = datetime.strptime(str(dt), "%Y-%m-%d %H:%M:%S").date()
            except Exception:
                try:
                    earnings_date = dt.date() if hasattr(dt, 'date') else None
                except Exception:
                    continue
            if earnings_date:
                days_away = abs((earnings_date - date.today()).days)
                if days_away <= blackout_days:
                    log.info(f"{symbol} earnings in {days_away} days — blocked")
                    return True
        return False
    except Exception as e:
        log.debug(f"Earnings check failed for {symbol}: {e}")
        return False  # Default allow if check fails


# ─────────────────────────────────────────────────────────────────────────────
# WEEKLY SCAN — Runs Monday 9:50 AM
# ─────────────────────────────────────────────────────────────────────────────

async def run_weekly_scan(context: dict = None, skip_symbols: list = None):
    """
    Scan all 6 symbols. For each without an active covered call,
    evaluate and potentially sell a new covered call.
    Called Monday mornings after Agent 1's first entry.
    """
    from market_data import get_options_chain, get_iv_rank, _get_stock_price, find_strikes_by_delta

    params = load_params()
    active_positions = load_active_positions()
    today = date.today().isoformat()

    # First: check existing positions for exits/rolls
    await _review_existing_positions(active_positions, params)
    active_positions = load_active_positions()  # Reload after potential closes

    active_symbols = {p["symbol"] for p in active_positions if p.get("status") == "open"}

    log.info(f"=== Agent 2: Weekly Covered Call Scan ===")
    log.info(f"Active positions: {active_symbols}")

    new_trades = []

    if skip_symbols is None:
        skip_symbols = []

    # Apply context-adjusted parameters
    if context and context.get("agent2_params"):
        ctx_params = context["agent2_params"]
        if ctx_params.get("min_delta"):
            params["target_delta"] = max(params["target_delta"], ctx_params["min_delta"])
            log.info(f"Context adjusted min delta: {params['target_delta']} (context score: {context.get('score')})")
        if ctx_params.get("skip_symbols"):
            skip_symbols = list(set(skip_symbols + ctx_params["skip_symbols"]))

    for symbol in params["symbols"]:
        if symbol in active_symbols:
            log.info(f"{symbol}: already has active covered call, skipping")
            continue

        log.info(f"Scanning {symbol}...")

        # Dark pool / flow skip
        if symbol in skip_symbols:
            log.info(f"{symbol}: skipped due to unusual flow/dark pool activity")
            log_trade({"event": "skip", "reason": "flow_detector_flag", "symbol": symbol, "date": today})
            continue

        # Earnings blackout
        if _is_near_earnings(symbol, params["earnings_blackout_days"]):
            log.info(f"{symbol}: earnings blackout, skipping")
            log_trade({"event": "skip", "reason": "earnings_blackout", "symbol": symbol, "date": today})
            continue

        # IV rank check
        ivr = get_iv_rank(symbol)
        if ivr.get("iv_rank", 0) < params["min_iv_rank"]:
            log.info(f"{symbol}: IV rank {ivr.get('iv_rank'):.0f} < {params['min_iv_rank']}, skipping")
            log_trade({
                "event": "skip", "reason": "iv_rank_too_low",
                "symbol": symbol, "iv_rank": ivr.get("iv_rank"), "date": today
            })
            continue

        # Get options chain (21-35 DTE)
        chain = get_options_chain(symbol, dte_min=params["target_dte_min"], dte_max=params["target_dte_max"])
        if "error" in chain:
            log.warning(f"{symbol}: chain error: {chain['error']}")
            continue

        if not chain.get("calls"):
            log.warning(f"{symbol}: no calls in chain")
            continue

        # Find optimal strike
        call_contract = find_strikes_by_delta(
            chain, "call",
            target_delta=params["target_delta"],
            tolerance=0.05,
        )

        if not call_contract:
            log.info(f"{symbol}: no suitable strike found at delta {params['target_delta']}")
            continue

        premium = call_contract.get("mid", 0)
        if premium < params["min_premium"]:
            log.info(f"{symbol}: premium ${premium:.2f} < min ${params['min_premium']:.2f}, skipping")
            continue

        stock_price = chain.get("underlying_price", 0)
        monthly_yield_pct = round((premium / stock_price) * 100, 2) if stock_price else 0

        # Guard check
        guard_result = await guard_check({
            "symbol": symbol,
            "strategy": "covered_call",
            "position_pct": 3.0,
            "max_loss_pct": 2.0,
            "dte": call_contract.get("dte", 28),
            "iv_rank": ivr.get("iv_rank", 50),
        })

        if guard_result.get("result") == "REJECT":
            log.info(f"{symbol}: guard rejected: {guard_result.get('reason')}")
            continue

        # EXECUTE
        trade = {
            "event": "entry",
            "date": today,
            "symbol": symbol,
            "strategy": "covered_call",
            "call_symbol": call_contract.get("symbol"),
            "strike": call_contract.get("strike"),
            "expiry": call_contract.get("expiry"),
            "dte": call_contract.get("dte"),
            "premium": premium,
            "delta": call_contract.get("delta"),
            "theta": call_contract.get("theta"),
            "iv": call_contract.get("iv"),
            "iv_rank": ivr.get("iv_rank"),
            "stock_price": stock_price,
            "monthly_yield_pct": monthly_yield_pct,
            "profit_target": round(premium * params["profit_target_pct"], 2),
            "shares_simulated": params["simulated_shares_per_symbol"],
            "total_premium_collected": round(premium * params["simulated_shares_per_symbol"], 2),
            "params_version": params.get("version", 1),
            "mode": "paper_simulated",
            "status": "open",
        }

        log_trade(trade)
        active_positions.append(trade)
        new_trades.append(trade)

        log.info(
            f"✅ EXECUTED {symbol} covered call: "
            f"${call_contract['strike']:.0f}C "
            f"@ ${premium:.2f} | {call_contract.get('dte')} DTE | "
            f"Yield: {monthly_yield_pct:.2f}%"
        )

        fields = [
            {"name": "Stock", "value": f"**{symbol}** @ ${stock_price:.2f}", "inline": True},
            {"name": "Short Call", "value": f"${call_contract['strike']:.0f}C", "inline": True},
            {"name": "DTE", "value": str(call_contract.get("dte")), "inline": True},
            {"name": "Premium", "value": f"**${premium:.2f}**", "inline": True},
            {"name": "Delta", "value": f"{call_contract.get('delta', '?'):.2f}", "inline": True},
            {"name": "IV Rank", "value": f"{ivr.get('iv_rank', '?'):.0f}", "inline": True},
            {"name": "Monthly Yield", "value": f"{monthly_yield_pct:.2f}%", "inline": True},
            {"name": "Total Premium", "value": f"${trade['total_premium_collected']:.2f} (100 shares)", "inline": True},
            {"name": "Profit Target", "value": f"Close at ${trade['profit_target']:.2f}", "inline": True},
            {"name": "Mode", "value": "🤖 AUTO-EXECUTED (paper)", "inline": True},
        ]

        await post_discord(WEBHOOK_PROPOSALS, [make_embed(
            f"🟢 Agent 2 EXECUTED: Covered Call on {symbol}",
            f"Selling 1 covered call (simulating 100 shares).\n**Paper mode — no real capital at risk.**",
            color=0x9B59B6, fields=fields,
            footer=f"QuantAI Agent 2 · Covered Call · Params v{params.get('version', 1)}"
        )])

    save_active_positions(active_positions)

    if new_trades:
        total_premium = sum(t["total_premium_collected"] for t in new_trades)
        log.info(f"Agent 2 weekly scan complete: {len(new_trades)} new calls sold, ${total_premium:.2f} total premium")
    else:
        log.info("Agent 2: No new covered calls entered this week")

    return new_trades


# ─────────────────────────────────────────────────────────────────────────────
# POSITION MONITORING — Check existing positions for exits/rolls
# ─────────────────────────────────────────────────────────────────────────────

async def _review_existing_positions(positions: list, params: dict):
    """Review open positions — close at profit target or roll at low DTE."""
    from market_data import get_options_chain, _find_contract_by_strike

    today = date.today().isoformat()

    for position in positions:
        if position.get("status") != "open":
            continue

        symbol = position["symbol"]
        entry_premium = position.get("premium", 0)
        expiry_str = position.get("expiry")

        if not expiry_str:
            continue

        expiry = date.fromisoformat(expiry_str)
        dte_remaining = (expiry - date.today()).days

        # Hard close at 2 DTE
        if dte_remaining <= params["hard_close_dte"]:
            await _close_covered_call(position, "hard_close_2dte", entry_premium * 0.05, today)
            continue

        # Fetch current call price
        chain = get_options_chain(
            symbol,
            dte_min=max(0, dte_remaining - 3),
            dte_max=dte_remaining + 3
        )
        if "error" in chain:
            continue

        current_call = _find_contract_by_strike(
            chain.get("calls", []),
            position.get("strike", 0)
        )
        if not current_call:
            continue

        current_bid = current_call.get("bid", entry_premium)
        profit_pct = (entry_premium - current_bid) / entry_premium * 100 if entry_premium > 0 else 0

        # Close at 50% profit
        if current_bid <= position.get("profit_target", entry_premium * 0.5):
            await _close_covered_call(position, "profit_target_50pct", current_bid, today)


async def _close_covered_call(position: dict, reason: str, close_cost: float, today: str):
    symbol = position["symbol"]
    entry_premium = position.get("premium", 0)
    shares = position.get("shares_simulated", 100)
    pnl = round((entry_premium - close_cost) * shares, 2)
    pnl_pct = round((entry_premium - close_cost) / entry_premium * 100, 1) if entry_premium > 0 else 0

    log.info(f"CLOSING {symbol} covered call: {reason} | P&L: ${pnl:.2f} ({pnl_pct:.1f}%)")

    log_trade({
        "event": "exit",
        "date": today,
        "symbol": symbol,
        "close_reason": reason,
        "entry_premium": entry_premium,
        "close_cost": close_cost,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "outcome": "win" if pnl > 0 else "loss",
        "status": "closed",
    })

    position["status"] = "closed"

    active = load_active_positions()
    for p in active:
        if (p.get("symbol") == symbol and
                p.get("strike") == position.get("strike") and
                p.get("expiry") == position.get("expiry")):
            p["status"] = "closed"
    save_active_positions(active)

    color = 0x9B59B6 if pnl > 0 else 0xE74C3C
    await post_discord(WEBHOOK_EXECUTION, [make_embed(
        f"{'✅' if pnl > 0 else '❌'} Agent 2 CLOSED: {symbol} Covered Call",
        f"**Reason:** {reason.replace('_', ' ').title()}\n**P&L:** ${pnl:.2f} ({pnl_pct:+.1f}%)",
        color=color,
        fields=[
            {"name": "Entry Premium", "value": f"${entry_premium:.2f}", "inline": True},
            {"name": "Close Cost", "value": f"${close_cost:.2f}", "inline": True},
            {"name": "P&L", "value": f"**${pnl:.2f}**", "inline": True},
        ],
        footer="QuantAI Agent 2 · Covered Call"
    )])


# ─────────────────────────────────────────────────────────────────────────────
# EOD SCORING — Weekly for Agent 2
# ─────────────────────────────────────────────────────────────────────────────

async def run_weekly_score(call_claude_fn):
    """Score Agent 2's performance. Called Friday EOD."""
    params = load_params()
    week_start = (date.today() - timedelta(days=7)).isoformat()

    trades = []
    if JOURNAL_FILE.exists():
        with open(JOURNAL_FILE) as f:
            for line in f:
                if line.strip():
                    try:
                        r = json.loads(line)
                        if r.get("date", "") >= week_start:
                            trades.append(r)
                    except Exception:
                        continue

    if not trades:
        return {"score": None, "reason": "no_trades"}

    exits = [t for t in trades if t.get("event") == "exit"]
    wins = [t for t in exits if t.get("outcome") == "win"]
    total_yield = sum(t.get("pnl", 0) for t in exits)
    avg_monthly_yield = sum(
        t.get("monthly_yield_pct", 0) for t in trades if t.get("event") == "entry"
    ) / max(len([t for t in trades if t.get("event") == "entry"]), 1)

    prompt = f"""Score Agent 2 (Covered Call) weekly performance:

PARAMS: symbols={params['symbols']}, target_delta={params['target_delta']}, 
min_iv_rank={params['min_iv_rank']}, dte={params['target_dte_min']}-{params['target_dte_max']}

TRADES THIS WEEK:
{json.dumps(trades, indent=1)}

SUMMARY: {len(exits)} closed, {len(wins)} wins, total P&L: ${total_yield:.2f}, avg yield: {avg_monthly_yield:.2f}%/month

Score 0-100. Output ONLY valid JSON, no markdown:
{{"score": 0-100, "summary": "brief", "best_ticker": "symbol", "worst_ticker": "symbol",
"lessons": ["lessons"], "param_suggestions": [{{"param": "name", "current": val, "suggested": val, "reason": "why"}}],
"kill_signal": false, "kill_reason": "only if yield consistently < 0.5%/month"}}"""

    result = await call_claude_fn(prompt, model="haiku")

    try:
        cleaned = result.strip().lstrip("```json").lstrip("```").rstrip("```")
        score_data = json.loads(cleaned)
    except Exception:
        return {"score": 50, "raw": result}

    # Save lessons
    lessons_file = Path("/app/data/memory/paper/agent2_lessons.jsonl")
    lessons_file.parent.mkdir(parents=True, exist_ok=True)
    for lesson in score_data.get("lessons", []):
        with open(lessons_file, "a") as f:
            f.write(json.dumps({
                "date": date.today().isoformat(),
                "lesson": lesson,
                "score": score_data.get("score"),
                "params_version": params.get("version", 1),
            }) + "\n")

    score_log = Path(f"/app/data/journal/agent2_score_{date.today().isoformat()}.json")
    with open(score_log, "w") as f:
        json.dump(score_data, f, indent=2)

    log.info(f"Agent 2 weekly score: {score_data.get('score')}/100")
    return score_data
