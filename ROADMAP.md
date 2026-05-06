# QuantAI — Master Roadmap
**Last updated: 2026-05-03**

---

## What This System Is

Three Claude trading agents (Alpha, Beta, Gamma) execute autonomously on cron. Sentinel (operations agent) watches the system 24/7 and self-heals routine issues. OpenClaw provides Discord-side conversational agents (Orchestrator / Research / Infra / Journal) that answer questions but never place trades. Everything logs to Google Sheets. System tests itself with 44 checks. The 13-check `system_monitor.py` runs every 2 min and feeds Sentinel.

**Status: Paper trading on IBKR (DUP851506, ~$1M equity). Cron running. Self-learning chain operational. Sentinel deployed 2026-05-03.**

---

## What's Built and Live

### Foundation
- [x] Four OpenClaw agents: Orchestrator (#chat), Research (#research), Infra (#infra), Journal (#journal)
- [x] Three trading agents: Alpha (ETF + equity), Beta (SPX/XSP/VIX index), Gamma (RSI mean-reversion)
- [x] Sentinel (operations agent, peer of trading agents)
- [x] `scripts/sync_workspaces.sh` — keeps OpenClaw in sync with git repo

### Intelligence + Debate
- [x] `market_intelligence.py` — on-demand packet (VIX, technicals, events, earnings, news)
- [x] `debate_chamber.py` — Bull/Bear/Judge selects top 2 trades for Alpha
- [x] `scan_options.py` — credit spreads + diagonals + iron condors across 100+ tickers

### Agent Alpha (defined-risk premium, ETF + equity)
- [x] `autonomous_execution.py` — IBKR mleg orders, live chain lookup, `REQUIRES_SHARES` strategy gate
- [x] Phase 5 partial-fill safeguard: `order_submitted` flag + `finally: sleep(0.5)` flush + post-submit recovery via `get_open_orders(coid)`
- [x] `run_pipeline.py` — 15-min cron, condition-gated (VIX, regime, timing, daily count)
- [x] Max 2 entries/day, hard close 3:30 PM, monitor every 15 min

### Agent Beta (regime-driven, SPX/XSP/VIX)
- [x] `beta_agent.py` — entry point, refuses if `BROKER_TYPE != ibkr`
- [x] 12-regime classifier + 8 strategy modules
- [x] Per-source risk gates scoped to `agent_beta`
- [x] Weekly historical event-move data via `event_moves_seeder.py`

### Agent Gamma (RSI(10) mean-reversion)
- [x] `gamma_agent.py --scan` (4:30 PM ET) → builds RSI watchlist
- [x] `gamma_agent.py --execute` (9:33 AM ET) → fires entries
- [x] Connors RSI(10) method on equity options

### Sentinel (operations agent)
- [x] `system_monitor.py` — deterministic 13-check report, every 2 min, all days
- [x] `sentinel_agent.py` — wrapper-driven schedule (8:30 AM apply, hourly observe, 4:15 PM apply, 9 PM observe weekdays + Sat/Sun 10 AM)
- [x] Code-enforced safety rails: `NEVER_MODIFY_PATHS`, `NEVER_TOUCH_PATHS`, position-gated services
- [x] Built-in safe_auto: errors.db catalog reclassification of known-noise patterns
- [x] Discord ✅/❌ approvals via reactions in `#karna-approvals`

### Self-Learning Pipeline
- [x] `agent_self_diagnosis.py` — post-close 3-gap diagnosis per closed trade
- [x] `trade_reviewer.py` — post-close thesis review, lessons, parameter suggestions (Haiku 2000-token responses)
- [x] `weekly_synthesis.py` — Friday aggregation of week's diagnoses + reviews into per-agent report

### Google Sheets Journal
- [x] 3 tabs: All Trades, Agent Trades, Summary
- [x] Auto-syncs after every execution
- [x] Trade IDs: A### (Alpha), B### (Beta), G### (Gamma)

### System Health
- [x] `system_test.py` — **44/44 passing** as of 2026-05-03
- [x] `eod_summary.py` — daily Alpha/Beta/Gamma summary posted to #chat at 4:05 PM
- [x] `heartbeat_monitor.py` — IBKR port 4002 + pipeline beat probe, 24/7
- [x] Error pipeline: `collect_errors.py` (every 2 min) → `errors.db` → catalog
- [x] 60-entry error catalog at `docs/error-catalog.json` with runbook references
- [x] **641+ pytest tests passing** — including 89 Sentinel tests + 26 manual-workflow regression tests

### Recently Retired (2026-05-03)
- [x] Manual trading workflow — fully autonomous, no manual stream
- [x] `auto_heal.py` → replaced by Sentinel
- [x] `self_evolution.py` + `fetch_sofi.py` → SOFI-only, no longer needed

---

## Track 1: Trading Progress

### Now — First Active Month
- [ ] Watch first Alpha/Beta/Gamma trades execute Monday morning
- [ ] Score each trading day in #chat
- [ ] Week 1 review: compare three agents' win rates in Google Sheet
- [ ] Sentinel daily digest in #system-health — review what was applied vs queued

### Month 1 Targets
- [ ] Alpha: 20+ trades, tracking toward 60%+ win rate
- [ ] Beta: 10+ trades, tracking toward 65%+ win rate
- [ ] Gamma: baseline established (10+ trades minimum)
- [ ] First validated capability_request actioned (from agent_self_diagnosis output)
- [ ] First weekly_synthesis report consumed and acted on

### Month 2–3
- [ ] Pattern engine activates (20+ closed trades) — first statistical insights
- [ ] Strategy parameter tuning based on weekly_synthesis suggestions
- [ ] Sentinel: zero quarantined fixes for 30 consecutive days
- [ ] First Sentinel-detected anomaly auto-resolved without operator intervention

### Month 3+ Pre-Live
- [ ] Subscribe MarketXLS Advanced ($94/mo) — real-time Greeks
- [ ] Subscribe Unusual Whales ($48/mo) — sweep detection
- [ ] Open IBKR live account (paper graduates)
- [ ] Complete pre-live checklist in SYSTEM_STATE.md
- [ ] First live capital: $5k, max 2 positions

### Month 4+ Scale
- [ ] Grow live allocation as win rates hold
- [ ] Migrate ETF income to XSP on IBKR for Section 1256 tax efficiency
- [ ] Target: $3–5k/month, building to $50k+ deployed

---

## Track 2: Platform Next Steps

### Near-term
```
P1  Self-learning consumption loop
    capability_requests/ entries should feed into the next agent cycle's
    decision context. Currently: written but not yet read by agents.

P1  Sentinel weekly performance synthesis
    Extend weekly_synthesis.py to update Sentinel's Performance Tracker
    (MTTD, MTTF, false-positive rate, fix counts).

P2  TradingView MCP signal integration
    Multi-timeframe RSI + BB squeeze as additional signal in market_intelligence.

P2  Proactive position closing
    position_monitor already detects 50% profit / 2x stop — wire auto-close
    via broker.place_mleg_order (currently alerts Discord only).
```

### Month 2
```
P2  FRED API macro data in intelligence packet
P2  Pattern engine auto-runs Friday when 20+ closed trades exist
P2  Proactive news alerts from Orchestrator on VIX spikes or FOMC approach
P3  Sentinel "Workflows" dashboard tab — architecture diagram + agent flow
```

### Month 3+ Pre-Live
```
P3  MarketXLS Advanced ($94/mo) — subscribe at live transition
P3  Unusual Whales ($48/mo) — subscribe at live transition
P3  Polygon.io ($29/mo) — evaluate need after 4 weeks paper data
```

---

## Weekly Health Check Routine

Every Friday:
```bash
# 1. Full system test
sudo python3 /home/trader/QuantAI/v2/shared-data/scripts/system_test.py
# Expected: 44/44 passed

# 2. Full pytest suite
cd /home/trader/QuantAI/v2/shared-data/tests && python3 -m pytest .
# Expected: 641+ passed, 0 failed

# 3. Sentinel status
sudo python3 /home/trader/QuantAI/v2/shared-data/scripts/sentinel_agent.py --status

# 4. system_monitor live state
sudo cat /var/dashboard/state/system-health-report.json | jq '.status, .data.checks'

# 5. Pipeline log for the week
sudo grep -E "entry|executed|approved|skipped" \
  /root/quantai-v2/shared-data/logs/pipeline.log | tail -30

# 6. Sync sheets
sudo python3 /home/trader/QuantAI/v2/shared-data/scripts/sheets_sync.py
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
| Week 2 | 44/44 system test, all 3 agents trading | system_test.py |
| Week 4 | Alpha >55% win rate over 15+ trades | Google Sheet Agent Trades |
| Month 2 | Alpha >60%, Beta >65% over 40+ trades | Google Sheet + journal stats |
| Month 2 | First weekly_synthesis suggestion implemented | weekly_reports/*.md |
| Month 3 | Positive P&L across all 3 agents | Google Sheet Summary |
| Month 3 | Sentinel: 30+ days zero quarantine | sentinel --status |
| Month 4 | Live $5k, win rate holds | real/trades.jsonl |
| Month 12 | $50k+ deployed, $3–5k/month | Full system |

---

## What We Are NOT Building

- Manual approval for agent trades — they run autonomously within guardrails
- Manual trading stream — fully autonomous via Alpha + Beta + Gamma
- Strategies that require owning shares (covered calls, collars, CSPs, covered strangles) — blocked by `REQUIRES_SHARES`
- Fixed entry times — agents enter when conditions are right, not on a clock
- CTO agent — Infra agent + Sentinel cover system work
- MarketXLS now — subscribe at live transition only
