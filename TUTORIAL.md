# QuantAI — How to Use Your System
**Updated: April 1, 2026**

Everything runs in Discord. Two trading streams run in parallel — autonomous agent trades and your manual trades. Both log to Google Sheets automatically.

---

## Your Discord Channels

| Channel | What it's for |
|---|---|
| **#chat** | Everything — talk to Orchestrator, get analysis, score the day |
| **#research** | SOFI briefs, credit spread reports, collar scans (auto-posted) |
| **#infra** | System health, errors, script runs |
| **#journal** | Log your manual trades, check stats |

---

## The Two Trading Streams

### Stream 1 — Agent Alpha and Beta (fully autonomous)

You do nothing. They run every 15 minutes during market hours via cron.

**Agent Alpha** — the opportunist. Trades any defined-risk premium strategy on any liquid ticker (>5M avg volume, >200 OI). Picks the right structure for conditions:
- Oversold + above EMA200 → bull put spread
- Overbought + below EMA200 → bear call spread
- Range-bound + VIX 15-28 → iron condor
- High IV + strong thesis → jade lizard
- Low IV → calendar spread
- Directional view → diagonal spread

**Agent Beta** — the range trader. Specializes in iron condors and butterflies on any liquid ticker when range-bound conditions are confirmed (VIX 13-28, RSI 35-65, ADX < 25).

When they trade you get a Discord notification in #chat and trades appear in Google Sheet "Agent Trades" tab automatically. Trade IDs start with `A` (A001, A002...).

They will NOT trade when: VIX ≥ 35, regime=halt, 2 trades already placed today, after 3 PM ET, earnings within 14 days on target ticker.

At 3:30 PM they automatically close any open same-day positions.

### Stream 2 — Your Manual Trades (SOFI + learning)

You decide, you execute on Webull, you log in #journal. Trade IDs start with `P` (P001, P002...).

---

## What You Say in #chat

```
any trades?              → full scan + debate + 2 proposals
SOFI update              → price, collar status, trigger level action
how are my positions?    → all open trades (agent + manual)
how is Alpha doing?      → Agent Alpha stats and open positions
how is Beta doing?       → Agent Beta stats and open positions
market conditions?       → current regime, VIX, risk flags
score today 78/100       → triggers self-evolution analysis
score today 80/100 --consolidate  → Fridays — weekly pattern analysis
what did the agents trade today?  → today's Alpha + Beta activity
```

Anything else — just ask naturally. "Should I roll my SOFI call?", "Why did Beta not trade today?", "Explain the jade lizard strategy."

---

## What You Say in #journal

```
log: sold 2x SOFI $16C Apr 18 for $1.10    → logs immediately, no questions
log: MSTR $115/$110 put spread Apr 10 $0.78 x1
close: P001 expired worthless
close: P001 bought back at $0.40
stats                                        → win rate, P&L, open count
open positions                               → all open trades
```

Journal agent fetches the underlying price automatically. Never asks questions.

---

## What You Say in #infra

```
health check             → full system status (15 checks)
show pipeline log        → last 30 lines of cron execution log
why didn't alpha trade?  → reads pipeline log, explains
show today's agent trades → reads journal for today
```

---

## What You Say in #research

```
SOFI brief               → full SOFI analysis with exact contract to sell
collar candidates        → weekly scan for new collar opportunities
how is Alpha performing? → reads journal and reports win rate
```

---

## Google Sheet — Your Live Dashboard

Bookmark: **https://docs.google.com/spreadsheets/d/1GidIf-oLY9NfeRGVTwwGFYzA4eZx2bYjvY7UOATiMM0**

| Tab | Contents |
|---|---|
| Summary | Win rate + P&L per stream, open count, last updated |
| Agent Trades | Alpha and Beta only — your benchmark for autonomous performance |
| Manual Trades | Your SOFI collar and learning trades |
| All Trades | Everything, color-coded: 🟡 open · 🟢 win · 🔴 loss |

---

## EOD Summary (auto-posted at 4:05 PM ET)

Every market day at 4:05 PM, this posts to #chat automatically:
```
📊 QuantAI Daily Summary — Apr 2, 2026

🤖 Agent Alpha (Bull put spreads & directional)
  Traded today: 1
  A001 MSTR BULL PUT SPREAD | Credit: $0.78 | OPEN
  All-time: 3 closed | Win rate: 67% | P&L: +$145

🤖 Agent Beta (Iron condors & range-bound)
  No trades today — VIX 23.9 not range-bound enough
  All-time: 1 closed | Win rate: 100% | P&L: +$62

👤 Amit (SOFI collar + manual trades)
  Open: P001 SOFI SELL CALL $16 Apr 18 | $220 premium
  All-time: 0 closed | Win rate: N/A

Total open: 2 | Combined P&L: +$207
Score today in #chat: `score today 78/100`
```

---

## SOFI Collar — 5 Rules, No Improvising

| SOFI Price | Action |
|---|---|
| $15.70 | MONITOR — nothing yet |
| $16.00 | ROLL call to $18, 2 weeks out |
| Called away | Accept profit, rebuy on dip, restart |
| $12.50 | MONITOR — assess conviction |
| $12.00 | EXERCISE put OR roll to $10 OR exit |

Ask "SOFI update" and Orchestrator tells you which level you're near and exactly what to do.

---

## System Health Check

Run anytime to verify everything is working:

```bash
python3 /home/trader/QuantAI/v2/shared-data/scripts/system_test.py
```

Tests 15 categories, shows pass/fail per check, gives fix instructions for failures. Takes ~2 minutes. Run weekly or after any deployment.

Expected result: **43/43 passed**

If something fails, the test tells you exactly how to fix it. Common fixes:
- Missing dependency → `pip3 install [pkg] --break-system-packages`
- Missing file → copy from repo to correct location
- Cron missing → `crontab -e` and add the two pipeline lines
- API key missing → check `/home/trader/QuantAI/.env`

---

## Daily Routine (5 min total)

**Morning:**
1. Open Google Sheet Summary tab
2. "#chat: SOFI update" — which trigger level are we near?
3. Watch for agent trade notification around 9:45-10 AM

**During market:**
4. Agents run themselves — watch #chat for notifications
5. If you manual trade → execute on Webull → log in #journal

**End of day:**
6. EOD summary auto-posts at 4:05 PM — review it
7. "#chat: score today 78/100" — triggers evolution if needed

**Friday:**
8. "#chat: score today 80/100 --consolidate"
9. "#journal: stats" — week breakdown
10. Compare Agent Trades tab vs Manual Trades

---

## What You Never Need to Do

- Approve agent trades — they execute autonomously
- SSH for normal operations — ask Infra in #infra
- Manually run scripts — Orchestrator runs them on request
- Remember exact contracts — ask Orchestrator for specifics

---

## Key Numbers

| Parameter | Value |
|---|---|
| Paper account | $100,040 |
| Max loss per trade | 2% |
| Stop loss | 2x credit |
| Profit target | 50% of max profit |
| Max agent positions | 3 simultaneously |
| Entry cutoff | 3:00 PM ET |
| Hard close | 3:30 PM ET |
| VIX halt | 35 |
| Agent win rate targets | Alpha ≥ 60%, Beta ≥ 65% |
