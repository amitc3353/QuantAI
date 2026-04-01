# QuantAI — Master Roadmap
**Last updated: March 31, 2026**

---

## What This System Actually Is

QuantAI runs on **OpenClaw** — four Claude agents (Orchestrator, Research, Infra, Journal) that live in Discord and have Bash tool access to run Python scripts. There is no separate scheduler, no Agent 1/Agent 2 as fixed bots, no Docker container per strategy. The Orchestrator IS the trading agent. It evaluates conditions and proposes whatever strategy fits — not a hardcoded one.

**Current focus:** SOFI collar + opportunistic credit spreads. Both paper trading.

---

## Track 1: Trading Execution (Amit's focus)

### Now — Active Paper Trading
- [x] SOFI collar strategy live (200 shares paper, ~$15 entry)
- [x] Credit spread scanner running — scans 100+ liquid tickers dynamically
- [x] Collar candidate scanner — finds new stocks worth collaring
- [x] Orchestrator proposes trades on demand with full analysis and debate
- [x] Journal agent logging all paper trades
- [ ] Log 20+ paper trades across both strategies
- [ ] First EOD scoring session (Orchestrator runs self_evolution.py)
- [ ] First week review — are debate proposals better than solo scan?
- [ ] Add 1-2 more collar candidates from scanner results

### Month 1 — Validate the System
- [ ] 40+ paper trades logged with outcomes
- [ ] Win rate ≥ 60% sustained over 3+ weeks
- [ ] Self-evolution makes its first validated config change
- [ ] Debate chamber quality confirmed — proposals are logical and well-argued
- [ ] Weekly digests from Journal agent showing P&L trend

### Month 2-3 — Expand Strategies
- [ ] Iron condors on SPY/QQQ when VIX supports (scanner handles this already)
- [ ] Cash-secured puts to acquire stocks Amit wants at a discount
- [ ] Bull put spreads as primary bullish vehicle (vs directional long)
- [ ] Position sizing logic — scale up on high-conviction setups

### Month 3+ — Pre-Live Preparation
- [ ] Subscribe MarketXLS Advanced ($94/mo) — real-time Greeks for live trading
- [ ] Subscribe Unusual Whales ($48/mo) — real sweep detection
- [ ] Open Interactive Brokers account (XSP = Section 1256, 60/40 tax treatment)
- [ ] Run pre-live checklist in SYSTEM_STATE.md
- [ ] Switch to live at $5k, max 2 positions

### Month 4+ — Scale
- [ ] Grow live allocation as win rate holds
- [ ] Migrate income strategy to XSP on IBKR
- [ ] Target: consistent 3-5%/month, scaling toward $50k+ deployed

---

## Track 2: Platform Development

### ✅ Completed — OpenClaw v2 Foundation (Mar 2026)
- [x] Four agents live in Discord: Orchestrator, Research, Infra, Journal
- [x] Orchestrator: runs scans, proposes trades, monitors positions, answers questions
- [x] Research: SOFI daily brief, credit spread top 2, collar candidate scan weekly
- [x] Infra: full system access, git, health checks, script deployment
- [x] Journal: paper/real trade logging, stats, weekly digests
- [x] scan_options.py — dynamic scanner, no hardcoded ticker list, 100+ symbols
- [x] SOFI collar params in sofi_collar.json with trigger actions
- [x] Paper and real journal separation (trades.jsonl per mode)

### ✅ Completed — Intelligence + Debate + Evolution (Mar 31, 2026)
- [x] market_intelligence.py — on-demand intelligence packet
  - VIX + term structure + regime (normal / caution / risk_off / halt)
  - RSI, MACD, BB, EMA200, ADX for all watchlist symbols
  - Fear & Greed, yield curve, treasury yields
  - Finnhub event calendar: FOMC, CPI, jobs report dates
  - Earnings dates + news sentiment per symbol
  - Pre-screened setups ranked by conviction score
  - Risk flags with HALT / WARNING / CAUTION levels
  - 90-min freshness check — auto-skips if packet still current, --force to override
- [x] debate_chamber.py — 3-agent Bull/Bear/Judge debate
  - Proposal Agent (Sonnet): any valid strategy, reads intelligence packet
  - Bull and Bear agents (Haiku): argue each proposal
  - Judge (Sonnet): selects top 2, formatted trade cards printed to stdout
  - Debate log appended to shared-data/logs/debate_log.jsonl
- [x] self_evolution.py — 6-step evolution pipeline
  - Observe today's journal → Critique current params → Generate ONE change
  - 5 validation gates: constitution, size, drift, safety, regression
  - Applies change to sofi_collar.json only if all 5 gates pass
  - Friday --consolidate: compresses 35 days of observations into principles
