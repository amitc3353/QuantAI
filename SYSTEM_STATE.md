# QuantAI — System State
**Last updated: March 31, 2026 | Update this file after every significant session**

This is the single source of truth. Start every new chat with: "Read SYSTEM_STATE.md."

---

## Infrastructure

| Component | Value |
|---|---|
| VPS | Hetzner CX31 · 87.99.141.55 |
| OS | Ubuntu 24 |
| Repo | github.com/amitc3353/QuantAI |
| Branch | v2-openclaw |
| Runtime | Docker Compose · project name: `quantai` |
| Trading mode | **PAPER** |
| Auto mode | **ON** |

**5 running containers:**
- `trader-discord` — Discord bot, all slash commands, chat agent, CTO command
- `trader-guards` — Guard engine FastAPI on port 8100
- `trader-orchestrator` — APScheduler, all agents, all automation
- `trader-alpaca` — Alpaca MCP server
- `trader-cto` — CTO listener, watches cto_queue.json, runs Claude Code, posts to Discord

---

## Capital Structure

| | Amount |
|---|---|
| Total paper account | $20,000 |
| Agent 1 allocation | $10,000 |
| Agent 2 allocation | $10,000 |
| Max simultaneous at risk | $2,000 (10%) |
| Daily loss limit (all agents) | $1,000 (5%) → halt ALL |
| Weekly loss limit | $2,000 (10%) → halt + alert |
| Max drawdown before pause | $4,000 |

---

## Agent 1 — Iron Condor Bot

**Strategy:** Sell 0DTE iron condors on SPY
**Status:** ACTIVE · autonomous · paper mode
**Config:** `configs/agent1_params.json` (v2)

| Parameter | Value |
|---|---|
| Primary symbol | SPY |
| Short delta | 0.10 |
| Wing width | $5.00 (adjusted to $6 when context=CAUTION) |
| Min credit | $0.50 |
| Profit target | 50% |
| Stop loss | 2x credit |
| Hard close | 3:30 PM ET |
| VIX range | 13.0 – 30.0 |
| Max daily trades | 2 |
| Max risk/trade | $500 (1 contract) |
| Daily loss limit | $300 → pause Agent 1 |

**Entry schedule:**
- Entry 1: 9:50 AM ET Mon-Fri
- Entry 2: 11:30 AM ET (only if Entry 1 ≥40% profit)

**Pre-entry checks (in order):**
1. Context score ≥ 40 (6 signals: VIX, event, macro, sentiment, flow, GEX)
2. Journal consultation via Haiku — skip/caution/proceed
3. VIX within range (13–30)
4. Strike selection (delta-based, 0.08–0.12)
5. **Flow check** — unusual activity near chosen short strikes (vol/OI > 5x → widen; >10x or sweeps → skip)
6. Credit check (min $0.50)
7. Guard engine approval

---

## Agent 2 — Covered Call Bot

**Strategy:** Sell covered calls on 6 portfolio holdings
**Status:** ACTIVE · autonomous · paper mode · Monday weekly scan
**Config:** `configs/agent2_params.json` (v2)

| Parameter | Value |
|---|---|
| Symbols | PLTR, TSM, MU, AMD, AVGO, ASML |
| Target delta | 0.20 |
| DTE range | 21 – 35 days |
| Min IV rank | 30 |
| Profit target | 50% |
| Roll trigger | Stock >2% through strike |
| Max rolls | 3 per position |
| Hard close | 2 DTE |
| Simulated shares | 10 per ticker |
| Earnings blackout | 14 days |

---

## NEW: Debate Chamber (built Mar 31, 2026)

**Purpose:** Three-agent debate selects exactly 2 trades per session from intelligence packet.
**File:** `orchestrator/debate_chamber.py`
**Runs:** 6:25 AM ET (pre-market) and 1:35 PM ET (mid-session) Mon-Fri

**Pipeline:**
1. **Proposal Agent** (Sonnet) — reads intelligence packet, generates 3-5 trade candidates
2. **Bull Agent** (Haiku) — argues FOR each candidate (concurrent with Bear)
3. **Bear Agent** (Haiku) — argues AGAINST each candidate
4. **Judge Agent** (Sonnet) — scores all debates, selects TOP 2
5. **Guard Engine** — validates both through 16-rule guard before posting
6. Posts full debate transcript to #trade-proposals with react ✅/❌/🔄

