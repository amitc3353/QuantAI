# Auto$ — Autonomous Trading Arena Blueprint
## Two AI traders competing. Self-learning. Live data. Paper money.

---

## The Agents

### Agent Alpha — "The Momentum Hunter"
- **Model:** Claude Sonnet (smarter, deeper analysis)
- **Capital:** $20,000 (Alpaca paper)
- **Style:** Follows strength. Sells premium in the direction of the trend.
- **Personality:** Confident, aggressive within rules, trusts technical momentum
- **Edge:** Pattern recognition, multi-timeframe analysis, quick to capitalize on moves
- **DTE preference:** Adapts — 0DTE when VIX high, 7-14 DTE in normal conditions

### Agent Beta — "The Contrarian"
- **Model:** Claude Haiku (faster, cheaper, forces efficiency)
- **Capital:** $20,000 (Alpaca paper)
- **Style:** Fades extremes. Sells premium against overreaction and panic.
- **Personality:** Patient, value-oriented, comfortable being early
- **Edge:** Mean-reversion, oversold/overbought exploitation, IV crush plays
- **DTE preference:** Adapts — prefers 7-14 DTE for mean-reversion time, 0DTE on high-conviction setups

### Reserve Capital
- $60,000 remains as cash reserve in the $100K paper account
- Agents can request capital increase after proving 3 consecutive profitable weeks
- Max allocation per agent: $30,000 (approved by you)

---

## Strategy Menu (both agents choose from this)

| Strategy | When to use | Max risk/trade |
|---|---|---|
| **Bull put spread** | RSI < 35, stock oversold, support nearby | $500 |
| **Bear call spread** | RSI > 65, stock overbought, resistance nearby | $500 |
| **Iron condor** | VIX 18-28, range-bound, no catalysts | $500 |
| **Collar** | Strong conviction long, wants protection | $1,000 |
| **Calendar spread** | Earnings approaching, IV crush expected | $300 |
| **Directional debit spread** | High conviction breakout/breakdown | $200 |

Agents choose strategy + ticker + DTE + strikes based on their style and market regime.
They are NOT limited to specific DTEs — 0DTE, 1DTE, 7DTE, 14DTE, 30DTE all available.

---

## Market Regime System

Agents classify regime every morning before first trade:

| Regime | Triggers | Strategy shift |
|---|---|---|
| **Bull** | VIX < 18, SPY > 200 DMA, uptrend | Put spreads, aggressive collars |
| **Neutral** | VIX 18-25, SPY near 200 DMA | Iron condors, calendar spreads |
| **Correction** | VIX 25-35, SPY < 200 DMA | Call spreads, wide distance, small size |
| **Crisis** | VIX > 35, SPY far below 200 DMA | NO premium selling. Cash. Recovery calls only. |

Current regime (Mar 2026): CORRECTION — VIX 31, SPY below 200 DMA, Iran war + oil shock.

---

## Pro Trader Knowledge Base (embedded in each agent's workspace)

### Core principles
- Never fight the trend on short-dated options
- IV crush after earnings is predictable — don't sell premium INTO earnings, sell AFTER
- When VIX spikes, sell premium far OTM — distance is your friend
- Time decay accelerates exponentially in last 2 hours — don't fight theta
- The first 30 min after open is noise — best entries come 10:00-10:30 AM
- Friday afternoon theta bleed is free money on weekly spreads
- Sector rotation matters — don't sell puts on a sector rotating OUT

### Crash playbook (learned from history)
- **2008 (slow bleed):** When 200 DMA breaks and VIX stays >30 for 2+ weeks, go fully defensive. Sell call spreads only. The crash took months — there were profitable call spread opportunities every week on bear rallies.
- **2020 March (fast crash):** VIX hit 82. All premium selling failed. The lesson: when VIX > 50, stop selling premium entirely. Buy cheap recovery calls 60-90 DTE. The V-shaped recovery made those worth 5-10x.
- **2022 (grinding bear):** Slow, relentless decline. Iron condors worked but needed wide wings. Put spreads got killed. Call spreads were the consistent winner. Key: selling against rally attempts, not trying to catch the bottom.
- **Current (2026 Iran/oil):** Correction regime. Oil shock is binary — ceasefire = rally, escalation = further decline. Position for either: small size, wide distance, avoid energy sector.

### Position management rules
- Take profit at 50% of credit received — don't get greedy
- Stop loss at 2x credit — cut losers fast
- 0DTE: hard close at 3:30 PM, no exceptions
- Rolling: only roll for a net credit, never add risk to save a loser
- If 3 consecutive losses: reduce size by 50% for next 5 trades

---

## Trading Schedule

| Time (ET) | Action |
|---|---|
| 9:35 AM | Morning scan: regime check, overnight news, VIX, pre-market data |
| 9:45 AM | First trading window opens |
| Every 30 min | Scan for new setups + monitor open positions |
| 3:30 PM | Close all 0DTE positions |
| 3:45 PM | Last entry window (only for next-day+ expiry) |
| 4:15 PM | EOD scoring + self-evolution pipeline |
| 4:30 PM | Daily summary posted to #chat |

Agents trade ANY time during market hours (9:30-4:00). The 30-min scan cycle means
they check 13 times per day. They can act on any scan if a setup passes their filters.

---

## Data Sources (all free)

| Source | What | How |
|---|---|---|
| **yfinance** | Price, volume, options chains, IV, technicals, historical data | Python SDK via Bash |
| **Finnhub** | News, earnings calendar, insider trades, economic events | REST API (60/min) |
| **Alpaca** | Paper trading execution, position management, account balance | Python SDK |
| **DuckDuckGo** | Breaking news, geopolitical events | OpenClaw built-in web search |
| **FRED** | Fed rate, CPI, unemployment, yield curve | REST API (free) |
| **Alpha Vantage** | Supplementary quotes, fundamentals | REST API (25/day) |

