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
| Runtime | OpenClaw multi-agent framework |
| OpenClaw gateway | /root/quantai-v2/ |
| Trading mode | PAPER |

**Two directory trees — both matter:**
- `/home/trader/QuantAI/` — git repo, all Python scripts
- `/root/quantai-v2/` — OpenClaw gateway reads agent workspaces here, all data written here

When updating agent instructions, copy files to BOTH locations.

---

## How This System Works

Four Claude model instances (agents) live in Discord. They have Bash tool access and run Python scripts directly. No fixed scheduler built into agents — a VPS cron job triggers the autonomous pipeline every 15 minutes during market hours.

**Two trading streams running in parallel:**

**Stream 1 — Autonomous (Agent Alpha + Beta)**
Cron fires every 15 min → pipeline evaluates conditions → if regime/VIX/timing passes all gates → debate chamber selects trades → Alpaca paper orders placed → journal logged → Google Sheet synced → Discord notification. No human approval.

**Stream 2 — Manual (Amit)**
SOFI collar strategy + any learning trades. Ask Orchestrator for analysis → execute on Webull → log in #journal → Google Sheet synced. Amit stays in the loop by choice to learn.

---

## The Four Agents

| Agent | Channel | Model | Role |
|---|---|---|---|
| Orchestrator | #chat | Claude Sonnet | Primary interface. Runs scans, proposes trades for Amit, monitors positions, answers everything |
| Research | #research | Claude Sonnet | SOFI daily brief, credit spread report, weekly collar candidate scan |
| Infra | #infra | Claude Sonnet | System health, files, git, script maintenance, debugging |
| Journal | #journal | Claude Haiku | Trade logging (paper + real), stats, Google Sheets sync |

**Workspace files (what agents read as instructions):**
- `/root/quantai-v2/workspace-{orchestrator,research,infra,journal}/AGENTS.md + SOUL.md`

OpenClaw reads workspace files fresh on every message — no restart needed after editing.

---

## Agent Alpha — Bull Put Spreads (Autonomous)

Runs via cron. Enters when conditions support a bullish bias on any liquid ticker.

| Parameter | Value |
|---|---|
| Strategy | Bull put spreads |
| Universe | 100+ liquid tickers (dynamic scan) |
| Entry condition | RSI < 45, above 200 EMA, IV rank > 30, earnings > 14 days |
| Spread width | $5–$10 depending on ticker price |
| Min credit | $0.30 |
| Stop loss | 2x credit |
| Profit target | 50% of max profit |
| Max positions | 2 simultaneously (shared with Beta) |
| Journal source tag | `agent_alpha` |

---

## Agent Beta — Iron Condors (Autonomous)

Runs via cron. Only enters when market is range-bound and VIX supports premium selling.

| Parameter | Value |
|---|---|
| Strategy | Iron condors |
| Symbols | SPY, QQQ |
| VIX range | 13–28 (skips if outside) |
| Short delta | 0.08–0.12 |
| Wing width | $5 (widens to $7 in caution regime) |
| Min credit | $0.50 |
| Stop loss | 2x credit |
| Profit target | 50% of max profit |
| Hard close | 3:30 PM ET |
| Journal source tag | `agent_beta` |

---

## Autonomous Pipeline (Cron-Triggered)

Crontab entry (runs as root):
```
*/15 9-16 * * 1-5  cd /home/trader/QuantAI && python3 v2/shared-data/scripts/run_pipeline.py >> /root/quantai-v2/shared-data/logs/pipeline.log 2>&1
5 16 * * 1-5  cd /home/trader/QuantAI && python3 v2/shared-data/scripts/run_pipeline.py eod >> /root/quantai-v2/shared-data/logs/pipeline.log 2>&1
```

**Decision logic every 15 min:**
1. Market open? (9:30–4:00 PM ET weekdays) → else skip
2. Opening volatility window? (9:30–9:45) → wait, no entry
3. Past entry cutoff? (3:00 PM) → monitor only, no new entries
4. Past hard close? (3:30 PM) → hard close all agent positions
5. VIX ≥ 35 or regime = halt → no entry
6. 2 entries already today → monitor only
7. All clear → run intelligence → scan → debate → execute