- [x] Orchestrator AGENTS.md: flexible strategies, on-demand intelligence, no fixed entry times
- [x] Infra AGENTS.md: health checks for all scripts, clean troubleshooting guide
- [x] Research AGENTS.md: reads intelligence packet to enrich SOFI briefs

---

## Priority 1: First Real Usage (This Week)

```
P0  Test market_intelligence.py on VPS
    python3 /root/quantai-v2/v2/shared-data/scripts/market_intelligence.py --force
    Verify: packet saved to cache/, regime shows correctly, all symbols have data

P0  Test debate_chamber.py
    python3 /root/quantai-v2/v2/shared-data/scripts/debate_chamber.py
    Verify: reads packet, 2 trade cards printed, debate_log.jsonl updated

P0  Log first paper trades in #journal — self_evolution needs journal data to work

P0  Test self_evolution.py with dummy score
    python3 /root/quantai-v2/v2/shared-data/scripts/self_evolution.py 75
    Verify: runs through all steps, posts output cleanly

P1  Ask Orchestrator in #chat: "run the debate" — verify full end-to-end flow
    Orchestrator should run intelligence → debate → post cards to #trade-proposals
```

---

## Priority 2: Data Quality Upgrades

```
P1  tradingview-mcp (328 stars, free, passes security threshold)
    Adds: multi-timeframe RSI, BB squeeze detection, candlestick patterns
    Wire as supplementary input into market_intelligence.py
    Low complexity install

P1  StockTwits sentiment (free, no key needed for basic data)
    20 lines of code, supplements Finnhub news sentiment
    Add to market_intelligence.py symbol snapshots

P1  FRED API macro data in intelligence packet
    Fed funds rate, CPI trend — free, key already exists from v1
    Wire fed_funds_rate into MacroSnapshot in market_intelligence.py

P1  pattern_engine.py — statistical pattern detection
    Needs 20+ journal entries first (accumulate through active trading)
    Goal: find what conditions actually predict wins vs losses
    Only reports patterns with p < 0.05 significance
```

---

## Priority 3: Position Monitoring

```
P2  Proactive position monitoring in Orchestrator
    After any trade posts, Orchestrator tracks it and checks at 50% profit / 2x stop
    Currently: Amit has to ask. Target: Orchestrator flags without being asked

P2  Position Greeks in intelligence packet
    Fetch open Alpaca positions in market_intelligence.py (already stubbed)
    Show delta/theta drift on open spreads
    Alert when condor delta exceeds 0.15
```

---

## Priority 4: Pre-Live Upgrades (Month 3+)

```
P3  MarketXLS Advanced ($94/mo) — subscribe at live transition, not before
    Real-time options Greeks, full chain data, 1,100+ functions via MCP
    Replaces yfinance approximations with institutional-grade data

P3  Unusual Whales ($48/mo) — subscribe at live transition
    Real sweep detection replaces DIY Vol/OI proxy
    DIY proxy adequate for paper; not reliable enough for live capital

P3  Polygon.io ($29/mo) — evaluate after 4+ weeks paper data
    Real-time quotes if Alpaca delay causes execution issues

P3  IBKR connector for XSP
    XSP = European-style S&P 500 options, Section 1256 60/40 tax treatment
    Build same interface as current Alpaca integration
```

---

## What We Are NOT Building

- **Fixed-schedule bots with locked strategies** — OpenClaw agents evaluate conditions before every trade. Clock-based entries ignore context. Removed.
- **Separate Agent 1 / Agent 2** — One Orchestrator proposing the best strategy for current conditions is more adaptable. Removed.
- **CTO agent** — Infra agent has full bash/git/file access. The CTO agent was a v1 workaround. Removed entirely.
- **MarketXLS now** — Paper trading doesn't need real-time Greeks. Subscribe when going live.

---

## Success Metrics

### Paper Trading
| Milestone | Target | If Missed |
|---|---|---|
| Week 2 | 10+ trades logged, scripts running cleanly | Debug before adding features |
| Week 4 | Win rate > 60% over 20+ trades | Review debate quality, check guard rules |
| Month 2 | Self-evolution makes first valid change | Check journal data volume and gate logic |
| Month 3 | Consistent positive paper P&L | Ready for live transition planning |

### Live (Month 4+)
| Milestone | Target |
|---|---|
| Month 4 | Live $5k, max 2 positions, win rate holds from paper |
| Month 6 | $15k deployed, 4 positions, SOFI scaled |
| Month 12 | $50k+ deployed, XSP on IBKR, $3-5k/month |
