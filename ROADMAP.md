# QuantAI — Master Roadmap

## Two Tracks, Running in Parallel

### Track 1: Options Trading Execution (YOUR focus)
You learn, you review, you make decisions. The bot executes.

### Track 2: Platform Development (AGENT focus)
Dev agents build, enhance, integrate. Runs 24/7 without you.

---

## Track 1: Options Trading Execution

### Phase A: First Strategy — SPY 0DTE Iron Condors (Week 1-2)
**Goal:** Paper trade 2 iron condors/day, learn the mechanics, get consistent.

- [x] Guard engine with all trading rules
- [x] Alpaca paper trading connected
- [x] Greeks engine (py_vollib)
- [x] Options analysis commands (/greeks, /bull_put, /iron_condor)
- [x] Morning brief automation
- [x] **Autonomous income bot** — Agent 1 auto-trades 2 SPY iron condors/day ✅ Mar 21, 2026
  - [x] Market data scanner (SPY price, VIX, options chain from Alpaca) ✅ Mar 21, 2026
  - [x] Strike selection engine (delta-based, 0.08-0.12 range) ✅ Mar 21, 2026
  - [x] Auto-entry at 9:50 AM and 11:30 AM ET ✅ Mar 21, 2026
  - [x] Monitoring: 50% profit close, 2x credit stop-loss, 3:30 PM hard close ✅ Mar 21, 2026
  - [x] Trade card posted to #trade-proposals with full reasoning ✅ Mar 21, 2026
  - [x] EOD trade review posted to #system-health ✅ Mar 21, 2026
- [ ] Set Alpaca paper account to $10k starting capital
- [ ] First live paper trade (Monday)
- [ ] End of Week 1 review — what worked, what didn't
- [ ] Tune strike selection based on Week 1 data
- [ ] End of Week 2 review — is win rate above 65%?

### Phase B: Add Weekly Layer (Week 3-4)
**Goal:** Add 5-7 DTE put spreads alongside daily iron condors.

- [ ] Weekly put spread scanner (Monday entry, Friday close)
- [ ] Delta 0.10 short strike, $5 wide
- [ ] Separate risk allocation (max 15% of account in weeklies)
- [ ] Compare daily vs weekly performance

### Phase C: Add Monthly Opportunistic Layer (Week 5-6)
**Goal:** Sell wider spreads when IV is elevated.

- [ ] IV rank tracker for SPY/QQQ/IWM
- [ ] Trigger: only enter when IV rank > 50
- [ ] 21-30 DTE credit spreads
- [ ] Max 20% of account in monthly positions

### Phase D: Broker Migration for Tax Efficiency (Month 3+)
**Goal:** Switch income strategy from SPY to XSP for 60/40 tax treatment.

- [ ] Open Interactive Brokers account
- [ ] Build IBKR client module (same interface as alpaca_client.py)
- [ ] Wire IBKR into QuantAI
- [ ] Migrate income strategy to XSP iron condors
- [ ] Keep Alpaca for equity trades

### Phase E: Scale to Live (Month 4+)
**Goal:** Transition from paper to real money.

- [ ] 2+ months of consistent paper results (5%+ monthly)
- [ ] Switch TRADING_MODE=live with small allocation ($5k)
- [ ] Max 2 simultaneous live positions initially
- [ ] Gradually increase over 3 months
- [ ] Target: $10k/month at $150k deployed

---

## Track 2: Platform Development (Agent-Driven)

### Priority 1: Live Data Infrastructure (CRITICAL — needed for trading)
**Goal:** Agents have comprehensive, real-time market awareness.

- [x] **Alpaca options chain integration** — pull live SPY options with Greeks, IV ✅ Mar 21, 2026
- [x] **yfinance integration** — VIX level, IV rank, market context ✅ Mar 21, 2026
- [ ] **Finnhub free tier** — news sentiment, economic calendar, earnings dates ⏳ building now
- [ ] **FRED API** — Fed funds rate, CPI, unemployment (free) ⏳ building now
- [x] Cache layer — store data locally, only fetch what's changed ✅ Mar 21, 2026
- [x] Market context builder — assemble all data into a compact context for Claude ✅ Mar 21, 2026