**Max 2 entries per day.** State tracked in `cache/daily_state.json`.

---

## Amit's Manual Trading

### SOFI Collar — paper, 200 shares
Primary learning strategy. Defined max loss regardless of downside.

| Parameter | Value |
|---|---|
| Shares | 200 paper (target: 1,000 long-term) |
| Entry | ~$15 |
| Call strike | $16, sell biweekly |
| Put strike | $12, buy monthly |
| Net income target | $170/month |
| Max loss | $600 |

5 pre-decided trigger actions — no improvising:

| SOFI Price | Action |
|---|---|
| $15.70 | MONITOR |
| $16.00 | ROLL call to $18, 2 weeks out |
| Called away | ACCEPT profit, rebuy on dip |
| $12.50 | MONITOR, assess conviction |
| $12.00 | EXERCISE put OR roll to $10 OR exit |

Full params: `/root/quantai-v2/shared-data/strategies/sofi_collar.json`

### Other manual trades
Amit can trade any strategy himself for learning. Log everything in #journal.
These appear in "Manual Trades" tab in Google Sheet, tracked separately from agents.

---

## Guard Rules — Always Enforced

| Rule | Value |
|---|---|
| Max loss per trade | 2% of account |
| Earnings blackout | 14 days minimum |
| VIX ≥ 35 | No new trades — halt |
| No-trade windows | 9:30–9:45 AM ET, 3:45–4:00 PM ET |
| Stop loss | 2x credit received |
| Profit target | Close at 50% of max profit |
| Max open agent positions | 3 simultaneously |
| Entry cutoff | 3:00 PM ET — no new entries after |
| Hard close | 3:30 PM ET — close all 0DTE positions |

---

## Scripts

All at `/home/trader/QuantAI/v2/shared-data/scripts/`
All data reads/writes go to `/root/quantai-v2/shared-data/`

| Script | What it does |
|---|---|
| `run_pipeline.py` | Master pipeline — runs every 15 min via cron. Decides entry/monitor/eod based on time and conditions |
| `market_intelligence.py` | Intelligence packet: VIX, technicals, events, earnings, news, regime. Auto-skips if <30 min old |
| `debate_chamber.py` | Bull/Bear/Judge debate → top 2 trade proposals for any valid strategy |
| `autonomous_execution.py` | Places Alpaca paper orders, logs fills as agent_alpha or agent_beta, syncs sheets |
| `scan_options.py` | Dynamic scanner across 100+ tickers — credit spreads + collar candidates |
| `self_evolution.py` | EOD config evolution. Pass today's score. 5-gate validation before any change |
| `sheets_sync.py` | Syncs trades.jsonl to Google Sheet (4 tabs) |
| `pattern_engine.py` | Statistical pattern detection — needs 20+ closed trades to activate |
| `fetch_sofi.py` | SOFI data for Research agent |

---

## Google Sheets Journal

URL: https://docs.google.com/spreadsheets/d/1GidIf-oLY9NfeRGVTwwGFYzA4eZx2bYjvY7UOATiMM0

| Tab | Contents |
|---|---|
| All Trades | Every trade, color-coded (yellow=open, green=win, red=loss) |
| Agent Trades | Alpha + Beta autonomous trades only |
| Manual Trades | Amit's SOFI collar and learning trades only |
| Summary | Live formulas: win rate, P&L, open count — per stream |

Trade IDs: `A001`, `A002`... = agent trades. `P001`, `P002`... = manual/paper trades.
Source field: `agent_alpha`, `agent_beta`, or `manual`.

Auto-syncs after every agent execution and every manual journal entry.

---

## Self-Evolution

Triggered when Amit says "score today 78/100" in #chat.
Orchestrator runs `self_evolution.py 78`.

