# QuantAI — System State
**Last updated: March 31, 2026 | Update after every significant session**

Start every new chat: "Read SYSTEM_STATE.md."

---

## Infrastructure

| Component | Value |
|---|---|
| VPS | Hetzner CX31 · 87.99.141.55 |
| OS | Ubuntu 24 |
| Repo | github.com/amitc3353/QuantAI · branch: v2-openclaw |
| Live path | /home/trader/QuantAI |
| Runtime | OpenClaw multi-agent framework |
| Trading mode | **PAPER** |

---

## How This System Works

QuantAI runs on **OpenClaw** — a multi-agent framework where each agent IS a Claude model with its own personality, instructions, and tool access. Agents are not bots running Python loops. They are Claude instances that read their workspace files, use Bash to run scripts, and respond in Discord.

**There is no scheduler.** Agents act when triggered — by Amit in Discord, by cron jobs posting to their channels, or by other agents delegating tasks.

**There is no fixed strategy per agent.** The Orchestrator evaluates current conditions and proposes whatever the data supports: credit spreads, collars, covered calls, iron condors, cash-secured puts — anything with defined risk. Guardrails are constant; strategy is flexible.

---

## The Four Agents

| Agent | Channel | Model | Role |
|---|---|---|---|
| **Orchestrator** | #chat | Claude Sonnet | Primary interface. Runs scans, proposes trades, answers questions, monitors positions |
| **Research** | #research | Claude Sonnet | Deep dives on specific tickers, SOFI daily brief, collar candidate scans |
| **Infra** | #infra | Claude Sonnet | System health, file ops, git, script fixes, deployment |
| **Journal** | #journal | Claude Haiku | Trade logging, P&L tracking, stats, weekly digests |

All four agents have Bash tool access. They run Python scripts directly.

---

## Active Strategies

### SOFI Collar (paper — 200 shares)
Amit's primary learning strategy. Defined max loss regardless of downside.

| Parameter | Value |
|---|---|
| Entry price | ~$15 (paper) |
| Shares | 200 (scaling to 1,000 long-term) |
| Call strike | $16 — sell biweekly |
| Put strike | $12 — buy monthly |
| Net income target | $170/month |
| Max loss | $600 |

5 pre-decided trigger actions at: $15.70, $16.00 (roll), assignment, $12.50, $12.00 (exercise/exit).
Full params: `/root/quantai-v2/v2/shared-data/strategies/sofi_collar.json`

### Credit Spreads (opportunistic)
Scanner-driven. Any liquid ticker where conditions are right.
Put spreads when bullish. Call spreads when bearish.
Weekly expiry, 4–7% from price, defined risk. One contract while learning.

### Covered Calls / Cash-Secured Puts
On existing holdings or to acquire stocks at a discount.
Proposed by scanner when IV rank > 30 and conditions support.

**Strategy is whatever the data says is best. Guardrails never change.**

---

## Guard Rules (always enforced)

| Rule | Value |
|---|---|
| Max loss per trade | 2% of account |
| Earnings blackout | 14 days minimum |
| VIX ≥ 35 | Advisory only — no new trades |
| No-trade windows | 9:30–9:45 AM ET, 3:45–4:00 PM ET |
| Stop loss | 2x credit received |
| Profit target | 50% of max profit |
| Max open positions | 3 simultaneously |

---

## Intelligence & Trade Proposal Flow

When Amit asks for trades or the Orchestrator scans:

```
1. market_intelligence.py    → on-demand packet (auto-skips if <90min old)
2. scan_options.py both       → credit spread + collar candidates
3. debate_chamber.py          → Bull/Bear/Judge → top 2 proposals
4. Orchestrator posts cards   → #trade-proposals
5. Amit reacts ✅ ❌ 🔄
```

No fixed schedule. Runs on demand when conditions warrant it.

### Intelligence Packet
Built by `market_intelligence.py`. Auto-detects session from time of day.
Pass `--force` to refresh regardless of age.
Contains: VIX + regime, technicals for all watchlist symbols, event calendar,
earnings dates, news sentiment, pre-screened setups, risk flags.

### Debate Chamber
Proposal Agent generates 3–5 candidates (any valid strategy).
Bull and Bear agents argue each one. Judge selects top 2.
Guard rules applied. Trade cards printed for Orchestrator to post.

### Self-Evolution
After EOD scoring: `python3 self_evolution.py [score]`
If score < 90: Observe → Critique → Generate ONE change → 5-gate validation → Apply.
Fridays: add `--consolidate` to compress observations into principles.

---

## Scripts (`/root/quantai-v2/v2/shared-data/scripts/`)

| Script | Purpose |
|---|---|
| `market_intelligence.py` | On-demand intelligence packet. --force to override freshness check. |
| `debate_chamber.py` | 3-agent debate. Auto-reads intelligence packet. |
| `self_evolution.py [score]` | EOD evolution pipeline. --consolidate on Fridays. |
| `scan_options.py` | Credit spread + collar scanner. Args: credit_spreads, collars, both. |
| `fetch_sofi.py` | SOFI data for Research agent. |

---

## Data Sources

| Source | What | Cost |
|---|---|---|
| yfinance | Price, technicals, fundamentals, VIX | Free |
| Finnhub | Events, earnings calendar, news | Free tier |
| Alpha Vantage | Earnings surprises | Free (25 req/day) |
| CNN | Fear & Greed (VIX fallback) | Free scrape |
| Anthropic API | All agent reasoning + scripts | ~$15–25/mo |
| MarketXLS | Real-time Greeks, screeners | **Not yet — pre-live only** |

---

## Monthly Cost

| Item | Cost |
|---|---|
| Claude API | ~$15–25/mo |
| VPS Hetzner CX31 | ~$12/mo |
| Data sources | $0 |
| **Total now** | **~$27–37/mo** |
| MarketXLS Advanced (pre-live addition) | +$94/mo |

---

## File Structure

```
/root/quantai-v2/v2/
  workspace-orchestrator/AGENTS.md   ← Orchestrator instructions
  workspace-research/AGENTS.md       ← Research agent instructions
  workspace-infra/AGENTS.md          ← Infra agent instructions
  workspace-journal/AGENTS.md        ← Journal agent instructions
  shared-data/
    scripts/                          ← All runnable scripts
    cache/                            ← market_intelligence.json, scan results
    journal/paper/trades.jsonl        ← Paper trade log
    journal/real/trades.jsonl         ← Real trade log
    logs/                             ← debate_log, evolution_log
    strategies/sofi_collar.json       ← Strategy params (evolution updates this)
```

---

## How to Use This Document

**Starting a new chat:**
> "Read SYSTEM_STATE.md. I want to [task]."

**After significant changes:** Update → push to GitHub → re-upload to Claude project.

*Last updated: March 31, 2026 — OpenClaw architecture accurately documented. Debate chamber, intelligence, and self-evolution built and pushed.*
