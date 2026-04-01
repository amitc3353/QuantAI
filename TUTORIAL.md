# QuantAI — System Tutorial
**Updated: March 31, 2026**

## How to use your autonomous trading system, every day.

---

## Your Discord Server — The Command Center

Everything happens in Discord. Here's what each channel is for:

| Channel | What happens here | Who posts |
|---|---|---|
| **#command** | Slash commands — trading, analysis, system ops | You |
| **#chat** | Talk naturally — ask questions, discuss strategy, `cto:` tasks | You + AI |
| **#research** | Morning briefs, CTO scan, intelligence summaries, debate outcomes | Orchestrator (auto) |
| **#trade-proposals** | Trade cards from Debate Chamber. React ✅ approve · ❌ reject · 🔄 defer | Orchestrator + You |
| **#execution-log** | Filled orders, confirmations, closes | Execution Agent (auto) |
| **#guard-log** | Guard approvals and rejections with reason codes | Guard Engine (auto) |
| **#system-health** | Startup, EOD scores, health checks, evolution events, digests | System (auto) |
| **#pr-updates** | Self-evolution changes + rejected proposals with gate results | Self-Evolution (auto) |

---

## What Actually Happens Each Day

### 6:00 AM Monday — CTO Intelligence Scan
The CTO agent scans GitHub, arXiv, and PyPI for new tools and integrations.
Posts a ranked proposal list to #research before anything else runs.

### 6:20 AM Mon–Fri — Market Intelligence Build
The intelligence service aggregates everything:
- VIX + term structure + regime classification
- RSI, MACD, Bollinger Bands, EMA200 for all 10 watchlist symbols
- Fear & Greed, yield curve, treasury yields
- Earnings calendar — flags any tickers within 14 days of earnings
- News sentiment via Finnhub for each symbol
- Open positions with live P&L and recommended actions
- Pre-screened high conviction setups ranked by score
- Risk flags: HALT / WARNING / CAUTION

Saved as `data/cache/market_intelligence_packet.json`.

### 6:25 AM Mon–Fri — Debate Chamber (Pre-Market)
The three-agent debate runs on the intelligence packet:

1. **Proposal Agent** reads the packet → generates 3–5 trade candidates with strikes, credits, and thesis
2. **Bull Agent** argues FOR each trade simultaneously
3. **Bear Agent** argues AGAINST each trade simultaneously
4. **Judge Agent** scores the debate → selects TOP 2
5. **Guard Engine** validates both → posts to #trade-proposals

Each trade card in #trade-proposals shows:
- Full trade structure (strikes, expiry, credit, max loss)
- Bull case (3–5 bullet points)
- Bear case (3–5 bullet points)
- Judge score + reasoning
- Guard result (APPROVED or REJECTED with reason)

**React ✅ to execute · ❌ to reject · 🔄 to defer to mid-session**

### 6:30 AM Mon–Fri — Morning Brief
Standard morning brief with macro context, sector overview, and watchlist summary.
Posts to #research alongside the debate chamber summary.

### 9:50 AM Mon–Fri — Agent 1: Entry 1
Autonomous iron condor entry on SPY.
Pre-entry checks: context score → journal consultation → VIX → flow check → credit → guard.
Trade card posted to #trade-proposals. Executes automatically in paper mode.

### 10:00 AM Monday — Agent 2: Weekly CC Scan
Scans PLTR/TSM/MU/AMD/AVGO/ASML for covered call opportunities.
Earnings blackout (14 days), min IV rank 30, delta 0.20, 21–35 DTE.

### 11:30 AM Mon–Fri — Agent 1: Entry 2 (conditional)
Only if Entry 1 has hit 40%+ profit. Same pre-entry check sequence.

### 1:30 PM Mon–Fri — Market Intelligence Build (Mid-Session)
Refreshes the intelligence packet with mid-session data.
Key additions vs morning: live session P&L on open positions, updated VIX,
afternoon RSI readings, any news that broke during morning session.

### 1:35 PM Mon–Fri — Debate Chamber (Mid-Session)
Second debate chamber run of the day. Focuses on:
- Afternoon setups (different from morning — often covered calls, not condors)
- Position management recommendations for open trades
- Any morning proposals that weren't approved and are still valid

