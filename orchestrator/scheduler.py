"""
Orchestrator — Full Trading Pipeline
=======================================
Morning brief → auto-propose high-conviction trades → guard check → post to #trade-proposals

Scheduled tasks:
  - 6:30 AM ET: Morning brief + auto-proposals
  - 4:30 PM ET: EOD scoring + lesson extraction
  - Every 5 min (market hours): Health check
"""

import os
import json
import logging
import asyncio
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO")),
    format="%(asctime)s [orchestrator] %(levelname)s: %(message)s",
)
log = logging.getLogger("orchestrator")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
GUARD_URL = os.getenv("GUARD_URL", "http://trader-guards:8100")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ALPHA_VANTAGE_KEY = os.getenv("ALPHA_VANTAGE_API_KEY", "")
SONNET_MODEL = os.getenv("CLAUDE_SONNET_MODEL", "claude-sonnet-4-20250514")
HAIKU_MODEL = os.getenv("CLAUDE_HAIKU_MODEL", "claude-haiku-4-5-20251001")
TRADING_MODE = os.getenv("TRADING_MODE", "paper")

WEBHOOK_RESEARCH = os.getenv("DISCORD_WEBHOOK_RESEARCH", "")
WEBHOOK_SYSTEM = os.getenv("DISCORD_WEBHOOK_SYSTEM", "")
WEBHOOK_PROPOSALS = os.getenv("DISCORD_WEBHOOK_PROPOSALS", "")

# AUTO_MODE: paper trading runs fully autonomous — no human approval gate
# Set AUTO_MODE=false in .env to revert to co-pilot (reaction-based approval)
AUTO_MODE = os.getenv("AUTO_MODE", "true").lower() == "true"

# Auto-proposal threshold: only propose trades when conviction >= this
AUTO_PROPOSE_THRESHOLD = int(os.getenv("AUTO_PROPOSE_THRESHOLD", "7"))

SYSTEM_STATE = {
    "halted": False,
    "trading_mode": TRADING_MODE,
    "auto_mode": AUTO_MODE,
    "last_brief": None,
    "errors_today": 0,
}


# ---------------------------------------------------------------------------
# Shared Claude helper — passed into agents for scoring
# ---------------------------------------------------------------------------
async def call_claude_for_agent(prompt: str, model: str = "haiku") -> str:
    """Thin wrapper so agents can call Claude without importing the full scheduler."""
    actual_model = HAIKU_MODEL if model == "haiku" else SONNET_MODEL
    return await call_claude(prompt, model=actual_model, max_tokens=1000)


# ---------------------------------------------------------------------------
# Discord webhook
# ---------------------------------------------------------------------------
async def post_to_discord(webhook_url: str, embeds: list[dict], content: str = ""):
    if not webhook_url:
        log.warning("No webhook URL, skipping post")
        return False
    payload = {}
    if content:
        payload["content"] = content
    if embeds:
        payload["embeds"] = embeds
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(webhook_url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status in (200, 204):
                    return True
                log.error(f"Webhook failed ({resp.status}): {(await resp.text())[:200]}")
                return False
    except Exception as e:
        log.error(f"Webhook error: {e}")
        return False


def make_embed(title, description, color=0x3498DB, fields=None, footer=None):
    embed = {"title": title, "description": description, "color": color,
             "timestamp": datetime.now(timezone.utc).isoformat()}
    if fields:
        embed["fields"] = fields
    if footer:
        embed["footer"] = {"text": footer}
    return embed


# ---------------------------------------------------------------------------
# Alpha Vantage
# ---------------------------------------------------------------------------
async def fetch_quote(symbol):
    if not ALPHA_VANTAGE_KEY:
        return {"symbol": symbol, "error": "No AV key"}
    url = f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol={symbol}&apikey={ALPHA_VANTAGE_KEY}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                data = await resp.json()
                quote = data.get("Global Quote", {})
                if not quote:
                    return {"symbol": symbol, "error": str(data.get("Note", data.get("Information", "No data")))[:100]}
                return {
                    "symbol": quote.get("01. symbol", symbol),
                    "price": float(quote.get("05. price", 0)),
                    "change": float(quote.get("09. change", 0)),
                    "change_pct": quote.get("10. change percent", "0%"),
                    "volume": int(quote.get("06. volume", 0)),
                    "high": float(quote.get("03. high", 0)),
                    "low": float(quote.get("04. low", 0)),
                }
    except Exception as e:
        return {"symbol": symbol, "error": str(e)}


async def fetch_all_quotes(symbols):
    quotes = []
    for s in symbols:
        quotes.append(await fetch_quote(s))
        await asyncio.sleep(1.5)
    return quotes


# ---------------------------------------------------------------------------
# Claude API
# ---------------------------------------------------------------------------
async def call_claude(prompt, system="", model=None, max_tokens=2000):
    if not ANTHROPIC_API_KEY:
        return '{"error": "no api key"}'
    model = model or SONNET_MODEL
    payload = {"model": model, "max_tokens": max_tokens,
               "messages": [{"role": "user", "content": prompt}]}
    if system:
        payload["system"] = system
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.anthropic.com/v1/messages",
                json=payload,
                headers={"Content-Type": "application/json", "x-api-key": ANTHROPIC_API_KEY,
                          "anthropic-version": "2023-06-01"},
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                data = await resp.json()
                if resp.status != 200:
                    return json.dumps({"error": str(data)})
                return "".join(b["text"] for b in data.get("content", []) if b.get("type") == "text")
    except Exception as e:
        return json.dumps({"error": str(e)})


# ---------------------------------------------------------------------------
# Guard check
# ---------------------------------------------------------------------------
async def check_guard(proposal):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{GUARD_URL}/check", json=proposal,
                                     timeout=aiohttp.ClientTimeout(total=5)) as resp:
                return await resp.json()
    except Exception as e:
        return {"result": "REJECT", "reason": f"Guard unreachable: {e}"}


