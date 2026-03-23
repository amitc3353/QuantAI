"""
agent1_iron_condor.py — Autonomous SPY/QQQ 0DTE Iron Condor Bot
================================================================
Strategy: Sell 0DTE iron condors on SPY (and QQQ as fallback).
Mode: FULLY AUTONOMOUS — no human approval required in paper mode.

Entry logic:
  - Runs at 9:50 AM ET (after open vol settles)
  - Second entry at 11:30 AM ET if first already at 40%+ profit
  - Skips: VIX < 13, VIX > 30, FOMC/CPI days, VIX spike day (+20% intraday)

Strike selection:
  - Short strikes: delta ~0.10 (loaded from strategy_params)
  - Wing width: $5 default (loaded from strategy_params)
  - Min credit: $0.50/spread

Exit logic (monitored every 5 min during market hours):
  - 50% profit → close immediately
  - 2x credit stop-loss → close immediately
  - 3:30 PM hard close regardless of P&L

Self-learning:
  - Every trade logged to per-agent journal
  - EOD scoring compares params vs outcomes
  - Auto-PR proposes param tweaks when score < 90

Capital allocation: $40,000 of $100k paper account
Max risk per trade: $500 (2x credit on $5 wide = worst case)
"""

import os
import json
import logging
from datetime import datetime, timezone, date
from pathlib import Path
import sys

# Add services to path
sys.path.insert(0, "/app/services")
sys.path.insert(0, "/app/discord-bot")

import aiohttp

log = logging.getLogger("agent1-iron-condor")

# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY PARAMETERS — loaded from file so self-improve can tweak them
# ─────────────────────────────────────────────────────────────────────────────

PARAMS_FILE = Path("/app/configs/agent1_params.json")
JOURNAL_FILE = Path("/app/data/memory/paper/agent1_journal.jsonl")

DEFAULT_PARAMS = {
    "version": 1,
    "primary_symbol": "SPY",
    "fallback_symbol": "QQQ",
    "short_delta": 0.10,
    "wing_width": 5.0,
    "min_credit": 0.50,
    "profit_target_pct": 0.50,     # Close at 50% of max profit
    "stop_loss_mult": 2.0,          # Close at 2x credit received
    "hard_close_hour": 15,
    "hard_close_minute": 30,
    "entry_1_hour": 9,
    "entry_1_minute": 50,
    "entry_2_hour": 11,
    "entry_2_minute": 30,
    "entry_2_requires_profit_pct": 0.40,  # Only enter 2nd if 1st is 40% profit
    "vix_min": 13.0,
    "vix_max": 30.0,
    "max_daily_trades": 2,
    "position_size_pct": 1.5,       # % of $40k per spread leg = ~$600
    "capital_allocation": 40000,
    "_note": "Tweaked by self-improve engine. See agent1_journal for history.",
}


def load_params() -> dict:
    if PARAMS_FILE.exists():
        try:
            with open(PARAMS_FILE) as f:
                params = json.load(f)
            # Merge with defaults for any missing keys
            return {**DEFAULT_PARAMS, **params}
        except Exception as e:
            log.warning(f"Could not load params: {e}, using defaults")
    return DEFAULT_PARAMS.copy()


def save_params(params: dict):
    PARAMS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(PARAMS_FILE, "w") as f:
        json.dump(params, f, indent=2)
    log.info(f"Agent 1 params saved (version {params.get('version', '?')})")


# ─────────────────────────────────────────────────────────────────────────────
# JOURNAL
# ─────────────────────────────────────────────────────────────────────────────

def log_trade(record: dict):
    JOURNAL_FILE.parent.mkdir(parents=True, exist_ok=True)
    record["agent"] = "agent1_iron_condor"
    record["timestamp"] = datetime.now(timezone.utc).isoformat()
    with open(JOURNAL_FILE, "a") as f:
        f.write(json.dumps(record) + "\n")
    log.info(f"Trade logged: {record.get('event')} | {record.get('symbol')} | {record.get('details', '')}")


# ─────────────────────────────────────────────────────────────────────────────
# GUARD CHECK
# ─────────────────────────────────────────────────────────────────────────────

