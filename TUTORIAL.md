# QuantAI — How to Use Your System
**Updated: April 1, 2026**

Everything runs in Discord. The system has two streams running in parallel — autonomous agent trades and your manual trades. Both log to Google Sheets automatically.

---

## Your Discord Channels

| Channel | What it's for |
|---|---|
| **#chat** | Everything — talk to Orchestrator, get analysis, score the day |
| **#research** | SOFI briefs, credit spread reports, collar scans (auto-posted) |
| **#infra** | System health, errors, deployments |
| **#journal** | Log your manual trades, check stats |

That's it. Four channels. No approval workflows, no slash commands to memorize.

---

## The Two Trading Streams

### Stream 1 — Agents Alpha and Beta (fully autonomous)

You do nothing. They run on their own every 15 minutes during market hours.

**Agent Alpha** trades bull put spreads on any liquid ticker when conditions are right.
**Agent Beta** trades iron condors on SPY/QQQ when VIX is 13-28 and market is range-bound.

When they trade, you get a Discord notification in #chat and the trade appears in your Google Sheet "Agent Trades" tab automatically.

They will NOT trade when:
- VIX ≥ 35
- Market regime is halt or risk_off
- Already 2 trades placed today
- After 3:00 PM ET (no time to manage)
- Earnings within 14 days on the ticker

At 3:30 PM they automatically close any open same-day positions.

### Stream 2 — Your Manual Trades (SOFI + learning)

You decide when to trade. You execute on Webull. You log it in #journal. That's it.

The Orchestrator gives you everything you need to decide — you never trade blind.

---

## What You Say in #chat

**To get trade ideas for yourself:**
```
any trades?
what looks good today?
run the scan
```
Orchestrator runs the intelligence packet + options scanner + debate chamber and gives you 2-3 setups with exact contracts, credits, and reasoning. You decide whether to execute.

**To check SOFI and your collar:**
```
SOFI update
how's my collar?
SOFI price vs triggers
```
Gives you current price, which trigger level you're near, what action to take, and the exact contract to trade if action is needed.

**To check your open positions:**
```
how are my positions?
open positions?
what do I have open?
```

**To check what the agents traded:**
```
what did alpha trade today?
agent trades this week
show me today's agent activity
```

**To score the day and trigger evolution:**
```
score today 72/100
score today 85/100
```
Score is your honest assessment — did trades make sense? Did you follow the plan? Below 90 triggers the self-evolution analysis. Fridays add --consolidate:
```
score today 80/100 --consolidate
```

**To get market context:**
```
market conditions?
what's the regime today?
is it a good day to trade?
```

**Anything else:**
Just ask naturally. "Should I roll my SOFI call?", "What happens if SPY drops 2% today?", "Explain iron condor gamma risk." The Orchestrator answers everything.

---

## What You Say in #journal

**Log a trade (always paper unless you say "real"):**
```
log: sold 2x SOFI $16C Apr 18 for $1.10
log: bought 2x SOFI $12P May 16 for $0.25
log: MSTR $115/$110 put spread Apr 10 credit $0.78 x1
```
Journal agent logs it immediately, fetches the underlying price automatically, confirms in one message. Never asks questions.

**Close a trade:**
```
close: P001 expired worthless
close: P001 bought back at $0.40
```

**Check your stats:**
```
stats
open positions
how am I doing?
weekly stats
```

---

## Google Sheet — Your Live Dashboard

Bookmark this on your phone:
**https://docs.google.com/spreadsheets/d/1GidIf-oLY9NfeRGVTwwGFYzA4eZx2bYjvY7UOATiMM0**

**Summary tab** — open this every morning:
- Open positions (agent + manual)
- Win rate: agent vs manual (tracked separately)
- Total P&L
- Last sync time

**Agent Trades tab** — Alpha and Beta performance only. This is how you evaluate whether the autonomous system is working.

**Manual Trades tab** — your SOFI collar and anything you trade yourself.

**All Trades tab** — everything combined, color-coded:
- 🟡 Yellow = OPEN position
- 🟢 Green = closed winner
- 🔴 Red = closed loser

Trade IDs: `A001`, `A002`... = agent trades. `P001`, `P002`... = your manual trades.

---

## SOFI Collar — 5 Rules, No Improvising

| SOFI Price | What you do |
|---|---|
| $15.70 | Nothing. Monitor. |
| $16.00 | ROLL the call to $18, 2 weeks out. Collect net credit. |
| Called away | Accept it. Rebuy on next dip. Restart collar. |
| $12.50 | Assess conviction. Is the thesis still intact? |
| $12.00 | Exercise put OR roll to $10 OR exit. Max loss accepted. |

The Orchestrator tells you which level you're near every time you ask "SOFI update." You never need to remember the numbers.

---

## What Happens Each Day (Automatically)

| Time (ET) | What happens |
|---|---|
| 6:00 AM | Research agent posts SOFI brief to #research |
| 9:45 AM | Pipeline first check — evaluates if conditions support entry |
| 9:45 AM–3:00 PM | Pipeline checks every 15 min — enters if conditions warrant |
| 3:00 PM | Entry cutoff — no new positions after this |
| 3:30 PM | Hard close — all agent same-day positions closed |
| 4:05 PM | EOD reminder posted to #chat — prompts you to score the day |

**You don't need to do anything for the agent stream.** Just watch Discord and Google Sheet.

---

## Daily Routine (What Amit Does)

**Morning (5 min):**
1. Open Google Sheet Summary tab — any overnight changes?
2. In #chat: "SOFI update" — where are we vs trigger levels?
3. Watch for agent trade notification in #chat around market open

**During market (as needed):**
4. If you want to paper trade something yourself → execute on Webull → log in #journal
5. Ask Orchestrator anything you're curious about
6. Agents handle themselves — you just watch

**End of day (2 min):**
7. In #chat: "score today [0-100]/100"
8. Check Google Sheet — P&L for the day

**Friday (10 min):**
9. In #chat: "score today [score]/100 --consolidate"
10. In #journal: "stats" — full week breakdown
11. Compare Agent Trades tab vs Manual Trades — who's winning this week?

---

## What You Never Need to Do

- Approve agent trades — they execute automatically within guardrails
- SSH into the VPS for normal operations — ask Infra agent in #infra
- Manually run scripts — Orchestrator runs them when you ask
- Remember contract specifics — ask Orchestrator for exact contracts
- Update configs manually — self-evolution handles it with validation

---

## If Something Seems Wrong

**Agent not trading when it should:**
In #infra: "check pipeline log" or "why aren't agents trading?"
Infra agent reads the log and tells you what happened.

**Trade not showing in Google Sheet:**
In #journal: "sync sheets" — forces a manual sync.
Or on VPS: `python3 /home/trader/QuantAI/v2/shared-data/scripts/sheets_sync.py`

**System feels slow or broken:**
In #infra: "health check" — disk, memory, all processes.

**SOFI hitting a trigger level:**
In #chat: "SOFI update" — Orchestrator tells you exactly what to do based on the pre-decided actions. Follow the plan. Don't improvise.

---

## Key Numbers to Know

| Thing | Value |
|---|---|
| Paper account size | $20,000 |
| Max loss per trade | 2% = $400 |
| Max open positions | 3 simultaneously |
| Stop loss | 2x credit received |
| Profit target | 50% of max profit (close early) |
| SOFI collar max loss | $600 |
| SOFI income target | $170/month at 200 shares |
| Agent entry cutoff | 3:00 PM ET |
| Hard close | 3:30 PM ET |
| VIX halt level | 35 (no trades above this) |
