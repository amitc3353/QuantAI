# QuantAI — System State
**Last updated: April 1, 2026 | Update after every significant session**

Start every new chat: "Read SYSTEM_STATE.md."

---

## Infrastructure

| Component | Value |
|---|---|
| VPS | Hetzner CX31 · 87.99.141.55 |
| OS | Ubuntu 24 |
| Repo | github.com/amitc3353/QuantAI |
| Active branch | feat/debate-chamber-intelligence-evolution (merged to main on VPS) |
| Repo path on VPS | /home/trader/QuantAI |
| Runtime | OpenClaw multi-agent framework |
| OpenClaw gateway | /root/quantai-v2/ |
| Trading mode | PAPER |

---

## How This System Works

QuantAI runs on **OpenClaw** — four Claude model instances (agents) that live in Discord channels and have Bash tool access to run Python scripts directly. No scheduler. No fixed bots. Agents act when Amit talks to them or when triggered.

**Two separate directory trees matter:**
- `/home/trader/QuantAI/` — the git repo, scripts, configs
- `/root/quantai-v2/` — where OpenClaw gateway runs and agents read their workspace files

When updating agent instructions, files must be copied to BOTH locations.

---

## The Four Agents

| Agent | Discord Channel | Model | What It Does |
|---|---|---|---|
| Orchestrator | #chat | Claude Sonnet | Primary interface. Runs scans, proposes trades, monitors positions, answers everything |
| Research | #research | Claude Sonnet | SOFI daily brief, credit spread report, weekly collar candidate scan |
| Infra | #infra | Claude Sonnet | System health, files, git, script maintenance, debugging |
| Journal | #journal | Claude Haiku | Trade logging (paper + real), P&L stats, Google Sheets sync |

**Workspace files (what agents read as instructions):**
- `/root/quantai-v2/workspace-orchestrator/AGENTS.md` + `SOUL.md`
- `/root/quantai-v2/workspace-research/AGENTS.md` + `SOUL.md`
- `/root/quantai-v2/workspace-infra/AGENTS.md` + `SOUL.md`
- `/root/quantai-v2/workspace-journal/AGENTS.md` + `SOUL.md`

OpenClaw reads these files fresh on every message — no restart needed after editing.

---

## Active Strategies

### SOFI Collar — paper, 200 shares
Primary learning strategy. Defined max loss regardless of downside.

| Parameter | Value |
|---|---|
| Entry price | ~$15 paper |
| Shares | 200 (target: 1,000 long-term) |
| Call strike | $16, sell biweekly |
| Put strike | $12, buy monthly |
| Net income target | $170/month |
| Hard max loss | $600 |

5 pre-decided trigger actions — no improvising at these levels:

| SOFI Price | Action |
|---|---|
| $15.70 | MONITOR — no action yet |
| $16.00 | ROLL call to $18, 2 weeks out, collect net credit |
| Called away | ACCEPT profit, rebuy on next dip, restart collar |
| $12.50 | MONITOR — assess conviction in thesis |
| $12.00 | EXERCISE put OR roll to $10 OR exit — max loss taken |

Full params: `/root/quantai-v2/shared-data/strategies/sofi_collar.json`

### Credit Spreads — opportunistic
Scanner finds these dynamically across 100+ tickers.
- Put spreads when bullish (RSI < 40, above 200 EMA)
- Call spreads when bearish (RSI > 60, extended move)
- Weekly expiry, 4-7% from price, defined risk
- 1 contract while learning
- Stop: 2x credit | Target: 50% profit

### Other strategies — condition-driven
Orchestrator can also propose: iron condors (SPY/QQQ when VIX 15-25), covered calls (IV rank > 30), cash-secured puts (to acquire stocks cheaper), bull put spreads. Strategy follows conditions. Guardrails never change.

---

## Guard Rules — always enforced, never negotiated

| Rule | Value |
|---|---|
| Max loss per trade | 2% of account |
| Earnings blackout | 14 days minimum |
| VIX ≥ 35 | Advisory only — no new positions |
| No-trade windows | 9:30–9:45 AM ET and 3:45–4:00 PM ET |
| Stop loss | 2x credit received |
| Profit target | Close at 50% of max profit |
| Max open positions | 3 simultaneously |

