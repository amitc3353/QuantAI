"""
Orchestrator — Cron Scheduler + Agent Router
==============================================
Schedules automated tasks:
  - 6:30 AM ET: Morning research brief → posts to #research via webhook
  - 4:30 PM ET: End-of-day journal + scoring
  - Every 5 min (market hours): Portfolio health check
"""

import os
import json
import logging
import asyncio
import hashlib
from datetime import datetime, timezone, timedelta
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
# Config
# ---------------------------------------------------------------------------
GUARD_URL = os.getenv("GUARD_URL", "http://trader-guards:8100")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ALPHA_VANTAGE_KEY = os.getenv("ALPHA_VANTAGE_API_KEY", "")
SONNET_MODEL = os.getenv("CLAUDE_SONNET_MODEL", "claude-sonnet-4-20250514")
HAIKU_MODEL = os.getenv("CLAUDE_HAIKU_MODEL", "claude-haiku-4-5-20251001")
TRADING_MODE = os.getenv("TRADING_MODE", "paper")

# Discord webhook URLs — create in Discord channel settings → Integrations → Webhooks
WEBHOOK_RESEARCH = os.getenv("DISCORD_WEBHOOK_RESEARCH", "")
WEBHOOK_SYSTEM = os.getenv("DISCORD_WEBHOOK_SYSTEM", "")

SYSTEM_STATE = {
    "halted": False,
    "trading_mode": TRADING_MODE,
    "last_brief": None,
    "last_health_check": None,
    "errors_today": 0,
}


# ---------------------------------------------------------------------------
# Discord Webhook — post embeds to any channel
# ---------------------------------------------------------------------------
async def post_to_discord(webhook_url: str, embeds: list[dict], content: str = ""):
    if not webhook_url:
        log.warning("No webhook URL configured, skipping Discord post")
        return False
    payload = {}
    if content:
        payload["content"] = content
    if embeds:
        payload["embeds"] = embeds
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                webhook_url, json=payload, timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status in (200, 204):
                    log.info(f"Posted to Discord ({resp.status})")
                    return True
                else:
                    body = await resp.text()
                    log.error(f"Webhook failed ({resp.status}): {body[:200]}")
                    return False
    except Exception as e:
        log.error(f"Webhook error: {e}")
        return False


