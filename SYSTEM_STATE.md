# QuantAI — System State
**Last updated: March 23, 2026 | Update this file after every significant session**

This is the single source of truth. Start every new chat with: "Read SYSTEM_STATE.md."

---

## Infrastructure

| Component | Value |
|---|---|
| VPS | Hetzner CX31 · 87.99.141.55 |
| OS | Ubuntu 24 |
| Repo | github.com/amitc3353/QuantAI |
| Runtime | Docker Compose · project name: `quantai` |
| Trading mode | **PAPER** |
| Auto mode | **ON** |

**4 running containers:**
- `trader-discord` — Discord bot, all slash commands, chat agent, CTO command
- `trader-guards` — Guard engine FastAPI on port 8100
- `trader-orchestrator` — APScheduler, all agents, all automation
- `trader-alpaca` — Alpaca MCP server

**Host-side process:**
- `cto_listener.sh` — runs on VPS host (PID in /tmp/cto_listener.pid), watches
  `/home/trader/QuantAI/data/cto_queue.json` for tasks, executes Claude Code,
  posts results to Discord. Auto-starts via crontab @reboot.

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
1. Context score ≥ 40
2. Journal consultation via Haiku — skip/caution/proceed
3. VIX within range
4. Guard engine approval

**Known issue fixed:** Was switching to QQQ when SPY IV rank was low.
Removed — IV rank doesn't gate 0DTE entries (VIX handles volatility).
Added 1DTE fallback when 0DTE chain is empty.

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

## Intelligence Layer — Context Score (0–100)

**File:** `services/context_builder.py`

| Signal | Weight | Source |
|---|---|---|
| VIX regime | 25 pts | yfinance `^VIX` |
| Event calendar | 15 pts | Finnhub / yfinance |
| Macro regime | 20 pts | FRED API |
| Sentiment | 20 pts | CBOE put/call + CNN Fear&Greed |
| Flow | 20 pts | Alpaca Vol/OI + volume proxy |

**Decisions:** ≥60=PROCEED · 40-59=CAUTION (wings +$2) · <40=SKIP
**Hard overrides:** FOMC/CPI day OR VIX ≥ 35 → always skip
**Auto-tune:** After 4 weeks, correlation_analyzer adjusts weights

---

## CTO Agent — Tech Intelligence

**Two modes:**

**1. Automated (Monday 6:00 AM):**
- `services/cto_agent.py` scans GitHub, arXiv, PyPI
- Posts ranked proposals to #research
- Security assessment on every proposal
- No auto-install ever

**2. On-demand (anytime):**
- Type `cto: [task]` in #chat → Claude Code investigates
- Or use `/cto [task]` slash command in #command
- Claude Code reads CLAUDE.md, actual files, live logs
- Posts result back to #chat (via DISCORD_WEBHOOK_CHAT)
- Host listener: `scripts/cto_listener.sh` (must be running)
- Queue file: `data/cto_queue.json`

**CTO can do autonomously:** bug fixes, log investigation, code reading,
GitHub/URL scanning, security checks, syntax validation

**CTO needs approval:** guard rule changes, param changes, new installs,
docker-compose changes, anything touching .env

---

## Data Sources

| Source | Data | Cost | Notes |
|---|---|---|---|
| yfinance | VIX, prices, IV rank | Free | 15-min delayed |
| Alpaca | Options chain with Greeks | Free | Paper account |
| FRED API | Fed rate, yield curve, CPI | Free | Daily, cached 6h |
| Finnhub | Earnings calendar, news | Free tier | 60 calls/min |
| CBOE | Put/call ratio | Free scrape | Fragile — falls back |
| CNN | Fear & Greed | Free scrape | Falls back to VIX proxy |
| Alpha Vantage | Morning brief quotes | Free tier | 25 req/day |

---

## Scheduler — 12 Jobs (all times ET)

| Time | Job | Days |
|---|---|---|
| 6:00 AM | CTO Tech Intelligence Scan | Monday |
| 6:30 AM | Morning Brief + Context + Auto-Proposals | Mon-Fri |
| 8:00 AM | Daily Digest → #system-health | Mon-Fri |
| 9:45 AM | Pre-entry context build | Mon-Fri |
| 9:50 AM | Agent 1: Entry 1 | Mon-Fri |
| 10:00 AM | Agent 2: Weekly CC Scan | Monday |
| 11:25 AM | Pre-entry 2 context build | Mon-Fri |
| 11:30 AM | Agent 1: Entry 2 (conditional) | Mon-Fri |
| Every 5 min | Agent 1 Monitor + Health Check | Market hours |
| 4:30 PM | EOD Scoring + Lessons + Self-Improve | Mon-Fri |
| 4:30 PM | Agent 1 EOD Score | Mon-Fri |
| 4:45 PM | Weekly Review + Correlation + CTO Report | Friday |
| 4:45 PM | Agent 2 Weekly Score | Friday |

---

## Discord Channels & Commands

| Channel | Purpose |
|---|---|
| #command | Slash commands |
| #chat | Natural language + `cto: [task]` |
| #research | Morning brief, CTO scan, context score |
| #trade-proposals | Trade cards, journal vetoes |
| #execution-log | Fills, closes, rolls |
| #system-health | Health checks, EOD scores, digests, CTO reports |
| #guard-log | Guard approvals/rejections |
| #pr-updates | Self-improvement PRs |