**Output:** 2 approved trade cards posted to Discord per session (4 max per day)

---

## NEW: Market Intelligence MCP (built Mar 31, 2026)

**Purpose:** Twice-daily structured intelligence packet fed to Debate Chamber.
**File:** `services/market_intelligence_mcp.py`
**Runs:** 6:20 AM ET and 1:30 PM ET Mon-Fri

**Data sources (all aggregated per run):**

| Source | Data | Cost |
|---|---|---|
| yfinance | Price, RSI, MACD, Bollinger Bands, EMA200, ADX, fundamentals | Free |
| Finnhub | Earnings calendar, economic events (FOMC/CPI/jobs), news sentiment | Free tier |
| Alpha Vantage | Earnings surprise history | Free (25 req/day) |
| Alpaca | Live positions, P&L, Greeks on open trades | Free (paper) |
| yfinance VIX | VIX spot, VIX3M, term structure, regime classification | Free |
| CNN F&G | Fear & Greed index (falls back to VIX proxy) | Free scrape |
| yfinance TNX | 2Y/10Y treasury yields, yield curve regime | Free |

**Output:** `data/cache/market_intelligence_packet.json`

**Packet contents:**
- Macro snapshot: VIX, F&G, yields, event calendar, market regime
- Symbol snapshots: technicals + fundamentals for all 10 watchlist tickers
- Open positions: live P&L and recommended actions
- High conviction setups: pre-screened candidates ranked by conviction score
- Risk flags: HALT/WARNING/CAUTION alerts
- Data quality score: 0–100 (decrements when sources fail)

---

## NEW: Self-Evolution Engine (built Mar 31, 2026)

**Purpose:** After every EOD scoring session, proposes ONE config change if score < 90.
**File:** `orchestrator/self_evolution.py`
**Runs:** 4:35 PM ET Mon-Fri (after EOD score) | Consolidation: 4:45 PM Friday

**6-step pipeline:**
1. **OBSERVE** — extract structured lessons from today's journal (wins, losses, timing)
2. **CRITIQUE** — compare outcomes to current config, find single biggest misalignment
3. **GENERATE** — propose ONE param change, traceable to specific evidence
4. **VALIDATE** — 5 gates: Constitution → Size → Drift → Safety → Regression (backtester)
5. **APPLY** — write change to config, bump version, post diff to #pr-updates
6. **CONSOLIDATE** (weekly) — compress 4+ weeks of observations into strategy principles

**Gates:**
- Constitution: change cannot violate any iron-clad rule
- Size: max 25% change in one step
- Drift: cannot drift from core strategy thesis
- Safety: no runaway loss scenarios
- Regression: backtester.py must show no win rate degradation over 30 days

**Never changes:** max_loss_pct (≤2%), delta range (0.05–0.20), min credit ($0.30), VIX bound (≤30), earnings blackout (≥14d)

---

## Intelligence Layer — Context Score (0–100)

**File:** `services/context_builder.py`

| Signal | Weight | Source |
|---|---|---|
| VIX regime | 25 pts | yfinance `^VIX` |
| Event calendar | 15 pts | Finnhub / yfinance |
| Macro regime | 15 pts | FRED API |
| Sentiment | 15 pts | CBOE put/call + CNN Fear&Greed |
| Flow | 15 pts | Alpaca Vol/OI + volume proxy |
| GEX | 15 pts | Alpaca option chain gamma × OI |

**Decisions:** ≥60=PROCEED · 40-59=CAUTION (wings +$2) · <40=SKIP
**Hard overrides:** FOMC/CPI day OR VIX ≥ 35 → always skip
**Cross-signal checks:** macro capped at 7 when extreme_fear; composite danger floor at 55 when VIX backwardation + F&G < 20

---

## CTO Agent — Tech Intelligence