# ---------------------------------------------------------------------------
# Watchlist
# ---------------------------------------------------------------------------
def load_watchlist():
    p = Path("/app/configs/watchlist.json")
    if p.exists():
        with open(p) as f:
            return json.load(f).get("symbols", [])
    return ["SPY", "QQQ", "NVDA"]


def load_strategies():
    p = Path("/app/configs/strategies.json")
    if p.exists():
        with open(p) as f:
            return json.load(f).get("strategies", {})
    return {}


# ---------------------------------------------------------------------------
# Morning Brief + Auto-Proposals
# ---------------------------------------------------------------------------
async def run_morning_brief():
    symbols = load_watchlist()
    log.info(f"=== Morning Brief: {', '.join(symbols)} ===")

    # Build morning context — enriches brief with all intelligence signals
    import sys
    sys.path.insert(0, "/app/services")
    morning_context = None
    try:
        from context_builder import build_context, build_context_embed
        morning_context = await build_context("SPY")
        log.info(f"Morning context: {morning_context['score']}/100 — {morning_context['decision_label']}")
    except Exception as ctx_err:
        log.warning(f"Morning context build failed: {ctx_err}")
    # Fetch market data
    quotes = await fetch_all_quotes(symbols[:10])
    market_data = []
    for q in quotes:
        if "error" not in q:
            market_data.append(f"{q['symbol']}: ${q['price']:.2f} ({q['change_pct']}) H={q['high']:.2f} L={q['low']:.2f} Vol={q['volume']:,}")
        else:
            market_data.append(f"{q['symbol']}: unavailable")
    market_summary = "\n".join(market_data)

    # Claude analysis
    log.info("Claude analysis...")
    system_prompt = (
        "You are a senior market research analyst for an options trading system. "
        "Analyze market data and produce a morning brief with trade recommendations. "
        "Output ONLY valid JSON array. No markdown, no code fences."
    )
    user_prompt = f"""Today: {datetime.now(timezone.utc).strftime('%A, %B %d, %Y')}. Mode: {TRADING_MODE}

Market data:
{market_summary}

For each symbol return:
{{"symbol":"SPY","price":570.25,"change_pct":"-0.3%","bias":"bullish|bearish|neutral","conviction":0-10,"thesis":"Under 40 words","key_risk":"One sentence","options_play":"Specific strategy with strikes/DTE or none","iv_estimate":"low|medium|high","proposed_trade":{{"strategy":"bull_put|iron_condor|covered_call|none","short_strike":0,"long_strike":0,"dte":30,"rationale":"Why this trade now"}}}}

Rules for proposed_trade:
- Only propose if conviction >= {AUTO_PROPOSE_THRESHOLD}
- Bull put: only when iv_estimate is medium or high
- Iron condor: only on indices (SPY/QQQ/IWM) when iv_estimate is high
- Set strategy to "none" if no good setup exists
- Be conservative. Missing a trade is better than a bad trade."""

    raw_result = await call_claude(user_prompt, system=system_prompt, max_tokens=3000)

    # Save
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M")
    brief_path = Path("/app/data/briefs")
    brief_path.mkdir(parents=True, exist_ok=True)
    with open(brief_path / f"brief_{TRADING_MODE}_{today_str}.json", "w") as f:
        f.write(raw_result)

    # Parse
    try:
        cleaned = raw_result.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0]
        brief_data = json.loads(cleaned)
    except json.JSONDecodeError:
        brief_data = None

    # Post brief to #research
    if brief_data and isinstance(brief_data, list):
        await post_brief_embeds(brief_data)

        # Post context score card to #research
        if morning_context:
            try:
                from context_builder import build_context_embed
                context_embed = build_context_embed(morning_context)
                await post_to_discord(WEBHOOK_RESEARCH, [context_embed])
            except Exception as ce:
                log.warning(f"Context embed post failed: {ce}")
        # Auto-propose high-conviction trades
        await auto_propose_trades(brief_data, quotes)
    else:
        await post_to_discord(WEBHOOK_RESEARCH, [
            make_embed("🔍 Morning Brief", raw_result[:2000], color=0x1ABC9C, footer=f"QuantAI · {TRADING_MODE}")
        ])

    SYSTEM_STATE["last_brief"] = datetime.now(timezone.utc).isoformat()
    log.info("Morning brief complete")
    return raw_result


