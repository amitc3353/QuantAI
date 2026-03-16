"""
Orchestrator — Cron Scheduler + Agent Router
==============================================
Schedules automated tasks:
  - 6:30 AM ET: Morning research brief
  - 4:30 PM ET: End-of-day journal + scoring
  - Every 5 min (market hours): Portfolio health check
  - Every 1 min: Heartbeat

Routes signals between agents:
  Research → Analysis → Guard → Execution
"""

import os
import json
import logging
import asyncio
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import aiohttp
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO")),
    format="%(asctime)s [orchestrator] %(levelname)s: %(message)s",
)
log = logging.getLogger("orchestrator")

# ---------------------------------------------------------------------------
# Service URLs (Docker internal network)
# ---------------------------------------------------------------------------
GUARD_URL = os.getenv("GUARD_URL", "http://trader-guards:8100")
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK_URL", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
SONNET_MODEL = os.getenv("CLAUDE_SONNET_MODEL", "claude-sonnet-4-20250514")
HAIKU_MODEL = os.getenv("CLAUDE_HAIKU_MODEL", "claude-haiku-4-5-20251001")

# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------
SYSTEM_STATE = {
    "halted": False,
    "trading_mode": os.getenv("TRADING_MODE", "paper"),
    "last_brief": None,
    "last_health_check": None,
    "errors_today": 0,
}


# ---------------------------------------------------------------------------
# Claude API helper — token-optimized
# ---------------------------------------------------------------------------
async def call_claude(
    prompt: str,
    system: str = "",
    model: str = None,
    max_tokens: int = 2000,
) -> str:
    """Call Claude API with token optimization."""
    if not ANTHROPIC_API_KEY:
        log.warning("No ANTHROPIC_API_KEY set, skipping Claude call")
        return '{"error": "no api key"}'

    model = model or SONNET_MODEL
    messages = [{"role": "user", "content": prompt}]

    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    if system:
        payload["system"] = system

    headers = {
        "Content-Type": "application/json",
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.anthropic.com/v1/messages",
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                data = await resp.json()
                if resp.status != 200:
                    log.error(f"Claude API error {resp.status}: {data}")
                    return json.dumps({"error": str(data)})
                # Extract text from response
                text_parts = [
                    block["text"]
                    for block in data.get("content", [])
                    if block.get("type") == "text"
                ]
                return "\n".join(text_parts)
    except Exception as e:
        log.error(f"Claude API call failed: {e}")
        return json.dumps({"error": str(e)})


# ---------------------------------------------------------------------------
# Guard Engine client
# ---------------------------------------------------------------------------
async def check_guard(trade_proposal: dict, portfolio: dict = None) -> dict:
    """Submit a trade proposal to the guard engine."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{GUARD_URL}/check",
                json=trade_proposal,
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                return await resp.json()
    except Exception as e:
        log.error(f"Guard engine unreachable: {e}")
        return {"result": "REJECT", "reason": f"Guard engine unreachable: {e}"}


async def halt_guards():
    """Emergency halt via guard engine."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{GUARD_URL}/halt") as resp:
                return await resp.json()
    except Exception as e:
        log.error(f"Failed to halt guard engine: {e}")


async def resume_guards():
    """Resume trading via guard engine."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{GUARD_URL}/resume") as resp:
                return await resp.json()
    except Exception as e:
        log.error(f"Failed to resume guard engine: {e}")


# ---------------------------------------------------------------------------
# Trade journal — JSONL append-only log
# ---------------------------------------------------------------------------
def log_trade(trade_data: dict):
    """Append a trade record to the JSONL journal."""
    journal_path = Path("/app/data/journal")
    journal_path.mkdir(parents=True, exist_ok=True)

    today = datetime.utcnow().strftime("%Y-%m-%d")
    filepath = journal_path / f"trades_{today}.jsonl"

    record = {
        "timestamp": datetime.utcnow().isoformat(),
        "hash": hashlib.sha256(json.dumps(trade_data, sort_keys=True).encode()).hexdigest()[:8],
        **trade_data,
    }

    with open(filepath, "a") as f:
        f.write(json.dumps(record) + "\n")

    log.info(f"Trade logged: {record['hash']} — {trade_data.get('symbol', '?')}")
    return record


# ---------------------------------------------------------------------------
# Agent pipeline: Research → Analysis → Guard → Execution
# ---------------------------------------------------------------------------
async def run_research_brief(symbols: list[str]) -> str:
    """
    Phase 2: Run the morning research brief.
    Token-optimized: sends compressed watchlist, gets structured JSON back.
    """
    watchlist_str = ", ".join(symbols)

    system_prompt = (
        "You are a senior market research analyst. Provide a concise, structured morning brief. "
        "Output ONLY valid JSON. No markdown, no preamble. Keep each thesis under 50 words. "
        "Optimize for signal-to-noise ratio."
    )

    user_prompt = f"""Morning brief for: {watchlist_str}
