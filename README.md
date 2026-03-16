# ⚡ Claude Auto-Trader

A Claude-powered autonomous trading system for options & equities.  
Discord-native · Guard-enforced · Paper-first · Self-improving

## Architecture

```
┌─────────────────────────────────────────────┐
│  Discord Hub (you + all agents)             │
├─────────────────────────────────────────────┤
│  Orchestrator (cron + event bus)            │
├──────────┬──────────┬──────────┬────────────┤
│ Research │ Analysis │  Guard   │ Execution  │
│ (Sonnet) │ (Sonnet) │ (determ) │  (Haiku)   │
├──────────┴──────────┴──────────┴────────────┤
│  Data: AlphaVantage · OpenBB · py_vollib    │
├─────────────────────────────────────────────┤
│  Alpaca (paper → live)                      │
└─────────────────────────────────────────────┘
```

## Quick Start

```bash
# 1. Clone and configure
git clone <your-repo-url> && cd claude-auto-trader
cp .env.example .env  # Fill in your keys

# 2. Run locally
docker compose up -d

# 3. Deploy to Hetzner VPS
./scripts/deploy.sh
```

## Project Structure

```
claude-auto-trader/
├── discord-bot/          # Discord bot (command center)
│   ├── bot.py            # Main bot entry
│   ├── cogs/             # Slash command modules
│   └── requirements.txt
├── guard-engine/         # Deterministic constraint layer (NO AI)
│   ├── guards.py         # All guard logic
│   ├── config.json       # Guard parameters
│   └── requirements.txt
├── orchestrator/         # Cron scheduler + agent router
│   ├── scheduler.py      # Cron jobs (morning brief, EOD scoring)
│   ├── agent_router.py   # Routes signals between agents
│   └── requirements.txt
├── services/
│   ├── openalice/        # OpenAlice submodule/mount
│   └── alpaca-mcp/       # Alpaca MCP server
├── configs/
│   ├── watchlist.json    # Your symbol watchlist
│   ├── strategies.json   # Strategy configs
│   └── channels.json     # Discord channel mapping
├── scripts/
│   ├── deploy.sh         # One-command VPS deployment
│   ├── setup-vps.sh      # First-time VPS hardening
│   └── backup.sh         # Data backup
├── data/
│   ├── logs/             # System logs
│   ├── journal/          # Trade journal (JSONL)
│   ├── briefs/           # Morning research briefs
│   └── cache/            # Market data cache
├── docker-compose.yml
├── .env.example
└── Dockerfile.*          # Per-service Dockerfiles
```

## The Three Laws

1. **NEVER break the rules you set** — Every trade passes Guards. No exceptions.
2. **Show your work** — Full reasoning chain on every suggestion.
3. **Paper first. Always** — 2 weeks paper minimum before live capital.

## Phases

- **Phase 1** (Weeks 1-3): Core infra — Discord bot, OpenAlice, Alpaca paper, Guard engine
- **Phase 2** (Weeks 4-8): Intelligence — TradingAgents, trading_skills, NautilusTrader
- **Phase 3** (Week 9+): Self-improvement loop, live trading (small)