async def post_brief_embeds(brief_data):
    header = make_embed(
        f"🔍 Morning Brief — {datetime.now(timezone.utc).strftime('%A, %b %d')}",
        f"**{TRADING_MODE.upper()}** mode | {len(brief_data)} symbols | Auto-propose threshold: {AUTO_PROPOSE_THRESHOLD}/10",
        color=0x1ABC9C, footer="QuantAI Research Agent",
    )
    embeds = []
    for item in brief_data[:10]:
        bias = item.get("bias", "neutral")
        conviction = item.get("conviction", 0)
        color = {"bullish": 0x2ECC71, "bearish": 0xE74C3C}.get(bias, 0x95A5A6)
        emoji = {"bullish": "🟢", "bearish": "🔴"}.get(bias, "⚪")
        bar = "█" * conviction + "░" * (10 - conviction)

        fields = [
            {"name": "Bias", "value": f"{emoji} {bias.title()}", "inline": True},
            {"name": "Conviction", "value": f"`{bar}` {conviction}/10", "inline": True},
            {"name": "IV", "value": item.get("iv_estimate", "?").title(), "inline": True},
        ]
        if item.get("options_play") and item["options_play"] != "none":
            fields.append({"name": "Options Play", "value": item["options_play"], "inline": False})
        if item.get("key_risk"):
            fields.append({"name": "Risk", "value": item["key_risk"], "inline": False})

        # Flag if auto-proposal will be generated
        trade = item.get("proposed_trade", {})
        if trade and trade.get("strategy") not in (None, "none", ""):
            fields.append({"name": "🤖 Auto-Proposal", "value": f"Trade card posting to #trade-proposals", "inline": False})

        price_str = f"**${item.get('price', 0):.2f}** ({item.get('change_pct', '')})\n" if item.get("price") else ""
        embeds.append(make_embed(
            item.get("symbol", "?"), f"{price_str}{item.get('thesis', '')}",
            color=color, fields=fields,
        ))

    await post_to_discord(WEBHOOK_RESEARCH, [header] + embeds[:9])
    if len(embeds) > 9:
        await post_to_discord(WEBHOOK_RESEARCH, embeds[9:])