### Priority 2: Discord → Code → Deploy Pipeline
**Goal:** Talk in #chat, agents make code changes, PRs get created.

- [ ] Mount project directory into Discord bot container
- [ ] Mount Docker socket for container management
- [ ] Install git + docker-cli in Discord bot Dockerfile
- [ ] Infra agent can: read files, edit files, git commit, git push, rebuild containers
- [ ] All code changes go through PR (never direct to main)
- [ ] QA check: run tests before merging

### Priority 3: Open-Source Integrations
**Goal:** Leverage the best open-source tools without building from scratch.

#### Financial Services Plugins (anthropics/financial-services-plugins)
- [ ] Evaluate which plugins are relevant
- [ ] Integrate market data plugins
- [ ] Integrate risk analysis plugins

#### Everything Claude Code (affaan-m/everything-claude-code)
- [ ] Extract agent patterns and skills
- [ ] Apply to QuantAI's agent architecture
- [ ] Copy useful hooks and configurations

#### GStack (garrytan/gstack)
- [ ] Evaluate stack components
- [ ] Integrate relevant infrastructure patterns

#### TradingAgents (TauricResearch)
- [ ] Multi-agent debate for research (bull vs bear)
- [ ] Fundamental + sentiment + technical analysis agents
- [ ] Wire into morning brief pipeline

#### trading_skills (staskh)
- [ ] 23 trading analysis tools
- [ ] Portfolio management commands
- [ ] PDF report generation

#### NautilusTrader
- [ ] Backtesting engine for strategy validation
- [ ] Test iron condor strategy on historical data
- [ ] Validate before promoting any strategy change

### Priority 4: Market Intelligence Dashboard
**Goal:** Comprehensive market awareness for trading decisions.

#### Indicators & Tracking
- [ ] VIX level + VIX term structure (contango/backwardation)
- [ ] Put/call ratio (market sentiment)
- [ ] Advance/decline line (market breadth)
- [ ] Sector rotation tracker (XLF, XLK, XLE, XLV, etc.)
- [ ] 200-day moving average vs current price (bull/bear signal)
- [ ] Fear & Greed index equivalent

#### Economic Indicators
- [ ] Fed funds rate + FOMC meeting dates
- [ ] CPI / inflation data
- [ ] Unemployment / jobs report dates
- [ ] GDP growth rate
- [ ] Treasury yield curve (2yr vs 10yr)

#### Geopolitical / Macro
- [ ] Major news event detection (wars, sanctions, elections)
- [ ] Trade policy changes (tariffs)
- [ ] Central bank decisions (global)

#### Institutional Flow
- [ ] 13F filings tracker (what are hedge funds buying/selling)
- [ ] Dark pool activity on SPY
- [ ] Options flow — unusual options activity
- [ ] Insider buying/selling signals

#### Weekly Analysis Bot
- [ ] Automated weekly market analysis posted to #research every Friday
- [ ] Performance review of all investments (stocks, crypto, 401k)
- [ ] Actionable suggestions based on data

### Priority 5: Personal Finance Integration
**Goal:** Track all investments in one place.

- [ ] 401k performance tracking (manual input or API if available)
- [ ] Stock portfolio tracker (Alpaca + any other brokers)
- [ ] Crypto portfolio tracker
- [ ] Net worth dashboard
- [ ] Tax projection based on trading activity
- [ ] Monthly financial summary posted to Discord

### Priority 6: Dev Agent Infrastructure
**Goal:** Autonomous development that runs without you.

- [ ] OpenClaw agent with QuantAI project context
- [ ] Claude Code agent with CLAUDE.md awareness
- [ ] Task queue system (read from ROADMAP.md or issues)
- [ ] Auto-PR creation for each task
- [ ] Test suite that runs before any merge
- [ ] Daily dev standup posted to #pr-updates (what was built, what's next)
- [ ] Code review agent (checks PRs for quality, security, consistency)

