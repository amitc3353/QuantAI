# QuantAI — How to Use Your System
**Updated: 2026-05-03**

Everything runs in Discord. Three trading agents (Alpha, Beta, Gamma) execute autonomously and log to Google Sheets. Sentinel watches the system and self-heals routine issues. You approve risky fixes from your phone via Discord reactions.

---

## Your Discord Channels

| Channel | What it's for |
|---|---|
| **#chat** | Talk to Orchestrator — analysis, agent status, score the day |
| **#research** | Credit-spread reports, regime briefs, scan output (auto-posted) |
| **#infra** | System health, errors, script runs |
| **#journal** | Read trade activity, ask for stats |
| **#system-health** | Sentinel daily summaries + critical alerts |
| **#karna-approvals** | Sentinel ✅/❌ approval cards |

---

## The Three Trading Agents (fully autonomous)

You do nothing. They run on cron during market hours.

**Agent Alpha** — the opportunist. Trades any defined-risk premium strategy on liquid tickers (>5M avg volume, >200 OI). Picks the structure for conditions:
- Oversold + above EMA200 → bull put spread
- Overbought + below EMA200 → bear call spread
- Range-bound + VIX 15–28 → iron condor
- High IV + strong thesis → jade lizard
- Low IV → calendar spread
- Directional view → diagonal spread

**Agent Beta** — the regime trader. 12-regime classifier drives which strategy module fires (event strangle, ratio spreads, BWB, VIX calls, etc.) on SPX / XSP / VIX index options.

**Agent Gamma** — the mean-reverter. RSI(10) Connors-method entries on equity options, scanned overnight at 4:30 PM ET, executed at 9:33 AM ET.

When an agent trades, you get a Discord notification in #chat and rows appear in the Google Sheet "Agent Trades" tab. Trade IDs: `A###` (Alpha), `B###` (Beta), `G###` (Gamma).

Agents will NOT trade when: VIX ≥ 35 (`VIX_HALT`), regime=halt, daily trade limit hit, after 3 PM ET, earnings within 14 days. At 3:30 PM they automatically close any same-day positions.

Agents NEVER attempt strategies that require owning shares (covered calls, collars, cash-secured puts, covered strangles) — these are blocked by the `REQUIRES_SHARES` defensive guard in `autonomous_execution.py`.

---

## What You Say in #chat

```
any trades?              → full scan + debate + 2 proposals
how are my positions?    → all open trades across agents
how is Alpha doing?      → Agent Alpha stats and open positions
how is Beta doing?       → Agent Beta stats and open positions
how is Gamma doing?      → Agent Gamma stats and open positions
market conditions?       → current regime, VIX, risk flags
score today 78/100       → triggers post-trade analysis
score today 80/100 --consolidate  → Fridays — weekly pattern analysis
what did the agents trade today?  → today's Alpha + Beta + Gamma activity
```

Anything else — just ask naturally. "Why did Beta not trade today?", "Explain the jade lizard strategy.", "Show me Gamma's RSI watchlist."

---

## What You Say in #infra

```
health check             → full system status (43+ checks)
show pipeline log        → last 30 lines of cron execution log
why didn't alpha trade?  → reads pipeline log, explains
sentinel status          → pending fixes, quarantine list, next slot
```

---

## What You Say in #research

```
how is Alpha performing? → reads journal, win rate
spread scan              → trigger run of scan_options.py "all"
regime now               → Beta's current regime classification
```

---

## Google Sheet — Your Live Dashboard

Bookmark: **https://docs.google.com/spreadsheets/d/1GidIf-oLY9NfeRGVTwwGFYzA4eZx2bYjvY7UOATiMM0**

| Tab | Contents |
|---|---|
| Summary | Win rate + P&L, open count, last updated |
| Agent Trades | Alpha + Beta + Gamma trades — your benchmark for autonomous performance |
| All Trades | Every entry, color-coded: 🟡 open · 🟢 win · 🔴 loss |

