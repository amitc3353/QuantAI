# CLAUDE.md — KARNA + QuantAI Project

## About this project

KARNA is a personal AI operations layer running 24/7 on a VPS. QuantAI is an autonomous options trading system — the first project on KARNA's infrastructure. The operator is Amit.

## How you work

You are working on files that are mounted from a remote VPS via SSHFS. Every file change you make is live on the production server immediately. Be careful and deliberate.

## Prevent accidental damage

- Before deleting, overwriting, or renaming any existing file, show me what will change and wait for confirmation.
- Never modify files outside the current working folder unless I explicitly ask you to.
- Always create a backup before editing production scripts: copy the file with a `.bak.YYYY-MM-DD` suffix before making changes.
- Never modify `/etc/systemd/system/openclaw.service`. OpenClaw uses `Type=simple` — changing this caused 18 hours of debugging. This is a hard rule.

## Keep things organized

- When creating new files, use the naming format YYYY-MM-DD-descriptive-name for docs and notes.
- At the end of a task, list all files created or modified with their locations.
- Commit changes to git with clear, descriptive commit messages.
- After modifying any workspace files (AGENTS.md, SOUL.md), run: `bash /home/trader/QuantAI/scripts/sync_workspaces.sh`

## Control the pace of autonomous work

- For multi-step tasks, outline your plan first and wait for my approval before executing.
- After each major step, briefly summarize what you did and what's next.
- 3-attempt debug budget: if a fix doesn't work after 3 attempts, stop and report what you tried.

## SECURITY — NON-NEGOTIABLE

- **NEVER read, cat, print, or expose .env files, API keys, tokens, or credentials.**
- **NEVER display contents of `/home/trader/QuantAI/.env` or any file containing secrets.**
- **NEVER display or read `/home/openclaw/.openclaw/openclaw.json` — it contains Discord bot tokens.**
- **NEVER include credentials in commit messages, logs, or output.**
- Credentials exist in environment variables and systemd unit files. You don't need to see them to work.
- If a script needs a credential, it reads from `os.environ` — you write the code, not the key.

## Architecture Overview

```
Amit (strategy, approvals via phone/Discord)
  |
  +-- Cowork (development — you)
  |
  +-- VPS (87.99.141.55 / Tailscale 100.84.147.23)
        |
        +-- KARNA (OpenClaw agent, Claude Sonnet 4.6)
        |   +-- Discord bot: #karna-command, #karna-approvals, etc.
        |
        +-- Agent Alpha Pipeline (Python cron, every 15 min during 9-16 ET Mon-Fri)
        |   +-- market_intelligence.py (yfinance: VIX, prices, technicals + SPX/Beta fields)
        |   +-- scan_options.py (78 tickers x 4 strategies, SLOW: 10-15 min)
        |   +-- debate_chamber.py (Bull/Bear/Judge LLM debate)
        |   +-- autonomous_execution.py (submits via broker.place_mleg_order, journal as A###)
        |
        +-- Agent Beta (Python cron, every 15 min during 9-16 ET Mon-Fri)
        |   +-- beta_agent.py (entry point — refuses if BROKER_TYPE != ibkr)
        |   +-- beta/regime_detector.py (12-regime classifier, deterministic)
        |   +-- beta/strategies/{8 modules} (event_strangle, ratios, BWB, vix_calls, etc.)
        |   +-- beta/risk_engine.py (per-source risk gates, scoped to agent_beta)
        |   +-- beta/event_moves_seeder.py (weekly historical event-move data)
        |   +-- journals as B###; exit_rules stored on each entry
        |
        +-- broker.py (adapter — BROKER_TYPE=ibkr default, alpaca fallback)
        |   +-- IBKR Bag/ComboLeg via ib_insync (port 4002, account DUP851506)
        |   +-- Alpaca paper REST (paper-api.alpaca.markets, fallback only)
        |
        +-- LiteLLM (Docker, localhost:4000)
        +-- Dashboard (localhost:8080, Tailscale: https://quantai.tail1465ff.ts.net/)
        +-- Docker legacy (trader-cto, trader-guards still running; trader-discord pruned 2026-05-06, source retained; trader-orchestrator no longer started)
```

## Key File Paths (relative to /home/trader/QuantAI/)

### Scripts
- `v2/shared-data/scripts/run_pipeline.py` — Main cron entry point
- `v2/shared-data/scripts/market_intelligence.py` — Market data
- `v2/shared-data/scripts/scan_options.py` — Options scanner
- `v2/shared-data/scripts/debate_chamber.py` — Trade debate
- `v2/shared-data/scripts/autonomous_execution.py` — Order execution + journaling
- `v2/shared-data/scripts/system_test.py` — 43-check health test