---

## Task Queue for Dev Agents

Format: `[priority] [effort] [description]`

### Immediate (this week)
```
P0 MEDIUM  Build autonomous income bot (SPY 0DTE iron condors)
P0 SMALL   Wire Alpaca options chain data into bot
P0 SMALL   Add yfinance for VIX level checking
P0 SMALL   Mount project dir + Docker socket in Discord bot container
P1 MEDIUM  Integrate Finnhub free tier for news/events
P1 SMALL   Add economic calendar awareness (FOMC, CPI dates)
```

### Next week
```
P1 MEDIUM  Build market context assembler (all data → compact prompt)
P1 SMALL   Add weekly put spread scanner (Layer 2)
P2 MEDIUM  Evaluate + integrate financial-services-plugins
P2 MEDIUM  Set up NautilusTrader backtesting for iron condor strategy
P2 SMALL   Add sector rotation tracker
```

### Week 3-4
```
P2 MEDIUM  Build market intelligence dashboard
P2 MEDIUM  Integrate TradingAgents for enhanced research
P2 SMALL   Add institutional flow tracking (13F, unusual options)
P3 MEDIUM  Personal finance integration (401k, crypto, net worth)
P3 MEDIUM  Set up IBKR connector for XSP trading
```

### Ongoing
```
P1 ALWAYS  Daily self-improvement loop (EOD scoring → auto-PRs)
P1 ALWAYS  Weekly review and strategy refinement
P2 ALWAYS  Open-source tool evaluation and integration
P3 ALWAYS  Code quality, test coverage, documentation
```

---

## Success Metrics

### Trading (Track 1)
- Week 1-2: Win rate > 60% on paper iron condors
- Month 1: Positive monthly return on $10k paper
- Month 2: Consistent 5%+ monthly returns
- Month 3: Transition to live trading with real capital
- Month 6: $3-5k/month income
- Month 12-18: $10k/month income at $150k deployed

### Platform (Track 2)
- Week 1: Live data flowing (VIX, options chain, news)
- Week 2: Discord → code → deploy working
- Month 1: 3+ open-source integrations active
- Month 2: Full market intelligence dashboard
- Month 3: Personal finance tracking live
- Ongoing: System improves itself daily without manual intervention

---

## Architecture Principle

**You focus on two things:**
1. Review trades in Discord, learn options, make strategic decisions
2. Learn agentic workflows by observing how the system builds itself

**Agents focus on everything else:**
- Building features
- Integrating data sources
- Running trades
- Scoring performance
- Generating improvements
- Keeping the system healthy

---

## Pre-Live Trading Checklist
**These must all be ✅ before switching TRADING_MODE=live**

### Data & Intelligence
- [ ] context_builder.py live and scoring every trading day
- [ ] macro_data.py: FRED + Finnhub feeding FOMC/CPI event calendar correctly
- [ ] sentiment_data.py: put/call ratio + Fear & Greed + VIX term structure running
- [ ] flow_detector.py: Vol/OI unusual activity detector validated on paper trades
- [ ] Dark pool proxy (10x equity volume spike) generating alerts in #guard-log
- [ ] All context scores logged and correlated against trade P&L for ≥ 4 weeks

### Paid Flow Upgrades (before live, not before paper)
- [ ] **Polygon.io** ($29/mo) — real options flow with Greeks on every contract,
      dark pool prints for SPY/QQQ. Add when paper trading shows consistent results.
      _Why: our Vol/OI proxy is free but 15-min delayed. Live trading deserves real-time._
- [ ] **Unusual Whales** ($30/mo) — options sweep alerts in real time.
      Add when transitioning to live. MCP server available → wire directly into Claude.
      _Why: sweep detection pre-entry could prevent the worst live losses._
- [ ] Both paid sources integrated into context_builder.py, replacing free proxies
- [ ] Context score accuracy verified: score ≥ 60 days should show higher win rate than score < 60 days

