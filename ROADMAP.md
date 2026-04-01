# QuantAI — Master Roadmap
**Updated: March 31, 2026**

## Two Tracks, Running in Parallel

### Track 1: Options Trading Execution (YOUR focus)
You learn, you review, you make decisions. The agents execute.

### Track 2: Platform Development (AGENT focus)
Dev agents build, enhance, integrate. Runs 24/7 without you.

---

## Track 1: Options Trading Execution

### Phase A: First Strategy — SPY 0DTE Iron Condors ✅ ACTIVE
- [x] Guard engine with all trading rules
- [x] Alpaca paper trading connected
- [x] Greeks engine (py_vollib)
- [x] Options analysis commands (/greeks, /bull_put, /iron_condor)
- [x] Morning brief automation
- [x] Agent 1 autonomous 0DTE iron condor bot ✅ Mar 21
- [x] Debate Chamber — 3-agent debate selects 2 trades/session ✅ Mar 31
- [x] Market Intelligence MCP — twice-daily intelligence packet ✅ Mar 31
- [ ] Run 40+ paper trades through debate chamber, track win rate
- [ ] End of Week 1 review — debate quality vs Agent 1 solo quality
- [ ] Tune strike selection based on first 2 weeks of data

### Phase B: Add Weekly Layer (Week 3-4)
- [ ] Weekly put spread scanner (Monday entry, Friday close)
- [ ] Delta 0.10 short strike, $5 wide
- [ ] Separate risk allocation (max 15% of account in weeklies)
- [ ] Compare daily vs weekly performance

### Phase C: Add Monthly Opportunistic Layer (Week 5-6)
- [ ] IV rank tracker for SPY/QQQ/IWM
- [ ] Trigger: only enter when IV rank > 50
- [ ] 21–30 DTE credit spreads
- [ ] Max 20% of account in monthly positions

### Phase D: Broker Migration for Tax Efficiency (Month 3+)
- [ ] Open Interactive Brokers account
- [ ] Build IBKR client module (same interface as alpaca_client.py)
- [ ] Migrate income strategy to XSP iron condors (60/40 tax treatment)
- [ ] Keep Alpaca for equity trades

### Phase E: Scale to Live (Month 4+)
**Prerequisites:** ≥40 paper trades, ≥60% win rate, 8+ weeks consistent, pre-live checklist complete
- [ ] MarketXLS Advanced ($94/mo) for real-time Greeks
- [ ] Unusual Whales ($48/mo) for live sweep detection
- [ ] Switch TRADING_MODE=live with $5k allocation
- [ ] Max 2 simultaneous live positions initially
- [ ] Gradually increase over 3 months
- [ ] Target: consistent 3–5%/month at full deployment

---

## Track 2: Platform Development

### ✅ COMPLETED — Core Infrastructure (Mar 21, 2026)
- [x] Alpaca options chain integration — live SPY options with Greeks, IV
- [x] yfinance integration — VIX level, IV rank, market context
- [x] Cache layer — 15-min TTL, avoids redundant API calls
- [x] Market context builder — assembles compact context for Claude
- [x] Agent 1 — fully autonomous SPY 0DTE iron condor bot
- [x] Agent 2 — fully autonomous covered call bot (PLTR/TSM/MU/AMD/AVGO/ASML)
- [x] Per-agent JSONL journals
- [x] Per-agent EOD scoring with param suggestions
- [x] Orchestrator scheduler with 12 jobs
- [x] Discord trade cards to #trade-proposals and #execution-log
- [x] configs/agent1_params.json, agent2_params.json (v2 baseline)

### ✅ COMPLETED — Intelligence Layer (Mar 24, 2026)
- [x] Context score bug fixes (VIX bad data, cross-signal contradictions)
- [x] Flow detector wired into Agent 1 pre-entry
- [x] GEX engine as 6th context signal (gamma × OI from Alpaca chain)
- [x] 6-signal context score: VIX=25, Event=15, Macro=15, Sentiment=15, Flow=15, GEX=15
- [x] CTO listener containerized (trader-cto)