# ---------------------------------------------------------------------------
# Auto-Propose: High conviction → guard check → post to #trade-proposals
# ---------------------------------------------------------------------------
async def auto_propose_trades(brief_data, quotes):
    """For each high-conviction signal, generate a trade card, guard-check it, post to #trade-proposals."""
    if not WEBHOOK_PROPOSALS:
        log.warning("No WEBHOOK_PROPOSALS configured, skipping auto-proposals")
        return

    proposals_posted = 0
    for item in brief_data:
        conviction = item.get("conviction", 0)
        trade = item.get("proposed_trade", {})

        if conviction < AUTO_PROPOSE_THRESHOLD:
            continue
        if not trade or trade.get("strategy") in (None, "none", ""):
            continue

        symbol = item.get("symbol", "?")
        strategy = trade.get("strategy", "unknown")
        short_strike = trade.get("short_strike", 0)
        long_strike = trade.get("long_strike", 0)
        dte = trade.get("dte", 30)
        rationale = trade.get("rationale", "")

        log.info(f"Auto-proposing: {strategy} on {symbol} (conviction {conviction}/10)")

        # Guard check
        guard_proposal = {
            "symbol": symbol,
            "strategy": strategy,
            "position_pct": 3.0,  # Conservative default
            "max_loss_pct": 1.5,
            "dte": dte,
            "iv_rank": 55 if item.get("iv_estimate") in ("medium", "high") else 30,
        }
        guard_result = await check_guard(guard_proposal)

        if guard_result.get("result") == "REJECT":
            log.info(f"Guard REJECTED {symbol} {strategy}: {guard_result.get('reason')}")
            await post_to_discord(WEBHOOK_PROPOSALS, [
                make_embed(
                    f"🛡️ Auto-Proposal REJECTED: {symbol}",
                    f"**{strategy}** — Guard: {guard_result.get('reason', '?')}\n"
                    f"Conviction: {conviction}/10 | Thesis: {item.get('thesis', '')}",
                    color=0xE74C3C,
                    footer="QuantAI Pipeline · Guard rejected",
                )
            ])
            continue

        # Build trade card embed
        price = item.get("price", 0)
        fields = [
            {"name": "Strategy", "value": strategy.replace("_", " ").title(), "inline": True},
            {"name": "Conviction", "value": f"{conviction}/10", "inline": True},
            {"name": "DTE", "value": str(dte), "inline": True},
            {"name": "Bias", "value": item.get("bias", "?").title(), "inline": True},
            {"name": "IV Estimate", "value": item.get("iv_estimate", "?").title(), "inline": True},
            {"name": "Guard", "value": "✅ APPROVED", "inline": True},
        ]

        if short_strike:
            fields.append({"name": "Short Strike", "value": f"${short_strike}", "inline": True})
        if long_strike:
            fields.append({"name": "Long Strike", "value": f"${long_strike}", "inline": True})

        fields.append({"name": "Rationale", "value": rationale[:200] if rationale else "See morning brief", "inline": False})
        fields.append({"name": "Key Risk", "value": item.get("key_risk", "See brief"), "inline": False})

        await post_to_discord(WEBHOOK_PROPOSALS, [
            make_embed(
                f"📊 Trade Proposal: {strategy.replace('_', ' ').title()} on {symbol}",
                f"**${price:.2f}** | Conviction {conviction}/10\n{item.get('thesis', '')}\n\n"
                f"⚠️ **Co-pilot mode**: Review this proposal and use `/bull_put` or `/iron_condor` "
                f"in Discord to analyze with real Greeks, then `/buy` to execute.",
                color=0xF1C40F,
                fields=fields,
                footer=f"QuantAI Pipeline · {TRADING_MODE} · Auto-proposed",
            )
        ])
        proposals_posted += 1

    if proposals_posted > 0:
        log.info(f"Posted {proposals_posted} auto-proposals to #trade-proposals")
    else:
        log.info("No symbols met auto-proposal threshold")


