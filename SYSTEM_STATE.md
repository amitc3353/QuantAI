# QuantAI — System State
**Last updated: March 21, 2026 | Auto-update this file after every significant change**

This is the single source of truth for the current system. When starting a new chat,
read this file first. It reflects actual deployed code, not aspirational plans.

---

## Infrastructure

| Component | Value |
|---|---|
| VPS | Hetzner CX31 · 87.99.141.55 |
| OS | Ubuntu 24 |
| Repo | github.com/amitc3353/QuantAI |
| Runtime | Docker Compose · project name: `quantai` |
| Trading mode | **PAPER** (Alpaca paper account) |
| Auto mode | **ON** (no human approval required) |

**4 running containers:**
- `trader-discord` — Discord bot, all slash commands, chat agent
- `trader-guards` — Guard engine FastAPI on port 8100
- `trader-orchestrator` — APScheduler, all agents, all automation
- `trader-alpaca` — Alpaca MCP server

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
| Max drawdown before pause | $4,000 ($20k → $16k) |

---

## Agent 1 — Iron Condor Bot

**Strategy:** Sell 0DTE iron condors on SPY (fallback: QQQ)
**Status:** ACTIVE · autonomous · paper mode
**Config:** `configs/agent1_params.json` (v2)

| Parameter | Value |
|---|---|
| Primary symbol | SPY |
| Short delta | 0.10 |
| Wing width | $5.00 |
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
- Entry 2: 11:30 AM ET (only if Entry 1 is ≥40% profit)

**Pre-entry checks (in order):**
1. Context score ≥ 40 (from `context_builder.py`)
2. Journal consultation via Haiku — skip/caution/proceed
3. VIX within range
4. Guard engine approval
5. Max daily trades not reached

**Exit rules:**
- 50% profit → auto-close
- 2x credit stop → auto-close
- 3:30 PM → hard close regardless

**Journals:** `data/memory/paper/agent1_journal.jsonl`
**Lessons:** `data/memory/paper/agent1_lessons.jsonl`
**EOD scores:** `data/journal/agent1_score_YYYY-MM-DD.json`

---

## Agent 2 — Covered Call Bot

**Strategy:** Sell covered calls on 6 portfolio holdings
**Status:** ACTIVE · autonomous · paper mode · Monday weekly scan
**Config:** `configs/agent2_params.json` (v2)

| Parameter | Value |
|---|---|
| Symbols | PLTR, TSM, MU, AMD, AVGO, ASML |
| Target delta | 0.20 |
| Delta range | 0.10 – 0.25 |
| DTE range | 21 – 35 days |
| Min IV rank | 30 |
| Profit target | 50% |
| Roll trigger | Stock >2% through strike |
| Max rolls | 3 per position |
| Hard close | 2 DTE remaining |
| Simulated shares | 10 per ticker |
| Monthly yield target | 1%+ (~$100/month) |
| Earnings blackout | 14 days |

**Entry schedule:** Monday 10:00 AM ET (weekly scan)

**Pre-entry checks (in order):**
1. Context score ≥ 40
2. Journal consultation — per-ticker vetoes
3. Dark pool flow scan (skip tickers with 3x+ volume spike)
4. Earnings blackout check (yfinance calendar)
5. IV rank ≥ 30
6. Guard engine approval

**Roll logic:** When stock rallies >2% through short strike:
- Buy back current call at ask
- Find new call: higher strike (delta ≤0.25) + 30 DTE extension
- Only execute if net credit ≥ $0.05
- Max 3 rolls per position

**Journals:** `data/memory/paper/agent2_journal.jsonl`

---

## Intelligence Layer — Context Score (0–100)

**File:** `services/context_builder.py`
**Runs:** 6:30 AM (morning brief) + 9:45 AM (pre-entry 1) + 11:25 AM (pre-entry 2) + Monday 9:50 AM
**Cache:** 20 minutes

| Signal | Weight | Source |
|---|---|---|
| VIX regime | 25 pts | yfinance `^VIX` |
| Event calendar | 15 pts | Finnhub / yfinance earnings |
| Macro regime | 20 pts | FRED API |
| Sentiment | 20 pts | CBOE put/call + CNN Fear&Greed |
| Flow | 20 pts | Alpaca Vol/OI + volume proxy |

**Decisions:**
- Score ≥ 60 → PROCEED (standard params)
- Score 40–59 → CAUTION (Agent 1: wings +$2, Agent 2: min delta 0.15)
- Score < 40 → SKIP (post reason to Discord)

**Hard overrides:** FOMC/CPI day OR VIX ≥ 35 → always skip regardless of score

**Weights auto-tune:** After 4 weeks of data, `correlation_analyzer.py` adjusts
weights based on which signals actually predicted outcomes.
Saved to: `configs/context_weights.json`