GUARD_URL = os.getenv("GUARD_URL", "http://trader-guards:8100")


async def guard_check(proposal: dict) -> dict:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{GUARD_URL}/check",
                json=proposal,
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                return await resp.json()
    except Exception as e:
        return {"result": "REJECT", "reason": f"Guard unreachable: {e}"}


# ─────────────────────────────────────────────────────────────────────────────
# DISCORD POSTING
# ─────────────────────────────────────────────────────────────────────────────

WEBHOOK_PROPOSALS = os.getenv("DISCORD_WEBHOOK_PROPOSALS", "")
WEBHOOK_SYSTEM = os.getenv("DISCORD_WEBHOOK_SYSTEM", "")
WEBHOOK_EXECUTION = os.getenv("DISCORD_WEBHOOK_EXECUTION", "")


async def post_discord(webhook: str, embeds: list[dict]):
    if not webhook:
        return
    try:
        async with aiohttp.ClientSession() as session:
            await session.post(webhook, json={"embeds": embeds}, timeout=aiohttp.ClientTimeout(total=10))
    except Exception as e:
        log.error(f"Discord post failed: {e}")


def make_embed(title, desc, color=0x3498DB, fields=None, footer=None):
    embed = {
        "title": title,
        "description": desc,
        "color": color,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if fields:
        embed["fields"] = fields
    if footer:
        embed["footer"] = {"text": footer}
    return embed


# ─────────────────────────────────────────────────────────────────────────────
# CORE ENTRY LOGIC
# ─────────────────────────────────────────────────────────────────────────────

# Track today's trades in memory
_today_trades = []
_active_positions = []  # {symbol, entry_credit, current_value, leg_details}


async def run_entry(entry_number: int = 1, context: dict = None):
    """
    Main entry function. Called by scheduler at 9:50 AM (entry 1) and 11:30 AM (entry 2).
    Fully autonomous — executes without human approval in paper mode.
    """
    from market_data import get_vix, get_options_chain, build_iron_condor_strikes, get_iv_rank

    params = load_params()
    today = date.today().isoformat()

    import os as _os
    log.info(f"=== Agent 1: Iron Condor Entry {entry_number} ===")

    # Load lessons and ask Claude (Haiku) whether to proceed given past experience
    relevant_lessons = []
    journal_veto = None  # Can be set to "skip" or "caution" with a reason
    try:
        import sys
        sys.path.insert(0, "/app/discord-bot")
        from memory import search_lessons, get_recent_trades
        from pathlib import Path as _Path
        import aiohttp as _aiohttp

        # Load lessons relevant to current conditions
        condor_lessons = search_lessons("condor")
        vix_lessons = search_lessons("vix")
        credit_lessons = search_lessons("credit")
        stop_lessons = search_lessons("stop loss")
        relevant_lessons = (condor_lessons + vix_lessons + credit_lessons + stop_lessons)[-8:]

        # Only run the journal check if we have meaningful lessons (3+)
        if len(relevant_lessons) >= 3:
            lesson_text = "\n".join(f"- {l.get('lesson','')}" for l in relevant_lessons)

            # Get recent trade outcomes for pattern context
            recent = get_recent_trades(10)
            recent_outcomes = []
            for t in recent[-5:]:
                sym = t.get("symbol","?")
                pnl = t.get("pnl", t.get("pnl_per_contract", 0))
                reason = t.get("close_reason","?")
                recent_outcomes.append(f"{sym}: {'win' if pnl and pnl>0 else 'loss'} ({reason})")

            anthropic_key = _os.getenv("ANTHROPIC_API_KEY", "")
            haiku_model = _os.getenv("CLAUDE_HAIKU_MODEL", "claude-haiku-4-5-20251001")

            if anthropic_key:
                prompt = f"""You are reviewing an iron condor entry decision based on past trading lessons.

CURRENT CONDITIONS:
- Symbol: {params.get('primary_symbol','SPY')}
- VIX: {vix_val:.1f} ({regime})
- Context score: {context.get('score','?') if context else 'unknown'}/100
- Wing width: ${params.get('wing_width',5):.0f} | Short delta: {params.get('short_delta',0.10):.2f}

LESSONS LEARNED FROM PAST TRADES:
{lesson_text}

RECENT OUTCOMES (last 5 trades):
{chr(10).join(recent_outcomes) if recent_outcomes else 'No recent trades yet'}

Based ONLY on these lessons and current conditions, reply with ONE of:
- "proceed" — conditions match past successful setups
- "caution: [one sentence reason]" — proceed but something warrants care
- "skip: [one sentence reason]" — a lesson clearly contradicts this entry

Be decisive. If lessons are not clearly relevant, say "proceed".
Reply with only the verdict, nothing else."""

                async with _aiohttp.ClientSession() as _sess:
                    async with _sess.post(
                        "https://api.anthropic.com/v1/messages",
                        json={"model": haiku_model, "max_tokens": 60,
                              "messages": [{"role": "user", "content": prompt}]},
                        headers={"Content-Type": "application/json",
                                 "x-api-key": anthropic_key,
                                 "anthropic-version": "2023-06-01"},
                        timeout=_aiohttp.ClientTimeout(total=8),
                    ) as _resp:
                        if _resp.status == 200:
                            _data = await _resp.json()
                            verdict = "".join(
                                b["text"] for b in _data.get("content", [])
                                if b.get("type") == "text"
                            ).strip().lower()

                            if verdict.startswith("skip"):
                                journal_veto = {"action": "skip", "reason": verdict}
                                log.info(f"Journal veto: {verdict}")
                            elif verdict.startswith("caution"):
                                journal_veto = {"action": "caution", "reason": verdict}
                                log.info(f"Journal caution: {verdict}")
                            else:
                                log.info(f"Journal check: proceed ({len(relevant_lessons)} lessons reviewed)")

    except Exception as le:
        log.debug(f"Journal check failed (non-blocking): {le}")

    # Act on journal veto if set
    if journal_veto and journal_veto["action"] == "skip":
        reason = journal_veto["reason"]
        log.info(f"SKIPPED by journal: {reason}")
        log_trade({"event": "skip", "reason": "journal_veto", "details": reason, "date": today})
        await post_discord(WEBHOOK_PROPOSALS, [make_embed(
            "📓 Agent 1 Skipped: Journal Veto",
            f"Past lessons say skip this entry.\n**Reason:** {reason}",
            color=0xF39C12,
            footer="QuantAI Journal · learning from history"
        )])
        return

    if journal_veto and journal_veto["action"] == "caution":
        # Caution: widen wings by $2 as extra protection
        params["wing_width"] = params.get("wing_width", 5.0) + 2.0
        log.info(f"Journal caution applied: wing_width widened to {params['wing_width']}")

    # Apply context-adjusted parameters if context was provided
    if context and context.get("agent1_params"):
        ctx_params = context["agent1_params"]
        if ctx_params.get("wing_width"):
            params["wing_width"] = ctx_params["wing_width"]
            log.info(f"Context adjusted wing_width: {params['wing_width']} (context score: {context.get('score')})")
        if ctx_params.get("short_delta"):
            params["short_delta"] = ctx_params["short_delta"]
            log.info(f"Context adjusted short_delta: {params['short_delta']}")

    # ── Pre-flight checks ──────────────────────────────────────────────────

    # 1. Max daily trades
    today_count = sum(1 for t in _today_trades if t.get("date") == today and t.get("event") == "entry")
    if today_count >= params["max_daily_trades"]:
        log.info(f"Max daily trades reached ({today_count}). Skipping.")
        return

    # 2. Entry 2 condition: only if entry 1 is at 40%+ profit
    if entry_number == 2 and _active_positions:
        best_profit = max(
            (p.get("unrealized_profit_pct", 0) for p in _active_positions), default=0
        )
        if best_profit < params["entry_2_requires_profit_pct"] * 100:
            log.info(f"Entry 2 skipped: active position only at {best_profit:.0f}% profit (need {params['entry_2_requires_profit_pct']*100:.0f}%)")
            return

    # 3. VIX check
    vix_data = get_vix()
    vix = vix_data.get("vix", 20)
    if not vix_data.get("tradeable", False):
        msg = f"VIX {vix:.1f} — {vix_data.get('regime_note', 'not tradeable')}"
        log.info(f"SKIP: {msg}")
        log_trade({"event": "skip", "reason": "vix_check", "details": msg, "vix": vix, "date": today})
        await post_discord(WEBHOOK_SYSTEM, [make_embed(
            "⏸️ Agent 1: Entry Skipped", msg, color=0xF39C12,
            footer=f"QuantAI Agent 1 · Entry {entry_number}"
        )])
        return

    # 4. IV check for symbol selection
    symbol = params["primary_symbol"]
    ivr = get_iv_rank(symbol)

    # If primary symbol IV too low, try fallback
    if ivr.get("iv_rank", 50) < 30:
        log.info(f"{symbol} IV rank {ivr.get('iv_rank')} too low, trying {params['fallback_symbol']}")
        symbol = params["fallback_symbol"]
        ivr = get_iv_rank(symbol)

    # ── Build the trade ────────────────────────────────────────────────────

    # Fetch 0DTE options chain
    chain = get_options_chain(symbol, dte_min=0, dte_max=1)
    if "error" in chain:
        log.error(f"Options chain failed: {chain['error']}")
        log_trade({"event": "error", "reason": "chain_fetch_failed", "details": chain["error"], "date": today})
        return

    if not chain.get("calls") or not chain.get("puts"):
        log.warning(f"Empty options chain for {symbol} — market may be closed")
        return

    # Find optimal strikes
    condor = build_iron_condor_strikes(
        chain,
        short_delta=params["short_delta"],
        wing_width=params["wing_width"],
    )

    if not condor:
        log.warning(f"Could not build condor strikes for {symbol}")
        log_trade({"event": "skip", "reason": "no_valid_strikes", "symbol": symbol, "date": today})
        return

    # Credit check
    credit = condor.get("estimated_credit", 0)
    if credit < params["min_credit"]:
        msg = f"Credit ${credit:.2f} < minimum ${params['min_credit']:.2f} — skipping"
        log.info(f"SKIP: {msg}")
        log_trade({"event": "skip", "reason": "insufficient_credit", "credit": credit, "date": today})
        return

    # ── Guard check ────────────────────────────────────────────────────────

    guard_proposal = {
        "symbol": symbol,
        "strategy": "iron_condor",
        "position_pct": params["position_size_pct"],
        "max_loss_pct": 1.5,
        "dte": condor.get("dte", 0),
        "iv_rank": ivr.get("iv_rank", 50),
    }

    guard_result = await guard_check(guard_proposal)

    if guard_result.get("result") == "REJECT":
        reason = guard_result.get("reason", "unknown")
        log.info(f"Guard REJECTED: {reason}")
        log_trade({"event": "guard_reject", "reason": reason, "symbol": symbol, "date": today})
        await post_discord(WEBHOOK_PROPOSALS, [make_embed(
            f"🛡️ Agent 1: Guard Rejected — {symbol}",
            f"**Iron Condor** could not execute.\nGuard reason: `{reason}`\n"
            f"Credit was ${credit:.2f} | VIX {vix:.1f} | IV Rank {ivr.get('iv_rank', '?')}",
            color=0xE74C3C, footer="QuantAI Agent 1"
        )])
        return

    # ── EXECUTE — Autonomous paper mode ───────────────────────────────────

    log.info(
        f"✅ EXECUTING Iron Condor on {symbol}: "
        f"P{condor['put_long_strike']}/{condor['put_short_strike']} "
        f"C{condor['call_short_strike']}/{condor['call_long_strike']} "
        f"Credit: ${credit:.2f} | DTE: {condor.get('dte', 0)}"
    )

    # In paper mode: simulate execution (Alpaca paper API doesn't support
    # multi-leg options orders — we log as if executed and track P&L manually)
    trade_record = {
        "event": "entry",
        "date": today,
        "entry_number": entry_number,
        "symbol": symbol,
        "strategy": "iron_condor",
        "put_long_strike": condor["put_long_strike"],
        "put_short_strike": condor["put_short_strike"],
        "call_short_strike": condor["call_short_strike"],
        "call_long_strike": condor["call_long_strike"],
        "entry_credit": credit,
        "max_profit": credit,
        "max_risk": condor.get("max_risk", params["wing_width"] - credit),
        "profit_target": round(credit * params["profit_target_pct"], 2),
        "stop_loss": round(credit * params["stop_loss_mult"], 2),
        "expiry": condor.get("expiry"),
        "dte": condor.get("dte", 0),
        "underlying_price": condor.get("underlying_price"),
        "vix_at_entry": vix,
        "iv_rank_at_entry": ivr.get("iv_rank"),
        "short_put_delta": condor.get("short_put_delta"),
        "short_call_delta": condor.get("short_call_delta"),
        "params_version": params.get("version", 1),
        "mode": "paper_simulated",
        "status": "open",
        "lessons_applied": [l.get("lesson", "")[:80] for l in relevant_lessons],
        # Context snapshot at entry — used by correlation_analyzer and backtester
        "context_score": context.get("score") if context else None,
        "context_decision": context.get("decision") if context else None,
        "context_components": {
            k: v.get("score") for k, v in
            (context.get("components", {}) if context else {}).items()
        } if context else {},
        "market_regime": context.get("vix_data", {}).get("regime") if context else None,
    }

    log_trade(trade_record)

    # Add to active positions
    position = {**trade_record, "current_credit": credit, "unrealized_profit_pct": 0}
    _active_positions.append(position)
    _today_trades.append(trade_record)

    # Post trade card to #trade-proposals
    fields = [
        {"name": "Underlying", "value": f"**{symbol}** @ ${condor['underlying_price']:.2f}", "inline": True},
        {"name": "VIX", "value": f"{vix:.1f}", "inline": True},
        {"name": "IV Rank", "value": f"{ivr.get('iv_rank', '?'):.0f}", "inline": True},
        {"name": "Put Spread", "value": f"${condor['put_long_strike']:.0f} / ${condor['put_short_strike']:.0f}", "inline": True},
        {"name": "Call Spread", "value": f"${condor['call_short_strike']:.0f} / ${condor['call_long_strike']:.0f}", "inline": True},
        {"name": "Credit", "value": f"**${credit:.2f}**", "inline": True},
        {"name": "Max Profit", "value": f"${credit:.2f} (at expiry)", "inline": True},
        {"name": "Max Risk", "value": f"${condor.get('max_risk', 0):.2f}", "inline": True},
        {"name": "Stop Loss", "value": f"At ${credit * params['stop_loss_mult']:.2f} debit", "inline": True},
        {"name": "Profit Target", "value": f"Close at ${credit * params['profit_target_pct']:.2f} debit (50%)", "inline": True},
        {"name": "Hard Close", "value": f"3:30 PM ET", "inline": True},
        {"name": "Mode", "value": "🤖 AUTO-EXECUTED (paper)", "inline": True},
    ]

    await post_discord(WEBHOOK_PROPOSALS, [make_embed(
        f"🟢 Agent 1 EXECUTED: Iron Condor on {symbol}",
        f"Entry #{entry_number} of {params['max_daily_trades']} for today.\n"
        f"**Paper mode — no real capital at risk.**",
        color=0x2ECC71, fields=fields,
        footer=f"QuantAI Agent 1 · Iron Condor · Params v{params.get('version', 1)}"
    )])

    log.info(f"Agent 1 entry complete. Active positions: {len(_active_positions)}")


# ─────────────────────────────────────────────────────────────────────────────
# POSITION MONITORING — Called every 5 min during market hours
# ─────────────────────────────────────────────────────────────────────────────

async def monitor_positions():
    """
    Check all active positions against exit rules.
    Auto-closes at profit target or stop loss.
    """
    if not _active_positions:
        return

    from market_data import get_options_chain
    params = load_params()
    today = date.today().isoformat()
    now = datetime.now()

    # Hard close time
    hard_close = now.replace(
        hour=params["hard_close_hour"],
        minute=params["hard_close_minute"],
        second=0, microsecond=0
    )

    for position in list(_active_positions):
        if position.get("status") != "open":
            continue

        symbol = position["symbol"]
        entry_credit = position["entry_credit"]
        profit_target = position["profit_target"]
        stop_loss = position["stop_loss"]

        # Fetch current mid prices for the legs
        chain = get_options_chain(symbol, dte_min=0, dte_max=1)
        if "error" in chain:
            continue

        # Find current value of the condor (cost to close)
        current_cost = _estimate_condor_current_cost(position, chain)

        if current_cost is None:
            continue

        profit_collected = entry_credit - current_cost
        profit_pct = (profit_collected / entry_credit) * 100 if entry_credit > 0 else 0

        # Update tracking
        position["current_credit"] = current_cost
        position["unrealized_profit_pct"] = profit_pct

        close_reason = None

        # Check exit conditions
        if now >= hard_close:
            close_reason = "hard_close_3:30pm"
        elif current_cost <= profit_target:
            close_reason = f"profit_target_{profit_pct:.0f}pct"
        elif current_cost >= stop_loss:
            close_reason = f"stop_loss_{profit_pct:.0f}pct"

        if close_reason:
            await _close_position(position, close_reason, current_cost, profit_pct, today)


def _estimate_condor_current_cost(position: dict, chain: dict) -> float:
    """Estimate current cost to close condor from chain data."""
    from market_data import _find_contract_by_strike

    try:
        put_short = _find_contract_by_strike(chain.get("puts", []), position["put_short_strike"])
        put_long = _find_contract_by_strike(chain.get("puts", []), position["put_long_strike"])
        call_short = _find_contract_by_strike(chain.get("calls", []), position["call_short_strike"])
        call_long = _find_contract_by_strike(chain.get("calls", []), position["call_long_strike"])

        if not all([put_short, put_long, call_short, call_long]):
            return None

        # Cost to close = buy back the shorts, sell the longs
        close_cost = (
            put_short.get("ask", 0)   # buy back short put
            - put_long.get("bid", 0)  # sell long put
            + call_short.get("ask", 0)  # buy back short call
            - call_long.get("bid", 0)   # sell long call
        )
        return round(close_cost, 2)
    except Exception as e:
        log.debug(f"Could not estimate close cost: {e}")
        return None


async def _close_position(position: dict, reason: str, close_cost: float, profit_pct: float, today: str):
    params = load_params()
    symbol = position["symbol"]
    entry_credit = position["entry_credit"]
    pnl = round((entry_credit - close_cost) * 100, 2)  # per 1 contract = 100 shares
    pnl_pct = round(profit_pct, 1)

    log.info(f"CLOSING {symbol} condor: {reason} | P&L: ${pnl:.2f} ({pnl_pct:.1f}%)")

    log_trade({
        "event": "exit",
        "date": today,
        "symbol": symbol,
        "close_reason": reason,
        "entry_credit": entry_credit,
        "close_cost": close_cost,
        "pnl_per_contract": pnl,
        "pnl_pct": pnl_pct,
        "outcome": "win" if pnl > 0 else "loss",
        "params_version": params.get("version", 1),
        "status": "closed",
    })

    position["status"] = "closed"
    if position in _active_positions:
        _active_positions.remove(position)

    color = 0x2ECC71 if pnl > 0 else 0xE74C3C
    emoji = "✅" if pnl > 0 else "❌"

    await post_discord(WEBHOOK_EXECUTION, [make_embed(
        f"{emoji} Agent 1 CLOSED: {symbol} Iron Condor",
        f"**Reason:** {reason.replace('_', ' ').title()}\n"
        f"**P&L:** ${pnl:.2f} ({pnl_pct:+.1f}%)",
        color=color,
        fields=[
            {"name": "Entry Credit", "value": f"${entry_credit:.2f}", "inline": True},
            {"name": "Close Cost", "value": f"${close_cost:.2f}", "inline": True},
            {"name": "P&L", "value": f"**${pnl:.2f}**", "inline": True},
        ],
        footer="QuantAI Agent 1 · Iron Condor"
    )])


# ─────────────────────────────────────────────────────────────────────────────
# EOD SCORING — Agent-specific
# ─────────────────────────────────────────────────────────────────────────────

async def run_eod_score(call_claude_fn):
    """
    Score today's Agent 1 trades. Called by orchestrator at 4:30 PM.
    Uses Claude Haiku for fast, cheap analysis.
    Saves lessons to agent1-specific lesson file.
    """
    today = date.today().isoformat()
    params = load_params()

    today_trades = []
    if JOURNAL_FILE.exists():
        with open(JOURNAL_FILE) as f:
            for line in f:
                if line.strip():
                    try:
                        r = json.loads(line)
                        if r.get("date") == today:
                            today_trades.append(r)
                    except Exception:
                        continue

    if not today_trades:
        log.info("Agent 1: No trades today to score")
        return {"score": None, "reason": "no_trades"}

    entries = [t for t in today_trades if t.get("event") == "entry"]
    exits = [t for t in today_trades if t.get("event") == "exit"]
    wins = [t for t in exits if t.get("outcome") == "win"]
    total_pnl = sum(t.get("pnl_per_contract", 0) for t in exits)

    prompt = f"""Score these Agent 1 (Iron Condor) trades from {today}:

PARAMS USED: short_delta={params['short_delta']}, wing_width={params['wing_width']}, 
profit_target={params['profit_target_pct']*100:.0f}%, stop_mult={params['stop_loss_mult']}x

TRADES:
{json.dumps(today_trades, indent=1)}

SUMMARY: {len(entries)} entries, {len(exits)} exits, {len(wins)} wins, total P&L: ${total_pnl:.2f}

Score 0-100. Output ONLY valid JSON, no markdown:
{{
  "score": 0-100,
  "summary": "2 sentence summary",
  "wins": ["what worked"],
  "losses": ["what didn't work"],
  "lessons": [
    "LESSON: [specific searchable lesson]. WHEN: [exact condition]. ACTION: [what to do].",
    "Example: LESSON: Stop loss hit when VIX spiked mid-day. WHEN: VIX rises >15% intraday after entry. ACTION: Close position proactively, do not wait for 2x stop."
  ],
  "param_suggestions": [{{"param": "name", "current": val, "suggested": val, "reason": "why"}}]
}}

LESSON FORMAT RULES (critical for the system to learn):
- Each lesson must be searchable by keyword (include: vix, condor, credit, stop, delta, etc.)
- Each lesson must have a WHEN condition (not vague — specific VIX levels, times, conditions)
- Each lesson must have an ACTION (what the agent should do differently)
- Bad lesson: "Be careful when VIX is high"
- Good lesson: "LESSON: Iron condor stopped out when VIX spiked >25 after 11 AM. WHEN: VIX >22 at second entry time (11:30 AM). ACTION: Skip entry 2 when VIX elevated at 11:25 AM check."
- Only write lessons supported by today's actual data
- param_suggestions: only if 3+ trades support the change"""

    result = await call_claude_fn(prompt, model="haiku")

    try:
        cleaned = result.strip().lstrip("```json").lstrip("```").rstrip("```")
        score_data = json.loads(cleaned)
    except Exception:
        log.warning(f"Could not parse Agent 1 score: {result[:200]}")
        return {"score": 50, "raw": result}

    # Save lessons
    lessons_file = Path("/app/data/memory/paper/agent1_lessons.jsonl")
    lessons_file.parent.mkdir(parents=True, exist_ok=True)
    for lesson in score_data.get("lessons", []):
        with open(lessons_file, "a") as f:
            f.write(json.dumps({
                "date": today,
                "lesson": lesson,
                "score": score_data.get("score"),
                "params_version": params.get("version", 1),
            }) + "\n")

    # Save score
    score_log = Path(f"/app/data/journal/agent1_score_{today}.json")
    score_log.parent.mkdir(parents=True, exist_ok=True)
    with open(score_log, "w") as f:
        json.dump(score_data, f, indent=2)

    log.info(f"Agent 1 EOD score: {score_data.get('score')}/100")
    return score_data