### Runtime Data (absolute paths on VPS)
- `/root/quantai-v2/shared-data/journal/paper/trades.jsonl` — Trade journal
- `/root/quantai-v2/shared-data/cache/` — Intelligence, debate, scan results
- `/root/quantai-v2/shared-data/logs/pipeline.log` — Pipeline log

### Dashboard
- `/home/trader/dashboard/` — Generator + collectors
- `/home/trader/dashboard/state/` — JSON state files

### Docs
- `docs/quantai-knowledge.md` — Living knowledge base

## Current State (April 27, 2026)

- Paper trading via broker adapter; **BROKER_TYPE=ibkr is the default** (set in `.env` on 2026-04-27). Alpaca paper retained as a fallback adapter.
- IBKR paper equity: $1,000,000 (account DUP851506). Alpaca paper kept active but no longer the active broker.
- 0 open positions — Alpaca holdings closed via `DELETE /v2/positions` during the migration; queued for fill at Mon 9:30 ET. Journal A008/A009/A010 marked CLOSED with reason=ibkr_migration_reset.
- Both Alpha (ETF spreads) and Beta (regime-driven SPX/XSP/VIX) live on IBKR.
- 43/43 system tests passing on IBKR. pre_trade_check 19/19 GO.
- Dashboard live at https://quantai.tail1465ff.ts.net/
- IB Gateway 10.37 active at localhost:4002 — **verified 2026-04-26**, full pipeline migration completed 2026-04-27.

## Alpaca API Gotchas

- mleg orders REQUIRE top-level `"qty": "1"` in payload
- mleg orders REJECT `"position_intent"` — never include it
- Options chain: `paper-api.alpaca.markets/v2/options/contracts`

## IB Gateway (IBKR Paper)

- Paper trading port: `localhost:4002`, clientId=1
- Account: `DUP851506`
- Service: `ibgateway.service` (systemd, enabled)
- Started by: `/opt/ibc/quantai_gateway_start.sh` (reads `IBKR_USERNAME` and `IBKR_PASSWORD` from `.env`)
- **NEVER run `systemctl status ibgateway` or `ps aux | grep ibgateway`** — these leak credentials into terminal output.
- Safe status check: `systemctl is-active ibgateway`
- Safe log check: `journalctl -u ibgateway -n 30 --no-pager | grep -v -i 'pass\|pw=\|--user='`
- Index options verified: XSP (47 expiries / 482 strikes), SPX (20/590), VIX (9/70)

### Connection probe (safe — no credentials in output)
```
python3 -c "from ib_insync import IB; ib=IB(); ib.connect('127.0.0.1',4002,clientId=1); print(ib.isConnected(), ib.managedAccounts()); ib.disconnect()"
```
Expected: `True ['DUP851506']`

### Credential note
Login username (`IBKR_USERNAME`) is the account login, not the paper account number DUP851506.
Both `IBKR_USERNAME` and `IBKR_PASSWORD` live in `.env` and are injected at runtime — never hardcoded.

## Cron Schedule

(VPS cron is in UTC — 13-20 UTC = 9-16 ET during DST.)

```
# Alpha pipeline
*/15 13-20 * * 1-5   run_pipeline.py
5 20 * * 1-5         run_pipeline.py eod
30 13 * * 1-5        pre_trade_check.py

# Agent Beta (regime-driven, IBKR-native — added 2026-04-27)
*/15 13-20 * * 1-5   beta_agent.py
0 6 * * 0            beta/event_moves_seeder.py     # weekly Sunday 6 AM UTC

# Monitoring
*/2 * * * *          heartbeat_monitor.py
*/2 13-20 * * 1-5    position_monitor.py
*/5 * * * *          error_detector.py
0 22 * * 5           error_learner.py               # weekly Friday 6 PM ET

# Dashboard collectors (every 1m unless noted)
* * * * *            collect_system.py
* * * * *            collect_karna.py
* * * * *            collect_quantai.py
* * * * *            collect_alpaca.py              # broker-aware; reads via broker.get_broker()
* * * * *            collect_beta.py                # added 2026-04-27
* * * * *            collect_cron.py
*/5 * * * *          collect_history.py
*/15 * * * *         collect_clawroute.py           # ClawRoute cost tracking

# Auto-Heal (Claude-driven; added 2026-04-29) — see docs/runbooks/runbook-auto-heal.md
30 12 * * 1-5        auto_heal.py --mode=apply      # 08:30 ET pre-market (overnight breakage)
0  15 * * 1-5        auto_heal.py --mode=observe    # 11:00 ET mid-trading (read-only, queues digest)
0  18 * * 1-5        auto_heal.py --mode=observe    # 14:00 ET mid-afternoon (read-only)
45 20 * * 1-5        auto_heal.py --mode=apply      # 16:45 ET post-close (drains digest, applies)
```