---

## Trade Intelligence Flow

When Amit asks for trades (any phrasing):

```
1. market_intelligence.py   → builds intelligence packet (skips if <90 min old)
2. scan_options.py both     → credit spread + collar candidates across 100+ tickers
3. debate_chamber.py        → Bull/Bear/Judge debate → top 2 proposals printed
4. Orchestrator posts cards → #trade-proposals
5. Amit reacts ✅ ❌ 🔄
6. If executed → Journal agent logs it → sheets_sync.py updates Google Sheet
```

No fixed schedule. Runs when Amit asks or conditions warrant.

---

## Scripts

All scripts live at `/home/trader/QuantAI/v2/shared-data/scripts/`
All data reads/writes go to `/root/quantai-v2/shared-data/`

| Script | What it does | How agents call it |
|---|---|---|
| `market_intelligence.py` | Intelligence packet: VIX, technicals, events, earnings, news, regime | `python3 .../market_intelligence.py` or `--force` |
| `debate_chamber.py` | Bull/Bear/Judge debate → top 2 trade proposals | `python3 .../debate_chamber.py` |
| `self_evolution.py` | EOD config evolution (score < 90 triggers analysis) | `python3 .../self_evolution.py 85` |
| `scan_options.py` | Dynamic scanner: credit spreads + collar candidates | `python3 .../scan_options.py both` |
| `fetch_sofi.py` | SOFI-specific data for Research agent | `python3 .../fetch_sofi.py` |
| `pattern_engine.py` | Statistical pattern detection (needs 20+ closed trades) | `python3 .../pattern_engine.py` |
| `sheets_sync.py` | Syncs trades.jsonl to Google Sheet | `python3 .../sheets_sync.py` |

---

## Intelligence Packet

Saved to: `/root/quantai-v2/shared-data/cache/market_intelligence.json`

Contains: VIX + VIX3M + term structure + regime (normal/caution/risk_off/halt), Fear & Greed (with VIX fallback), 10Y/2Y yields + yield curve regime, Finnhub event calendar (days to FOMC/CPI/jobs), per-symbol data (RSI-14, MACD, BB position, EMA200, ADX, above-EMA200, P/E, market cap, days to earnings, news sentiment) for SPY/QQQ/NVDA/PLTR/TSM/AMD/AVGO/ASML/MU/SOFI/CCJ, pre-screened setups ranked by conviction, risk flags.

Freshness: auto-skips if under 90 minutes old. Use `--force` to override.

---

## Google Sheets Journal

Sheet: https://docs.google.com/spreadsheets/d/1GidIf-oLY9NfeRGVTwwGFYzA4eZx2bYjvY7UOATiMM0

| Tab | Contents |
|---|---|
| All Trades | Every trade (agent + manual), color-coded by status |
| Agent Trades | Only trades proposed by debate chamber / Orchestrator scan |
| Manual Trades | Only trades Amit logged himself in #journal |
| Summary | Live formulas: win rate, P&L, trade count per source |

Color coding: yellow = OPEN, green = closed winner, red = closed loser.

Journal reads/writes: `/root/quantai-v2/shared-data/journal/paper/trades.jsonl`

**Logging a trade:** In #journal say `log: sold 2x SOFI $16C Apr 18 for $1.10`
Journal agent logs it, fetches price automatically, syncs to sheet. No questions.

**Closing a trade:** In #journal say `close: P001 expired worthless` or `close: P001 bought back at $0.40`

---

## Self-Evolution

Triggered by Orchestrator after EOD scoring.
In #chat say: `score today 78/100` → Orchestrator runs `self_evolution.py 78`

Pipeline if score < 90:
1. Observe — extracts what happened from journal
2. Critique — finds single biggest param misalignment
3. Generate — proposes ONE config change with evidence
4. Validate — 5 gates: constitution, size, drift, safety, regression
5. Apply — updates sofi_collar.json, posts result to #infra
6. Consolidate (Fridays) — compresses observations into strategy principles

