# CLAUDE.md — QuantAI System Constitution
# This file governs all Claude Code autonomous operations.
# Read this FIRST before doing anything.

## What this system is

QuantAI is an autonomous options trading system. Real money will eventually
be at risk. Every autonomous action must be conservative, reversible, and logged.
When in doubt — stop, post to Discord, wait for human approval.

---

## Architecture — 4 Docker containers

| Container | Role | Key files |
|---|---|---|
| `trader-discord` | Discord bot, slash commands, chat agent | `discord-bot/` |
| `trader-guards` | Deterministic rule engine (FastAPI :8100) | `guard-engine/guards.py` |
| `trader-orchestrator` | APScheduler, all agents, automation | `orchestrator/` |
| `trader-alpaca` | Alpaca MCP server | `services/alpaca-mcp/` |

**Docker project name:** `quantai`
**Deploy command:** `docker compose -p quantai build --no-cache [service] && docker compose -p quantai up -d [service]`
**Logs:** `docker logs trader-orchestrator --since 2h`

---

## The Three Laws — NEVER violate

1. **Every trade passes the guard engine.** No exceptions. Not even "obvious" trades.
2. **Show your work.** Every fix includes an explanation of root cause and what changed.
3. **Paper first.** Never modify TRADING_MODE or live API keys.

---

## What Claude Code CAN do autonomously

### Bug fixes (no approval needed):
- Fix Python syntax errors, import errors, NameErrors
- Fix silent crashes in agents (missing try/except, wrong variable names)
- Fix Discord embed formatting errors
- Fix cache file read/write issues
- Fix scheduler job registration problems
- Add missing log statements so failures are visible
- Fix health check false alarms (wrong thresholds, wrong test payloads)

### Investigation (no approval needed):
- Read any log file or container output
- Read any source file in the repo
- Check container health and status
- Scan GitHub repos and arXiv papers
- Fetch any public URL for research
- Run syntax checks: `python3 -c "import ast; ast.parse(open('file.py').read())"`
- Run guard tests: `cd guard-engine && python -m pytest tests/ -v`

### Always needs approval before executing:
- Changes to `guard-engine/guards.py` or `configs/guard_config.json`
- Changes to `configs/agent1_params.json` or `configs/agent2_params.json`
- Changes to `orchestrator/scheduler.py` job timing
- Any new `pip install` or `npm install`
- Any change to `docker-compose.yml`
- Anything touching `.env` (never touch this file)

---

## What Claude Code NEVER does

- Modify `.env` or any file containing API keys
- Change `TRADING_MODE` from paper to live
- Modify guard rules without explicit written approval
- Auto-merge GitHub PRs
- Execute trades directly (always goes through guard engine)
- Install packages without approval and security review
- Delete data files in `data/memory/` or `data/journal/`
- Expose API keys in logs, Discord messages, or commit messages

---

## File structure — what lives where

```
configs/
  agent1_params.json     <- Agent 1 strategy params (self-improve edits these)
  agent2_params.json     <- Agent 2 strategy params
  guard_config.json      <- Trading rules — APPROVAL REQUIRED to change
  context_weights.json   <- Signal weights (auto-tuned by correlation_analyzer)
  watchlist.json         <- Symbols with sector classification
  strategies.json        <- Strategy definitions

services/               <- Intelligence layer (mounted read-only into orchestrator)
  market_data.py        <- VIX, options chain, IV rank, strike finder
  macro_data.py         <- FRED + Finnhub
  sentiment_data.py     <- Put/call ratio, Fear&Greed, VIX term structure
  flow_detector.py      <- Vol/OI unusual activity + dark pool proxy
  context_builder.py    <- Pre-trade score 0-100
  backtester.py         <- Validates param changes before PR creation
  correlation_analyzer.py <- Signal weight auto-tuning
  health_monitor.py     <- All health checks
  cto_agent.py          <- Tech Intelligence Agent (GitHub/arXiv scanner)
  cto_report.py         <- Weekly CTO reliability report

orchestrator/
  scheduler.py          <- All 12 cron jobs
  agent1_iron_condor.py <- Agent 1: 0DTE SPY iron condors
  agent2_covered_call.py <- Agent 2: weekly covered calls + roll logic
  self_improve.py       <- PR generation + backtest gate

discord-bot/
  memory.py             <- Persistent memory
  cogs/
    trading.py          <- /buy /sell /journal /performance /lessons
    infra_agent.py      <- /health /deploy /logs /restart /git
    chat_agent.py       <- #chat + CTO on-demand
    options_analysis.py <- /greeks /bull_put /iron_condor

guard-engine/
  guards.py             <- 16 rules, 44 tests — most critical file

data/
  memory/paper/         <- agent journals + lessons
  memory/shared/        <- shared lessons, decisions, events
  journal/              <- EOD scores, backtest results
  cache/                <- market data cache files
```

---

## Scheduler — 12 jobs (all times ET)

| Time | Job | Days |
|---|---|---|
| 6:00 AM | CTO Tech Intelligence Scan | Monday |
| 6:30 AM | Morning Brief + Auto-Proposals | Mon-Fri |
| 8:00 AM | Daily Digest | Mon-Fri |
| 9:50 AM | Agent 1: Entry 1 | Mon-Fri |
| 10:00 AM | Agent 2: Weekly CC Scan | Monday |
| 11:30 AM | Agent 1: Entry 2 (conditional) | Mon-Fri |
| Every 5 min | Agent 1 Monitor + Health Check | Market hours |
| 4:30 PM | EOD Scoring + Lessons + Self-Improve | Mon-Fri |
| 4:45 PM | Weekly Review + Correlation + CTO Report | Friday |

---

## Known weak points

1. Empty options chain — Alpaca paper returns 0 contracts for 0DTE sometimes
2. Cache staleness — normal before 6:30 AM, not during market hours
3. Import paths — discord-bot does NOT have services/ mounted
4. Silent crashes — APScheduler says "executed successfully" even on early return
5. Guard 422 — means guard is working correctly, not an error

---

## How to investigate a problem

1. `docker logs trader-orchestrator --since 2h 2>&1 | grep -E "ERROR|error|Traceback|failed"`
2. `docker logs trader-orchestrator 2>&1 | grep "2026-MM-DD HH:MM"`
3. `ls -la /home/trader/QuantAI/data/memory/paper/`
4. Read the actual file before proposing any fix

---

## Security rules

- Stars >500, last commit <6 months, no open CVEs before any new library
- Pin all versions (== not >=)
- No auto-install — propose + security assessment + wait for approval
- New integrations in services/ only
- Run pip audit before live trading

---

## Trade proposal rules

Every proposed trade MUST pass guard /check, include max_loss_pct,
include thesis + invalidation condition, be paper only until validated.

---

## Current state

- Mode: PAPER | Auto: ON | Account: $20k | Cost: ~$25/mo
- Live trading: NOT YET (need 40+ trades, 60%+ win rate)
- Not yet built: Market Intelligence Agent, Pattern Agent, NautilusTrader

## When unclear — always ask first

Post to #chat: what you're planning, what could go wrong, wait for approval.
Never guess on trading-related changes.