### ✅ COMPLETED — Debate Chamber + Intelligence MCP (Mar 31, 2026)
- [x] `services/market_intelligence_mcp.py` — twice-daily intelligence packet
  - [x] Macro: VIX + term structure + regime, F&G, yields, yield curve
  - [x] Symbols: RSI, MACD, BB, EMA200, ADX, fundamentals, news sentiment for all 10 tickers
  - [x] Events: FOMC/CPI/jobs calendar from Finnhub
  - [x] Earnings: next earnings dates + surprise history per symbol
  - [x] Open positions: live P&L + recommended actions from Alpaca
  - [x] High conviction setups: pre-screened, ranked by conviction score
  - [x] Risk flags: HALT/WARNING/CAUTION with specific reasons
  - [x] Market regime classification: normal/caution/risk_off/halt
  - [x] Data quality score (decrements when sources fail)
- [x] `orchestrator/debate_chamber.py` — 3-agent Bull/Bear/Judge debate
  - [x] Proposal Agent (Sonnet): generates 3–5 trade candidates from packet
  - [x] Bull Agent (Haiku): argues FOR each proposal
  - [x] Bear Agent (Haiku): argues AGAINST each proposal
  - [x] Judge Agent (Sonnet): scores debate, selects TOP 2
  - [x] Guard check on all approved trades before posting
  - [x] Full debate transcript to #trade-proposals (formatted trade cards)
  - [x] Debate summary to #research
  - [x] Debate log to data/memory/shared/debate_log.jsonl
- [x] `orchestrator/self_evolution.py` — 6-step evolution pipeline
  - [x] Step 1 OBSERVE: extract structured lessons from journal
  - [x] Step 2 CRITIQUE: identify single biggest param misalignment
  - [x] Step 3 GENERATE: propose ONE traceable config change
  - [x] Step 4 VALIDATE: 5 gates (constitution/size/drift/safety/regression)
  - [x] Step 5 APPLY: write change, bump version, post to #pr-updates
  - [x] Step 6 CONSOLIDATE: weekly compression into strategy principles
- [x] `orchestrator/scheduler_additions.py` — 6 new scheduler jobs
  - [x] 6:20 AM — Market Intelligence pre-market
  - [x] 6:25 AM — Debate Chamber pre-market
  - [x] 1:30 PM — Market Intelligence mid-session
  - [x] 1:35 PM — Debate Chamber mid-session
  - [x] 4:35 PM — Self-Evolution Engine
  - [x] 4:45 PM Friday — Weekly Consolidation

---

## Priority 1 (Next Session): Wire Everything In

These files are built and ready. Next step is integrating them into the live system.

```
P0 SMALL   Add scheduler_additions.py jobs into orchestrator/scheduler.py
P0 SMALL   Add market_intelligence_mcp.py and debate_chamber.py to orchestrator requirements.txt
P0 SMALL   Add DISCORD_WEBHOOK_PROPOSALS and DISCORD_WEBHOOK_RESEARCH to .env
P0 SMALL   Test run market_intelligence_mcp.py standalone — verify packet builds correctly
P0 SMALL   Test run debate_chamber.py standalone — verify 2 proposals post to Discord
P0 SMALL   Wire self_evolution EOD score: pass actual score from self_improve.py
P0 MEDIUM  Add aiohttp to orchestrator/requirements.txt (dependency for new services)
```

---

## Priority 2: Data Quality Upgrades

```
P1 MEDIUM  Add MarketXLS MCP (Standard $56/mo) — technicals + fundamentals via MCP
           Adds: real options chain data, screeners, sector performance
           Why now: Intelligence packet currently uses yfinance approximations
           Target: replace yfinance technical calcs with MarketXLS precision

P1 SMALL   Integrate tradingview-mcp (328 stars, passes threshold) as 7th context signal
           Adds: BB squeeze scanner, RSI on multiple timeframes, candlestick patterns
           Already evaluated — low complexity, free

P1 SMALL   Add StockTwits sentiment to sentiment_data.py (free, portable)
           Wire as additional signal in intelligence packet news_sentiment field

P1 MEDIUM  services/pattern_engine.py — statistical win/loss pattern detection
           Requires: 20+ trades in journal (accumulating now)
           Goal: find patterns that EOD scoring misses (day-of-week, VIX level at entry, etc.)
```

