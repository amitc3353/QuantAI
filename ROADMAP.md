# QuantAI — Master Roadmap
**Last updated: April 1, 2026**

---

## What This System Is

Four Claude agents on OpenClaw. Agent Alpha and Beta execute autonomously via 15-min cron. Amit trades SOFI collar and learning trades manually. Everything logs to Google Sheets. System tests itself with 43 checks.

**Status: Paper trading, fully operational. First live cron runs tomorrow.**

---

## What's Built and Live

### Foundation
- [x] Four agents: Orchestrator (#chat), Research (#research), Infra (#infra), Journal (#journal)
- [x] All agents know Agent Alpha and Beta — ask any channel about their performance
- [x] SOFI collar strategy active (200 paper shares, 5 trigger actions)

### Intelligence + Debate
- [x] market_intelligence.py — on-demand packet (VIX, 11 symbols, events, earnings, news)
- [x] debate_chamber.py — Bull/Bear/Judge selects top 2 trades from full strategy toolkit
- [x] scan_options.py — 100+ tickers, 5M volume filter, 200 OI gate, 10 strategies evaluated

### Agent Alpha and Beta (autonomous)
- [x] autonomous_execution.py — Alpaca paper orders, strategy gate, contract verification
- [x] run_pipeline.py — 15-min cron, condition-gated (VIX, regime, timing, daily count)
- [x] Agent Alpha: full strategy toolkit, any liquid ticker, condition-driven
- [x] Agent Beta: condors/butterflies, any liquid ticker, VIX 13-28 entry gate
- [x] Max 2 entries/day, hard close 3:30 PM, monitor every 15 min
- [x] Cron active: `*/15 9-16 * * 1-5` + `5 16 * * 1-5` (EOD)

### Self-Evolution
- [x] self_evolution.py — 6-step, 5-gate validation, updates sofi_collar.json
- [x] pattern_engine.py — statistical detection (activates at 20+ closed trades)

### Google Sheets Journal
- [x] 4 tabs: All Trades, Agent Trades, Manual Trades, Summary
- [x] Auto-syncs after every execution and manual log
- [x] Agent trades: A-prefix IDs, source=agent_alpha/agent_beta
- [x] Manual trades: P-prefix IDs, source=manual

### System Health
- [x] system_test.py — 43-check end-to-end health test
- [x] eod_summary.py — daily Alpha/Beta/Amit summary posted to #chat at 4:05 PM

### Current test result: 41/43 (2 fixes pending — see below)

---

## Immediate — Fix Before Tomorrow Morning

```
FIX 1: Install aiohttp
  pip3 install aiohttp --break-system-packages

FIX 2: Copy google_service_account.json to correct location
  cp /home/trader/QuantAI/v2/shared-data/google_service_account.json \
     /root/quantai-v2/shared-data/google_service_account.json

Verify: python3 v2/shared-data/scripts/system_test.py → should show 43/43
```

---

## Track 1: Trading Progress

### Now — First Active Month
- [ ] system_test.py showing 43/43
- [ ] Watch first Alpha/Beta trades execute tomorrow morning (9:45 AM ET)
- [ ] Log SOFI trades as you manage the collar
- [ ] Score each trading day in #chat
- [ ] Week 1 review: compare agent win rate vs manual in Google Sheet

### Month 1 Targets
- [ ] Alpha: 20+ trades, tracking toward 60%+ win rate
- [ ] Beta: 10+ trades, tracking toward 65%+ win rate
- [ ] Self-evolution: first validated config change applied
- [ ] SOFI collar: 2 full cycles (sell, expire, repeat)

### Month 2-3
- [ ] Pattern engine activates (20+ closed trades) — first statistical insights
- [ ] Scale SOFI to 500 shares if thesis holding
- [ ] Add 1-2 more collar candidates from scanner (HIMS at IV rank 93 is strong)

### Month 3+ Pre-Live
- [ ] Subscribe MarketXLS Advanced ($94/mo)
- [ ] Subscribe Unusual Whales ($48/mo)
- [ ] Open IBKR for XSP (Section 1256 = 60/40 tax)
- [ ] Complete pre-live checklist in SYSTEM_STATE.md
- [ ] First live capital: $5k, max 2 positions

### Month 4+ Scale
- [ ] Grow live allocation as win rate holds
- [ ] Migrate SPY/QQQ income to XSP on IBKR for tax efficiency
- [ ] Scale SOFI collar to 1,000 shares ($1k/month target)
- [ ] Target: $3-5k/month, building to $50k+ deployed

---

## Track 2: Platform Next Steps

### Next Session
```
P1  tradingview-mcp (328 stars, free, vetted)
    Adds multi-timeframe RSI, BB squeeze detection
    Wire as 7th signal into market_intelligence.py

P1  Proactive position closing
    When monitor detects 50% profit or 2x stop, auto-close via Alpaca
    Currently: alerts to Discord but doesn't close automatically

P2  Morning intelligence cron (6:30 AM ET)
    Fresh packet waiting when Amit wakes up
    Add to crontab: 30 6 * * 1-5  python3 market_intelligence.py --force
```

### Month 2
```
P2  pattern_engine.py auto-runs Friday when 20+ closed trades exist
P2  FRED API macro data in intelligence packet (free, key exists)
P2  Proactive news alerts from Orchestrator when VIX spikes or FOMC approaches
```

### Month 3+ Pre-Live
```
P3  MarketXLS Advanced ($94/mo) — subscribe at live transition
P3  Unusual Whales ($48/mo) — subscribe at live transition
P3  IBKR connector for XSP trading
P3  Polygon.io ($29/mo) — evaluate need after 4+ weeks paper data
```

---

## Weekly Health Check Routine

Every Friday:
```bash
# Run system test
python3 /home/trader/QuantAI/v2/shared-data/scripts/system_test.py

# Check pipeline log for the week
grep -E "entry|executed|approved|skipped" \
  /root/quantai-v2/shared-data/logs/pipeline.log | tail -30

# Check evolution log
tail -5 /root/quantai-v2/shared-data/logs/evolution_log.jsonl

# Sync sheets
python3 /home/trader/QuantAI/v2/shared-data/scripts/sheets_sync.py
```

In Discord:
```
#chat:    score today 80/100 --consolidate
#journal: stats
```

---

## Success Metrics

| Milestone | Target | Check |
|---|---|---|
| Week 2 | 43/43 system test, agents trading | system_test.py |
| Week 4 | Alpha >55% win rate over 15+ trades | Google Sheet Agent Trades tab |
| Month 2 | Alpha >60%, Beta >65% over 40+ trades | Google Sheet + journal stats |
| Month 2 | Self-evolution first change applied | evolution_log.jsonl |
| Month 3 | Positive P&L both streams | Google Sheet Summary tab |
| Month 4 | Live $5k, win rate holds | real/trades.jsonl |
| Month 12 | $50k+ deployed, $3-5k/month | Full system |

---

## What We Are NOT Building

- Manual approval for agent trades — they run autonomously within guardrails
- Fixed entry times — agents enter when conditions are right, not on a clock
- CTO agent — Infra agent handles all system work
- MarketXLS now — subscribe at live transition only