For each symbol provide:
{{"symbol": str, "bias": "bullish"|"bearish"|"neutral", "conviction": 0-10, "thesis": str, "key_risk": str, "iv_rank_estimate": "low"|"medium"|"high"}}
Return as JSON array. Today is {datetime.utcnow().strftime('%Y-%m-%d')}."""

    result = await call_claude(user_prompt, system=system_prompt, max_tokens=1500)

    # Save brief
    brief_path = Path("/app/data/briefs")
    brief_path.mkdir(parents=True, exist_ok=True)
    today = datetime.utcnow().strftime("%Y-%m-%d_%H%M")
    with open(brief_path / f"brief_{today}.json", "w") as f:
        f.write(result)

    SYSTEM_STATE["last_brief"] = datetime.utcnow().isoformat()
    log.info(f"Morning brief generated for {len(symbols)} symbols")
    return result


async def run_analysis(symbol: str, strategy: str, context: str = "") -> str:
    """
    Phase 2: Run options analysis on a specific symbol/strategy.
    Returns a structured trade card.
    """
    system_prompt = (
        "You are an options specialist. Analyze the proposed trade and return a structured trade card. "
        "Output ONLY valid JSON. Include: symbol, strategy, direction, strikes, expiry, premium, "
        "max_profit, max_loss, breakeven, pop (probability of profit), greeks (delta, gamma, theta, vega), "
        "thesis, and risk_notes. Be conservative on probability estimates."
    )

    user_prompt = f"""Analyze: {strategy} on {symbol}
Context: {context if context else 'Standard analysis requested'}
Return trade card as JSON."""

    return await call_claude(user_prompt, system=system_prompt, max_tokens=1200)


async def run_eod_journal():
    """
    End-of-day: Score today's trades and generate journal entry.
    Uses Haiku for cost efficiency.
    """
    today = datetime.utcnow().strftime("%Y-%m-%d")
    journal_file = Path(f"/app/data/journal/trades_{today}.jsonl")

    if not journal_file.exists():
        log.info("No trades today — skipping EOD journal")
        return

    trades = []
    with open(journal_file) as f:
        for line in f:
            if line.strip():
                trades.append(json.loads(line))

    if not trades:
        return

    system_prompt = (
        "You are a trading performance analyst. Score today's trades and identify patterns. "
        "Output JSON with: score (0-100), summary, winners, losers, patterns, rule_suggestions."
    )

    user_prompt = f"Score these trades from {today}:\n{json.dumps(trades, indent=1)}"

    result = await call_claude(
        user_prompt,
        system=system_prompt,
        model=HAIKU_MODEL,  # Haiku for cost efficiency
        max_tokens=800,
    )

    # Save scoring result
    score_path = Path("/app/data/journal")
    with open(score_path / f"score_{today}.json", "w") as f:
        f.write(result)

    log.info(f"EOD scoring complete for {today}")
    return result


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
async def health_check():
    """Periodic health check — verify all services are responsive."""
    checks = {}

    # Guard engine
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{GUARD_URL}/health", timeout=aiohttp.ClientTimeout(total=3)
            ) as resp:
                checks["guard_engine"] = "healthy" if resp.status == 200 else "unhealthy"
    except Exception:
        checks["guard_engine"] = "unreachable"

    checks["orchestrator"] = "healthy"
    checks["trading_mode"] = SYSTEM_STATE["trading_mode"]
    checks["halted"] = SYSTEM_STATE["halted"]
    checks["errors_today"] = SYSTEM_STATE["errors_today"]
    checks["timestamp"] = datetime.utcnow().isoformat()

    SYSTEM_STATE["last_health_check"] = checks
    log.debug(f"Health check: {checks}")
    return checks


# ---------------------------------------------------------------------------
# Scheduled tasks
# ---------------------------------------------------------------------------
def load_watchlist() -> list[str]:
    """Load watchlist from config."""
    config_path = Path("/app/configs/watchlist.json")
    if config_path.exists():
        with open(config_path) as f:
            return json.load(f).get("symbols", [])
    return ["SPY", "QQQ", "NVDA"]


async def scheduled_morning_brief():
    """6:30 AM ET — automated morning brief."""
    log.info("=== Morning Brief Starting ===")
    symbols = load_watchlist()
    try:
        await run_research_brief(symbols)
    except Exception as e:
        log.error(f"Morning brief failed: {e}")
        SYSTEM_STATE["errors_today"] += 1


async def scheduled_eod_scoring():
    """4:30 PM ET — automated end-of-day scoring."""
    log.info("=== EOD Scoring Starting ===")
    try:
        await run_eod_journal()
    except Exception as e:
        log.error(f"EOD scoring failed: {e}")
        SYSTEM_STATE["errors_today"] += 1


async def scheduled_health_check():
    """Every 5 minutes during market hours."""
    await health_check()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def main():
    log.info("Orchestrator starting...")
    log.info(f"  Trading mode: {SYSTEM_STATE['trading_mode']}")
    log.info(f"  Guard URL: {GUARD_URL}")

    scheduler = AsyncIOScheduler(timezone="US/Eastern")

    # Morning brief: 6:30 AM ET, Mon-Fri
    scheduler.add_job(
        scheduled_morning_brief,
        CronTrigger(hour=6, minute=30, day_of_week="mon-fri"),
        id="morning_brief",
        name="Morning Research Brief",
    )

    # EOD scoring: 4:30 PM ET, Mon-Fri
    scheduler.add_job(
        scheduled_eod_scoring,
        CronTrigger(hour=16, minute=30, day_of_week="mon-fri"),
        id="eod_scoring",
        name="EOD Scoring",
    )

    # Health check: every 5 min during market hours (9:30-16:00 ET)
    scheduler.add_job(
        scheduled_health_check,
        CronTrigger(minute="*/5", hour="9-16", day_of_week="mon-fri"),
        id="health_check",
        name="Health Check",
    )

    scheduler.start()
    log.info(f"Scheduler started with {len(scheduler.get_jobs())} jobs")

    # Keep alive
    try:
        while True:
            await asyncio.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()
        log.info("Orchestrator shut down")


if __name__ == "__main__":
    asyncio.run(main())
