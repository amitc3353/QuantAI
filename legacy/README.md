# Legacy — the v1 architecture (retired)

Everything in this directory is the **first generation** of QuantAI
(March–April 2026). None of it is on the live trading path. It is kept
because the v1 → v2 evolution is part of the project's story, and the
git history that explains it lives here.

## What v1 was

A Docker microservice stack:

| Component | What it did |
|---|---|
| `guard-engine/` | FastAPI service — 20 named guard functions (position size, max loss, DTE, liquidity, VIX, earnings blackout, portfolio delta/theta, sector concentration, cooldown, kill switch) run as a fail-fast pipeline over every trade proposal. Zero LLM tokens by design. 44 tests. |
| `orchestrator/` | Scheduler + two strategy agents (iron condor, covered call) + `self_improve.py`: an early self-correcting loop that opened **backtest-gated GitHub PRs** when the daily performance score dropped — config-delta proposals from an LLM, validated by `services/backtester.py`, discarded on backtest failure, never auto-merged. |
| `discord-bot/` | Slash-command bot for briefs/approvals. Pruned 2026-05-06 after an audit showed zero functional traffic for 30 days (see `docs/archive` BACKLOG entry). |
| `services/` | Backtester (honest about its limits — mid-price fills, "sanity check, not proof"), CTO agent, market data. |
| `docker-compose.yml`, `Dockerfile.cto`, `cto_listener.py` | The container stack and its glue. |

## Why v2 replaced it

The v1 design had a long-running daemon problem: state lived in
containers, restarts lost context, and the guard engine was a network
hop away from the code it guarded. v2 inverted the design:

- **Cron is the metronome** — every script runs to completion and exits.
  No message bus, no long-lived process, nothing to drift.
- **Guards moved in-process** — the FastAPI guard engine became the
  `_*_gate.py` modules imported directly by each agent
  (`v2/shared-data/scripts/`), so a network failure can't skip a safety
  check. Fail-open vs fail-closed is argued per-gate in each docstring.
- **The journal became the single source of truth** — one append-only
  JSONL every component reads.
- **Self-improvement became human-gated** — v1's auto-PR loop was
  replaced by Sentinel (`v2/shared-data/scripts/sentinel_agent.py`),
  which proposes fixes through hard-coded Python rails and a Discord
  human-approval gate instead of opening PRs autonomously.

Known v1 defects are documented rather than fixed (it's retired):
`guards.py` `check_no_trade_zones` uses server-local time instead of ET,
`check_cooldown` breaks on tz-aware timestamps, and
`configs/guard_config.json` declares drawdown limits no guard reads.

If you're evaluating the project, read the live system first:
[`../README.md`](../README.md) and
[`../docs/architecture.md`](../docs/architecture.md).