### Strategy Validation
- [ ] Agent 1 paper win rate ≥ 60% over minimum 40 trades
- [ ] Agent 1 context score correlation confirmed (≥60 score days outperform <60 days)
- [ ] Agent 2 monthly yield ≥ 1% average over 8 weeks
- [ ] No single week drawdown > $500 on paper in last 4 weeks
- [ ] Guard engine tested: emergency stop, daily loss halt, weekly loss halt all verified
- [ ] Self-improve engine has run ≥ 4 weeks and params have been tuned at least once

### Infrastructure
- [ ] VPS monitored: disk, memory, container restarts all healthy for 30+ days
- [ ] GitHub PAT rotated (current one exposed in chat — rotate immediately)
- [ ] Separate live Alpaca API keys created and stored in .env (never same as paper)
- [ ] Live account funded with initial allocation ($5k max per blueprint)
- [ ] Emergency stop tested end-to-end on paper before going live

---


---

## Completed This Session — Mar 21, 2026

### Intelligence Layer — building now
- [ ] `services/macro_data.py` — FRED + Finnhub macro/event data
- [ ] `services/sentiment_data.py` — put/call ratio, Fear & Greed, VIX term structure
- [ ] `services/flow_detector.py` — Vol/OI unusual activity (free Unusual Whales proxy) + dark pool volume proxy
- [ ] `services/context_builder.py` — pre-trade score 0-100 gating all agent entries
- [ ] `orchestrator/scheduler.py` — context score wired into Agent 1 + Agent 2 entry logic

### Data Layer (`services/market_data.py`)
- [x] VIX fetch via yfinance with regime classification (13 zones: normal/elevated/high/danger/halt)
- [x] Options chain fetch via Alpaca with Greeks, bid/ask, OI
- [x] IV Rank calculation (52-week HV percentile proxy, no API cost)
- [x] Strike finder by delta (`find_strikes_by_delta`)
- [x] Iron condor builder (`build_iron_condor_strikes`)
- [x] 15-min local cache (avoids redundant API calls)
- [x] Full market context snapshot (`get_market_context`)

### Agent 1 (`orchestrator/agent1_iron_condor.py`)
- [x] Fully autonomous SPY/QQQ 0DTE iron condor bot
- [x] Entry 1 at 9:50 AM, Entry 2 at 11:30 AM (conditional on 40% profit)
- [x] VIX gate (13–30 tradeable range)
- [x] Guard check before every entry
- [x] 50% profit target, 2x stop loss, 3:30 PM hard close
- [x] Per-agent strategy params (`configs/agent1_params.json`) — self-improve can tweak
- [x] Per-agent JSONL journal (`data/memory/paper/agent1_journal.jsonl`)
- [x] Per-agent EOD scoring with param suggestions
- [x] Discord trade cards to #trade-proposals, closes to #execution-log

### Agent 2 (`orchestrator/agent2_covered_call.py`)
- [x] Fully autonomous covered call bot on PLTR/TSM/MU/AMD/AVGO/ASML
- [x] Monday 10 AM weekly scan with earnings blackout check
- [x] IV rank gate (min 30), delta 0.20, 21-35 DTE
- [x] 50% profit target, 2 DTE hard close
- [x] Per-agent params (`configs/agent2_params.json`)
- [x] Per-agent JSONL journal (`data/memory/paper/agent2_journal.jsonl`)
- [x] Friday weekly scoring with kill signal detection

### Orchestrator Updates (`orchestrator/scheduler.py`)
- [x] `AUTO_MODE=true` flag — no human approval in paper mode
- [x] Agent 1 jobs: entry1, entry2, monitor (every 5 min), EOD score
- [x] Agent 2 jobs: Monday weekly scan, Friday weekly score
- [x] `call_claude_for_agent()` shared helper for agent scoring