**Key commands:**
- `cto: [task]` — ask CTO anything in #chat (posts back to #chat)
- `/cto [task]` — same but via slash command
- `/journal` — today's trades + P&L + lessons
- `/performance [days]` — win rates, P&L, context correlation
- `/lessons [query]` — browse/search all lessons
- `/health` — system health check
- `/deploy` — git pull + no-cache build + restart (always --no-cache now)
- `/emergency_stop` — halt all new trades

---

## Self-Improvement Loop

**Trigger:** EOD score < 90

**Flow:**
1. Haiku scores trades → extracts LESSON/WHEN/ACTION structured lessons
2. `backtester.py` validates param changes against 30 days journal
3. If backtest passes → GitHub PR created
4. If backtest fails → PR discarded, reason posted to Discord

**Weekly correlation (Friday 4:45 PM):**
- Measures: high-score days vs low-score days win rate
- Per-signal accuracy analysis
- Auto-adjusts weights after 4 weeks

---

## Journal System

**What's logged:** event type, symbol, strikes, credit, VIX, IV rank,
context score + components, market regime, lessons applied, outcome, P&L

**How journals teach agents:**
1. Pre-entry: Haiku reads lessons → skip/caution/proceed
2. Backtester: validates params against history
3. Correlation: measures signal predictiveness
4. Weekly review: strategic adjustments
5. #chat: injects recent trades + lessons into every conversation

---

## File Structure

```
configs/
  agent1_params.json      ← v2, self-improve can edit
  agent2_params.json      ← v2
  guard_config.json       ← APPROVAL REQUIRED to change
  context_weights.json    ← auto-tuned by correlation_analyzer
  watchlist.json          ← symbols + sector classification

services/                 ← mounted read-only into orchestrator
  market_data.py          ← VIX, options chain, IV rank, strike finder
  macro_data.py           ← FRED + Finnhub
  sentiment_data.py       ← PCR, Fear&Greed, VIX term structure
  flow_detector.py        ← Vol/OI + dark pool proxy
  context_builder.py      ← Pre-trade score 0-100
  backtester.py           ← Param change validator
  correlation_analyzer.py ← Signal weight auto-tuner
  health_monitor.py       ← All health checks
  cto_agent.py            ← Tech Intelligence (GitHub/arXiv/PyPI scanner)
  cto_report.py           ← Weekly CTO reliability report

orchestrator/
  scheduler.py            ← All 12 cron jobs
  agent1_iron_condor.py   ← Agent 1 full logic
  agent2_covered_call.py  ← Agent 2 + roll logic
  self_improve.py         ← PR generation + backtest gate

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
  cto_listener.sh         ← HOST process, watches cto_queue.json,
                             runs Claude Code, posts to Discord

data/
  memory/paper/           ← agent1_journal.jsonl, agent2_journal.jsonl
  memory/shared/          ← lessons.jsonl, decisions.jsonl
  journal/                ← EOD scores, backtest results, CTO reports
  cache/                  ← market data (15-60 min TTL)
  cto_queue.json          ← CTO task queue (container writes, host reads)
  logs/                   ← cto_TIMESTAMP.log files
```

---

## Known Technical Debt & Pre-Live Checklist

- [ ] Market Intelligence Agent (daily news synthesis, 6th context signal)
- [ ] Pattern & Learning Agent (statistical pattern detection)
- [ ] Replace flow_detector.py with Polygon.io ($29/mo) before live
- [ ] Add Unusual Whales ($30/mo) before live
- [ ] Verify CBOE scraper stability
- [ ] 4+ weeks correlation analysis to validate context score
- [ ] Agent 1 win rate ≥ 60% over 40+ paper trades
- [ ] pip audit before live trading
- [ ] Separate live Alpaca API keys

---

## Monthly Cost

| Item | Cost |
|---|---|
| Claude API (Haiku + Sonnet + Code) | ~$10–20/mo |
| VPS (Hetzner CX31) | ~$12/mo |
| All data sources | $0 |
| **Total** | **~$22–32/mo** |

---

## Security Rules

- Manual review before any open-source integration
- Pin all versions (== not >=) after vetting
- No auto-install from agent suggestions
- New integrations in services/ only (read-only mount)
- pip audit before live trading transition
- Rotate GitHub PAT after every session where it appears in chat

## Trade Proposal Rules (all agents)

Every proposed trade MUST pass guard /check, include max_loss_pct,
include thesis + invalidation condition, paper only until validated.

---

## How to Use This Document

**Starting a new chat:**
> "Read SYSTEM_STATE.md. I want to [task]."

**After significant changes:**
1. Push updated SYSTEM_STATE.md to GitHub
2. Download from repo and re-upload to this Claude project

**For research/improvement sessions:**
> "Read SYSTEM_STATE.md. Research what's new in [topic] that could
> improve our system. Consider cost, complexity, security rules.
> Propose only if clearly beneficial."

**For CTO tasks:**
> In #chat: `cto: [what you want to investigate or fix]`

---

*Last updated: March 23, 2026 — CTO agent operational, /deploy --no-cache fixed*
*Next update: after first week of paper trading data (March 28, 2026)*