News check: every 30 minutes, agents pull latest Finnhub market news + SOFI news.
If breaking news scores high urgency (war escalation, Fed emergency, market halt),
agents evaluate whether to close positions or add hedges immediately.

---

## Guardrails (non-negotiable, enforced before every trade)

### Per-trade limits
- Max risk: $500/trade ($1,000 for collars only)
- Max contracts: 5 per position
- Min distance: 4% from current price (6% when VIX > 25)
- No earnings within 7 days
- Bid/ask spread < $0.25 (liquidity check)

### Per-agent limits
- Max 3 open positions at a time
- Daily loss limit: $500 → agent pauses for the day
- Weekly loss limit: $1,500 → agent pauses for the week
- Monthly drawdown limit: $3,000 → agent pauses, you review

### System-wide limits
- VIX > 40: ALL trading paused, both agents go to cash
- No trading during FOMC announcements (30 min before through 30 min after)
- No trading first 15 min or last 15 min of market

### Self-evolution constraints
- No parameter change can increase max risk per trade
- No parameter change can reduce distance below 3%
- Changes must be validated against 30-day backtest
- Max 1 parameter change per day per agent
- Changes logged to Google Sheet with before/after + reasoning

---

## Self-Evolution Engine (runs 4:15 PM daily)

### 6-step pipeline per agent:
1. **OBSERVE** — Read today's trades from journal. What happened? P&L, market conditions, timing.
2. **CRITIQUE** — Compare outcome to current parameters. Be specific: "Entry at 9:50 AM lost $120 because opening volatility crushed the spread. Entry after 10:30 would have won."
3. **GENERATE** — Propose ONE change: `[param] [old] → [new] because [evidence]`. No evidence = no change.
4. **VALIDATE** — 5 gates:
   - Constitution: violates guardrails? → REJECT
   - Regression: backtest on 30-day journal → win rate must not drop
   - Size: change too large? (delta shift >0.05 in one step) → REJECT
   - Drift: drifting from core strategy? → REJECT
   - Safety: could cause larger losses? → REJECT
5. **APPLY** — Write change, bump config version, log to Google Sheet
6. **CONSOLIDATE** (weekly, Friday) — Compress repeated observations into strategy principles

---

## Google Sheets Trade Log

### Tabs:
- **Alpha Trades** — every trade by Agent Alpha
- **Beta Trades** — every trade by Agent Beta
- **Manual Trades** — your SOFI collar + credit spreads on Webull
- **Scoreboard** — monthly Alpha vs Beta comparison
- **Evolution Log** — every parameter change by both agents

### Columns per trade:
Date, Time, Agent, Strategy, Ticker, Direction, Short Strike, Long Strike,
DTE, Credit, Max Loss, VIX at Entry, RSI at Entry, Regime, Outcome,
P&L ($), P&L (%), Time Held, Close Reason (target/stop/expiry/manual),
Reasoning (1 sentence), Lesson Learned (post-close)

### Access:
- Google Sheets API (free, agents write via Python)
- You view from phone anytime
- Filterable, sortable, chartable

---

## Scoring Formula (monthly)

```
Score = (win_rate × 40) + (total_profit_pct × 40) + (risk_adjusted × 20)

Where:
  win_rate = wins / total_trades (0-100 scale)
  total_profit_pct = net_P&L / starting_capital × 100
  risk_adjusted = (net_P&L / max_drawdown) × 10, capped at 20
```

Monthly winner gets: their identity emphasized, bigger font in the scoreboard,
and first pick of capital increase requests. Loser gets: "learn from Alpha/Beta"
injected into their next morning scan prompt.

---

## Build Order

### Session 1: Agent Setup + Execution
- Alpha + Beta workspace files (AGENTS.md, SOUL.md, knowledge base)
- Alpaca paper trading Python wrapper (place orders, check positions, get balance)
- Guard engine as workspace checklist (not separate service)
- Strategy menu implementation (credit spread builder, iron condor builder)
- Test: both agents can execute a paper trade via Alpaca

### Session 2: Data + Google Sheets
- Market regime classifier script
- Finnhub news integration
- Google Sheets API setup + trade logging script
- Scanner upgrade: agents use live data to find trades
- Test: agent scans, finds trade, executes, logs to sheet

### Session 3: Self-Evolution + Backtesting
- Self-evolution 6-step pipeline
- Historical backtest capability (yfinance 1-5 year data)
- Crash playbook integration (regime-specific strategy shifts)
- Evolution log to Google Sheet
- Test: agent completes full day cycle including EOD evolution

### Session 4: Cron + Monitoring + Competition
- Cron jobs: 30-min scan cycle, EOD scoring, weekly consolidation, monthly scoreboard
- Position monitor (check open positions, apply stop/target logic)
- Error tracker (Infra agent watches both traders)
- Daily summary to #chat
- Full end-to-end test: simulate a complete trading week

---

## Cost Estimate

| Item | Monthly |
|---|---|
| Alpha (Sonnet): ~15-20 trade decisions/week | $12-20 |
| Beta (Haiku): ~15-20 trade decisions/week | $2-4 |
| Self-evolution (Haiku): daily for both | $3-5 |
| Existing agents (Orchestrator, Research, Infra, Journal) | $15-25 |
| VPS (Hetzner) | $12 |
| Data sources | $0 |
| **Total** | **~$45-65/mo** |

No MarketXLS needed until going live. All data sources are free.