**Automated (Monday 6:00 AM):** Scans GitHub, arXiv, PyPI → ranked proposals to #research
**On-demand:** `cto: [task]` in #chat or `/cto` slash command → Claude Code runs, posts back
**Container:** `trader-cto` (restart: unless-stopped)
**Queue:** `data/cto_queue.json`

---

## Scheduler — 18 Jobs (all times ET)

| Time | Job | Days |
|---|---|---|
| 6:00 AM | CTO Tech Intelligence Scan | Monday |
| 6:20 AM | **Market Intelligence — Pre-Market** | Mon-Fri |
| 6:25 AM | **Debate Chamber — Pre-Market (2 trades)** | Mon-Fri |
| 6:30 AM | Morning Brief + Context + Auto-Proposals | Mon-Fri |
| 8:00 AM | Daily Digest → #system-health | Mon-Fri |
| 9:45 AM | Pre-entry context build | Mon-Fri |
| 9:50 AM | Agent 1: Entry 1 | Mon-Fri |
| 10:00 AM | Agent 2: Weekly CC Scan | Monday |
| 11:25 AM | Pre-entry 2 context build | Mon-Fri |
| 11:30 AM | Agent 1: Entry 2 (conditional) | Mon-Fri |
| 1:30 PM | **Market Intelligence — Mid-Session** | Mon-Fri |
| 1:35 PM | **Debate Chamber — Mid-Session (2 trades)** | Mon-Fri |
| Every 5 min | Agent 1 Monitor + Health Check | Market hours |
| 4:30 PM | EOD Scoring + Lessons | Mon-Fri |
| 4:30 PM | Agent 1 EOD Score | Mon-Fri |
| 4:35 PM | **Self-Evolution Engine** | Mon-Fri |
| 4:45 PM | Weekly Review + Correlation + CTO Report | Friday |
| 4:45 PM | **Weekly Strategy Consolidation** | Friday |
| 4:45 PM | Agent 2 Weekly Score | Friday |

---

## Data Sources

| Source | Data | Cost | Notes |
|---|---|---|---|
| yfinance | VIX, prices, IV rank, technicals, fundamentals | Free | 15-min delayed |
| Alpaca | Options chain, positions, Greeks, execution | Free | Paper account |
| FRED API | Fed rate, yield curve, CPI | Free | Daily, cached 6h |
| Finnhub | Earnings calendar, events, news | Free tier | 60 calls/min |
| CBOE | Put/call ratio | Free scrape | Falls back to proxy |
| CNN | Fear & Greed | Free scrape | Falls back to VIX proxy |
| Alpha Vantage | Earnings surprises, quotes | Free tier | 25 req/day |
| MarketXLS MCP | **PLANNED** — technicals, fundamentals, Greeks, screeners | $56–94/mo | Integrate before live |

---

## Discord Channels & Commands

| Channel | Purpose |
|---|---|
| #command | Slash commands |
| #chat | Natural language + `cto: [task]` |
| #research | Morning brief, CTO scan, intelligence packet summary, debate summaries |
| #trade-proposals | Trade cards from Debate Chamber + Agent 1/2 auto-proposals. React ✅/❌/🔄 |
| #execution-log | Fills, closes, rolls |
| #system-health | Health checks, EOD scores, digests, evolution events |
| #guard-log | Guard approvals/rejections |
| #pr-updates | Self-evolution changes + rejected proposals with gate results |

**Key commands:**
- `cto: [task]` — ask CTO anything in #chat
- `/journal` — today's trades + P&L + lessons
- `/performance [days]` — win rates, P&L, context correlation
- `/lessons [query]` — browse/search all lessons
- `/health` — system health check
- `/deploy` — git pull + no-cache build + restart
- `/emergency_stop` — halt all new trades

---

## File Structure