---

## Data Sources

| Source | Data | Cost | Key/Auth | Notes |
|---|---|---|---|---|
| yfinance | VIX, stock prices, IV rank, options chain | Free | None | 15-min delayed |
| Alpaca | Options chain with Greeks | Free | `ALPACA_API_KEY` | Paper account |
| FRED API | Fed rate, yield curve, CPI, unemployment | Free | None | Daily, cached 6h |
| Finnhub | Earnings calendar, FOMC dates, news | Free tier | `FINNHUB_API_KEY` | 60 calls/min |
| CBOE | Put/call ratio | Free scrape | None | Fragile — falls back to yfinance |
| CNN | Fear & Greed index | Free scrape | None | Falls back to VIX proxy |
| Alpha Vantage | Stock quotes for morning brief | Free tier | `ALPHA_VANTAGE_API_KEY` | 25 req/day |

**Known data limitations:**
- Flow detection is 15-min delayed (Alpaca free tier)
- Dark pool proxy ~60% accuracy (volume spike heuristic)
- IV rank uses HV proxy, not true IV (no options data cost)
- CBOE scraper is fragile — HTML structure can change
- **Upgrade path before live trading:** Polygon.io ($29/mo) + Unusual Whales ($30/mo)

---

## Scheduler — All Automated Jobs (11 total)

| Time (ET) | Job | Days |
|---|---|---|
| 6:30 AM | Morning brief + context score + auto-proposals | Mon-Fri |
| 8:00 AM | Daily digest → #system-health | Mon-Fri |
| 9:45 AM | Pre-entry context build | Mon-Fri |
| 9:50 AM | Agent 1: Entry 1 | Mon-Fri |
| 10:00 AM | Agent 2: Weekly CC scan | Monday only |
| 11:25 AM | Pre-entry 2 context build | Mon-Fri |
| 11:30 AM | Agent 1: Entry 2 (conditional) | Mon-Fri |
| Every 5 min | Agent 1: Position monitor + health check | Market hours |
| 4:30 PM | EOD scoring + lessons + self-improve | Mon-Fri |
| 4:30 PM | Agent 1: EOD score | Mon-Fri |
| 4:45 PM | Weekly review + correlation analysis | Friday |
| 4:45 PM | Agent 2: Weekly score | Friday |

---

## Discord Channels & Commands

| Channel | Purpose |
|---|---|
| #command | Slash commands |
| #chat | Natural language with memory context |
| #research | Morning brief, context score card |
| #trade-proposals | Trade cards, guard rejections, journal vetoes |
| #execution-log | Fills, closes, rolls |
| #system-health | Health checks, EOD scores, digests, weekly reviews |
| #guard-log | Guard approvals/rejections |
| #pr-updates | Self-improvement PRs |

**Key commands:**
- `/journal` — today's trades + P&L + lessons
- `/performance [days]` — win rates, P&L, context correlation
- `/lessons [query]` — browse/search all lessons
- `/health` — full system health check
- `/deploy` — git pull + build + restart (no SSH needed)
- `/emergency_stop` — halt all new trades
- `/rules` — view current guard rules
- `/greeks`, `/bull_put`, `/iron_condor` — options analysis

---

## Self-Improvement Loop

**Trigger:** EOD score < 90

**Flow:**
1. Claude (Haiku) scores day's trades → extracts structured lessons
2. `backtester.py` validates any proposed param changes against 30 days of journal data
3. If backtest passes → GitHub PR created via API
4. If backtest fails → PR discarded, reason posted to Discord
5. You review and merge (or reject) the PR

**Lesson format** (structured for searchability):
```
LESSON: [what happened]. WHEN: [exact condition]. ACTION: [what to do].
```

**Param change gate:** New params must not reduce win rate by more than 5pp
on historical data. Protects against overfitting.

**Weekly correlation analysis (Friday 4:45 PM):**
- Measures: did high-score (≥60) days win more than low-score days?
- Per-signal: which of the 5 signals actually predicts outcomes?
- After 4 weeks: auto-adjusts signal weights in context_builder
- Requires `MIN_TRADES_FOR_ANALYSIS = 10` before drawing conclusions

---

## Journal System

**What's logged per trade:**
- Event type (entry/exit/skip/roll/error/guard_reject/journal_veto)
- Symbol, strategy, strikes, credit/premium
- VIX at entry, IV rank, context score, which signals fired
- Market regime snapshot
- Which lessons were consulted (lessons_applied)
- Outcome, P&L, close reason

**How journals feed back into decisions:**
1. **Pre-entry:** Agent loads relevant lessons → Haiku call → skip/caution/proceed
2. **Backtester:** Validates param changes against journal history
3. **Correlation analyzer:** Measures signal predictiveness from journal data
4. **Weekly review:** Claude reads full week's journal for strategic adjustments
5. **#chat agent:** `build_context()` injects recent trades + lessons into every conversation