# ---------------------------------------------------------------------------
# EOD Scoring + Lesson Extraction
# ---------------------------------------------------------------------------
async def run_eod_scoring():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Check for trades in both old journal and memory
    journal_file = Path(f"/app/data/journal/trades_{today}.jsonl")
    memory_file = Path(f"/app/data/memory/{TRADING_MODE}/trade_journal.jsonl")

    trades = []
    for f in [journal_file, memory_file]:
        if f.exists():
            with open(f) as fh:
                for line in fh:
                    if line.strip():
                        try:
                            record = json.loads(line)
                            if record.get("timestamp", "").startswith(today):
                                trades.append(record)
                        except json.JSONDecodeError:
                            continue

    if not trades:
        log.info("No trades today")
        await post_to_discord(WEBHOOK_SYSTEM, [
            make_embed("📊 End of Day", f"No trades today ({TRADING_MODE}). System healthy.",
                        color=0x3498DB, footer="QuantAI EOD")
        ])
        return

    # Score with Claude
    result = await call_claude(
        f"Score these {len(trades)} trades from {today}:\n{json.dumps(trades[:20], indent=1)}",
        system=(
            "You are a trading performance analyst. Output ONLY valid JSON, no markdown:\n"
            '{"score":0-100,"summary":"Brief summary","winners":["list"],"losers":["list"],'
            '"patterns":["patterns observed"],"lessons":["actionable lessons"],'
            '"rule_suggestions":["suggested rule changes"]}'
        ),
        model=HAIKU_MODEL, max_tokens=1000,
    )

    # Save
    score_path = Path("/app/data/journal")
    score_path.mkdir(parents=True, exist_ok=True)
    with open(score_path / f"score_{TRADING_MODE}_{today}.json", "w") as f:
        f.write(result)

    # Parse and post
    try:
        cleaned = result.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0]
        score_data = json.loads(cleaned)
    except json.JSONDecodeError:
        score_data = None

    if score_data:
        score = score_data.get("score", 0)
        color = 0x2ECC71 if score >= 70 else (0xF39C12 if score >= 50 else 0xE74C3C)

        fields = [
            {"name": "Score", "value": f"**{score}/100**", "inline": True},
            {"name": "Trades", "value": str(len(trades)), "inline": True},
            {"name": "Mode", "value": TRADING_MODE, "inline": True},
        ]
        if score_data.get("patterns"):
            fields.append({"name": "Patterns", "value": "\n".join(f"• {p}" for p in score_data["patterns"][:3]), "inline": False})
        if score_data.get("lessons"):
            fields.append({"name": "Lessons", "value": "\n".join(f"• {l}" for l in score_data["lessons"][:3]), "inline": False})
        if score_data.get("rule_suggestions"):
            fields.append({"name": "Rule Suggestions", "value": "\n".join(f"• {r}" for r in score_data["rule_suggestions"][:2]), "inline": False})

        await post_to_discord(WEBHOOK_SYSTEM, [
            make_embed(
                f"📊 EOD Score: {score}/100",
                score_data.get("summary", "See details below"),
                color=color, fields=fields,
                footer=f"QuantAI EOD · {TRADING_MODE}",
            )
        ])

        # Save lessons to memory
        lessons_file = Path("/app/data/memory/shared/lessons.jsonl")
        lessons_file.parent.mkdir(parents=True, exist_ok=True)
        for lesson in score_data.get("lessons", []):
            record = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "lesson": lesson,
                "source": "auto_eod",
                "confidence": 0.7,
                "originated_in": TRADING_MODE,
            }
            with open(lessons_file, "a") as f:
                f.write(json.dumps(record) + "\n")

        if score < 90 and score_data.get("rule_suggestions"):
            log.warning(f"Score {score} < 90 — triggering self-improvement engine")
            from self_improve import run_daily_improvement
            try:
                await run_daily_improvement(score_data, today)
            except Exception as e:
                log.error(f"Self-improvement failed: {e}", exc_info=True)

    log.info(f"EOD scoring complete for {today}")


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
async def health_check():
    """Full health check using health_monitor service."""
    import sys
    sys.path.insert(0, "/app/services")
    try:
        from health_monitor import run_full_health_check, build_health_embeds, should_alert
        report = await run_full_health_check()

        # Only post to Discord if critical or first run of the hour
        now = datetime.now(timezone.utc)
        is_first_of_hour = now.minute < 5
        is_critical = report.get("overall") == "critical"

        if is_critical and should_alert(report):
            embeds = build_health_embeds(report)
            await post_to_discord(WEBHOOK_SYSTEM, embeds)
            log.warning(f"CRITICAL health alert posted to Discord: {report['issue_count']} issue(s)")
        elif is_first_of_hour and report.get("overall") != "healthy":
            embeds = build_health_embeds(report)
            await post_to_discord(WEBHOOK_SYSTEM, embeds)

        return report
    except Exception as e:
        log.error(f"Health check failed: {e}", exc_info=True)
        # Fallback to simple check
        checks = {}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{GUARD_URL}/health", timeout=aiohttp.ClientTimeout(total=3)) as resp:
                    checks["guard_engine"] = "healthy" if resp.status == 200 else "unhealthy"
        except Exception:
            checks["guard_engine"] = "unreachable"
        return {"overall": "unknown", "checks": checks, "error": str(e)}


# ---------------------------------------------------------------------------
# Scheduled wrappers
# ---------------------------------------------------------------------------
async def scheduled_morning_brief():
    log.info("=== Scheduled Morning Brief ===")
    try:
        await run_morning_brief()
    except Exception as e:
        log.error(f"Brief failed: {e}", exc_info=True)
        SYSTEM_STATE["errors_today"] += 1
        await post_to_discord(WEBHOOK_SYSTEM, [make_embed("❌ Brief Failed", str(e)[:500], color=0xE74C3C)])