---

## EOD Summary (auto-posted at 4:05 PM ET)

Every market day at 4:05 PM, this posts to #chat automatically:

```
📊 QuantAI Daily Summary — May 3, 2026

🤖 Agent Alpha (Bull put spreads & directional strategies)
  Traded today: 1
  A001 MSTR BULL PUT SPREAD | Credit: $0.78 | OPEN
  All-time: 3 closed | Win rate: 67% | P&L: +$145

🤖 Agent Beta (Iron condors & range-bound strategies)
  No trades today — VIX 23.9 not range-bound enough
  All-time: 1 closed | Win rate: 100% | P&L: +$62

🤖 Agent Gamma (RSI(10) mean-reversion on equity options)
  No trades today — RSI watchlist empty after overnight scan
  All-time: 0 closed | Win rate: N/A

Total open: 1 | Combined P&L: +$207
```

---

## Sentinel — your operations co-pilot

Sentinel runs on its own schedule (8:30 AM, hourly observe through market, 4:15 PM apply, 9 PM observe weekdays + 10 AM Sat/Sun). It:

- Reads `system-health-report.json` (deterministic 13-check) plus `errors.db` plus the catalog
- Auto-applies safe fixes (catalog reclassification, stale lock cleanup, non-trading collector restarts)
- Posts ✅/❌ approval cards for risky fixes (code edits, ibgateway restart with positions open, etc.) to `#karna-approvals`
- Never touches trading-path scripts. Never touches credentials.

Tap ✅ on a card from your phone → it runs at the next apply slot. Tap ❌ → dismissed. Cards expire after 48h.

---

## System Health Check

Run anytime to verify everything is working:

```bash
sudo python3 /home/trader/QuantAI/v2/shared-data/scripts/system_test.py
```

Expected result: **44/44 passed**.

Or check live state without running anything:

```bash
sudo cat /var/dashboard/state/system-health-report.json | jq '.status, .data.checks'
```

13 checks: ibkr_port, litellm_4000, clawroute_18790, cron_freshness, disk, memory, self_learning_sla, weekly_synthesis, collector_staleness, journal_schema, test_results, graphify, open_positions.

---

## Daily Routine (3 min total)

**Morning:** open Google Sheet Summary tab, glance at #chat for any Sentinel pre-market card.

**During market:** agents run themselves. #chat shows their entries. #karna-approvals shows any fix proposals from Sentinel.

**End of day:** review the EOD auto-post in #chat. Tap ✅ on any Sentinel cards you trust. Friday — `score today 80/100 --consolidate` triggers weekly synthesis (Sentinel will also post a daily digest in #system-health if anything was applied).

---

## What You Never Need to Do

- Approve agent trades — they execute autonomously within their guardrails
- SSH for normal operations — ask Infra in #infra
- Manually run scripts — Orchestrator runs them on request
- Trade manually — agents cover all defined-risk strategies on the planned tickers

---

## If Agents Seem to Have Wrong Information

If an agent says something that's clearly outdated, their workspace file is stale. Fix it with one command on the VPS:

```bash
bash /home/trader/QuantAI/scripts/sync_workspaces.sh
```

This syncs all `AGENTS.md` and `SOUL.md` files from the git repo to where OpenClaw reads them. Agents pick up the changes on the very next message. No restart needed.

---

## Key Numbers

| Parameter | Value |
|---|---|
| IBKR paper account | $1,000,000 (DUP851506) |
| Position-sizing cap (Alpha) | $50,000 (`AGENT_ACCOUNT_CAP`) |
| Max loss per trade | 2% |
| Stop loss | 2× credit |
| Profit target | 50% of max profit |
| Max simultaneous positions per agent | 3 |
| Entry cutoff | 3:00 PM ET |
| Hard close | 3:30 PM ET |
| VIX halt | 35 |
| Agent win rate targets | Alpha ≥ 60%, Beta ≥ 65%, Gamma TBD |