### 4:30 PM Mon–Fri — EOD Scoring
Scores today's trades 0–100. Extracts lessons. Posts to #system-health.

### 4:35 PM Mon–Fri — Self-Evolution Engine
If EOD score < 90:
1. Observes today's journal — wins, losses, timing, credits
2. Critiques: which param caused the biggest misalignment?
3. Generates ONE targeted change with evidence
4. Runs 5 validation gates (constitution, size, drift, safety, backtest)
5. If all pass → applies change, bumps config version, posts diff to #pr-updates
6. If any gate fails → logs rejection reason, stores observation for future data

### 4:45 PM Friday — Weekly Review + Consolidation
- Weekly performance review posted to #system-health
- Strategy consolidation: 4+ weeks of observations compressed into durable principles
- CTO report on system health and reliability
- Correlation analysis: does context score predict outcomes?

---

## Reading Trade Cards in #trade-proposals

Every trade card looks like this:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 TRADE PROPOSAL — SPY IRON CONDOR
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Legs:
  SELL PUT $555 0DTE
  BUY  PUT $550 0DTE
  SELL CALL $575 0DTE
  BUY  CALL $580 0DTE

Credit: $0.75 | Max Loss: $4.25 (1.7%)
Prob of Profit: 68%
Debate Score: 74/100

Thesis: SPY range-bound at 563, VIX 17 contango. RSI 52 neutral.
Invalidation: SPY breaks above 572 or below 558 on volume.

🐂 Bull: [3-5 reasons this trade works]
🐻 Bear: [3-5 reasons to be careful]
⚖️ Judge: Strong risk/reward. Bear point on gap risk is valid but wings absorb $5 moves.

Guard: ✅ GUARD APPROVED
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
React ✅ to execute · ❌ to reject · 🔄 to defer
```

In autonomous paper mode, approved trades execute automatically.
In live mode, you must react ✅ to confirm.

---

## Reading #pr-updates (Self-Evolution)

When evolution applies a change:
```
🧬 Evolution Applied — Agent 1
param:     min_credit
old value: 0.50
new value: 0.60
version:   v3