async def scheduled_eod_scoring():
    log.info("=== Scheduled EOD ===")
    try:
        await run_eod_scoring()
    except Exception as e:
        log.error(f"EOD failed: {e}", exc_info=True)
        SYSTEM_STATE["errors_today"] += 1


async def scheduled_weekly_review():
    log.info("=== Scheduled Weekly Review ===")
    try:
        from self_improve import run_weekly_review
        await run_weekly_review()
    except Exception as e:
        log.error(f"Weekly review failed: {e}", exc_info=True)


async def scheduled_health_check():
    await health_check()


# ---------------------------------------------------------------------------
# Agent 1 — SPY/QQQ 0DTE Iron Condor (fully autonomous)
# ---------------------------------------------------------------------------
async def scheduled_agent1_entry1():
    """9:50 AM ET — Agent 1 first iron condor entry."""
    if SYSTEM_STATE.get("halted"):
        log.info("System halted — skipping Agent 1 entry 1")
        return
    log.info("=== Agent 1: Entry 1 (9:50 AM) ===")
    try:
        import sys
        sys.path.insert(0, "/app/services")
        sys.path.insert(0, "/app/services")
        from context_builder import build_context
        from agent1_iron_condor import run_entry

        context = await build_context("SPY")
        log.info(f"Agent 1 Entry 1 context: {context['score']}/100 — {context['decision_label']}")

        if context.get("decision") == "skip" or context.get("hard_skip"):
            reason = context.get("hard_skip_reason") or context.get("summary", "Context score too low")[:200]
            log.info(f"Agent 1 Entry 1 SKIPPED: {reason}")
            await post_to_discord(WEBHOOK_PROPOSALS, [make_embed(
                "⏸️ Agent 1: Entry 1 Skipped",
                f"Context: **{context['score']}/100 — {context['decision_label']}**\n{reason}",
                color=0xF39C12, footer="QuantAI Context Engine"
            )])
            return

        await run_entry(entry_number=1, context=context)
    except Exception as e:
        log.error(f"Agent 1 entry 1 failed: {e}", exc_info=True)
        SYSTEM_STATE["errors_today"] += 1
        await post_to_discord(WEBHOOK_SYSTEM, [make_embed(
            "\u274c Agent 1 Entry 1 Failed", str(e)[:500], color=0xE74C3C,
            footer="QuantAI Agent 1"
        )])


async def scheduled_agent1_entry2():
    """11:30 AM ET — Agent 1 second entry (conditional)."""
    if SYSTEM_STATE.get("halted"):
        return
    log.info("=== Agent 1: Entry 2 (11:30 AM) ===")
    try:
        import sys
        sys.path.insert(0, "/app/services")
        sys.path.insert(0, "/app/services")
        from context_builder import build_context
        from agent1_iron_condor import run_entry

        context = await build_context("SPY")
        if context.get("decision") == "skip" or context.get("hard_skip"):
            log.info(f"Agent 1 Entry 2 SKIPPED: {context.get('decision_label')}")
            return

        await run_entry(entry_number=2, context=context)
    except Exception as e:
        log.error(f"Agent 1 entry 2 failed: {e}", exc_info=True)


async def scheduled_agent1_monitor():
    """Every 5 min during market hours — monitor Agent 1 positions."""
    if SYSTEM_STATE.get("halted"):
        return
    try:
        import sys
        sys.path.insert(0, "/app/services")
        from agent1_iron_condor import monitor_positions
        await monitor_positions()
    except Exception as e:
        log.error(f"Agent 1 monitor failed: {e}", exc_info=True)


async def scheduled_agent1_eod():
    """4:30 PM ET — Agent 1 EOD scoring."""
    log.info("=== Agent 1: EOD Score ===")
    try:
        import sys
        sys.path.insert(0, "/app/services")
        from agent1_iron_condor import run_eod_score
        score_data = await run_eod_score(call_claude_for_agent)
        if score_data and score_data.get("score"):
            score = score_data["score"]
            color = 0x2ECC71 if score >= 70 else (0xF39C12 if score >= 50 else 0xE74C3C)
            fields = []
            if score_data.get("lessons"):
                fields.append({
                    "name": "Lessons",
                    "value": "\n".join(f"\u2022 {l}" for l in score_data["lessons"][:3]),
                    "inline": False
                })
            if score_data.get("param_suggestions"):
                fields.append({
                    "name": "Param Suggestions",
                    "value": "\n".join(
                        f"\u2022 `{s['param']}`: {s['current']} \u2192 {s['suggested']} ({s['reason']})"
                        for s in score_data["param_suggestions"][:2]
                    ),
                    "inline": False
                })
            await post_to_discord(WEBHOOK_SYSTEM, [make_embed(
                f"\U0001f916 Agent 1 EOD: {score}/100",
                score_data.get("summary", ""),
                color=color, fields=fields,
                footer="QuantAI Agent 1 \u00b7 Iron Condor"
            )])
    except Exception as e:
        log.error(f"Agent 1 EOD score failed: {e}", exc_info=True)


