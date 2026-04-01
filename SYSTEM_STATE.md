# QuantAI — System State
**Last updated: March 31, 2026 | Update after every significant session**

Start every new chat: "Read SYSTEM_STATE.md."

---

## Infrastructure

| Component | Value |
|---|---|
| VPS | Hetzner CX31 · 87.99.141.55 |
| OS | Ubuntu 24 |
| Repo | github.com/amitc3353/QuantAI |
| Active branch | v2-openclaw |
| Live path on VPS | /home/trader/QuantAI |
| Runtime | OpenClaw multi-agent framework |
| Trading mode | PAPER |

---

## How This System Works

QuantAI uses **OpenClaw** — a multi-agent framework where each agent is a Claude model instance with its own personality, instructions, and tool access. Agents are not bots running scheduled loops. They are Claude instances that read workspace files, execute Python scripts via Bash tool, and respond in Discord.

**No fixed scheduler.** Agents act when Amit talks to them in Discord, or when a cron job posts a message to their channel to trigger a task.

**No fixed strategy per agent.** The Orchestrator reads current conditions and proposes whatever makes sense: credit spreads, collars, covered calls, iron condors, cash-secured puts. Strategy follows conditions. Guardrails are constant.

---

## The Four Agents

| Agent | Channel | Model | Role |
|---|---|---|---|
| Orchestrator | #chat | Claude Sonnet | Primary interface — scans, proposes trades, monitors positions, answers all questions |
| Research | #research | Claude Sonnet | SOFI daily brief, credit spread report, weekly collar candidate scan |
| Infra | #infra | Claude Sonnet | System health, files, git, script maintenance, deployment |
| Journal | #journal | Claude Haiku | Trade logging (paper + real), P&L stats, weekly/monthly digests |

Config: `/root/quantai-v2/.openclaw/config.js`
Workspaces: `/root/quantai-v2/v2/workspace-{orchestrator,research,infra,journal}/`

---

## Active Strategies

### SOFI Collar — paper, 200 shares
Primary learning strategy. Defined max loss regardless of downside.

| Parameter | Value |
|---|---|
| Shares | 200 (paper) |
| Entry price | ~$15 |
| Call strike | $16, sell biweekly |
| Put strike | $12, buy monthly |
| Net income target | $170/month |
| Hard max loss | $600 |
| Scale plan | 200 → 500 → 1,000 shares over 12 months |

5 pre-decided trigger actions (no improvising at these levels):
- $15.70 → MONITOR
- $16.00 → ROLL call to $18, 2 weeks out
- Called away → ACCEPT profit, rebuy on dip
- $12.50 → MONITOR, assess conviction
- $12.00 → EXERCISE put OR roll to $10 OR exit

Full params: `/root/quantai-v2/v2/shared-data/strategies/sofi_collar.json`

### Credit Spreads — opportunistic
Scanner finds these dynamically. Not tied to specific tickers.
- Put spreads (bullish bias): RSI < 40 + above 200 EMA
- Call spreads (bearish bias): RSI > 60 + extended move
- Weekly expiry, 4-7% from price, 1 contract while learning
- Stop: 2x credit | Target: 50% profit

### Everything Else — condition-driven
When conditions support it, the Orchestrator can propose:
- Iron condors (SPY/QQQ when VIX 15-25, range-bound)
- Covered calls (on holdings with IV rank > 30)
- Cash-secured puts (to acquire a stock at a lower price)
- Bull put spreads (stronger bullish structure than put spread alone)

**Strategy is whatever the data says is best. Guardrails never change.**

---

## Guard Rules — always enforced, never negotiated

| Rule | Value |
|---|---|
| Max loss per trade | 2% of account |
| Earnings blackout | 14 days before and after |
| VIX ≥ 35 | Advisory only — no new positions |
| No-trade windows | 9:30-9:45 AM ET and 3:45-4:00 PM ET |
| Stop loss | 2x credit received |
| Profit target | Close at 50% of max profit |
| Max simultaneous open | 3 positions |

---

## Trade Intelligence Flow

When Amit asks for trades or the Orchestrator decides to scan:

```
market_intelligence.py    →  intelligence packet (auto-fresh if < 90 min old)
       ↓
scan_options.py both      →  credit spread + collar candidates
       ↓
debate_chamber.py         →  Bull/Bear/Judge debate → top 2 proposals
       ↓
Orchestrator posts cards  →  #trade-proposals
       ↓
Amit reacts ✅ ❌ 🔄
```

There is no fixed schedule for this. It runs when:
- Amit asks "any trades?" or "what looks good?"
- Orchestrator notices a significant market move
- Intelligence packet is stale and Amit is asking about conditions

---

## Scripts

All in `/root/quantai-v2/v2/shared-data/scripts/`

| Script | What it does | How to run |
|---|---|---|
| market_intelligence.py | Builds intelligence packet from all data sources. Auto-skips if < 90 min old. | `python3 market_intelligence.py` or `--force` |
| debate_chamber.py | 3-agent Bull/Bear/Judge debate. Reads intelligence packet. | `python3 debate_chamber.py` |
| self_evolution.py | EOD evolution pipeline. Pass today's score. | `python3 self_evolution.py 75` |
| scan_options.py | Credit spread + collar scanner across 100+ tickers. | `python3 scan_options.py both` |
| fetch_sofi.py | SOFI-specific data fetch for Research agent. | `python3 fetch_sofi.py` |

---

## Intelligence Packet

