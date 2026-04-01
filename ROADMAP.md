# QuantAI — Master Roadmap
**Last updated: April 1, 2026**

---

## What This System Is

Four Claude agents (Orchestrator, Research, Infra, Journal) running on OpenClaw in Discord. No scheduler. No fixed bots. Agents act when triggered. Strategy is condition-driven — whatever the data supports. Guardrails are fixed. The system learns from every trade through self-evolution.

**Current status: Paper trading, fully operational.**

---

## What's Built and Working

### Core System (March 2026)
- [x] Four agents live in Discord: Orchestrator (#chat), Research (#research), Infra (#infra), Journal (#journal)
- [x] Orchestrator: runs full scan + debate on demand, monitors SOFI collar triggers, answers any question
- [x] Research: SOFI daily brief, credit spread top 2, weekly collar candidate scan
- [x] Infra: system health, file ops, git, script debugging
- [x] Journal: trade logging (paper + real), stats, Google Sheets sync
- [x] SOFI collar strategy live — 200 paper shares, 5 trigger actions pre-decided
- [x] scan_options.py — dynamic scanner, 100+ tickers, credit spreads + collar candidates

### Intelligence + Debate + Evolution (March 31, 2026)
- [x] market_intelligence.py — on-demand intelligence packet (VIX, technicals, events, earnings, news for 11 symbols)
- [x] debate_chamber.py — 3-agent Bull/Bear/Judge debate selects top 2 trades from any valid strategy
- [x] self_evolution.py — 6-step EOD evolution (Observe/Critique/Generate/Validate/Apply/Consolidate)
- [x] pattern_engine.py — statistical pattern detection (ready, needs 20+ closed trades)
- [x] sheets_sync.py — Google Sheets journal (4 tabs: All/Agent/Manual Trades + Summary)
- [x] Google Sheet live: https://docs.google.com/spreadsheets/d/1GidIf-oLY9NfeRGVTwwGFYzA4eZx2bYjvY7UOATiMM0
- [x] All scripts reading/writing to correct /root/quantai-v2/shared-data/ paths
- [x] .env auto-loading in all scripts — no manual export needed
- [x] P001 logged: SELL 2x SOFI $16C Apr 18 @ $1.10 (paper)

---

## Track 1: Trading (Amit's focus)

### Now — First Month of Paper Trading
- [ ] Log all trades in #journal — build up the dataset
- [ ] Run debate chamber daily — evaluate proposal quality
- [ ] Score each trading day in #chat: "score today 78/100"
- [ ] Add 1-2 more collar candidates from scanner (HIMS at IV rank 93 is compelling)
- [ ] First EOD evolution cycle (needs a few trading days of data)
- [ ] End of week 1 review — are debate proposals better than solo scan?

### Month 1 — Validate
- [ ] 40+ paper trades logged with outcomes
- [ ] Win rate ≥ 60% sustained over 3+ weeks
- [ ] Self-evolution makes first validated config change
- [ ] Debate chamber vs manual trade comparison visible in Google Sheet
- [ ] Weekly Research briefs happening consistently

### Month 2-3 — Expand
- [ ] Add iron condors on SPY/QQQ when VIX supports (scanner handles this)
- [ ] Add cash-secured puts to acquire stocks at discount
- [ ] Scale SOFI collar: 200 → 500 shares as confidence builds
- [ ] Pattern engine producing statistically significant findings

### Month 3+ — Pre-Live Preparation
- [ ] Subscribe MarketXLS Advanced ($94/mo) for real-time Greeks
- [ ] Subscribe Unusual Whales ($48/mo) for real sweep detection
- [ ] Open IBKR account for XSP (Section 1256 = 60/40 tax treatment)
- [ ] Complete pre-live checklist in SYSTEM_STATE.md
- [ ] First live trade: $5k allocation, max 2 positions

### Month 4+ — Scale
- [ ] Grow allocation as win rate holds
- [ ] Migrate SPY income strategy to XSP on IBKR
- [ ] Target: 3-5%/month consistent, scaling to $50k+ deployed

---

## Track 2: Platform Development

### Next Session — Small Improvements
```
P1  tradingview-mcp integration (328 stars, free, already vetted)
    Adds: multi-timeframe RSI, BB squeeze detection, candlestick patterns
    Wire as input to market_intelligence.py — 7th intelligence signal

P1  Proactive position monitoring
    Orchestrator currently waits to be asked about positions
    Target: Orchestrator proactively flags when a position hits 50% profit or 2x stop
    Simple: check open trades in journal at start of each session

P2  Cron job for morning intelligence + debate
    Currently: Amit has to ask "any trades?" each morning
    Target: Run at 6:30 AM ET automatically, post to #trade-proposals
    Simple addition to VPS crontab
```

### Month 2
```
P2  pattern_engine.py activation (auto-runs once 20+ closed trades exist)
    Already built — just needs data to analyze

P2  Market intelligence agent (proactive, not just on-demand)
    Watches RSS feeds, posts to #research when breaking news matters
    "FOMC decision in 2 hours — recommend smaller size today"

P2  FRED API macro data in intelligence packet
    Fed funds rate, CPI trend — free, adds yield curve context
```

### Month 3+ (Pre-Live)
```
P3  MarketXLS Advanced ($94/mo) — subscribe at live transition
    Real-time options Greeks via MCP
    Replaces yfinance approximations with institutional-grade data

P3  Unusual Whales ($48/mo) — subscribe at live transition
    Real sweep detection replaces DIY Vol/OI proxy in scan_options.py

P3  IBKR connector
    Same interface as current Alpaca integration
    XSP iron condors for Section 1256 tax treatment

P3  Polygon.io ($29/mo) — evaluate need after 4+ weeks paper data
    Real-time quotes if Alpaca delay causes execution issues
```

---

## What We Are NOT Building

- **Fixed-schedule bots with locked strategies** — agents evaluate conditions before every trade. Removed.
- **Separate Agent 1 / Agent 2** — one Orchestrator that picks the best strategy beats two locked bots. Removed.
- **CTO agent** — Infra agent has full access. Redundant. Removed.
- **MarketXLS now** — paper trading doesn't need real-time Greeks. Subscribe at live transition only.

---

## Success Metrics

### Paper Trading
| Milestone | Target | If Not Hit |
|---|---|---|
| Week 2 | 10+ trades, all scripts running cleanly | Debug before adding features |
| Week 4 | Win rate > 60% over 20+ trades | Review debate quality, adjust guard rules |
| Month 2 | Self-evolution applies first valid change | Check journal data volume |
| Month 3 | Consistent positive P&L on paper | Plan live transition |

### Live Trading (Month 4+)
| Milestone | Target |
|---|---|
| Month 4 | Live $5k, max 2 positions, win rate holds from paper |
| Month 6 | $15k deployed, SOFI at 500 shares |
| Month 12 | $50k+ deployed, XSP on IBKR, $3-5k/month income |

---

## Daily Operation Reference

### What Amit does each trading day

**Morning (before 9:30 AM):**
- Check #research for SOFI brief (Research agent posts at 6:30 AM)
- Check #chat — say "any trades?" to get debate proposals
- Review trade cards in #trade-proposals — react ✅ ❌ 🔄

**During market hours:**
- If you execute a paper trade on Webull, log it: `log: [details]` in #journal
- Check SOFI price vs trigger levels — ask Orchestrator: "SOFI update"
- If a position hits 50% profit or 2x stop, close it on Webull and log the close

**End of day:**
- Score the day in #chat: "score today [0-100]/100"
- Check Google Sheet for P&L update
- Check #infra if any system issues occurred

**Friday:**
- Say "score today [score]/100 --consolidate" for weekly pattern consolidation
- Review weekly digest from Journal agent

### What you never need to do
- SSH into the VPS for normal operations (Infra agent handles it via #infra)
- Manually run scripts (Orchestrator runs them when you ask)
- Update configs manually (self-evolution handles it with gate validation)