### Configs Updated
- [x] `configs/watchlist.json` — updated to portfolio tickers (SPY/QQQ/NVDA/PLTR/TSM/AMD/AVGO/ASML/MU/CCJ/VRT)
- [x] `configs/guard_config.json` — expanded whitelist, tuned iron condor IV rank for 0DTE
- [x] `configs/strategies.json` — agents defined with autonomous mode, capital allocation
- [x] `configs/agent1_params.json` — Agent 1 v1 baseline params
- [x] `configs/agent2_params.json` — Agent 2 v1 baseline params
- [x] `orchestrator/requirements.txt` — added yfinance, alpaca-py
- [x] `.env.example` — added AUTO_MODE, DISCORD_WEBHOOK_EXECUTION

### Capital Allocation Plan
| Agent | Strategy | Capital | Risk/trade |
|---|---|---|---|
| Agent 1 | SPY/QQQ 0DTE Iron Condors | $40,000 | ~$500 max |
| Agent 2 | Covered Calls (portfolio tickers) | $30,000 | Capped by underlying |
| Reserve | Cash-Secured Puts (Week 3+) | $30,000 | Held in reserve |

### Success Benchmarks
| Milestone | Target | Kill Signal |
|---|---|---|
| Week 2 | Agent 1 win rate > 60% | < 45% after 20 trades |
| Month 1 | Both agents net positive | Either agent down > 5% |
| Month 2 | Agent 1 > 3%/mo, Agent 2 > 1%/mo | Consistent underperformance |
| Month 3 | Combined > 5%/mo on $100k paper | Kill weakest, double down on winner |


---

## Phase: Intelligence Agents (This Week)

### Priority: Tech Intelligence Agent FIRST
Build the tool-finder before building more tools.
Once running, it evaluates every subsequent build decision.

---

### Agent 3 — Tech Intelligence Agent (Session 1 — Tuesday)
**Goal:** Weekly scan of GitHub, arXiv, finance blogs, open-source releases.
Proposes specific integrations ranked by impact/effort/cost.
Posts to #research every Monday morning before market open.

**What it does:**
- Scans GitHub trending repos in: algorithmic trading, options, ML finance, data feeds
- Scans arXiv for new papers on: volatility forecasting, options pricing, market microstructure
- Reads finance/quant blogs: QuantLib, Quantopian community, Two Sigma blog, AQR research
- Evaluates against current system (reads SYSTEM_STATE.md as context)
- Produces structured report: "replace X with Y, saves $Z/mo, effort: low/medium/high"
- Posts to #research every Monday 6:00 AM (before morning brief)

**Approval flow:** Posts proposals → you review → discuss in #chat → approve → next session builds it

**Cost:** 1 Sonnet call/week (~$0.10). Entirely worth it.

**Files:**
- `services/tech_intelligence.py` — GitHub/arXiv/blog scanner + evaluator
- `orchestrator/scheduler.py` — Monday 6:00 AM job

---

### Agent 4 — Market Intelligence Agent (Session 2 — Wednesday)
**Goal:** Proactive daily market synthesis. Not just data — an opinion.
Posts unprompted when something significant happens.
Speaks in #chat when asked about current market conditions.

**What it does:**
- 6:20 AM daily (before morning brief): synthesizes overnight news, Fed signals,
  geopolitical developments, futures positioning, sector flows
- Posts a "market conviction" to #research:
  "FOMC Thursday — go smaller on condors, widen wings to $7"
  "Tariff news dropped overnight — SPY gap risk elevated, skip Entry 1 today"
  "VIX term inverted — something institutional is hedging, reduce size"
- Monitors RSS feeds continuously: Reuters, AP, Fed announcements, CBOE alerts
- Responds in #chat with market context when asked
- Feeds conviction directly into context score as a 6th signal

**Data sources (all free):**
- Reuters RSS, AP RSS, MarketWatch RSS
- Fed Reserve press releases (RSS)
- CBOE volatility alerts
- Finnhub news (already have key)
- Web search for breaking geo-political events