## Auto-Heal

Claude-driven triage layer that sits **on top of** the rule-based monitors (`error_detector.py`, `heartbeat_monitor.py`, `position_monitor.py`, `error_learner.py`). Runs 4×/day around the trading window.

- **Out-of-window slots (12:30 / 20:45 UTC)** can mutate. Auto-applies safe-class fixes; queues riskier ones to `#karna-approvals` for one-tap ✅ on Discord.
- **In-window slots (15:00 / 18:00 UTC)** are read-only. Queue findings into a daily digest posted at 20:45.
- Hard rules enforced in Python (not LLM-overrideable): no `.env`, no openclaw/ibgateway service touch, no journal mutation, path allowlist, `.bak` before any edit, 80-line diff cap, 3-attempt budget, open-position guard on trading-path files.
- Operator commands: `auto_heal.py --status` / `--dry-run` / `--rollback <fix_id>` / `--reset <fix_id>`.
- Full doc: `docs/runbooks/runbook-auto-heal.md`.

## What Needs Building (priority order)

- ✅ Heartbeat monitoring (Phase B) — done 2026-04-17
- ✅ Position threshold monitor (Slice D) — done 2026-04-17
- ✅ Error taxonomy + runbooks (Phase E) — done 2026-04-17
- ✅ Broker adapter + IBKR migration (ADR-004) — done 2026-04-26
- ✅ Agent Beta (regime-driven, 8 strategies, native index options) — done 2026-04-27
- ✅ Auto-Heal routine (Claude-driven, 4×/day, Discord ✅ approvals) — done 2026-04-29
- 🔄 Beta first-week observation (active — watch beta.log + dashboard Mon-Fri)
- ⏳ First real (non-dry-run) order via IBKR — pending market hours
- ⏳ Strategy-level position grouping in position_monitor — deferred
- ⏳ Strategy rework / parameter tuning — awaiting first 30 days of Beta data
- ✅ **`place_mleg_order` partial-fill safeguard — done 2026-05-03.**
  `_broker_ibkr.py`: `order_submitted` flag + `finally: sleep(0.5)` flush + post-submit
  recovery via `_find_open_order_by_ref()` / `get_open_orders()`. Callers
  (`autonomous_execution.py`, `beta_agent.py`) reconcile after `None` via `get_open_orders()`.
  `position_monitor.py`: `reconcile_ghost_positions()` fires 🔴 Discord alert on any broker
  position not in any open journal entry (60-min cooldown per symbol).

## Git Rules

- **Local `main` on this VPS is the source of truth.** `origin/main` on GitHub is stale and not maintained.
- Do not `git pull`, `git fetch && merge`, `git rebase`, or `git push` (force or otherwise) against `origin/main`. The two histories diverged 42 local vs 40 remote (common ancestor `c91c801`); ~12 commit pairs are patch-equivalent and ~75 files differ — automatic reconciliation is unsafe.
- All new work commits to local `main` (or feature branches merged into local `main`). Treat the GitHub remote as a frozen archive.
- Feature branches, never force-push main.
- `bash /home/trader/QuantAI/scripts/sync_workspaces.sh` after workspace changes.

## Design Principles

- LLMs only where judgment needed. Python for mechanical work.
- Cost conscious: cheap models for cheap tasks.
- Data sovereignty: everything on VPS, no external services beyond LLM providers.
- Approval gates: paper trades auto-execute, code changes need review.

## graphify

This project has a graphify knowledge graph at graphify-out/.

Rules:
- Before answering architecture or codebase questions, read graphify-out/GRAPH_REPORT.md for god nodes and community structure
- If graphify-out/wiki/index.md exists, navigate it instead of reading raw files
- For cross-module "how does X relate to Y" questions, prefer `graphify query "<question>"`, `graphify path "<A>" "<B>"`, or `graphify explain "<concept>"` over grep — these traverse the graph's EXTRACTED + INFERRED edges instead of scanning files
- After modifying code files in this session, run `graphify update .` to keep the graph current (AST-only, no API cost)
