# QuantAI — System State
**Last updated: April 1, 2026 | Update after every significant session**

Start every new chat: "Read SYSTEM_STATE.md."

---

## Infrastructure

| Component | Value |
|---|---|
| VPS | Hetzner CX31 · 87.99.141.55 |
| OS | Ubuntu 24 |
| Repo | github.com/amitc3353/QuantAI (main branch) |
| Repo path on VPS | /home/trader/QuantAI |
| OpenClaw gateway | /root/quantai-v2/ |
| Trading mode | PAPER |
| Paper account equity | $100,040 |

**Two directory trees — both matter:**
- `/home/trader/QuantAI/` — git repo, all Python scripts
- `/root/quantai-v2/` — OpenClaw reads agent workspaces here, all data written here

---

## How This System Works

Four Claude model instances (agents) live in Discord. They have Bash tool access and run Python scripts directly. A VPS cron job triggers the autonomous pipeline every 15 minutes during market hours.

**Two trading streams:**
- **Autonomous** — Agent Alpha + Beta execute via cron, no human approval
- **Manual** — Amit trades SOFI collar + learning trades, logs in #journal

---

## The Four Agents

| Agent | Channel | Model | Role |
|---|---|---|---|
| Orchestrator | #chat | Claude Sonnet | Primary interface, runs scans, monitors positions, answers everything |
| Research | #research | Claude Sonnet | SOFI brief, credit spreads, collar candidates |
| Infra | #infra | Claude Sonnet | Health checks, files, git, debugging |
| Journal | #journal | Claude Haiku | Trade logging, stats, Google Sheets sync |

All four agents know Agent Alpha and Beta. Ask any channel about their performance.

---

## Agent Alpha — The Opportunist

Trades any defined-risk premium strategy on any liquid ticker.

| Parameter | Value |
|---|---|
| Universe | Any ticker: avg volume >5M, options OI >200, bid/ask <$0.15 |
| Strategies | Bull put spread, bear call spread, iron condor, iron butterfly, jade lizard, calendar spread, diagonal spread |
| Strategy selection | Picks based on RSI, trend, VIX, IV rank |
| Min credit | $0.30 |
| Stop loss | 2x credit |
| Profit target | 50% |
| Journal tag | `agent_alpha` · IDs: A001, A002... |

## Agent Beta — The Range Trader

Specializes in range-bound strategies across all liquid tickers.

| Parameter | Value |
|---|---|
| Universe | Any liquid ticker (same filters as Alpha) |
| Strategies | Iron condor, iron butterfly (first choice), bull/bear spreads when one side only |
| Entry conditions | VIX 13-28, RSI 35-65, ADX <25, no event within 2 days |
| Short delta | 0.08–0.12 |
| Wing width | $5 (widens to $7 in caution) |
| Min credit | $0.50 |
| Hard close | 3:30 PM ET |
| Journal tag | `agent_beta` · IDs: A003, A004... (shared A-series with Alpha) |

---

## Autonomous Pipeline (Cron)

```
*/15 9-16 * * 1-5   run_pipeline.py          → every 15 min market hours
5    16  * * 1-5    run_pipeline.py eod      → 4:05 PM ET daily
```

**Decision logic every 15 min:**
1. Market open (9:30-4 PM ET weekdays)? → else exit
2. Opening volatility window (9:30-9:45)? → wait, no entry
3. Past 3 PM? → monitor only, no new entries
4. Past 3:30 PM? → hard close all agent positions
5. VIX ≥ 35 or regime=halt? → no entry
6. 2 entries already today? → monitor only
7. All clear → intelligence → scan → debate → execute

Max 2 entries per day. State in `cache/daily_state.json`.

---

## Amit's Manual Trading

### SOFI Collar — paper, 200 shares

| Parameter | Value |
|---|---|
| Entry | ~$15 paper |
| Call strike | $16, sell biweekly |
| Put strike | $12, buy monthly |
| Net income target | $170/month |
| Max loss | $600 |

5 pre-decided triggers (no improvising):

| Price | Action |
|---|---|
| $15.70 | MONITOR |
| $16.00 | ROLL call to $18, 2 weeks out |
| Called away | Accept profit, rebuy on dip |
| $12.50 | MONITOR, assess conviction |
| $12.00 | EXERCISE OR roll to $10 OR exit |

---

## Guard Rules — Always Enforced

| Rule | Value |
|---|---|
| Max loss per trade | 2% of account |
| Earnings blackout | 14 days |
| VIX ≥ 35 | No new trades |
| No-trade windows | 9:30-9:45 AM, 3:45-4:00 PM ET |
| Stop loss | 2x credit |
| Profit target | 50% of max profit |
| Max agent positions | 3 simultaneously |
| Entry cutoff | 3:00 PM ET |
| Hard close | 3:30 PM ET |
| Strategy gate | No covered calls/collars/CSPs (require shares) |

---

## Scripts

All at `/home/trader/QuantAI/v2/shared-data/scripts/`
All data at `/root/quantai-v2/shared-data/`