---

## Priority 3: Position Monitor Upgrade

```
P1 MEDIUM  Upgrade position_monitor to use Alpaca MCP for live Greeks
           Currently: monitors price only
           Target: monitor delta/theta on open condors, alert if delta exceeds 0.15
           Feed: position data from intelligence packet open_positions field

P2 SMALL   Add mid-session position review to Debate Chamber mid-session run
           If open condor > 50% profit → post close recommendation to #trade-proposals
           If open condor delta > 0.15 → post adjustment recommendation
```

---

## Priority 4: Market Intelligence Expansion

```
P2 MEDIUM  Market Intelligence Agent — daily news synthesis conviction modifier
           services/market_intelligence.py (proactive, not just packet-based)
           6:20 AM: synthesizes overnight news → "FOMC today — skip Entry 1"
           Posts conviction to #research unprompted when something material happens

P2 SMALL   Add congressional trading data via Finnhub → Agent 2 directional bias
           If insiders buying stock → boost covered call conviction
           If insiders selling → flag in intelligence packet

P2 SMALL   Add FRED API macro regime to intelligence packet
           Currently in context_builder.py but not in intelligence packet
           Wire fed_funds_rate into MacroSnapshot.fed_funds_rate field
```

---

## Priority 5: Pre-Live Upgrades (Month 3+)

```
P3 MEDIUM  Unusual Whales API ($48-50/mo) — real options sweep detection
           Replace flow_detector.py Vol/OI proxy with actual sweep alerts
           Wire into Debate Chamber as additional bear argument evidence
           Add before live transition — sweep detection prevents worst live losses

P3 MEDIUM  Polygon.io ($29/mo) — real-time options flow with Greeks
           Replace 15-min delayed yfinance data with real-time
           Required for live trading; paper trading can use delayed data

P3 MEDIUM  Set up IBKR connector for XSP trading
           XSP iron condors = 60/40 tax treatment (Section 1256)
           Wire same interface as alpaca_client.py
```

---

## Ongoing

```
P1 ALWAYS  Debate Chamber quality review (read #trade-proposals daily, evaluate reasoning)
P1 ALWAYS  Self-Evolution monitoring (read #pr-updates, understand each proposed change)
P1 ALWAYS  EOD score tracking (target: average > 85/100 over any 10-day rolling period)
P2 ALWAYS  Weekly context score correlation (does score predict win rate? validate monthly)
P2 ALWAYS  CTO scan review every Monday (evaluate proposals before dismissing)
P3 ALWAYS  Code quality, test coverage, documentation
```

---

## Task Queue for Dev Agents

### Immediate (this week)
```
P0 SMALL   Wire scheduler_additions.py into scheduler.py (paste 6 new jobs)
P0 SMALL   Add aiohttp to requirements.txt
P0 SMALL   Add new env vars to .env.example: DISCORD_WEBHOOK_PROPOSALS, DISCORD_WEBHOOK_RESEARCH
P0 SMALL   Smoke test market_intelligence_mcp.py — log packet to console
P0 SMALL   Smoke test debate_chamber.py — verify Discord posts
P1 SMALL   Wire actual EOD score from self_improve.py → self_evolution.py
P1 SMALL   Add tradingview-mcp as 7th signal in context_builder.py
```

### Next week
```
P1 MEDIUM  MarketXLS MCP integration (Standard tier) — replace yfinance technical calcs
P1 SMALL   StockTwits sentiment in sentiment_data.py
P1 MEDIUM  pattern_engine.py — statistical detection (needs 20+ journal entries first)
P1 MEDIUM  Position monitor upgrade — live Greeks via Alpaca MCP
P2 SMALL   Congressional trading data — Finnhub endpoint → intelligence packet
P2 SMALL   FRED macro data → intelligence packet MacroSnapshot
```