# ---------------------------------------------------------------------------
# Agent 2 — Covered Call Bot (Monday weekly scan)
# ---------------------------------------------------------------------------
async def scheduled_agent2_weekly():
    """Monday 10:00 AM ET — Agent 2 weekly covered call scan."""
    if SYSTEM_STATE.get("halted"):
        return
    log.info("=== Agent 2: Weekly Covered Call Scan ===")
    try:
        import sys
        sys.path.insert(0, "/app/services")
        sys.path.insert(0, "/app/services")
        from context_builder import build_context
        from agent2_covered_call import run_weekly_scan
        from flow_detector import scan_watchlist_flow
        import concurrent.futures

        context = await build_context("SPY")
        log.info(f"Agent 2 weekly context: {context['score']}/100 — {context['decision_label']}")

        if context.get("decision") == "skip" or context.get("hard_skip"):
            reason = context.get("hard_skip_reason") or "Context score too low"
            log.info(f"Agent 2 weekly SKIPPED: {reason}")
            await post_to_discord(WEBHOOK_SYSTEM, [make_embed(
                "⏸️ Agent 2: Weekly Scan Skipped",
                f"Context: **{context['score']}/100**\n{reason}",
                color=0xF39C12, footer="QuantAI Context Engine"
            )])
            return

        watchlist = ["PLTR", "TSM", "MU", "AMD", "AVGO", "ASML"]
        loop = asyncio.get_event_loop()
        with concurrent.futures.ThreadPoolExecutor() as pool:
            flow_scan = await loop.run_in_executor(pool, scan_watchlist_flow, watchlist)
        flagged = flow_scan.get("flagged_symbols", [])
        if flagged:
            log.warning(f"Dark pool signals on: {flagged}")
            await post_to_discord(WEBHOOK_SYSTEM, [make_embed(
                "⚠️ Agent 2: Dark Pool Signals",
                f"Unusual volume detected: **{', '.join(flagged)}**\nThese symbols will be skipped.",
                color=0xF39C12, footer="QuantAI Flow Detector"
            )])

        new_trades = await run_weekly_scan(context=context, skip_symbols=flagged)
        if new_trades:
            await post_to_discord(WEBHOOK_SYSTEM, [make_embed(
                f"\U0001f7e3 Agent 2: {len(new_trades)} Covered Call(s) Entered",
                "\n".join(
                    f"**{t['symbol']}** ${t['strike']:.0f}C | ${t['premium']:.2f} premium | "
                    f"{t['dte']} DTE | {t['monthly_yield_pct']:.2f}% yield"
                    for t in new_trades
                ),
                color=0x9B59B6, footer="QuantAI Agent 2 \u00b7 Covered Call"
            )])
    except Exception as e:
        log.error(f"Agent 2 weekly scan failed: {e}", exc_info=True)
        SYSTEM_STATE["errors_today"] += 1


async def scheduled_agent2_friday_score():
    """Friday 4:45 PM ET — Agent 2 weekly scoring."""
    log.info("=== Agent 2: Weekly Score ===")
    try:
        import sys
        sys.path.insert(0, "/app/services")
        from agent2_covered_call import run_weekly_score
        score_data = await run_weekly_score(call_claude_for_agent)
        if score_data and score_data.get("score"):
            score = score_data["score"]
            color = 0x9B59B6 if score >= 70 else (0xF39C12 if score >= 50 else 0xE74C3C)
            await post_to_discord(WEBHOOK_SYSTEM, [make_embed(
                f"\U0001f7e3 Agent 2 Weekly Score: {score}/100",
                score_data.get("summary", ""),
                color=color,
                fields=[
                    {"name": "Best Ticker", "value": score_data.get("best_ticker", "?"), "inline": True},
                    {"name": "Kill Signal", "value": str(score_data.get("kill_signal", False)), "inline": True},
                ],
                footer="QuantAI Agent 2 \u00b7 Covered Call"
            )])
    except Exception as e:
        log.error(f"Agent 2 Friday score failed: {e}", exc_info=True)



# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def main():
    log.info("Orchestrator starting...")
    log.info(f"  Mode: {TRADING_MODE}")
    log.info(f"  Research webhook: {'✅' if WEBHOOK_RESEARCH else '❌'}")
    log.info(f"  Proposals webhook: {'✅' if WEBHOOK_PROPOSALS else '❌'}")
    log.info(f"  System webhook: {'✅' if WEBHOOK_SYSTEM else '❌'}")
    log.info(f"  Alpha Vantage: {'✅' if ALPHA_VANTAGE_KEY else '❌'}")
    log.info(f"  Anthropic: {'✅' if ANTHROPIC_API_KEY else '❌'}")
    log.info(f"  Auto-propose threshold: {AUTO_PROPOSE_THRESHOLD}/10")
    log.info(f"  Auto-mode: {'AUTONOMOUS' if AUTO_MODE else 'Co-pilot (approval required)'}")

    scheduler = AsyncIOScheduler(timezone="US/Eastern")

    scheduler.add_job(scheduled_morning_brief,
        CronTrigger(hour=6, minute=30, day_of_week="mon-fri"),
        id="morning_brief", name="Morning Brief + Auto-Proposals")

    scheduler.add_job(scheduled_eod_scoring,
        CronTrigger(hour=16, minute=30, day_of_week="mon-fri"),
        id="eod_scoring", name="EOD Scoring + Lessons + Self-Improve")

    scheduler.add_job(scheduled_weekly_review,
        CronTrigger(hour=16, minute=45, day_of_week="fri"),
        id="weekly_review", name="Weekly Review (Friday)")

    scheduler.add_job(scheduled_health_check,
        CronTrigger(minute="*/5", hour="9-16", day_of_week="mon-fri"),
        id="health_check", name="Health Check")

    # --- Agent 1: SPY/QQQ 0DTE Iron Condor ---
    scheduler.add_job(scheduled_agent1_entry1,
        CronTrigger(hour=9, minute=50, day_of_week="mon-fri"),
        id="agent1_entry1", name="Agent 1: Iron Condor Entry 1")

    scheduler.add_job(scheduled_agent1_entry2,
        CronTrigger(hour=11, minute=30, day_of_week="mon-fri"),
        id="agent1_entry2", name="Agent 1: Iron Condor Entry 2")

    scheduler.add_job(scheduled_agent1_monitor,
        CronTrigger(minute="*/5", hour="9-16", day_of_week="mon-fri"),
        id="agent1_monitor", name="Agent 1: Position Monitor")

    scheduler.add_job(scheduled_agent1_eod,
        CronTrigger(hour=16, minute=30, day_of_week="mon-fri"),
        id="agent1_eod", name="Agent 1: EOD Score")

    # --- Agent 2: Covered Call ---
    scheduler.add_job(scheduled_agent2_weekly,
        CronTrigger(hour=10, minute=0, day_of_week="mon"),
        id="agent2_weekly", name="Agent 2: Weekly CC Scan")

    scheduler.add_job(scheduled_agent2_friday_score,
        CronTrigger(hour=16, minute=45, day_of_week="fri"),
        id="agent2_friday", name="Agent 2: Weekly Score (Friday)")

    scheduler.start()
    log.info(f"Scheduler: {len(scheduler.get_jobs())} jobs")
    for job in scheduler.get_jobs():
        log.info(f"  → {job.name}: {job.trigger}")

    # Run startup health check and post to Discord
    log.info("Running startup health check...")
    try:
        import sys
        sys.path.insert(0, "/app/services")
        from health_monitor import run_full_health_check, build_startup_embed
        startup_report = await run_full_health_check()
        startup_embed = build_startup_embed(startup_report)
        await post_to_discord(WEBHOOK_SYSTEM, [startup_embed])
        log.info(f"Startup health: {startup_report['overall']} ({startup_report['issue_count']} issues)")
    except Exception as e:
        log.error(f"Startup health check failed: {e}")

    if os.getenv("RUN_BRIEF_NOW", "").lower() == "true":
        log.info("RUN_BRIEF_NOW — running immediate brief with auto-proposals...")
        await run_morning_brief()

    try:
        while True:
            await asyncio.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
