# Claude Auto-Trader

A Claude-powered autonomous trading system for options & equities, run from Discord. Paper trading via Alpaca, with a deterministic guard layer in front of every trade.

> **Status: Phase 1 — paper trading only.** Live-trading code paths are stubbed/commented out. Several Discord commands are wired but their downstream Claude integrations are still marked TODO in the source.

## Architecture

Four services orchestrated via Docker Compose ([docker-compose.yml](docker-compose.yml)):

| Service | Image source | Purpose |
| --- | --- | --- |
| `discord-bot` | [discord-bot/](discord-bot/) | discord.py bot — slash commands, channel routing, ✅-reaction trade approval |
| `guard-engine` | [guard-engine/](guard-engine/) | FastAPI service exposing pure-Python trading guards on `127.0.0.1:8100` |
| `orchestrator` | [orchestrator/](orchestrator/) | APScheduler cron jobs + Claude API client (morning brief, EOD, weekly review, health) |
| `alpaca-mcp` | [services/alpaca-mcp/](services/alpaca-mcp/) | Thin Docker wrapper that clones [alpacahq/alpaca-mcp-server](https://github.com/alpacahq/alpaca-mcp-server) at build time |

All services share a `trader-net` bridge network. Only `guard-engine` exposes a port, and only on `127.0.0.1`.

## Components

### Guard engine ([guard-engine/guards.py](guard-engine/guards.py))

Deterministic constraint layer. **No Claude tokens are spent here.** Every trade proposal is evaluated against 20 guard functions before it can be executed.

**HTTP API** (FastAPI, port 8100):

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Healthcheck |
| `GET` | `/config` | Return active guard config |
| `POST` | `/check` | Evaluate a `TradeProposal` → `GuardResult` (`APPROVE`/`REJECT`) |
| `POST` | `/halt` | Emergency stop — reject all new trades |
| `POST` | `/resume` | Lift halt |
| `POST` | `/reload` | Reload [configs/guard_config.json](configs/guard_config.json) from disk |

**Guards implemented** (all in [guard-engine/guards.py](guard-engine/guards.py)):

- *System / position-level:* `check_halted`, `check_whitelist`, `check_position_size`, `check_max_loss`, `check_max_contracts`, `check_min_dte`, `check_liquidity`
- *Timing:* `check_no_trade_zones`, `check_earnings_blackout`, `check_vix`, `check_cooldown`
- *Portfolio-level:* `check_portfolio_delta`, `check_portfolio_theta`, `check_max_positions`, `check_sector_concentration`, `check_daily_loss`
- *Strategy-specific:* `check_pmcc`, `check_bull_put`, `check_iron_condor`, `check_covered_call`

Tests live in [guard-engine/tests/test_guards.py](guard-engine/tests/test_guards.py) and run on every push/PR via [.github/workflows/ci.yml](.github/workflows/ci.yml).

### Discord bot ([discord-bot/](discord-bot/))

`discord.py >= 2.3.0`. Entry point: [discord-bot/bot.py](discord-bot/bot.py). Loads four cogs:

- **Top-level commands** ([bot.py](discord-bot/bot.py)): `/status`, `/brief`, `/analyze`, `/guard_check`, `/emergency_stop`, `/resume`, `/watchlist`, `/rules`
- **Trading** ([cogs/trading.py](discord-bot/cogs/trading.py)): `/account`, `/positions`, `/orders`, `/quote`, `/buy`, `/sell`, `/limit_buy`, `/limit_sell`, `/cancel`, `/close`. Also a ✅-reaction handler in `#trade-proposals` that approves and forwards trades.
- **Options analysis** ([cogs/options_analysis.py](discord-bot/cogs/options_analysis.py)): `/greeks`, `/bull_put`, `/iron_condor`, `/covered_call` (uses `py_vollib`).
- **Infra agent** ([cogs/infra_agent.py](discord-bot/cogs/infra_agent.py)): `/read`, `/ls`, `/edit`, `/git`, `/commit`, `/deploy`, `/logs`, `/health`, `/restart`, `/run`, `/config` — operate on the project from inside Discord.
- **Chat agent** ([cogs/chat_agent.py](discord-bot/cogs/chat_agent.py)): responds to `@mentions`; legacy prefix commands `/decide`, `/lessons`, `/stats`, `/remember`.

Channel routing comes from [configs/channels.json](configs/channels.json) or `CHANNEL_*` env vars.

> Some commands (`/brief`, `/analyze`, `/emergency_stop`) currently dispatch placeholder responses — the source has explicit `# TODO: Phase 2` markers where the orchestrator/agent integration will be wired.

### Orchestrator ([orchestrator/scheduler.py](orchestrator/scheduler.py))

`APScheduler` `AsyncIOScheduler` running in `US/Eastern`. Four scheduled jobs:

| When (ET) | Job | Claude model |
| --- | --- | --- |
| 06:30, Mon–Fri | Morning brief + auto-proposals (Alpha Vantage quotes → Claude → Discord embed; auto-proposes trades scoring ≥ `AUTO_PROPOSE_THRESHOLD`) | Sonnet |
| 16:30, Mon–Fri | EOD scoring + lesson extraction | Sonnet |
| 16:45, Fri | Weekly review (`self_improve.run_weekly_review`) | Sonnet |
| every 5 min, 09:00–16:00 Mon–Fri | System health check, posts to Discord | Haiku |

Models are configurable via `CLAUDE_SONNET_MODEL` and `CLAUDE_HAIKU_MODEL`; defaults in [.env.example](.env.example) point at `claude-sonnet-4-20250514` and `claude-haiku-4-5-20251001`. Calls hit `https://api.anthropic.com/v1/messages` directly (`anthropic-version: 2023-06-01`) — no SDK.

### Alpaca MCP ([services/alpaca-mcp/](services/alpaca-mcp/))

Only a [Dockerfile](services/alpaca-mcp/Dockerfile) lives here. At build time it `git clone`s the upstream [alpacahq/alpaca-mcp-server](https://github.com/alpacahq/alpaca-mcp-server) and runs `alpaca_mcp_server.py`. The bot's trading cog talks to Alpaca directly via the `alpaca-py` SDK; the MCP container is the path Claude uses for tool-based broker access.

## Configuration

All runtime config lives in [configs/](configs/):

- [configs/guard_config.json](configs/guard_config.json) — guard parameters (position/portfolio/timing/strategy thresholds, symbol whitelist, `halted` flag)
- [configs/watchlist.json](configs/watchlist.json) — `symbols` list + `sectors` map
- [configs/strategies.json](configs/strategies.json) — strategy definitions (`pmcc`, `bull_put`, `iron_condor`, `covered_call`) with per-strategy `mode`: `advisory` / `copilot` / `autonomous`
- [configs/channels.json](configs/channels.json) — Discord channel ID map (command, chat, research, trade_proposals, guard_log, execution_log, system_health, pr_updates)

Secrets and host-specific values go in `.env` — see [.env.example](.env.example) for the full list. Notable groups: Discord (token + channel IDs), Anthropic (`ANTHROPIC_API_KEY`, model overrides), Alpaca paper (`ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, `ALPACA_PAPER=true`), Alpha Vantage (`ALPHA_VANTAGE_API_KEY`), runtime (`TZ`, `LOG_LEVEL`, `TRADING_MODE`). Live-trading vars (`ALPACA_LIVE_*`) are commented in the example with a "Phase 3 ONLY" note.

## Running locally

Prerequisites assumed but **not verified by code**: Docker Engine + Compose plugin, valid `.env`. The project does not ship a top-level `Makefile` or installer.

```bash
cp .env.example .env       # fill in tokens + channel IDs
docker compose up -d
docker compose logs -f
```

Run guard tests:

```bash
cd guard-engine
python -m pytest tests/ -v
```

## Deployment

Helper scripts in [scripts/](scripts/) target a Hetzner Ubuntu 24.04 VPS:

- [scripts/setup-vps.sh](scripts/setup-vps.sh) — first-time host setup (creates `trader` user, installs Docker, configures UFW). Pipe over SSH: `ssh root@<vps-ip> 'bash -s' < scripts/setup-vps.sh`.
- [scripts/deploy.sh](scripts/deploy.sh) — rsync source to the VPS (excluding `.env`, `data/`, `logs/`, `cache/`) then rebuild and restart containers. Usage: `./scripts/deploy.sh <VPS_IP>` or `VPS_IP=… ./scripts/deploy.sh`.
- [scripts/backup.sh](scripts/backup.sh) — tarballs `data/journal`, `data/briefs`, `configs/` to a timestamped archive and prunes older than 30 days.

## CI

[.github/workflows/ci.yml](.github/workflows/ci.yml) runs on push/PR that touches `guard-engine/**` or `configs/guard_config.json`:

- `test-guards` — Python 3.12, installs guard-engine deps + `pytest httpx`, runs `pytest tests/ -v`.
- `lint` — `ruff check guard-engine/ orchestrator/ discord-bot/`.

## The Three Laws

These appear in [CLAUDE.md](CLAUDE.md) and are the operational rules the system is being built to enforce:

1. **Every trade passes the guard pipeline.** No bypasses.
2. **Show the reasoning chain on every suggestion.** No opaque outputs.
3. **Paper first.** Minimum two weeks on paper before any live capital.

## Project layout

```
.
├── discord-bot/            # discord.py bot + cogs
├── guard-engine/           # FastAPI guard service + tests
├── orchestrator/           # APScheduler cron + Claude API client
├── services/alpaca-mcp/    # Dockerfile wrapping upstream alpaca-mcp-server
├── configs/                # guard_config / watchlist / strategies / channels
├── scripts/                # setup-vps / deploy / backup
├── data/                   # journal, briefs, cache, logs, memory (runtime-written)
├── docker-compose.yml
├── .env.example
├── .github/workflows/ci.yml
├── CLAUDE.md               # project guidelines + Three Laws
├── ROADMAP.md              # phase plan
├── SETUP_GUIDE.md          # detailed install walkthrough
└── TUTORIAL.md             # operator workflow
```

## Phase status (from source, not from docs)

What's actually wired up today:

- ✅ All 20 guards implemented and tested
- ✅ FastAPI guard service + emergency halt/resume
- ✅ Discord bot with slash commands across 5 cogs
- ✅ Alpaca paper trading via `alpaca-py` (positions, orders, quotes, cancel/close)
- ✅ Cron-driven morning brief, EOD scoring, weekly review, health checks via Claude
- ✅ CI on guard-engine

What's stubbed or marked TODO in the code:

- Several `/brief`, `/analyze`, `/emergency_stop` flows have `# TODO: Phase 2` markers where the agent dispatch should happen
- `position_pct` in [discord-bot/cogs/trading.py](discord-bot/cogs/trading.py) is hardcoded to `2.0` with a TODO to compute from real portfolio
- Live trading env vars are present in `.env.example` but commented out and labelled "Phase 3 ONLY"
- No TradingAgents or NautilusTrader integration yet (planned for Phase 2 per [ROADMAP.md](ROADMAP.md))

## License

No `LICENSE` file is present in the repository. Treat as **all rights reserved** until one is added.

## Further reading

- [CLAUDE.md](CLAUDE.md) — agent-facing project guide
- [ROADMAP.md](ROADMAP.md) — phase plan (Phase 1 → 3)
- [SETUP_GUIDE.md](SETUP_GUIDE.md) — full setup walkthrough
- [TUTORIAL.md](TUTORIAL.md) — day-to-day operator workflow