---

## File Structure

```
QuantAI/
├── configs/
│   ├── agent1_params.json      ← Agent 1 strategy params (self-improve can edit)
│   ├── agent2_params.json      ← Agent 2 strategy params
│   ├── context_weights.json    ← Signal weights (auto-tuned by correlation_analyzer)
│   ├── guard_config.json       ← All trading rules (deterministic, never bypassed)
│   ├── strategies.json         ← Strategy definitions and agent metadata
│   └── watchlist.json          ← Trading symbols with sector classification
│
├── services/                   ← Shared intelligence layer (mounted into orchestrator)
│   ├── market_data.py          ← VIX, options chain, IV rank, strike finder
│   ├── macro_data.py           ← FRED + Finnhub macro/events
│   ├── sentiment_data.py       ← Put/call ratio, Fear&Greed, VIX term structure
│   ├── flow_detector.py        ← Vol/OI unusual activity + dark pool proxy
│   ├── context_builder.py      ← Pre-trade score 0-100 (assembles all signals)
│   ├── backtester.py           ← Validates param changes before PR creation
│   ├── correlation_analyzer.py ← Measures context score predictiveness, tunes weights
│   └── health_monitor.py       ← All health checks, silent failure detection
│
├── orchestrator/
│   ├── scheduler.py            ← All 11 cron jobs, agent wiring
│   ├── agent1_iron_condor.py   ← Agent 1 full logic
│   ├── agent2_covered_call.py  ← Agent 2 full logic + roll logic
│   └── self_improve.py         ← PR generation, backtest gate, weekly review
│
├── discord-bot/
│   ├── memory.py               ← Persistent memory (trades, lessons, decisions, events)
│   └── cogs/
│       ├── trading.py          ← /buy /sell /journal /performance /lessons
│       ├── infra_agent.py      ← /health /deploy /logs /restart /git
│       ├── chat_agent.py       ← #chat natural language with memory context
│       └── options_analysis.py ← /greeks /bull_put /iron_condor /covered_call
│
├── guard-engine/
│   └── guards.py               ← Deterministic FastAPI rule engine (16 rules, 44 tests)
│
└── data/
    ├── memory/paper/           ← agent1_journal.jsonl, agent2_journal.jsonl,
    │                              agent1_lessons.jsonl, agent2_lessons.jsonl,
    │                              agent2_positions.json
    ├── memory/shared/          ← lessons.jsonl, decisions.jsonl, system_events.jsonl
    ├── journal/                ← EOD scores, backtest results, correlation reports
    └── cache/                  ← VIX, macro, sentiment, options chain (15-60 min TTL)
```

---

## Known Technical Debt & Upgrade Path

### Before live trading (non-negotiable):
- [ ] Replace `flow_detector.py` with Polygon.io ($29/mo) for real-time flow
- [ ] Add Unusual Whales ($30/mo) for options sweep detection
- [ ] Verify CBOE scraper is working (fragile HTML scrape)
- [ ] Run correlation analysis for 4+ weeks to validate context score
- [ ] Confirm Agent 1 win rate ≥ 60% over 40+ paper trades
- [ ] Test emergency stop end-to-end
- [ ] Create separate live Alpaca API keys

### Known limitations in current paper system:
- Flow detection is 15-min delayed — fine for paper, insufficient for live
- CBOE put/call scraper has no guaranteed stability
- IV rank uses HV proxy (acceptable accuracy, not true IV)
- Backtester uses mid-price as fill proxy (no slippage simulation)
- Agent 2 roll logic not yet battle-tested — monitor first rolls carefully
- NautilusTrader backtesting not yet integrated (in roadmap)

### Current monthly cost estimate:
| Item | Cost |
|---|---|
| Claude API (Haiku + Sonnet) | ~$8–15/mo |
| Finnhub free tier | $0 |
| All other data | $0 |
| Alpaca trading | $0 |
| VPS (Hetzner CX31) | ~$12/mo |
| **Total** | **~$20–27/mo** |

---

## How to Use This Document

**Starting a new chat in this project:**
> "Read SYSTEM_STATE.md. I want to [discuss X / debug Y / research Z]."

**After any significant change:**
Update this file and re-upload it to the Claude project to replace the stale version.
Also push to GitHub so the repo stays in sync.

**For research sessions:**
> "Read SYSTEM_STATE.md. Research what's new in [options flow / AI trading / open source tools]
> that could improve our system. Consider cost, complexity, and our current architecture.
> Propose only if clearly beneficial. I'll approve before any implementation."

---

*This document reflects the system as of March 21, 2026 — the day the full system was built.*
*Next update: after first week of paper trading data (March 28, 2026).*