```
configs/
  agent1_params.json      ← v2, self-evolution can edit (with gate approval)
  agent2_params.json      ← v2
  guard_config.json       ← APPROVAL REQUIRED to change
  context_weights.json    ← auto-tuned by correlation_analyzer
  watchlist.json          ← symbols + sector classification

services/
  market_data.py          ← VIX, options chain, IV rank, strike finder
  market_intelligence_mcp.py ← ★ NEW: twice-daily intelligence packet
  macro_data.py           ← FRED + Finnhub
  sentiment_data.py       ← PCR, Fear&Greed, VIX term structure
  flow_detector.py        ← Vol/OI + dark pool proxy
  gex_engine.py           ← Gamma exposure approximation (6th context signal)
  context_builder.py      ← Pre-trade score 0-100 (6 signals + cross-checks)
  backtester.py           ← Param change validator (used by self-evolution)
  correlation_analyzer.py ← Signal weight auto-tuner
  health_monitor.py       ← All health checks
  cto_agent.py            ← Tech Intelligence (GitHub/arXiv/PyPI scanner)
  cto_report.py           ← Weekly CTO reliability report

orchestrator/
  scheduler.py            ← All 18 cron jobs
  scheduler_additions.py  ← ★ NEW: paste these 6 jobs into scheduler.py
  agent1_iron_condor.py   ← Agent 1 full logic
  agent2_covered_call.py  ← Agent 2 + roll logic
  debate_chamber.py       ← ★ NEW: 3-agent Bull/Bear/Judge debate
  self_evolution.py       ← ★ NEW: 6-step evolution pipeline
  self_improve.py         ← PR generation + backtest gate (legacy)

discord-bot/
  memory.py               ← Persistent memory
  cogs/
    trading.py            ← /buy /sell /journal /performance /lessons
    infra_agent.py        ← /health /deploy /logs /restart /cto
    chat_agent.py         ← #chat + cto: trigger
    options_analysis.py   ← /greeks /bull_put /iron_condor

guard-engine/
  guards.py               ← 16 rules, 44 tests

scripts/
  cto_listener.py         ← Dockerized CTO task runner (trader-cto container)

data/
  memory/paper/           ← agent1_journal.jsonl, agent2_journal.jsonl
  memory/shared/          ← lessons.jsonl, decisions.jsonl,
                             debate_log.jsonl, evolution_observations.jsonl,
                             evolution_log.jsonl, strategy_principles.jsonl
  cache/                  ← market_intelligence_packet.json + market data
  journal/                ← EOD scores, backtest results, CTO reports
  cto_queue.json
  logs/
```

---

## Monthly Cost

| Item | Cost |
|---|---|
| Claude API (Haiku + Sonnet + Code) | ~$15–25/mo |
| VPS (Hetzner CX31) | ~$12/mo |
| All data sources | $0 |
| MarketXLS (PLANNED, before live) | $56–94/mo |
| **Total now** | **~$27–37/mo** |
| **Total pre-live** | **~$83–131/mo** |

---

## Pre-Live Checklist

- [ ] Debate Chamber running ≥ 4 weeks with logged debate history
- [ ] Self-Evolution applied ≥ 1 successful change with backtest validation
- [ ] MarketXLS MCP integrated (Advanced plan for real-time Greeks)
- [ ] Agent 1 win rate ≥ 60% over 40+ paper trades
- [ ] Context score correlation confirmed (high-score days outperform low-score days)
- [ ] Agent 2 monthly yield ≥ 1% average over 8 weeks
- [ ] No single week drawdown > $500 on paper in last 4 weeks
- [ ] Guard engine emergency stop tested end-to-end
- [ ] Unusual Whales ($48-50/mo) integrated for live sweep detection
- [ ] Separate live Alpaca API keys created (never same as paper)
- [ ] pip audit clean before live transition
- [ ] GitHub PAT rotated

---

## Security Rules

- Manual review before any open-source integration (>500 stars threshold)
- Pin all versions (== not >=) after vetting
- No auto-install from agent suggestions
- pip audit before live trading transition
- Rotate GitHub PAT after every session where it appears in chat

---

## How to Use This Document

**Starting a new chat:**
> "Read SYSTEM_STATE.md. I want to [task]."

**After significant changes:**
1. Push updated SYSTEM_STATE.md to GitHub
2. Download from repo and re-upload to this Claude project

**For CTO tasks:**
> In #chat: `cto: [what you want to investigate or fix]`

---

*Last updated: March 31, 2026 — Debate Chamber built, Market Intelligence MCP built, Self-Evolution Engine built, scheduler additions documented, all three docs updated*
*Next update: after first week of paper trading with debate chamber active*
