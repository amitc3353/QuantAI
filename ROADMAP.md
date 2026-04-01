# QuantAI — Master Roadmap
**Last updated: April 1, 2026**

---

## What This System Is

Four Claude agents on OpenClaw. Two autonomous trading agents (Alpha + Beta) running on a 15-min cron cycle. One manual stream for Amit's learning trades. Everything logged to Google Sheets automatically.

**Current status: Paper trading, fully operational as of April 1, 2026.**

---

## What's Built and Live

### OpenClaw Foundation
- [x] Orchestrator (#chat) — scans, debates, SOFI monitoring, answers everything
- [x] Research (#research) — SOFI daily brief, credit spreads, collar candidates
- [x] Infra (#infra) — health checks, files, git, debugging
- [x] Journal (#journal) — trade logging, stats, Google Sheets sync

### Intelligence + Debate
- [x] market_intelligence.py — on-demand packet: VIX, technicals, events, earnings, news for 11 symbols
- [x] debate_chamber.py — Bull/Bear/Judge selects top 2 trades from any valid strategy
- [x] scan_options.py — dynamic scanner across 100+ tickers

### Autonomous Execution (Agent Alpha + Beta)
- [x] autonomous_execution.py — places Alpaca paper orders, logs fills, syncs sheets
- [x] run_pipeline.py — 15-min cron trigger, condition-gated (VIX, regime, timing, daily count)
- [x] Agent Alpha: bull put spreads, any liquid ticker, condition-driven
- [x] Agent Beta: iron condors on SPY/QQQ, only when VIX 13-28 and range-bound
- [x] Max 2 entries per day, tracked in daily_state.json
- [x] Hard close at 3:30 PM ET on all agent positions
- [x] Cron active: every 15 min 9-4 PM ET weekdays + EOD at 4:05 PM

### Self-Evolution
- [x] self_evolution.py — 6-step pipeline, 5-gate validation, updates sofi_collar.json
- [x] pattern_engine.py — statistical detection (needs 20+ closed trades to activate)

### Google Sheets Journal
- [x] 4 tabs: All Trades, Agent Trades, Manual Trades, Summary
- [x] Live formulas: win rate, P&L, open count per stream
- [x] Color coded: yellow=open, green=win, red=loss
- [x] Auto-syncs after every execution and every manual log
- [x] P001 logged: SELL 2x SOFI $16C Apr 18 @ $1.10 (manual, paper)

### Manual Trading (Amit)
- [x] SOFI collar strategy: 200 paper shares, 5 trigger actions pre-decided
- [x] Journal logging: "log: [trade]" in #journal → auto-logged, no questions
- [x] Completely separate from agent trades in Google Sheet

---

## Track 1: Trading Progress

### Now — First Active Month
- [ ] Watch first autonomous agent trades execute tomorrow morning
- [ ] Log SOFI collar trades as you manage them
- [ ] Score each trading day: "score today 78/100" in #chat
- [ ] End of week 1 — compare agent win rate vs manual win rate in Google Sheet
- [ ] Add 1-2 more collar candidates (HIMS at IV rank 93 is strong candidate)

### Month 1 Targets
- [ ] Agent Alpha: 20+ trades, tracking toward 60%+ win rate
- [ ] Agent Beta: 10+ condors, tracking toward 65%+ win rate
- [ ] Self-evolution: first validated config change applied
- [ ] SOFI collar: 2 full cycles completed (sell call, let expire, repeat)

### Month 2-3
- [ ] Pattern engine activates (20+ closed trades) — first statistical insights
- [ ] Scale SOFI collar to 500 shares if thesis holding
- [ ] Evaluate adding XSP iron condors (same as SPY condors but tax-advantaged)
- [ ] Consider additional collar candidates alongside SOFI

### Month 3+ Pre-Live
- [ ] Subscribe MarketXLS Advanced ($94/mo) for real-time Greeks
- [ ] Subscribe Unusual Whales ($48/mo) for real sweep detection
- [ ] Open IBKR account for XSP (Section 1256 = 60/40 long/short tax treatment)
- [ ] Complete pre-live checklist in SYSTEM_STATE.md
- [ ] First live capital: $5k, max 2 positions

### Month 4+ Scale
- [ ] Grow live allocation as win rate holds
- [ ] Migrate SPY/QQQ income strategy to XSP on IBKR for tax efficiency
- [ ] Scale SOFI collar to 1,000 shares ($1k/month target)
- [ ] Target: $3-5k/month consistent, building toward $50k+ deployed

---

## Track 2: Platform Next Steps

### Next Session
```
P1  tradingview-mcp (328 stars, free, vetted)
    Adds multi-timeframe RSI, BB squeeze detection
    Wire as 7th signal input to market_intelligence.py

P1  Proactive position monitoring in Orchestrator
    Currently: monitor script alerts Discord but Orchestrator doesn't proactively close
    Target: when alert fires, Orchestrator places close order via Alpaca automatically

P2  Morning intelligence cron (6:30 AM ET)
    Run market_intelligence.py at 6:30 AM so agents have fresh data when you wake up
    Add to crontab: 30 6 * * 1-5
```

### Month 2
```
P2  pattern_engine.py activation — auto-runs Friday when 20+ closed trades exist
P2  FRED API macro data wired into intelligence packet (free, key exists)
P2  Market intelligence proactive alerts — post to #research when VIX spikes or news breaks
```

### Month 3+ (Pre-Live)
```
P3  MarketXLS Advanced — subscribe at live transition, not before
P3  Unusual Whales — subscribe at live transition
P3  IBKR connector — same interface as Alpaca, for XSP trading
P3  Polygon.io ($29/mo) — evaluate if Alpaca delayed data causes issues
```

---

## What We Are NOT Building

- **Manual trade approval flow** — agents execute autonomously. Amit approves nothing for agent trades.
- **Fixed entry times** — agents enter when conditions are right, not on a clock.
- **CTO agent** — Infra agent handles all system work.
- **MarketXLS now** — paper trading doesn't need real-time Greeks. Subscribe at live transition.
- **Multiple strategies per agent** — Alpha = bull put spreads, Beta = iron condors. Simple, focused, validatable.

---

## Success Metrics

### Paper Trading
| Milestone | Target | If Not Hit |
|---|---|---|
| Week 2 | First agent trades executed, journal populating | Debug execution pipeline |
| Week 4 | Agent Alpha win rate > 55% over 15+ trades | Review debate quality |
| Month 2 | Agent Alpha > 60%, Beta > 65% over 40+ trades | Tune strategy params via evolution |
| Month 2 | Self-evolution applies first validated change | Check journal data volume |
| Month 3 | Consistent positive P&L across both streams | Plan live transition |

### Live Trading (Month 4+)
| Milestone | Target |
|---|---|
| Month 4 | Live $5k, agents trading, win rate holds from paper |
| Month 6 | $15k deployed, SOFI at 500 shares |
| Month 12 | $50k+ deployed, XSP on IBKR, $3-5k/month |

---

## Daily Routine (Reference)

**Morning:**
- Open Google Sheet Summary tab — check overnight changes
- In #chat: "SOFI update" — check collar status vs trigger levels
- Watch pipeline.log or Discord for agent entry notifications

**During market:**
- Agents run autonomously — watch #chat for Discord notifications
- If you want to make a manual trade: execute on Webull → log in #journal
- Ask Orchestrator anything: "what's SPY doing?", "should I roll my SOFI call?"

**End of day:**
- In #chat: "score today 78/100" → evolution runs
- Check Google Sheet for full day P&L
- Fridays: "score today 80/100 --consolidate" for weekly pattern analysis

**Weekly (Friday):**
- In #journal: "stats" — full P&L breakdown
- Compare Agent Trades tab vs Manual Trades tab — who's winning?
- Read evolution log for any config changes
