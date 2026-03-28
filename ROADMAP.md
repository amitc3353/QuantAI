# QuantAI v2 — Roadmap

## Strategy: SOFI Collar — Income Machine

### Phase 1: Foundation (This Weekend — Mar 28-29)
- [x] OpenClaw multi-agent architecture designed
- [x] 4 agent workspaces created (Orchestrator, Research, Infra, Journal)
- [x] SOFI collar strategy config (`sofi_collar.json`)
- [x] SOFI data fetch script (`fetch_sofi.py`)
- [x] Discord setup guide + bot creation instructions
- [x] VPS setup script (Node.js, OpenClaw 2026.3.23-2, security hardening)
- [x] v1 Docker system archived to `v1-archive/`
- [ ] Install OpenClaw on VPS
- [ ] Create Discord server + 4 bots
- [ ] Configure .env with all tokens
- [ ] Verify all 4 agents respond from phone
- [ ] Test SOFI data fetch

### Phase 2: Automation (Week 1 — Mar 30 - Apr 4)
- [ ] Cron: Daily SOFI brief at 6:30 AM ET
- [ ] Cron: Health check at 8:00 AM ET
- [ ] Cron: Weekly journal digest Friday 4:30 PM
- [ ] Research agent: SOFI options chain analysis (IV, Greeks, premium)
- [ ] Journal agent: natural language trade logging working
- [ ] Infra agent: error detection + auto-reporting
- [ ] Price alert system: SOFI at trigger levels → #alerts

### Phase 3: Paper Trading (Weeks 1-4 — Apr 2026)
- [ ] First paper collar opened on SOFI
- [ ] First biweekly call sold (paper)
- [ ] First monthly put bought (paper)
- [ ] Track all trades in journal (paper mode)
- [ ] Week 1 review: strategy mechanics working?
- [ ] Week 2 review: premium targets achievable?
- [ ] Week 4 review: ready for real money?

### Phase 4: Live Trading (Month 2+)
- [ ] Switch to real money: 200 shares, $3,000
- [ ] First real collar trade
- [ ] Monthly P&L tracking vs $170 target
- [ ] Emotional journal: how does real money feel different?

### Phase 5: Scale (Month 4+)
- [ ] Increase to 500 shares (5 contracts)
- [ ] Target: $500-$1,000/month
- [ ] Add second ticker if SOFI thesis holds
- [ ] Long term: 1,000 shares, $1,000/month

---

## Platform Roadmap

### Immediate (this week)
```
P0  Install OpenClaw on VPS, connect Discord
P0  Verify all 4 agents respond from phone
P0  Test SOFI data fetch script
P1  Set up cron jobs (daily brief, health check, weekly digest)
P1  Test journal trade logging flow
```

### Next week
```
P1  SOFI options chain analysis in research brief
P1  IV rank tracking for call-sell timing
P1  Infra agent error monitoring + alerting
P2  Weekly journal analysis (patterns, win rate)
P2  Add Finnhub news integration for SOFI
```

### Week 3-4
```
P2  Price alert system at trigger levels
P2  Options premium calculator for strike selection
P2  Historical brief comparison (trend analysis)
P3  Add second ticker support to research agent
P3  Infra agent: auto-PR for config improvements
```

---

## Success Metrics

### Trading
- Week 1-4: Paper collar mechanics smooth, all legs logged
- Month 1: Biweekly calls sold on schedule, puts renewed monthly
- Month 2: Real money, net income ≥ $150/month
- Month 4+: Scale to 5+ contracts, $500+/month

### Platform
- Day 1: All 4 agents respond from phone ✓
- Week 1: Daily SOFI brief arriving by 6:30 AM ET ✓
- Week 2: Trade logging smooth, no missed entries
- Month 1: Zero unplanned downtime, health checks clean
- Ongoing: System improves without manual intervention

---

## Architecture Principle

**You (Amit) focus on:**
1. Reading the daily SOFI brief on your phone
2. Making trading decisions (sell call, buy put, roll, hold)
3. Logging trades in #journal
4. Reviewing weekly digests

**Agents handle everything else:**
- Research: daily data pull, analysis, recommendations
- Infra: system health, error fixing, deployments
- Journal: trade records, stats, pattern detection
- Orchestrator: routing, answering questions, strategy enforcement

---

## v1 → v2 Migration Notes

### What changed
- Docker Compose (5 containers) → OpenClaw (1 process, 4 agents)
- Custom Python Discord bot → OpenClaw Discord integration
- SPY 0DTE iron condors → SOFI collar strategy
- CTO agent via file queue → Infra agent with direct bash/git access
- Fragile cron via APScheduler → OpenClaw native cron
- Custom guard engine → Strategy rules in agent workspace

### What we kept
- Hetzner VPS (same machine)
- Alpaca paper trading
- yfinance + Finnhub data sources
- Journal concept (paper vs real separation)
- GitHub repo (same repo, new branch)

### What we archived
- `v1-archive/docker-compose.yml`
- `v1-archive/discord-bot/` (custom Python bot)
- `v1-archive/guard-engine/` (FastAPI guards)
- `v1-archive/orchestrator/` (APScheduler + agents)
- `v1-archive/services/` (market data, context builder, etc.)
- `v1-archive/scripts/` (CTO listener, deploy scripts)

Old configs in `configs/` and data in `data/` kept at repo root for reference.