def make_embed(title: str, description: str, color: int = 0x3498DB,
               fields: list[dict] = None, footer: str = None) -> dict:
    embed = {
        "title": title,
        "description": description,
        "color": color,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if fields:
        embed["fields"] = fields
    if footer:
        embed["footer"] = {"text": footer}
    return embed


# ---------------------------------------------------------------------------
# Alpha Vantage — free market data
# ---------------------------------------------------------------------------
async def fetch_quote(symbol: str) -> dict:
    if not ALPHA_VANTAGE_KEY:
        return {"symbol": symbol, "error": "No Alpha Vantage key"}
    url = (
        f"https://www.alphavantage.co/query"
        f"?function=GLOBAL_QUOTE&symbol={symbol}&apikey={ALPHA_VANTAGE_KEY}"
    )
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                data = await resp.json()
                quote = data.get("Global Quote", {})
                if not quote:
                    note = data.get("Note", data.get("Information", "No data"))
                    return {"symbol": symbol, "error": str(note)[:100]}
                return {
                    "symbol": quote.get("01. symbol", symbol),
                    "price": float(quote.get("05. price", 0)),
                    "change": float(quote.get("09. change", 0)),
                    "change_pct": quote.get("10. change percent", "0%"),
                    "volume": int(quote.get("06. volume", 0)),
                    "prev_close": float(quote.get("08. previous close", 0)),
                    "high": float(quote.get("03. high", 0)),
                    "low": float(quote.get("04. low", 0)),
                }
    except Exception as e:
        log.error(f"Alpha Vantage error for {symbol}: {e}")
        return {"symbol": symbol, "error": str(e)}


async def fetch_all_quotes(symbols: list[str]) -> list[dict]:
    quotes = []
    for symbol in symbols:
        quote = await fetch_quote(symbol)
        quotes.append(quote)
        await asyncio.sleep(1.5)  # AV free tier: ~5 calls/min
    return quotes


# ---------------------------------------------------------------------------
# Claude API
# ---------------------------------------------------------------------------
async def call_claude(prompt: str, system: str = "", model: str = None,
                      max_tokens: int = 2000) -> str:
    if not ANTHROPIC_API_KEY:
        log.warning("No ANTHROPIC_API_KEY")
        return '{"error": "no api key"}'
    model = model or SONNET_MODEL
    headers = {
        "Content-Type": "application/json",
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
    }
    payload = {
        "model": model, "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        payload["system"] = system
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.anthropic.com/v1/messages",
                json=payload, headers=headers,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                data = await resp.json()
                if resp.status != 200:
                    log.error(f"Claude API error {resp.status}: {data}")
                    return json.dumps({"error": str(data)})
                return "".join(
                    b["text"] for b in data.get("content", []) if b.get("type") == "text"
                )
    except Exception as e:
        log.error(f"Claude API failed: {e}")
        return json.dumps({"error": str(e)})


# ---------------------------------------------------------------------------
# Morning Brief — the main automation
# ---------------------------------------------------------------------------
def load_watchlist() -> list[str]:
    config_path = Path("/app/configs/watchlist.json")
    if config_path.exists():
        with open(config_path) as f:
            return json.load(f).get("symbols", [])
    return ["SPY", "QQQ", "NVDA"]


async def run_morning_brief():
    symbols = load_watchlist()
    log.info(f"=== Morning Brief: {', '.join(symbols)} ===")

    # Step 1: Fetch real market data
    log.info("Fetching market data...")
    quotes = await fetch_all_quotes(symbols[:10])

    market_data = []
    for q in quotes:
        if "error" not in q:
            market_data.append(
                f"{q['symbol']}: ${q['price']:.2f} ({q['change_pct']}) "
                f"H={q['high']:.2f} L={q['low']:.2f} Vol={q['volume']:,}"
            )
        else:
            market_data.append(f"{q['symbol']}: unavailable — {q.get('error', '?')}")
    market_summary = "\n".join(market_data)

    # Step 2: Claude analysis
    log.info("Running Claude analysis...")
    system_prompt = (
        "You are a senior market research analyst for an options trading system. "
        "Analyze the provided market data and produce a concise morning brief. "
        "Output ONLY valid JSON array. No markdown, no preamble, no code fences."
    )
    user_prompt = f"""Today: {datetime.now(timezone.utc).strftime('%A, %B %d, %Y')}. Mode: {TRADING_MODE}

Market data:
{market_summary}

Return JSON array. Each item:
{{"symbol":"SPY","price":570.25,"change_pct":"-0.3%","bias":"bullish|bearish|neutral","conviction":0-10,"thesis":"Under 40 words","key_risk":"One sentence","options_play":"Strategy or none","iv_estimate":"low|medium|high"}}

Only analyze symbols with data. Be specific about levels."""

    raw_result = await call_claude(user_prompt, system=system_prompt, max_tokens=2000)

    # Step 3: Save
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M")
    brief_path = Path("/app/data/briefs")
    brief_path.mkdir(parents=True, exist_ok=True)
    with open(brief_path / f"brief_{TRADING_MODE}_{today_str}.json", "w") as f:
        f.write(raw_result)

    # Step 4: Parse and post to Discord
    try:
        cleaned = raw_result.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0]
        brief_data = json.loads(cleaned)
    except json.JSONDecodeError:
        brief_data = None

    if brief_data and isinstance(brief_data, list):
        await post_brief_embeds(brief_data)
    else:
        await post_to_discord(WEBHOOK_RESEARCH, [
            make_embed("🔍 Morning Brief", raw_result[:2000], color=0x1ABC9C,
                        footer=f"QuantAI · {TRADING_MODE}")
        ])

    SYSTEM_STATE["last_brief"] = datetime.now(timezone.utc).isoformat()
    log.info("Morning brief posted to Discord")
    return raw_result


async def post_brief_embeds(brief_data: list[dict]):
    header = make_embed(
        f"🔍 Morning Brief — {datetime.now(timezone.utc).strftime('%A, %b %d')}",
        f"**{TRADING_MODE.upper()}** mode | {len(brief_data)} symbols analyzed",
        color=0x1ABC9C, footer="QuantAI Research Agent",
    )
    symbol_embeds = []
    for item in brief_data[:10]:
        bias = item.get("bias", "neutral")
        conviction = item.get("conviction", 0)
        if bias == "bullish":
            color, emoji = 0x2ECC71, "🟢"
        elif bias == "bearish":
            color, emoji = 0xE74C3C, "🔴"
        else:
            color, emoji = 0x95A5A6, "⚪"
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
        price_str = ""
        if item.get("price"):
            price_str = f"**${item['price']:.2f}** ({item.get('change_pct', '')})\n"
        symbol_embeds.append(make_embed(
            item.get("symbol", "?"), f"{price_str}{item.get('thesis', 'No thesis')}",
            color=color, fields=fields,
        ))
    await post_to_discord(WEBHOOK_RESEARCH, [header] + symbol_embeds[:9])
    if len(symbol_embeds) > 9:
        await post_to_discord(WEBHOOK_RESEARCH, symbol_embeds[9:])


# ---------------------------------------------------------------------------
# EOD Scoring
# ---------------------------------------------------------------------------
async def run_eod_scoring():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    journal_file = Path(f"/app/data/journal/trades_{today}.jsonl")
    if not journal_file.exists():
        log.info("No trades today")
        await post_to_discord(WEBHOOK_SYSTEM, [
            make_embed("📊 End of Day", f"No trades today ({TRADING_MODE}). System healthy.",
                        color=0x3498DB, footer="QuantAI EOD")
        ])
        return
    trades = []
    with open(journal_file) as f:
        for line in f:
            if line.strip():
                trades.append(json.loads(line))
    if not trades:
        return
    result = await call_claude(
        f"Score trades from {today}:\n{json.dumps(trades, indent=1)}",
        system="Output JSON: {score:0-100, summary:str, patterns:[str], suggestions:[str]}",
        model=HAIKU_MODEL, max_tokens=800,
    )
    with open(Path("/app/data/journal") / f"score_{TRADING_MODE}_{today}.json", "w") as f:
        f.write(result)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
async def health_check():
    checks = {}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{GUARD_URL}/health", timeout=aiohttp.ClientTimeout(total=3)) as resp:
                checks["guard_engine"] = "healthy" if resp.status == 200 else "unhealthy"
    except Exception:
        checks["guard_engine"] = "unreachable"
    checks.update({
        "orchestrator": "healthy", "trading_mode": TRADING_MODE,
        "halted": SYSTEM_STATE["halted"], "errors": SYSTEM_STATE["errors_today"],
    })
    SYSTEM_STATE["last_health_check"] = checks
    return checks


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
        await post_to_discord(WEBHOOK_SYSTEM, [
            make_embed("❌ Morning Brief Failed", str(e)[:500], color=0xE74C3C)
        ])

async def scheduled_eod_scoring():
    log.info("=== Scheduled EOD ===")
    try:
        await run_eod_scoring()
    except Exception as e:
        log.error(f"EOD failed: {e}", exc_info=True)
        SYSTEM_STATE["errors_today"] += 1

async def scheduled_health_check():
    await health_check()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def main():
    log.info("Orchestrator starting...")
    log.info(f"  Mode: {TRADING_MODE}")
    log.info(f"  Research webhook: {'✅' if WEBHOOK_RESEARCH else '❌ NOT SET'}")
    log.info(f"  Alpha Vantage: {'✅' if ALPHA_VANTAGE_KEY else '❌ NOT SET'}")
    log.info(f"  Anthropic: {'✅' if ANTHROPIC_API_KEY else '❌ NOT SET'}")

    scheduler = AsyncIOScheduler(timezone="US/Eastern")

    scheduler.add_job(scheduled_morning_brief,
        CronTrigger(hour=6, minute=30, day_of_week="mon-fri"),
        id="morning_brief", name="Morning Brief")

    scheduler.add_job(scheduled_eod_scoring,
        CronTrigger(hour=16, minute=30, day_of_week="mon-fri"),
        id="eod_scoring", name="EOD Scoring")

    scheduler.add_job(scheduled_health_check,
        CronTrigger(minute="*/5", hour="9-16", day_of_week="mon-fri"),
        id="health_check", name="Health Check")

    scheduler.start()
    log.info(f"Scheduler: {len(scheduler.get_jobs())} jobs")
    for job in scheduler.get_jobs():
        log.info(f"  → {job.name}: {job.trigger}")

    # Test mode: run brief immediately
    if os.getenv("RUN_BRIEF_NOW", "").lower() == "true":
        log.info("RUN_BRIEF_NOW — running immediate brief...")
        await run_morning_brief()

    try:
        while True:
            await asyncio.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