Constitution-protected (never changes): max_loss_pct ≤ 2%, min_credit ≥ $0.30, earnings_blackout ≥ 14d, delta range 0.05-0.20, VIX upper ≤ 30, stop_loss_multiplier ≤ 3x

---

## Data Sources

| Source | Data | Cost |
|---|---|---|
| yfinance | Price, technicals, fundamentals, VIX, IV rank | Free |
| Finnhub | Event calendar, earnings dates, news | Free tier |
| Alpha Vantage | Earnings surprise history | Free (25 req/day) |
| CNN | Fear & Greed index (VIX fallback if scrape fails) | Free scrape |
| Anthropic API | All agent reasoning + debate chamber + evolution | ~$15-25/mo |
| Google Sheets API | Journal sync | Free |
| MarketXLS Advanced | Real-time Greeks, options screeners | NOT subscribed — pre-live only ($94/mo) |
| Unusual Whales | Real sweep detection, dark pool | NOT subscribed — pre-live only ($48/mo) |

---

## Monthly Cost

| Item | Cost |
|---|---|
| Claude API | ~$15-25/mo |
| VPS Hetzner CX31 | ~$12/mo |
| All data sources | $0 |
| **Total now** | **~$27-37/mo** |
| + MarketXLS Advanced (at live) | +$94/mo |
| + Unusual Whales (at live) | +$48/mo |
| **Total at live transition** | **~$169-179/mo** |

---

## Key File Paths on VPS

```
/root/quantai-v2/
  workspace-orchestrator/
    SOUL.md                    ← Orchestrator personality
    AGENTS.md                  ← Orchestrator operating manual
  workspace-research/
    SOUL.md + AGENTS.md        ← Research agent
  workspace-infra/
    SOUL.md + AGENTS.md        ← Infra agent
  workspace-journal/
    SOUL.md + AGENTS.md        ← Journal agent
  shared-data/
    journal/paper/trades.jsonl ← All paper trades (source of truth)
    journal/real/trades.jsonl  ← All real trades
    cache/
      market_intelligence.json ← Intelligence packet
      debate_output.json       ← Last debate results
      credit_spread_scan.json  ← Last scan results
      collar_candidates.json   ← Last collar scan
    logs/
      debate_log.jsonl         ← All debate sessions
      evolution_log.jsonl      ← All evolution events
      evolution_observations.jsonl
    strategies/
      sofi_collar.json         ← Strategy params (evolution updates this)
    google_service_account.json ← Google Sheets auth

/home/trader/QuantAI/
  v2/shared-data/scripts/      ← All Python scripts
    market_intelligence.py
    debate_chamber.py
    self_evolution.py
    scan_options.py
    fetch_sofi.py
    pattern_engine.py
    sheets_sync.py
  .env                         ← All API keys and config
```

---

## Pre-Live Checklist

Before any real capital:
- [ ] 40+ paper trades logged, 60%+ win rate over 3+ weeks
- [ ] Self-evolution ran ≥ 4 weeks, at least 1 validated change applied
- [ ] Debate chamber proposals reviewed weekly — quality confirmed
- [ ] Subscribe MarketXLS Advanced ($94/mo)
- [ ] Subscribe Unusual Whales ($48/mo)
- [ ] Open IBKR account for XSP (Section 1256, 60/40 tax treatment)
- [ ] Separate live Alpaca API keys (never reuse paper keys)
- [ ] Emergency stop tested end-to-end
- [ ] pip audit clean on all scripts

---

## Security

- GitHub PAT: create fresh each session, delete immediately after. Never reuse.
- API keys: all in /home/trader/QuantAI/.env — never hardcoded in scripts
- google_service_account.json: in /root/quantai-v2/shared-data/ — never commit to git
- New open-source tools: >500 stars, manual review, pin versions with ==

---

## How to Use This Document

Start every new Claude chat:
> "Read SYSTEM_STATE.md. I want to [task]."

After significant changes: update this file → push to GitHub → re-upload to Claude project.

*Last updated: April 1, 2026 — Full system live. Debate chamber proven. Google Sheets journal working. All 7 scripts deployed. Correct paths verified.*