Pipeline if score < 90:
1. Observe — reads today's journal
2. Critique — finds biggest param misalignment
3. Generate — ONE specific config change with evidence
4. Validate — 5 gates: constitution, size, drift, safety, regression
5. Apply — updates sofi_collar.json, posts to #infra
6. Consolidate (Fridays) — compresses 35 days of observations into principles

Constitution-protected (never changes via evolution): max_loss_pct ≤ 2%, min_credit ≥ $0.30, earnings_blackout ≥ 14d, delta range 0.05-0.20, VIX upper ≤ 30, stop_loss_multiplier ≤ 3x

---

## Data Sources

| Source | Data | Cost |
|---|---|---|
| yfinance | Price, technicals, fundamentals, VIX | Free |
| Finnhub | Event calendar, earnings, news | Free tier |
| Alpha Vantage | Earnings surprise history | Free (25 req/day) |
| CNN | Fear & Greed (VIX fallback) | Free scrape |
| Alpaca paper API | Order placement, position data | Free |
| Google Sheets API | Journal sync | Free |
| Anthropic API | All agent reasoning + debate + evolution | ~$15-25/mo |
| MarketXLS Advanced | Real-time Greeks, screeners | NOT subscribed — pre-live only ($94/mo) |
| Unusual Whales | Real sweep detection | NOT subscribed — pre-live only ($48/mo) |

---

## Monthly Cost

| Item | Now | At Live |
|---|---|---|
| Claude API | ~$15-25 | ~$25-40 |
| VPS Hetzner CX31 | $12 | $12 |
| Data sources | $0 | $0 |
| MarketXLS Advanced | — | $94 |
| Unusual Whales | — | $48 |
| **Total** | **~$27-37/mo** | **~$179-194/mo** |

---

## Key File Paths

```
/root/quantai-v2/
  workspace-{orchestrator,research,infra,journal}/
    SOUL.md      ← Agent personality
    AGENTS.md    ← Operating manual
  shared-data/
    journal/paper/trades.jsonl   ← All paper trades (source of truth)
    journal/real/trades.jsonl    ← Real trades (future)
    cache/
      market_intelligence.json   ← Intelligence packet
      debate_output.json         ← Last debate results
      daily_state.json           ← Entries today counter
      credit_spread_scan.json    ← Last scan
      collar_candidates.json     ← Last collar scan
    logs/
      pipeline.log               ← All pipeline runs
      debate_log.jsonl           ← All debate sessions
      evolution_log.jsonl        ← All evolution events
      evolution_observations.jsonl
    strategies/
      sofi_collar.json           ← Strategy params (evolution updates this)
    google_service_account.json  ← Google Sheets auth (never commit to git)

/home/trader/QuantAI/
  v2/shared-data/scripts/        ← All Python scripts
  .env                           ← All API keys
```

---

## Pre-Live Checklist

- [ ] Agent Alpha win rate ≥ 60% over 40+ closed trades
- [ ] Agent Beta win rate ≥ 65% over 40+ closed trades
- [ ] Self-evolution ran ≥ 4 weeks, ≥ 1 validated change applied
- [ ] No single week drawdown > $500 on paper in last 4 weeks
- [ ] Subscribe MarketXLS Advanced ($94/mo)
- [ ] Subscribe Unusual Whales ($48/mo)
- [ ] Open IBKR for XSP (Section 1256, 60/40 tax)
- [ ] Separate live Alpaca API keys
- [ ] Emergency stop tested end-to-end

---

## Security

- GitHub PAT: create fresh each session, delete immediately after. Never reuse.
- google_service_account.json: never commit to git (in .gitignore)
- All API keys in /home/trader/QuantAI/.env only
- New open-source tools: >500 stars, manual review, pin versions

---

## Starting a New Chat

> "Read SYSTEM_STATE.md. I want to [task]."

After significant changes: update this file → push to GitHub → re-upload to Claude project.

*Last updated: April 1, 2026 — Autonomous execution live. Agent Alpha (bull put spreads) and Agent Beta (iron condors) running via 15-min cron. Google Sheets journal live. Self-evolution pipeline ready.*
