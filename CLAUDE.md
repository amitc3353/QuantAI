# CLAUDE.md — Claude Auto-Trader

## What this project is

A Claude-powered autonomous trading system for options & equities. Discord is the command center. Hetzner VPS runs the infrastructure. Alpaca is the broker (paper trading first, always).

## Architecture

4-service Docker Compose stack:
- **discord-bot/** — Discord bot with slash commands and reaction-based trade approval. Python, discord.py.
- **guard-engine/** — Deterministic constraint layer (FastAPI). ZERO AI tokens. Every trade must pass ALL guards before execution. This is the safety layer — never bypass it.
- **orchestrator/** — Cron scheduler (APScheduler) + Claude API client. Runs morning briefs at 6:30 AM ET, EOD scoring at 4:30 PM ET.
- **services/alpaca-mcp/** — Alpaca MCP server for broker connectivity.

## Key files

- `guard-engine/guards.py` — The most important file. All trading rules live here. 16 guards covering position limits, portfolio limits, timing zones, and strategy-specific rules. If you change a guard, run tests.
- `discord-bot/bot.py` — Slash commands and channel routing.
- `orchestrator/scheduler.py` — Cron jobs and Claude API calls. Token-optimized prompts.
- `configs/guard_config.json` — Guard parameters (position sizes, limits, thresholds).
- `configs/watchlist.json` — Symbol watchlist with sector mapping.
- `configs/strategies.json` — Strategy definitions (PMCC, bull put, iron condor, covered call).
- `configs/channels.json` — Discord channel ID mapping.

## The Three Laws (NEVER violate these)

1. Every trade must pass the Guard pipeline. No exceptions.
2. Full reasoning chain on every suggestion. No opacity.
3. Paper first. 2 weeks minimum before live capital.

## Development workflow

```bash
# Run guard tests (always run after changing guards.py)
cd guard-engine && python -m pytest tests/ -v

# Local Docker
docker compose up -d
docker compose logs -f

# Deploy to VPS
./scripts/deploy.sh $VPS_IP
```

## When making changes

- If modifying guard rules: update BOTH `guard-engine/guards.py` AND `configs/guard_config.json`. Add tests.
- If adding a slash command: add it to `discord-bot/bot.py` with proper embed formatting.
- If changing the orchestrator schedule: update `orchestrator/scheduler.py`.
- Never store API keys in code. They go in `.env` only.
- Never loosen a guard rule without adding a paper-trading validation period.

## Token optimization rules

- Research prompts: structured JSON output, max 1500 tokens.
- EOD scoring: uses Haiku (not Sonnet) for cost efficiency.
- Cache market data locally in `data/cache/` — only send deltas to Claude.
- System prompts are terse. User prompts include only what's needed.

## Current phase: Phase 1

Core infrastructure — Discord bot, guard engine, orchestrator, Alpaca paper trading.
Phase 2 adds TradingAgents, trading_skills, NautilusTrader.
Phase 3 adds self-improvement loop and small live allocation.