Rationale: 3 of last 5 stop-outs had credit below $0.55.
Evidence: avg loss -$180 on sub-$0.55 entries vs +$32 on above-$0.55 entries.
All 5 gates passed ✅ | EOD Score was 72/100
```

When a proposed change is rejected:
```
🔬 Evolution Proposed — REJECTED
Change: short_delta 0.10 → 0.08
Failed gates:
• ❌ Regression: win rate degraded -4.2% over 30-day backtest
Observation stored for future data accumulation.
```

You don't need to do anything with these. They're your transparency window into how the system is improving itself.

---

## Commands Reference

### Trading Commands (#command)

| Command | When to use | Example |
|---|---|---|
| `/account` | Check portfolio value, cash, buying power | `/account` |
| `/positions` | See all open positions with P&L | `/positions` |
| `/quote SPY` | Get live price, bid/ask, volume | `/quote symbol:SPY` |
| `/buy` | Buy shares (through guards, needs ✅ in live mode) | `/buy symbol:SPY qty:1` |
| `/sell` | Sell shares | `/sell symbol:SPY qty:1` |
| `/close` | Close entire position | `/close symbol:SPY` |
| `/emergency_stop` | HALT all new trades immediately | `/emergency_stop` |
| `/resume` | Resume after halt | `/resume` |

### Options Analysis Commands

| Command | When to use | Example |
|---|---|---|
| `/greeks` | Compute delta/theta/gamma/vega | `/greeks symbol:SPY strike:565 dte:0 option_type:put iv:0.18` |
| `/iron_condor` | Full iron condor analysis | `/iron_condor symbol:SPY put_short:555 put_long:550 call_short:575 call_long:580 dte:0 iv:0.18` |
| `/bull_put` | Bull put spread analysis | `/bull_put symbol:SPY short_strike:555 long_strike:550 dte:30 iv:0.22` |
| `/covered_call` | Covered call analysis | `/covered_call symbol:PLTR strike:32 dte:28 iv:0.55` |

### Intelligence & Guard Commands

| Command | When to use | Example |
|---|---|---|
| `/guard_check` | Test if a trade would pass guards | `/guard_check symbol:SPY position_pct:2 max_loss_pct:1.7 dte:0` |
| `/rules` | View current guard rules | `/rules` |
| `/status` | Quick system status | `/status` |
| `/journal` | Today's trades + P&L + lessons | `/journal` |
| `/performance [days]` | Win rates, P&L, context correlation | `/performance days:14` |
| `/lessons [query]` | Browse/search lessons | `/lessons iron condor` |

### Dev & Ops Commands

| Command | When to use | Example |
|---|---|---|
| `/health` | Full system health | `/health` |
| `/deploy` | Pull latest code from GitHub + rebuild | `/deploy` |
| `/logs` | View service logs | `/logs service:orchestrator lines:50` |
| `/restart` | Restart a service (needs ✅) | `/restart service:trader-orchestrator` |
| `/cto [task]` | Ask CTO agent to investigate | `/cto check why Entry 2 keeps stopping out` |

### Memory Commands (#chat)

```
remember: [lesson]     — save a lesson permanently
lessons                — view all saved lessons
lessons [query]        — search lessons
stats                  — view trade statistics
cto: [task]            — trigger CTO agent on-demand
```

---

## The #chat Channel

Talk naturally. No slash commands needed.

**Learning options:**
```
What is an iron condor in simple terms?
Why do we use delta 0.10 for short strikes?
What happens to our position if SPY drops 2% today?
Explain why we only change one param at a time in evolution.
```

**Strategy discussion:**
```
Should we trade iron condors on FOMC days?
Why did the Debate Chamber reject that QQQ trade?
What patterns do you see in our losing trades this week?
The Bear Agent kept flagging gap risk — should we widen wings?
```

**Intelligence review:**
```
What was the intelligence packet saying at 6:20 AM today?
Which symbols have the best setup right now?
Why is the market regime showing CAUTION?
How has context score correlated with our win rate so far?
```

---

## The Guard Engine — Rules That Never Bend

Every trade from every source (Agent 1, Agent 2, Debate Chamber) must pass ALL guards.

**Position rules:** max 5% portfolio/trade · max 2% loss at entry · max 10 contracts · min 21 DTE for CC · min $0.15 bid-ask spread
**Portfolio rules:** max ±0.30 delta · max -$50/day theta · max 8 positions · max 3 per sector · -3% daily triggers pause
**Timing rules:** no trading 9:30–9:45 AM · no trading 3:45–4:00 PM · 14-day earnings blackout · VIX > 35 = advisory only · 24h cooldown after closing

---

## Self-Evolution — What It Can and Can't Change

**Can tune (with gate approval):**
- `min_credit` — minimum credit to enter
- `short_delta` — target delta for short strikes (0.05–0.20 range)
- `wing_width` — wing width in dollars
- `profit_target_pct` — % of max profit to close
- `entry2_profit_threshold` — Entry 2 trigger level
- Context weight parameters (after 4 weeks of data)

**Can never change (constitution-protected):**
- `max_loss_pct` (stays ≤ 2%)
- `hard_close_hour` (stays ≤ 3:30 PM)
- `vix_upper` (stays ≤ 30)
- `earnings_blackout_days` (stays ≥ 14)
- `max_daily_loss` (stays ≤ $1,000)

---

## Paper vs Live Trading

Currently: **PAPER MODE** — all trades use fake money via Alpaca simulator.

### Switching to live (future):
1. Complete pre-live checklist in SYSTEM_STATE.md
2. Prove consistent results (40+ trades, 60%+ win rate, 8+ weeks paper)
3. Add MarketXLS MCP ($94/mo Advanced for real-time Greeks)
4. Add Unusual Whales ($48/mo for real sweep detection)
5. Create separate live Alpaca API keys (never reuse paper keys)
6. Switch `TRADING_MODE=live` in .env
7. Start at $5k allocation, max 2 simultaneous live positions

---

## Key Principles

1. **Never override the guards.** They protect you from compounding losses.
2. **Paper first, always.** The debate chamber proves ideas before real capital.
3. **One change at a time.** Evolution moves in small, validated steps.
4. **Read the debates.** The Bull/Bear transcripts are where the learning happens.
5. **Watch #pr-updates.** That's the system improving itself — understand each change.
6. **The morning packet at 6:20 AM is your edge.** Read it before market open.