**Files:**
- `services/market_intelligence.py` — news ingestion + synthesis + conviction scoring
- `orchestrator/scheduler.py` — 6:20 AM daily job + continuous news monitor
- `services/context_builder.py` — add conviction as 6th signal (weight: 15pts, rebalance others)

---

### Agent 5 — Pattern & Learning Agent (Session 3 — Thursday/Friday)
**Goal:** Close the loop between trade history and future decisions.
After 2+ weeks of data, identifies patterns in winning vs losing trades
and proactively adjusts strategy parameters.

**What it does:**
- Reads all journal data: context scores, market conditions, outcomes
- Identifies patterns: "condors entered when PCR > 1.1 win 78% vs 52% overall"
- Identifies anti-patterns: "entries on Monday open underperform by 15%"
- Updates context weights automatically (correlation_analyzer already does this partially)
- Posts weekly pattern report to #research Friday alongside CTO report
- Feeds discovered patterns back into agent entry decisions via memory

**What makes this different from existing EOD scoring:**
- EOD scoring looks at today's trades
- Pattern agent looks across weeks/months for statistically significant patterns
- Requires minimum 20 trades to start drawing conclusions
- Uses proper statistical significance testing before acting on a pattern

**Files:**
- `services/pattern_engine.py` — statistical pattern detection across journal history
- `orchestrator/scheduler.py` — Friday 4:30 PM job (before correlation analysis)

---

## Intelligence Agent Architecture

```
                    ┌─────────────────────────────┐
                    │     INTELLIGENCE LAYER       │
                    │                              │
  GitHub/arXiv ──→  │  Tech Intelligence Agent    │──→ #research (Monday)
  Blogs/Papers      │  (weekly, Monday 6 AM)      │    Proposals → your approval
                    │                              │
  News/RSS ──────→  │  Market Intelligence Agent  │──→ #research (daily 6:20 AM)
  Fed/Geo-pol       │  (daily + continuous)       │    Conviction → context score
                    │                              │
  Journal data ──→  │  Pattern & Learning Agent   │──→ #research (Friday)
  Trade history     │  (weekly + triggered)       │    Patterns → agent params
                    └─────────────────────────────┘
                                  │
                    ┌─────────────▼─────────────┐
                    │      CONTEXT BUILDER       │
                    │  Score 0-100 (6 signals)   │
                    │  + market conviction       │
                    └─────────────┬─────────────┘
                                  │
                    ┌─────────────▼─────────────┐
                    │    AGENT 1 + AGENT 2       │
                    │    Better decisions        │
                    └────────────────────────────┘
```

## Week Plan

| Day | Session | What gets built |
|---|---|---|
| Tuesday | Session 1 | Tech Intelligence Agent — GitHub/arXiv scanner, Monday proposals |
| Wednesday | Session 2 | Market Intelligence Agent — news synthesis, daily conviction, 6th context signal |
| Thursday | Session 3 | Pattern & Learning Agent — statistical pattern detection across journals |
| Friday | Review | Let all 3 agents run, review outputs, tune as needed |

## Success Criteria for Intelligence Agents

| Agent | Ready when... | Kill signal |
|---|---|---|
| Tech Intelligence | Posts first Monday report with 3+ actionable proposals | Proposals are vague or irrelevant after 3 weeks |
| Market Intelligence | Conviction accurately flags 70%+ of high-VIX days in advance | False positives causing too many skips |
| Pattern & Learning | Identifies first statistically significant pattern (p < 0.05) | No patterns found after 40+ trades (not enough data yet) |

## Long-term Vision: Quant-Grade System

**Month 1-2 (now):** Paper trading, learning loop, intelligence agents
**Month 3:** Live trading small ($5k), Polygon.io + Unusual Whales added
**Month 4-6:** Pattern library building, multi-strategy expansion
**Month 6-12:** Statistical edge validation, position sizing optimization
**Year 2+:** Institutional-grade: tick data, order flow, ML signal generation

The gap between retail and quant is not tools — it is validated edge.
Everything we build is working toward that validation.