| Script | Purpose |
|---|---|
| `run_pipeline.py` | Master cron entry point — conditions → entry/monitor/eod |
| `market_intelligence.py` | Intelligence packet: VIX, technicals, events, earnings, news |
| `debate_chamber.py` | Bull/Bear/Judge → top 2 trade proposals (any strategy, any ticker) |
| `autonomous_execution.py` | Places Alpaca paper orders, logs, syncs sheets |
| `scan_options.py` | Dynamic scanner: 100+ tickers, credit spreads + collar candidates |
| `self_evolution.py` | EOD evolution: 6-step, 5-gate validation |
| `sheets_sync.py` | Syncs journal to Google Sheet (4 tabs) |
| `eod_summary.py` | Daily summary posted to #chat at 4:05 PM |
| `pattern_engine.py` | Statistical patterns — needs 20+ closed trades |
| `fetch_sofi.py` | SOFI data for Research agent |
| `system_test.py` | **43-check health test — run weekly** |

---

## System Health Check

```bash
python3 /home/trader/QuantAI/v2/shared-data/scripts/system_test.py
```

Tests 15 categories, 43 checks. Expected result: **43/43 passed**.

Categories: environment variables, Python dependencies, all script files, all workspace files, cron jobs, Alpaca API, intelligence packet, all major scripts, journal state, Google Sheets, OpenClaw gateway.

Run: after any deployment, weekly, or when something feels off.

---

## Google Sheets Journal

URL: https://docs.google.com/spreadsheets/d/1GidIf-oLY9NfeRGVTwwGFYzA4eZx2bYjvY7UOATiMM0

| Tab | Contents |
|---|---|
| All Trades | Every trade, color-coded |
| Agent Trades | Alpha + Beta only |
| Manual Trades | Amit's trades only |
| Summary | Live win rate, P&L, open count per stream |

Trade IDs: `A001`... = agent trades · `P001`... = manual trades
Auto-syncs after every execution and every journal entry.

---

## Data Sources

| Source | Data | Cost |
|---|---|---|
| yfinance | Price, technicals, VIX, fundamentals | Free |
| Finnhub | Events, earnings, news | Free tier |
| Alpha Vantage | Earnings surprises | Free (25/day) |
| CNN | Fear & Greed (VIX fallback) | Free scrape |
| Alpaca paper API | Order execution, positions | Free |
| Google Sheets API | Journal sync | Free |
| Anthropic API | All agent reasoning + debate + evolution | ~$15-25/mo |
| MarketXLS Advanced | Real-time Greeks | NOT subscribed — pre-live ($94/mo) |
| Unusual Whales | Real sweep detection | NOT subscribed — pre-live ($48/mo) |

---

## Monthly Cost

| Item | Now | At Live |
|---|---|---|
| Claude API | ~$15-25 | ~$25-40 |
| VPS | $12 | $12 |
| Data | $0 | $0 |
| MarketXLS Advanced | — | $94 |
| Unusual Whales | — | $48 |
| **Total** | **~$27-37** | **~$179-194** |

---

## Key File Paths

```
/root/quantai-v2/
  workspace-{orchestrator,research,infra,journal}/
    SOUL.md + AGENTS.md          ← Agent instructions (edit here for live effect)
  shared-data/
    journal/paper/trades.jsonl   ← All paper trades (source of truth)
    cache/
      market_intelligence.json   ← Intelligence packet
      debate_output.json         ← Last debate results
      daily_state.json           ← Entries today counter
    logs/
      pipeline.log               ← All cron pipeline runs
      debate_log.jsonl           ← All debate sessions
      evolution_log.jsonl        ← All evolution events
    strategies/sofi_collar.json  ← Strategy params (evolution updates this)
    google_service_account.json  ← Google Sheets auth (never commit to git)

/home/trader/QuantAI/
  v2/shared-data/scripts/        ← All Python scripts (git-tracked)
  .env                           ← All API keys
```

---

## Pre-Live Checklist

- [ ] Agent Alpha win rate ≥ 60% over 40+ closed trades
- [ ] Agent Beta win rate ≥ 65% over 40+ closed trades
- [ ] system_test.py: 43/43 consistently
- [ ] Self-evolution applied ≥ 1 validated change
- [ ] No weekly drawdown > $500 in last 4 weeks
- [ ] Subscribe MarketXLS Advanced ($94/mo)
- [ ] Subscribe Unusual Whales ($48/mo)
- [ ] Open IBKR for XSP (Section 1256 tax treatment)
- [ ] Separate live Alpaca API keys
- [ ] pip audit clean

---

## Security

- GitHub PAT: create fresh each session, delete immediately after
- google_service_account.json: never commit to git
- All API keys in /home/trader/QuantAI/.env only

---

## Starting a New Chat

> "Read SYSTEM_STATE.md. I want to [task]."

After changes: update this file → push to GitHub → re-upload to Claude project.

## Workspace Sync

After any AGENTS.md or SOUL.md update, always run:
```bash
bash /home/trader/QuantAI/scripts/sync_workspaces.sh
```
Copies all workspace files from git repo to `/root/quantai-v2/` where OpenClaw reads them. Agents pick up changes on the next message — no restart needed. If agents seem to have stale knowledge, this is the fix.

---

*Last updated: April 2, 2026 — 43/43 system test confirmed. Two execution bugs fixed: iron condors now submitted as single mleg order (fixes uncovered options error); strike selection queries Alpaca live chain before ordering (fixes asset not found). Journal clean: 1 real trade (P001 SOFI $16C Apr 18). Agents ready for first real execution tomorrow.*
