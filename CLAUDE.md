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
        +-- QuantAI Pipeline (Python cron, every 15 min during 9-16 ET Mon-Fri)
        |   +-- market_intelligence.py (yfinance: VIX, prices, technicals)
        |   +-- scan_options.py (78 tickers x 4 strategies, SLOW: 10-15 min)
        |   +-- debate_chamber.py (Bull/Bear/Judge LLM debate)
        |   +-- autonomous_execution.py (mleg orders to Alpaca, journal, sync)
        |
        +-- LiteLLM (Docker, localhost:4000)
        +-- Dashboard (localhost:8080, Tailscale: https://quantai.tail1465ff.ts.net/)
        +-- Docker legacy (trader-orchestrator, trader-discord, trader-cto, trader-guards)
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

## Current State (April 26, 2026)

- Paper trading on Alpaca (~$99,368 equity)
- 0 open positions (closed for strategy rework)
- Pipeline works end-to-end: scan, debate, execute, journal, sync
- 42/43 system tests passing
- Dashboard live at https://quantai.tail1465ff.ts.net/
- IB Gateway 10.37 installed at localhost:4002 (paper, DUP851506) — **pending IP whitelist** (see below)

## Alpaca API Gotchas

- mleg orders REQUIRE top-level `"qty": "1"` in payload
- mleg orders REJECT `"position_intent"` — never include it
- Options chain: `paper-api.alpaca.markets/v2/options/contracts`

## IB Gateway (IBKR Paper)

- Paper trading port: `localhost:4002`, clientId=1
- Account: `DUP851506`
- Service: `ibgateway.service` (systemd, enabled)
- Started by: `/opt/ibc/quantai_gateway_start.sh` (reads `IBKR_PASSWORD` from `.env`)
- **NEVER run `systemctl status ibgateway` or `ps aux | grep ibgateway`** — these leak `IBKR_PASSWORD` into terminal output.
- Safe status check: `systemctl is-active ibgateway`
- Safe log check: `journalctl -u ibgateway -n 30 --no-pager | grep -v -i 'pass\|pw=\|--user='`

### IBKR Login — Action Required Before Service Can Connect

Root cause diagnosed 2026-04-26: IBKR rejects VPS login with `NSErrorResponse.INVALID_USERNAME_OR_BAD_IP`.
The VPS IP **87.99.141.55** must be whitelisted at the **login level** in IBKR Client Portal.

**IBKR has two separate IP controls — we need the LOGIN-level one:**
- ❌ Settings → API Settings → Trusted IPs — this gates TCP connections *after* login (not what we need)
- ✅ **Settings → User Settings → Security → Trusted IPs** — this gates the login itself (required)

**Amit: exact steps:**
1. Log in at https://www.interactivebrokers.com/sso/Login
2. Click username (top right) → **Settings** → **User Settings**
3. Under **Security** section → **Trusted IPs**
4. Add `87.99.141.55` → Save
5. Wait **10–15 minutes** for propagation across IBKR auth servers
6. Then: `sudo systemctl start ibgateway` on the VPS

After whitelist propagates: verify with:
```
python3 -c "from ib_insync import IB; ib=IB(); ib.connect('127.0.0.1',4002,clientId=1); print(ib.isConnected(), ib.managedAccounts()); ib.disconnect()"
```
Expected: `True ['DUP851506']`

## Cron Schedule

```
*/15 9-16 * * 1-5   run_pipeline.py
5 16 * * 1-5        run_pipeline.py eod
30 9 * * 1-5        pre_trade_check.py
* * * * *           collect_system.py
* * * * *           collect_karna.py
* * * * *           collect_quantai.py
```

## What Needs Building (priority order)

1. Heartbeat monitoring (Phase B)
2. Position threshold monitor (Slice D)
3. Strategy rework (awaiting Amit's direction)
4. Error taxonomy + runbooks (Phase E)

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