`/root/quantai-v2/v2/shared-data/cache/market_intelligence.json`

Built by market_intelligence.py. Contains:

**Macro:** VIX + VIX3M + term structure, regime classification (normal/caution/risk_off/halt), Fear & Greed score, 10Y/2Y treasury yields + yield curve regime, Finnhub event calendar (days to FOMC/CPI/jobs), today's event flag

**Per symbol (all watchlist):** Price + change, volume vs average, RSI-14, MACD signal, Bollinger Band position + width, SMA20/EMA50/EMA200, ADX, above-EMA200 flag, P/E ratio, market cap, days to next earnings, news sentiment score

**Setups:** Pre-screened high conviction setups ranked by score — any strategy type

**Risk flags:** Specific HALT/WARNING/CAUTION alerts with reasons

**Quality score:** 0-100, decrements when data sources fail

---

## Self-Evolution

Runs after Orchestrator scores the day's trades (0-100 scale).

```
If score < 90:
  1. Observe  — extract what happened from journal
  2. Critique — find single biggest param misalignment
  3. Generate — propose ONE specific config change with evidence
  4. Validate — 5 gates (constitution → size → drift → safety → regression)
  5. Apply    — update sofi_collar.json, post to Discord
  6. Log      — append to evolution_log.jsonl

If score ≥ 90:
  No change needed — post confirmation

Every Friday: add --consolidate flag
  Compresses 35 days of observations into durable strategy principles
```

Constitution (gates can never change these):
- max_loss_pct ≤ 2%, min_credit ≥ $0.30, earnings_blackout ≥ 14 days
- delta range 0.05-0.20, VIX upper ≤ 30, stop_loss_multiplier ≤ 3x

---

## Data Sources

| Source | Data | Cost |
|---|---|---|
| yfinance | Price, volume, RSI, MACD, BB, EMAs, VIX, fundamentals | Free |
| Finnhub | Event calendar, earnings dates, news headlines | Free tier |
| Alpha Vantage | Earnings surprise history | Free (25 req/day) |
| CNN | Fear & Greed index (falls back to VIX proxy) | Free scrape |
| Anthropic API | All agent reasoning, debate chamber, self-evolution | ~$15-25/mo |
| **MarketXLS** | **Real-time Greeks, options screeners, 1,100+ functions** | **Not subscribed — pre-live only ($94/mo Advanced)** |
| **Unusual Whales** | **Real sweep detection, dark pool, net premium flow** | **Not subscribed — pre-live only ($48/mo)** |

---

## File Structure

```
/root/quantai-v2/
├── .openclaw/
│   └── config.js                    ← Agent definitions + Discord bindings
└── v2/
    ├── workspace-orchestrator/
    │   ├── SOUL.md                  ← Personality
    │   └── AGENTS.md                ← Full operating manual
    ├── workspace-research/
    │   ├── SOUL.md
    │   └── AGENTS.md
    ├── workspace-infra/
    │   ├── SOUL.md
    │   └── AGENTS.md
    ├── workspace-journal/
    │   ├── SOUL.md
    │   └── AGENTS.md
    └── shared-data/
        ├── scripts/
        │   ├── market_intelligence.py
        │   ├── debate_chamber.py
        │   ├── self_evolution.py
        │   ├── scan_options.py
        │   └── fetch_sofi.py
        ├── cache/
        │   ├── market_intelligence.json
        │   ├── credit_spread_scan.json
        │   └── collar_candidates.json
        ├── journal/
        │   ├── paper/trades.jsonl
        │   ├── real/trades.jsonl
        │   └── digests/
        ├── logs/
        │   ├── debate_log.jsonl
        │   ├── evolution_log.jsonl
        │   └── evolution_observations.jsonl
        └── strategies/
            └── sofi_collar.json     ← Self-evolution updates this
```

---

## Monthly Cost

| Item | Monthly |
|---|---|
| Claude API (4 agents + scripts) | ~$15-25 |
| VPS Hetzner CX31 | ~$12 |
| All data sources | $0 |
| **Total now** | **~$27-37** |
| + MarketXLS Advanced (pre-live) | +$94 |
| + Unusual Whales (pre-live) | +$48 |
| **Total at live** | **~$169-179** |

---

## Pre-Live Checklist

Before any real capital:
- [ ] 40+ paper trades, 60%+ win rate sustained over 3+ weeks
- [ ] Self-evolution ran ≥ 4 weeks, ≥ 1 validated change applied
- [ ] Debate chamber proposals reviewed weekly — quality confirmed
- [ ] Subscribe MarketXLS Advanced ($94/mo)
- [ ] Subscribe Unusual Whales ($48/mo)
- [ ] Open IBKR account for XSP (Section 1256 tax treatment)
- [ ] Create separate live Alpaca API keys (never reuse paper keys)
- [ ] pip audit clean on all scripts
- [ ] Emergency stop tested end-to-end in paper mode first

---

## Security Rules

- GitHub PAT rotated after every session where it appears in chat
- No hardcoded credentials in any script (all via env vars)
- New open-source integrations: >500 stars, manual review first, pin versions
- Infra agent needs Amit approval before installing new packages or changing strategy params

---

## Starting a New Chat

> "Read SYSTEM_STATE.md. I want to [task]."

After significant changes: update this file → push to GitHub → re-upload to Claude project.

*Last updated: March 31, 2026 — Complete rewrite. Accurately reflects OpenClaw v2 architecture. No v1 ghost references.*