### Week 3-4
```
P2 MEDIUM  Market Intelligence Agent (proactive news conviction)
P2 MEDIUM  NautilusTrader backtesting for iron condor strategy validation
P2 MEDIUM  Full market intelligence dashboard
P3 MEDIUM  Personal finance integration (401k, crypto, net worth tracker)
```

---

## Success Metrics

### Trading (Track 1)
| Milestone | Target | Kill Signal |
|---|---|---|
| Week 2 | Debate Chamber win rate > 60% | < 45% after 20 debate-selected trades |
| Week 4 | Evolution applies first valid change | No change applied after 20 trading days |
| Month 1 | Both agents + debate chamber net positive | Either agent down > 5% |
| Month 2 | Debate Chamber > 3%/mo, Agent 2 > 1%/mo | Consistent underperformance |
| Month 3 | Combined > 5%/mo on $20k paper | Kill weakest, scale winner |
| Month 4+ | Transition to live at $5k, scale to $50k+ | Live drawdown > 10% → pause |

### Platform (Track 2)
| Milestone | Target |
|---|---|
| Week 1 | Debate Chamber posting 4 trade cards/day |
| Week 2 | Self-Evolution observing and critiquing (even if no changes applied) |
| Month 1 | First self-evolution change successfully applied and validated |
| Month 2 | MarketXLS integrated, intelligence packet quality score > 90 consistently |
| Month 3 | Pattern engine finding statistically significant patterns (p < 0.05) |
| Ongoing | System improves itself daily without manual intervention |

---

## Long-Term Vision

**Month 1-2 (now):** Paper trading + debate chamber validation + self-evolution learning
**Month 3:** Live trading small ($5k), Polygon.io + Unusual Whales, MarketXLS
**Month 4-6:** Pattern library building, multi-strategy expansion
**Month 6-12:** Statistical edge validation, position sizing optimization, XSP transition
**Year 2+:** Institutional-grade: tick data, order flow, ML signal generation

The gap between retail and quant is not tools — it is **validated edge**.
Everything we build is working toward that validation.
The debate chamber is the mechanism that proves edge before capital goes live.

---

## Architecture Overview (Current State)

```
6:20 AM + 1:30 PM
      │
      ▼
┌─────────────────────────────────────┐
│    MARKET INTELLIGENCE MCP          │
│    market_intelligence_mcp.py       │
│                                     │
│  yfinance: price, RSI, MACD, EMA    │
│  Finnhub: events, earnings, news    │
│  Alpha Vantage: earnings surprises  │
│  CNN/CBOE: Fear&Greed, PCR          │
│  Alpaca: positions + P&L            │
│                                     │
│  Output: intelligence_packet.json   │
└───────────────┬─────────────────────┘
                │
                ▼
┌─────────────────────────────────────┐
│    DEBATE CHAMBER                   │
│    debate_chamber.py                │
│                                     │
│  Proposal Agent (Sonnet)            │
│  → 3-5 trade candidates             │
│                                     │
│  Bull Agent (Haiku) ──┐             │
│                        ├── parallel │
│  Bear Agent (Haiku) ──┘             │
│                                     │
│  Judge Agent (Sonnet)               │
│  → TOP 2 selected                   │
└───────────────┬─────────────────────┘
                │
                ▼
┌─────────────────────────────────────┐
│    GUARD ENGINE (16 rules, 44 tests)│
│    guard-engine/guards.py           │
└───────────────┬─────────────────────┘
                │ APPROVED
                ▼
┌─────────────────────────────────────┐
│    AGENT 1 + AGENT 2 EXECUTION      │
│    Alpaca paper trading             │
│    → #trade-proposals + #execution  │
└───────────────┬─────────────────────┘
                │
                ▼
┌─────────────────────────────────────┐
│    SELF-EVOLUTION ENGINE            │
│    self_evolution.py (4:35 PM)      │
│                                     │
│  Observe → Critique → Generate      │
│  → Validate (5 gates)               │
│  → Apply → Consolidate (weekly)     │
│                                     │
│  → configs/agent1_params.json       │
│  → #pr-updates                      │
└─────────────────────────────────────┘
```
