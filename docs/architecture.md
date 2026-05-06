# QuantAI — Architecture

**Status**: living document. Last full revision: 2026-05-06.
**Scope**: every meaningful piece of QuantAI as it actually runs today — agents, broker adapter, monitoring, Sentinel, dashboard, KARNA, journals, ADRs.
**Pattern**: each section is structured **ELI10 → How it actually works → Why → Good / Bad / Could be better**. The intent is that a 10-year-old can follow the intuition and an interviewer can grill you for 60 minutes from the same text.

### Companion documents

- **[ARCHITECTURE_SUMMARY.md](./ARCHITECTURE_SUMMARY.md)** — the ~1500-word public-facing intro. Read this first if you only have 10 minutes. It links back into the deep sections below.
- **[STATE.md](./STATE.md)** — the dated "current state snapshot" (halt status, open positions, trade counts). Lives separately because it ages fast.
- The interview cheat sheet (formerly §21) is kept as a private operator note outside this repo.

The rest of this doc is the deep dive. Skim §1 for the thesis, §2 for the wiring diagram, then jump via the TOC.

---

## §0 Table of contents

- [§1 — 30-second pitch](#1--30-second-pitch)
- [§2 — Big-picture diagram](#2--big-picture-diagram)
- [§3 — The four agents](#3--the-four-agents)
  - [§3.1 — Agent Alpha (LLM-debated ETF spreads)](#31--agent-alpha-llm-debated-etf-spreads)
  - [§3.2 — Agent Beta (regime-driven SPX/XSP/VIX)](#32--agent-beta-regime-driven-spxxspvix)
  - [§3.3 — Agent Gamma (Connors-RSI mean-reversion)](#33--agent-gamma-connors-rsi-mean-reversion)
  - [§3.4 — Agent Sentinel (infrastructure ops)](#34--agent-sentinel-infrastructure-ops)
- [§4 — Broker adapter + safeguards](#4--broker-adapter--safeguards)
- [§5 — The pipeline](#5--the-pipeline)
- [§6 — Debate chamber](#6--debate-chamber)
- [§7 — Journal — sacred source of truth](#7--journal--sacred-source-of-truth)
- [§8 — Monitoring](#8--monitoring)
- [§9 — Self-learning loop](#9--self-learning-loop)
- [§10 — KARNA, OpenClaw, Discord, workspaces](#10--karna-openclaw-discord-workspaces)
- [§11 — The dashboard — command center](#11--the-dashboard--command-center)
- [§12 — Cron — the metronome](#12--cron--the-metronome)
- [§13 — LLM cost discipline / ClawRoute](#13--llm-cost-discipline--clawroute)
- [§14 — Graphify](#14--graphify)
- [§15 — Architecture decision records (ADRs)](#15--architecture-decision-records-adrs)
- [§16 — Safety culture: 12 hard rules in code](#16--safety-culture-12-hard-rules-in-code)
- [§17 — What's good](#17--whats-good)
- [§18 — What's bad / weak / risky](#18--whats-bad--weak--risky)
- [§19 — What could be better](#19--what-could-be-better)
- §20 — Current state snapshot → moved to **[STATE.md](./STATE.md)**
- §21 — Interview cheat sheet → kept private (not in this repo)
- [§22 — Glossary](#22--glossary)

---

## §1 — 30-second pitch

> **Thesis.** QuantAI is an autonomous, multi-agent options-trading system on a $1M IBKR paper account. It uses LLMs **only where judgment is needed** and enforces **all safety rules in code, not in prompts**. Four agents (Alpha LLM-debated, Beta deterministic regime, Gamma single-setup, Sentinel ops) run as cron-driven Python scripts on a single VPS, sharing one journal and one broker adapter. The whole thing costs ~$4–5/month in LLM spend.

### ELI10

QuantAI is a robot that places carefully-controlled options trades on a $1,000,000 paper-money brokerage account, 24/7. The robot is actually four small robots, each with one specialty:

- **Alpha** — talks to itself first (a Bull and a Bear argue, a Judge votes yes/no), then sells safe option spreads on ETFs
- **Beta** — looks at "what kind of weather is the market in?" and picks a strategy from a list. Never asks anyone, never debates. Trades the big stock indexes (SPX, XSP, VIX)
- **Gamma** — only knows ONE play: "buy the dip in a strong stock." Waits patiently. When all the rules say go, goes
- **Sentinel** — doesn't trade. Watches everything else. Fixes broken pipes. Asks the operator's permission for the riskier fixes by sending a message to a phone

There's also a human (Amit) who can override anything from his phone via Discord buttons.

### How it actually works

QuantAI is a single-VPS Linux deployment. The trading agents run as Python crons. Each agent reads market data, runs its decision logic, and (if its rules pass) submits a multi-leg options order through a single broker adapter. Successful orders write a single line to one append-only journal (`trades.jsonl`). A position monitor reads that journal every two minutes, fetches broker positions, applies exit rules, and closes legs when triggered. Every closed trade kicks off two short LLM passes (per-trade self-diagnosis + post-trade review) whose outputs roll up into a Sonnet-driven weekly synthesis and a dashboard "Self-Learning" tab.

A separate ops agent (Sentinel) runs four times per weekday plus weekend observe slots. It reads logs, errors, and health snapshots, classifies findings, and either silently auto-applies safe fixes or queues riskier ones for a Discord ✅ approval card. Sentinel is path-allowlisted away from the trading code and the journal — it can heal infrastructure, not edit traders.

A static-then-reactive dashboard renders everything: collectors write versioned JSON state files, a single React SPA polls them every 30 seconds. The whole UI is fronted by Tailscale (mesh-identity auth, no app login).

### Why this design

> **LLMs only where judgment is needed; everything else is deterministic Python; hard rules in code, not prompts.**

That single sentence drives every other choice in this document. You will see it again in §16.

- **Beta is zero-LLM.** A regime classifier is a numeric problem with no ambiguity. Reproducibility, audit-ability, zero variance per cycle.
- **Gamma is zero-LLM.** Connors RSI(10) and a 200-day SMA do not require Claude to interpret. Determinism wins.
- **Alpha uses LLMs because it needs to synthesize news, earnings, regime, and macro into a trade thesis.** Even there, the constitution lives in code (path allowlists, NEVER lists, daily caps) and the LLM only votes within those rails.
- **Sentinel uses LLMs because incident triage benefits from reading prose.** But its NEVER lists are enforced in Python — a "creative" Sentinel cannot bypass them.

### Good

- Three independent trading edges (LLM judgment, regime determinism, single-setup specialty) on one account
- Cost discipline as architecture (§13) — ~$4–5/month total LLM spend for the whole system
- Layered defense (§8) — five independent monitors, none of which trust the others' state
- Broker adapter (§4) — one env var swap toggles between IBKR and Alpaca

### Bad

- Single VPS, single IB Gateway, single Tailscale relay — no HA, no failover (§18)
- Manual workspace sync, manual dashboard structure edits (§18)
- Strategy data is thin — only ~9 trading days live at the time of this writing

### Could be better

See §19 for the full list. Top three: a secondary VPS standby, a CI dry-run gate before any cron change, and an automated drift detector for the dashboard.

---

## §2 — Big-picture diagram

### ELI10

This is the wiring diagram. Every box is a real thing on the VPS. Arrows show who talks to whom.

### How it actually works

```
                    ┌──────────────────────────────────────────┐
                    │  Operator (Amit)                          │
                    │  • phone Discord ✅ approvals             │
                    │  • Cowork SSHFS sessions for code edits   │
                    └──────────┬────────────────────────┬───────┘
                               │                        │
                  Discord bot  │                        │ SSHFS
                               ▼                        ▼
   ┌─────────────────────────────────────────────────────────────────┐
   │                      VPS  (87.99.141.55)                         │
   │                      Tailscale 100.84.147.23                     │
   │  Ubuntu 6.8.0-90-generic · 75G disk · 3.7G RAM                   │
   │                                                                  │
   │  ┌───────────────────────────────────────────────────────────┐  │
   │  │  KARNA / OpenClaw  (24/7 Claude Sonnet 4.6)                │  │
   │  │  systemd: Type=simple · /etc/karna/secrets.env             │  │
   │  └─────┬─────────────────────────────┬───────────────────────┘  │
   │        │ tools                        │ Discord bot              │
   │        ▼                              ▼                          │
   │  ┌─────────┐    cron (UTC)   ┌─────────────────────────┐        │
   │  │ ClawRoute│◄────────────── │ 4 agents + monitoring   │        │
   │  │ :18790   │   LLM ingress  │                         │        │
   │  │ tier rt  │                │ Alpha · Beta · Gamma    │        │
   │  └────┬────┘                 │ Sentinel                │        │
   │       │                      │ heartbeat / position /  │        │
   │       │                      │ system / error monitors │        │
   │       │                      └─────────┬───────────────┘        │
   │       │                                │                         │
   │       ▼                                ▼                         │
   │  ┌─────────┐                ┌────────────────────┐               │
   │  │Anthropic│                │ broker.py adapter   │               │
   │  │  API    │                │ BROKER_TYPE env var │               │
   │  └─────────┘                └─────┬─────────┬────┘               │
   │                                    │         │                   │
   │                            primary │         │ fallback          │
   │                                    ▼         ▼                   │
   │                           ┌──────────┐  ┌──────────┐             │
   │                           │  IBKR    │  │ Alpaca   │             │
   │                           │ Gateway  │  │ Paper    │             │
   │                           │ :4002    │  │ REST     │             │
   │                           │ DUP851506│  │          │             │
   │                           └────┬─────┘  └────┬─────┘             │
   │                                │             │                   │
   │                                └──────┬──────┘                   │
   │                                       ▼                          │
   │                          ┌──────────────────────────┐            │
   │                          │ trades.jsonl              │            │
   │                          │ (append-only journal)     │            │
   │                          │  A### / B### / G###       │            │
   │                          └─────┬──────────────┬─────┘            │
   │                                │              │                  │
   │                                ▼              ▼                  │
   │                       ┌────────────┐   ┌──────────────┐          │
   │                       │ collectors │   │ self-learning │          │
   │                       │ (cron 1-15m)│   │ loop          │          │
   │                       └─────┬──────┘   └─────┬────────┘          │
   │                             │                │                   │
   │                             ▼                ▼                   │
   │                      ┌─────────────────────────────┐             │
   │                      │ /var/dashboard/state/*.json │             │
   │                      └────────────┬────────────────┘             │
   │                                   ▼                              │
   │                      ┌─────────────────────────────┐             │
   │                      │ /var/dashboard/index.html   │             │
   │                      │ (single-page React SPA)     │             │
   │                      │ python3 -m http.server 8080 │             │
   │                      └────────────┬────────────────┘             │
   └───────────────────────────────────┼──────────────────────────────┘
                                       │
                                       ▼ Tailscale serve
                          https://quantai.tail1465ff.ts.net/
```

### Node legend

- **KARNA** — the 24/7 Claude Sonnet 4.6 instance hosted by the OpenClaw runtime. It is the supervisor that the operator talks to. It does not trade; it runs tools, drafts code, and answers questions in Discord channels.
- **ClawRoute** — single LLM ingress on `localhost:18790`. Every Anthropic call from QuantAI scripts routes through this proxy. Provides tier routing (cheaper models for cheaper tasks), centralized cost tracking, audit trail. Escape valve: `LLM_BYPASS_CLAWROUTE=1` to bypass and call Anthropic directly.
- **Broker adapter** (`broker.py`) — `BrokerBase` abstract class with `get_broker()` factory keyed off the `BROKER_TYPE` env var. Today's default is `ibkr`. Used by every trading-path script.
- **IBKR Gateway** — IB Gateway 10.37 running on `localhost:4002`, paper account `DUP851506`, started by `/opt/ibc/quantai_gateway_start.sh` (IBC wrapper that injects credentials). Daily restart at 23:45 ET.
- **Alpaca Paper REST** — `paper-api.alpaca.markets`. Demoted to fallback after the 2026-04-27 IBKR migration; retained because Alpaca supports ETF options and is a known-working second source.
- **Journal** — `/root/quantai-v2/shared-data/journal/paper/trades.jsonl`. Append-only JSONL, one trade per line. The single source of truth for trade state.
- **Collectors** — ten `collect_*.py` scripts on cron (1–15 min cadence). Each writes one or more JSON state files under `/var/dashboard/state/`. The dashboard reads these.
- **Sentinel** — `sentinel_agent.py`. Fires four times per weekday (apply slots at 8:30 AM and 4:15 PM ET; observe slots at 10/11/12/1/2/3 PM and 9 PM ET) plus a weekend observe at 10 AM. Path-allowlisted out of the trading code.
- **Dashboard** — three layers (collectors → JSON state → React SPA). Hand-edited `index.html` (no build step); `python3 -m http.server 8080` bound to `127.0.0.1`; Tailscale serve fronts it.

### Why this shape

- One VPS keeps operational complexity low.
- Cron is the metronome — no message bus, no queue, no orchestrator. Each agent runs to completion and exits. State lives on disk.
- The broker adapter is the only abstraction between the trading agents and the actual order placement. Switching brokers is one env var.
- The journal is the only contract between writers and readers. Adding a new agent is "pick a trade-ID prefix and start writing lines."

---

## §3 — The four agents

QuantAI is four small, independent processes. Each has its own identity file (`v2/shared-data/agents/AGENT_*_IDENTITY.md`), its own log file, its own trade-ID prefix, and its own narrow mandate. They share three things: the broker adapter, the journal, and the position monitor.

### §3.1 — Agent Alpha (LLM-debated ETF spreads)

#### ELI10

Alpha is the careful kid who, before doing anything, asks Mr. Bull and Ms. Bear to argue about it, then has a Judge decide yes or no. Alpha trades safe-ish option spreads on regular stocks and ETFs.

#### How it actually works

- **Identity file**: `v2/shared-data/agents/AGENT_ALPHA_IDENTITY.md`. Mandate: "premium-collecting, defined-risk options income agent. Capital preservation prime directive."
- **Trade-ID prefix**: `A###`
- **Cron entry point**: `*/15 13-20 * * 1-5 run_pipeline.py` (every 15 min during 9 AM–4 PM ET, weekdays)
- **Pipeline files** (in the order they run):
  - `v2/shared-data/scripts/run_pipeline.py` — dispatcher; checks market hours, daily budget, regime
  - `v2/shared-data/scripts/market_intelligence.py` — pulls VIX, prices, technicals, SPX/Beta context via yfinance; writes cache JSON
  - `v2/shared-data/scripts/scan_options.py` — 78 tickers × 4 strategies (bull put spread, bear call spread, iron condor, diagonal); filters via `max_loss / 50000 ≤ 2%` cap
  - `v2/shared-data/scripts/debate_chamber.py` — Proposal (Sonnet) → Bull/Bear PY templates → Judge (Sonnet); see §6
  - `v2/shared-data/scripts/autonomous_execution.py` — submits via `broker.place_mleg_order` and writes journal entry as `A###`
- **Strategies in arsenal**:
  - `bull_put_spread` — bullish/neutral, sell ATM-ish put + buy further OTM put for cap
  - `bear_call_spread` — bearish/neutral, sell ATM-ish call + buy further OTM call for cap
  - `iron_condor` — both wings simultaneously; range-bound thesis
  - `diagonal_spread` — short near-term + long far-term; theta-decay harvest
- **Universe**: 78 ETFs and large caps. List is hardcoded in `scan_options.py`.

#### Decision flow

1. Cron fires `run_pipeline.py`.
2. Market hours check. If outside 9:30 AM–4:00 PM ET, exit.
3. Daily-budget check (see §16 rule 3): count today's *journal-written* `A###` entries. If ≥2, exit.
4. Run `market_intelligence.py`. Cache result in `/root/quantai-v2/shared-data/cache/market_intelligence.json`.
5. **VIX gate**: if VIX ≥ 35, regime = HALT, exit.
6. Run `scan_options.py`. Output the top candidates to `cache/scan_results.json`.
7. Run `debate_chamber.py`. The judge returns a 0–100 score per candidate. Threshold: ≥60 to approve.
8. For each approved candidate, run `autonomous_execution.py`:
   - `find_spread_strikes()` — query the broker chain, snap proposed strikes to nearest available
   - `broker.place_mleg_order()` — submit the multi-leg combo
   - Wait for fill (up to 5s poll), then write journal `A###` entry
9. Write a heartbeat to `/tmp/quantai-heartbeats/pipeline.beat` regardless of outcome.

#### Risk gates

- **Per-trade max loss**: ≤ 2% of $50k effective sizing cap = $1,000 max loss per `A###`
- **Daily entries cap**: 2 (counted from journal; silent-failure-proof)
- **Daily P&L halt**: -5% of effective equity stops new entries for the day
- **Time blackouts**: no entries 9:30–9:45 AM ET (open auction) or after 3:00 PM ET (theta-burn risk)
- **Hard close**: 3:30 PM ET on Alpha trades — `position_monitor.py` will close any 0/1-DTE Alpha leg unconditionally (see §8.2)
- **Earnings blackout**: 14 calendar days before scheduled earnings — absolute, no override
- **VIX gate**: VIX ≥ 35 → HALT regime → no entries
- **Regime gate**: `regime != "halt"` required (regime computed by Beta's classifier; Alpha shares it)

#### What Alpha won't do

- Will not enter without VIX < 35
- Will not enter within 14 days of an earnings event
- Will not exceed 2 entries/day, period
- Will not enter inside 9:30–9:45 or after 3:00 ET
- Will not hold an Alpha trade past 3:30 PM ET on the day it expires
- Will not bypass the debate chamber — even if the LLM goes rogue, the daily cap and max-loss gates are in code, not in the prompt

#### Good

- LLM judgment combined with hard-rule rails
- 78-ticker universe gives the scanner plenty to choose from
- Debate chamber catches "this looks great but earnings is in 4 days"-class mistakes
- Cost: ~$0.002–0.003 per debate cycle (see §6)

#### Bad

- **No price re-validation** between debate time and execution time. If the credit moves between the LLM's vote and the order submission, Alpha submits at whatever the broker's chain query gives. This is a real gap (§18).
- Snap-to-nearest-strike behavior can swap the structure if the LLM's proposed strike isn't available.
- Hard close at 3:30 is alert-only; actual closing logic lives in `position_monitor.py` (subtle gotcha).

#### Could be better

- Add an IV/credit re-validation step before `place_mleg_order` (mid-quote check vs the debate-time credit; abort if drifted > N%).
- Make the debate's Bull/Bear templates strategy-aware *and* condition-aware (currently strategy-only).
- Strategy-level position grouping — the position monitor today treats each Alpha trade independently; an iron condor with both wings touched should be a single decision.

---

### §3.2 — Agent Beta (regime-driven SPX/XSP/VIX)

#### ELI10

Beta is the kid who NEVER asks anyone for advice. He looks at a chart that says "in this kind of weather, do this," and does it. He trades the big indexes (SPX, XSP, VIX) which have special tax + settlement perks.

#### How it actually works

- **Identity file**: `v2/shared-data/agents/AGENT_BETA_IDENTITY.md`. Mandate: "Index options specialist. Regime-driven, deterministic, zero-LLM. Structural edge: Section 1256 tax, European settlement, cash settlement."
- **Trade-ID prefix**: `B###`
- **Cron entry point**: `*/15 13-20 * * 1-5 beta_agent.py` (parallel to Alpha's pipeline)
- **Key files**:
  - `v2/shared-data/scripts/beta_agent.py` — main loop. **Refuses to run if `BROKER_TYPE != ibkr`** (Beta needs index options, which Alpaca doesn't have).
  - `v2/shared-data/scripts/beta/regime_detector.py` — classifies the market into one of 12 regimes
  - `v2/shared-data/scripts/beta/strategies/*.py` — 8 strategy modules
  - `v2/shared-data/scripts/beta/risk_engine.py` — per-source risk gates, scoped to `agent_beta`
  - `v2/shared-data/scripts/beta/event_moves_seeder.py` — weekly Sunday cron, pulls historical event moves from Finnhub
  - `v2/shared-data/scripts/beta/_chain_helpers.py` — index-option chain helpers (CBOE exchange, tradingClass disambiguation)

#### The 12 regimes (priority chain, first match wins)

1. **HALT** — VIX ≥ 35 or other system halt; no trading
2. **CRISIS** — VIX > 30, big drawdown
3. **MEAN_REVERSION_OVERBOUGHT** — RSI > 75, ADX low
4. **MEAN_REVERSION_OVERSOLD** — RSI < 25, ADX low
5. **HIGH_VOL** — IV rank elevated
6. **SQUEEZE** — Bollinger Band width percentile in lowest decile
7. **PRE_EVENT** — known event (FOMC, NFP, CPI) within N days
8. **TREND_UP** — price above SMA200, ADX > 25
9. **TREND_DOWN** — price below SMA200, ADX > 25
10. **LOW_VOL** — VIX < 15, IV rank low
11. **RANGE** — bounded by recent highs/lows
12. **NORMAL** — default fallback

#### The 8 strategies (regime → strategy mapping)

| Strategy | File | Picked when |
|---|---|---|
| `event_strangle` | `beta/strategies/event_strangle.py` | PRE_EVENT |
| `broken_wing_butterfly` | `beta/strategies/broken_wing_butterfly.py` | HIGH_VOL, RANGE, NORMAL |
| `calendar_spread` | `beta/strategies/calendar_spread.py` | LOW_VOL, RANGE |
| `call_ratio_backspread` | `beta/strategies/call_ratio_backspread.py` | TREND_UP |
| `put_ratio_backspread` | `beta/strategies/put_ratio_backspread.py` | CRISIS, TREND_DOWN |
| `credit_spread_offset` | `beta/strategies/credit_spread_offset.py` | HIGH_VOL |
| `debit_spread` | `beta/strategies/debit_spread.py` | TREND_UP, TREND_DOWN, MR-OVERSOLD |
| `vix_calls` | `beta/strategies/vix_calls.py` | CRISIS |

Each strategy module exposes the same interface: `select_strikes(broker, market_intel, regime_data) -> dict`, `simulated_slippage(legs) -> float`, `exit_rules(entry) -> dict`.

#### Decision flow

1. Cron fires `beta_agent.py`.
2. Refuse if `BROKER_TYPE != ibkr`.
3. Load `cache/market_intelligence.json` and `cache/event_moves.json`.
4. Run `regime_detector.py` → returns one of the 12 regimes (deterministic, no LLM).
5. Pick strategy from the regime mapping.
6. Run strategy's `select_strikes()` against the broker chain.
7. `risk_engine.can_enter()`:
   - Max 3 open Beta trades simultaneously
   - Max 2 entries per day (counted from journal)
   - 5-loss circuit breaker — if last 5 closed trades are all losses, halt for 24h
   - -2% daily / -5% weekly drawdown halts
   - Portfolio-level Greeks correlation gates (don't pile up delta/vega in same direction)
8. `position_size()` — compute contracts based on $50k effective sizing cap (1% per trade = $500 risk)
9. `broker.place_mleg_order()` on IBKR (always — no Alpaca fallback for Beta)
10. Write journal `B###` entry with `decision`, `regime_at_entry`, `regime_data`, `exit_rules`, `simulated_slippage`, `net_delta`, `net_vega`

#### Why $50k effective cap on a $1M account

Beta's sizing logic uses `min(broker_equity, 50000)` as the position-size denominator. The drawdown gates use the *real* broker equity ($1M). The asymmetry is intentional: it caps single-trade risk at 1% of $50k = $500 per trade so the strategy can be evaluated honestly without a $1M paper account inflating sizes.

#### What Beta won't do

- Will not run on Alpaca (refuses at startup)
- Will not enter during HALT regime
- Will not exceed 3 open trades or 2 entries/day
- Will not bypass the 5-loss circuit breaker
- Will not exceed the daily/weekly drawdown caps
- Will not call any LLM — full stop

#### Good

- Fully deterministic — same inputs always produce same outputs
- Zero LLM cost
- 12 regimes × 8 strategies gives broad coverage of market conditions
- Per-trade `exit_rules` means each trade carries its own exit logic with it (the position monitor doesn't need to know what regime spawned a trade)
- Section 1256 tax treatment + European exercise + cash settlement — three structural advantages over equity options

#### Bad

- IV Rank is computed from 21-day realized volatility as a proxy, not from the actual chain — noisy approximation (§18)
- Finnhub free tier limits `event_moves_seeder` to ~3 weeks of history; the 8-event window takes time to fill
- 12 regimes × 8 strategies = wide coverage, but only one strategy fires per cycle (no ensemble)
- **No price re-validation** between strategy selection and order submission — same gap as Alpha (§18)

#### Could be better

- True chain-derived IV rank (would require option chain history snapshots)
- Multi-strategy ensemble within one regime (e.g., 50% calendar + 50% BWB if both are valid)
- Add credit-band re-validation before submission

---

### §3.3 — Agent Gamma (Connors-RSI mean-reversion)

#### ELI10

Gamma is the kid who only knows ONE play: "buy the dip in a strong stock." He's REALLY good at that one play, but most days he just sits and watches.

#### How it actually works

- **Identity file**: `v2/shared-data/agents/AGENT_GAMMA_IDENTITY.md`. Mandate: "Mean-reversion specialist. Connors RSI pullback. One setup."
- **Trade-ID prefix**: `G###`
- **Two-phase cron**:
  - **Scan**: `30 20 * * 1-5 gamma_agent.py --scan` (4:30 PM ET, after close)
  - **Execute**: `33 13 * * 1-5 gamma_agent.py --execute` (9:33 AM ET, after open)
- **Key files**:
  - `v2/shared-data/scripts/gamma_agent.py` — main entry
  - `v2/shared-data/scripts/gamma/scanner.py` — fetches 252 daily bars, computes indicators, ranks candidates
  - `v2/shared-data/scripts/gamma/_indicators.py` — Wilder RSI(10), 200-day SMA
  - `v2/shared-data/scripts/gamma/strike_selector.py` — picks 0.50-delta long + 0.27-delta short for a bull call debit spread
  - `v2/shared-data/scripts/gamma/risk_check.py` — sector cap, daily/open-trade limits, circuit breaker
  - `v2/shared-data/scripts/gamma/earnings.py` — 7-day earnings blackout

#### The single setup

```
IF      RSI(10) < 30                  (Wilder's RSI; deeply oversold short-term)
AND     close > 200-day SMA           (long-term uptrend confirmed)
AND     not within 7 days of earnings (blackout)
AND     liquid (volume + open interest thresholds)
THEN    buy a bull call debit spread, 14–21 DTE, 0.50/0.27 delta legs
EXIT    on RSI(10) > 40, OR 10 trading days elapsed,
        OR price closes < 200 SMA, OR +150% gain, OR -50% loss
```

#### Universe

27 instruments — chosen for liquidity and earnings predictability:

- **4 indices** — SPY, QQQ, IWM, DIA (ETF proxies; Gamma uses ETFs not native indexes because debit spreads on cash indexes are awkward)
- **3 ETFs** — XLF, XLE, XLK
- **20 mega-caps** — AAPL, MSFT, GOOGL, AMZN, META, NVDA, TSLA, BRK.B, JPM, V, JNJ, WMT, PG, UNH, HD, MA, BAC, DIS, CSCO, NKE

#### Decision flow

**Scan phase (4:30 PM ET):**

1. For each of 27 instruments, fetch 252 daily bars (yfinance).
2. Compute Wilder RSI(10) and 200-day SMA on each.
3. Filter: RSI(10) < 30 AND close > 200-SMA AND not in earnings blackout.
4. Apply sector cap (max 2 per sector to avoid concentration).
5. Apply per-cycle limits: ≤ 3 open Gamma trades, ≤ 2 new entries/day.
6. Write surviving candidates to `cache/gamma_pending_entries.json`.
7. If zero candidates, log and exit (this is the common case).

**Execute phase (9:33 AM ET, next morning):**

1. Read `cache/gamma_pending_entries.json`.
2. For each candidate, run `_revalidate()`:
   - Re-fetch fresh daily bar (the previous day closed; market gapped overnight)
   - Reject if RSI(10) ≥ 35 (soft bound — drifted too far)
   - Reject if close fell below 200-SMA (uptrend broken overnight)
3. For survivors, `strike_selector.build_spread()` queries the IBKR chain for nearest 14–21 DTE expiration, picks 0.50-delta long + 0.27-delta short.
4. `risk_check.pass_all()` — sector cap, max-open, circuit breaker
5. `broker.place_mleg_order()` with `client_id=22` (Alpha=1, Beta=21, Gamma=22 — prevents IB clientId collisions)
6. Write `G###` journal entry with `exit_rules` payload.

#### Why two phases?

The scan happens after market close so it sees the closing price. Execution waits until the next morning so:

- Gap-risk filter — `_revalidate()` re-checks the trade after overnight news
- Liquidity is better at 9:33 AM than at 9:30 AM open auction
- Operator has time to manually cancel any candidate that looks wrong

#### What Gamma won't do

- Will not enter without RSI(10) < 30 at scan and < 35 at execute
- Will not enter when close < 200-day SMA
- Will not enter within 7 days of earnings (stocks only — indexes don't have earnings)
- Will not exceed 3 open Gamma trades or 2/day entries
- Will not bypass the sector cap (max 2/sector)
- Will not exceed the 3-loss circuit breaker (48h pause)

#### Backtest

Connors RSI(10) + 200-SMA on the equivalent universe (1996–2019): 88.89% win rate, +1.17% expected value per trade. Backtest is in `services/backtester.py` (not currently wired to CI; see §18).

#### Good

- One setup means the operator knows exactly what's being traded
- Daily-bar driven — no intraday noise
- The two-phase scan/execute split with `_revalidate()` is the only re-validation in the trading path (§18)
- Backtest evidence is strong
- Zero LLM cost

#### Bad

- Sparse — at the time of writing, **zero G### trades have been placed in 7 trading days live** (no setup met all criteria)
- Will under-trade in trending markets (RSI rarely dips below 30 if everyone's buying every dip)
- 200-SMA filter eliminates legitimate mean-reversion candidates in sideways markets

#### Could be better

- Allow shorter SMAs in non-trending regimes
- Add a "near-miss" diagnostic to the dashboard (currently shows "0 candidates" without saying *why* — could be 24/27 above 200-SMA, 0/27 with RSI < 30, etc.)
- Track a hypothetical "what if I had taken the next-best signal" log for review

---

### §3.4 — Agent Sentinel (infrastructure ops)

#### ELI10

Sentinel is the janitor who fixes broken stuff. He has a list of things he's NOT allowed to touch — and the list is in code, so even if he "thinks" he should, he can't. He doesn't trade. He fixes.

#### How it actually works

- **Identity file**: `v2/shared-data/agents/AGENT_SENTINEL_IDENTITY.md`
- **Replaces**: `auto_heal.py.retired.2026-05-03` (and `auto_heal.py.bak.2026-04-30` etc. — multiple iterations preceded Sentinel)
- **Trigger reason**: the 2026-05-02 IBKR outage proved auto_heal had four structural gaps — weekday-only, ibgateway blanket-blocked, NEVER_RESTART_SERVICES defined-but-not-code-enforced, no errors.db access. Sentinel was built to fix every one.
- **Main script**: `v2/shared-data/scripts/sentinel_agent.py`
- **Cron entry**: `*/15 * * * * sentinel_agent.py --auto`
  - The `--auto` wrapper reads the ET clock and dispatches to one of: 8:30 AM apply, 10/11/12/1/2/3 PM observe, 4:15 PM apply, 9 PM observe (weekday); 10 AM observe (weekend). If the tick is not a slot, exit silently.
- **Skills loaded** (under `v2/shared-data/agents/skills/`):
  - `infrastructure-health-metrics.md` — every cycle
  - `incident-triage.md` — when health report has warning/error
  - `earnings-risk.md` — apply cycles only
  - `execution-quality.md` — apply cycles only

#### Apply vs observe

| Mode | Trigger times | Model | Action |
|---|---|---|---|
| **observe** | 10 AM–3 PM hourly + 9 PM weekday + 10 AM weekend | Haiku | Read state, queue digest, never mutate |
| **apply** | 8:30 AM + 4:15 PM weekday | Sonnet | Read state + execute safe-auto + queue propose-wait to Discord |

Apply mode self-downgrades to observe if the runtime ET clock is inside 13:00–20:00 UTC (trading window). This is enforced in code, not in the prompt — so if the wrapper clock drifts, the trading agents still own the trading window.

#### Fix classification

Sentinel classifies every proposed fix into one of three classes:

| Class | Behavior |
|---|---|
| `safe_auto` | Auto-execute if Python NEVER lists pass. Examples: stale `/tmp/*.lock` cleanup, restart of *non-trading* collector crons, IBKR gateway restart **only when** market_hours=false AND open_positions=0 AND `ibkr_port` check is "error" |
| `propose_wait` | Post a ✅ card to `#karna-approvals`. Operator taps to approve. Next apply slot polls reaction. 48-hour TTL. Examples: any code edit, service restart other than collectors, novel error patterns |
| `never_touch` | Observe-only. Examples: `autonomous_execution.py`, `beta_agent.py`, `gamma_agent.py`, `position_monitor.py`, `_broker_ibkr.py`, `broker.py`, `.env`, `/home/openclaw/`, journal directory, `/etc/systemd/`, `openclaw.service` |

#### The three NEVER lists (code-enforced)

- `NEVER_MODIFY_PATHS` — trading-path Python files. `is_command_safe()` blocks any patch that touches them.
- `NEVER_TOUCH_PATHS` — `.env`, openclaw, ibgateway, journal directory. Any read of these via Sentinel's tool surface is denied.
- `NEVER_RESTART_SERVICES_BLANKET` — `openclaw.service` (CLAUDE.md hard rule), `ibgateway.service` (only via the position-aware path).

These are checked in `is_command_safe()` and `validate_proposal()` BEFORE any proposal executes. Even if the LLM tags a proposal `safe_auto`, the Python gate can still reject it.

#### Other safety rails

- **3-attempt budget per fix-id** (sha1 of diff + files + commands). After three failed apply attempts, the fix-id is quarantined and Sentinel posts an escalation to Discord.
- **`.bak` before any edit** — every diff applied creates `<file>.bak.YYYY-MM-DD-HHMMSS-sentinel`. `--rollback <fix_id>` restores from receipt.
- **80-line diff cap** — proposals with diffs larger than 80 lines are auto-classified as `propose_wait` regardless of LLM tag.
- **State file** at `/var/dashboard/auto_heal_data/state.json` — tracks attempts, quarantined ids, last apply timestamp.
- **Lock file** at `/tmp/sentinel_agent.lock` — single-instance enforcement.
- **Built-in safe_auto: catalog reclassification** — known-noise patterns (fail2ban, UFW SSH brute force, health-monitor stale-socket) get auto-flipped to `severity=info, resolved=true` to keep the dashboard from drowning in scanner noise.

#### What Sentinel won't do

- Will not edit any trading-path file
- Will not touch `.env`, openclaw, ibgateway service file, or the journal
- Will not restart ibgateway when open positions exist OR during market hours (BOTH must be false)
- Will not bypass the 3-attempt budget
- Will not run uninstrumented or below Haiku
- Will not read positions for P&L
- Will not modify trades.jsonl

#### Approval flow

```
1. Sentinel apply slot fires
2. LLM (Sonnet) reviews context bundle:
   - state.json
   - errors.db top 50 critical/error
   - system-health-report.json (the 13-check report)
   - quantai-sentinel.json (last cycle's tile)
   - error-catalog.json (taxonomy reference)
3. LLM emits proposals[] with fix_class tags
4. For each proposal:
   - validate_proposal() runs Python NEVER-list gate
   - If gate blocks safe_auto → reclassify to propose_wait
5. safe_auto fixes:
   - .bak the targets
   - apply patch (with 80-line cap)
   - run shell commands (with timeout)
   - record receipt
6. propose_wait fixes:
   - Post ✅ card to #karna-approvals
   - Card includes: title, description, fix_id, diff (truncated), commands
7. Next apply slot:
   - Poll Discord for ✅ reactions on pending cards
   - For approved cards (still within 48h TTL), apply with same gate
8. Receipts written to /var/dashboard/auto_heal_data/receipts/<fix_id>.json
9. Tile written to /var/dashboard/state/quantai-sentinel.json (dashboard reads)
```

#### Cost

~$4–5/month total. Observe runs on Haiku (cheap); apply runs on Sonnet but only twice per weekday.

#### Good

- Code-enforced safety (not just prompt text)
- 4 cycles/weekday + weekend means coverage without noise
- Fix-id + 3-attempt budget prevents hammering
- Clear separation: apply does, observe queues
- Position-aware ibgateway guard is the exact lesson from the 2026-05-02 outage

#### Bad

- **Pending-approval cards re-post every apply slot until acted on** — no "I asked you yesterday" memory (§18)
- The 48h TTL is good for stale proposals but does mean a vacationing operator can lose context
- Sentinel can't update the dashboard's structural HTML (path allowlist excludes `/var/dashboard/index.html`) — only its state JSON tile
- Catalog reclassification rules are hardcoded in Sentinel; adding a new noise pattern requires a code edit

#### Could be better

- "Snoozed for 24h" state on pending fixes (the user dismissed once, don't ask again until tomorrow)
- Dashboard structural drift detector that compares `index.html` agent list against `AGENT_*_IDENTITY.md` files
- Configurable noise patterns instead of hardcoded
- Read-only access to `/var/dashboard/index.html` so Sentinel can *propose* drift fixes (still propose-wait — humans approve)

---

## §4 — Broker adapter + safeguards

### ELI10

The broker adapter is like a LEGO plug. Same shape, different brand. The trading agents don't know if they're talking to IBKR or Alpaca — they just call `broker.place_mleg_order(legs)` and it works.

### §4.1 — The adapter pattern

- **File**: `v2/shared-data/scripts/broker.py`
- **Abstract base**: `BrokerBase` — defines the methods every implementation must provide
- **Factory**: `get_broker()` reads `BROKER_TYPE` env var and returns the right concrete class. Default: `ibkr` since 2026-04-27.
- **Concrete classes**:
  - `_broker_ibkr.py` → `IBKRBroker`
  - `_broker_alpaca.py` → `AlpacaBroker`

#### The interface (every implementation provides)

```python
class BrokerBase:
    def connect(self) -> bool: ...
    def disconnect(self) -> None: ...
    def get_account(self) -> Optional[dict]: ...
    def get_positions(self) -> list: ...
    def verify_legs_flat(self, legs: list) -> list: ...   # returns symbols still non-zero
    def fetch_option_chain(self, symbol: str, ...) -> list: ...
    def get_quote(self, symbol: str) -> Optional[dict]: ...
    def get_option_quote(self, occ: str) -> Optional[dict]: ...
    def place_mleg_order(self, legs, qty, tif, client_order_id) -> Optional[dict]: ...
    def close_position(self, legs, qty, client_order_id) -> Optional[dict]: ...
    def get_order_status(self, order_id: str) -> Optional[dict]: ...
    def poll_order(self, order_id: str) -> Optional[dict]: ...
    def get_open_orders(self, client_order_id=None) -> list: ...
```

#### The callers

- `autonomous_execution.py` (Alpha)
- `beta_agent.py` (Beta)
- `gamma_agent.py` (Gamma)
- `position_monitor.py` (closes via `close_position`)
- `pre_trade_check.py` (system_test sister script)
- `collect_alpaca.py` (yes, name is misleading — it uses the adapter, so it pulls from IBKR by default since the migration)

Switching brokers is a single env var change in `.env`: `BROKER_TYPE=alpaca` or `BROKER_TYPE=ibkr`.

### §4.2 — IBKR implementation

- **Library**: `ib_insync 0.9.86`
- **Gateway**: IB Gateway 10.37, paper trading port `4002`, `clientId=1` (default; Beta uses 21, Gamma uses 22 to avoid clientId collisions)
- **Account**: `DUP851506` (paper)
- **Service**: `ibgateway.service` (systemd, enabled). Started by `/opt/ibc/quantai_gateway_start.sh` which reads `IBKR_USERNAME` and `IBKR_PASSWORD` from `.env` and injects them via the IBC wrapper.
- **Login note**: `IBKR_USERNAME` is the *login name*, not the paper account number `DUP851506`. Keep these distinct.
- **Daily restart**: 23:45 ET (forced by IBC). Connect attempts during 23:30–00:15 ET fail by design — see the restart-window guard below.

#### Bag/ComboLeg combos

Every multi-leg order is built as a single `Bag` security with `ComboLeg` sub-legs. Critical: this submits the legs as ONE order. Without it, you get "leg risk" — one leg fills, the other doesn't, and you're holding a naked position.

```python
combo_legs = [
    ComboLeg(conId=qc.conId, ratio=int(leg.get("ratio_qty", 1)),
             action=leg["side"].upper(), exchange=qc.exchange or "SMART")
    for spec, leg in specs_with_legs
]
bag = Bag(symbol=root, exchange="SMART", currency="USD")
bag.secType = "BAG"
bag.comboLegs = combo_legs
order = MarketOrder("BUY", qty)   # see §4.4 for the limitation here
```

For index roots (SPX, SPXW, XSP, VIX, VIXW, RUT, NDX), the bag uses `exchange="CBOE"` and tradingClass disambiguation (e.g., SPXW vs SPX vs SPX-weeklies) discovered at runtime via `reqSecDefOptParams`. Never hardcoded.

#### `_IBKRNoiseFilter`

The ib_insync logger emits ~3000 events per day in normal operation: 354-class paper-data warnings, 10090/10168/10197 OPRA gap notices, "connection refused" chatter during the nightly restart, and so on. The filter at `_broker_ibkr.py:91-114` drops these. Without it, real signals drown in noise.

#### Connection resilience (`connect()`)

- 3 retries with 5-second backoff
- 15-second connection timeout per attempt
- **Restart-window guard**: refuses to connect during 23:30 ET–00:15 ET. The IB Gateway forces a daily restart at 23:45 ET, so attempting to connect in that window just fails noisily. The guard logs an ERROR once and exits cleanly.
- Per-attempt failures logged at DEBUG; single summary ERROR after 3 failures
- Returns `False` on failure (does not raise) — callers gate further operations on the return value (a poor-man's circuit breaker)

#### Phase 5b partial-fill safeguard (added 2026-05-03 → 2026-05-05)

This is the most clever piece in the codebase, born of two real incidents (A018 close treated `Cancelled` as success; A021/A022 entries treated `Cancelled` as success).

`place_mleg_order` (lines 603–767):

```python
order_submitted = False
try:
    trade = self._ib.placeOrder(bag, order)
    order_submitted = True
    self._ib.sleep(1)
    result = self._trade_to_result(trade, client_order_id)
    raw_status = result.get("status", "").strip()
    status_norm = raw_status.lower()

    # Terminal failure → return None (caller writes accurate journal)
    if status_norm in _BROKER_TERMINAL_FAILURE_STATUSES:
        # cancelled, canceled, apicancelled, apicanceled, rejected, inactive
        return None

    # Indeterminate → poll briefly for terminal state
    if status_norm in _BROKER_INDETERMINATE_STATUSES:
        # submitted, presubmitted, pendingsubmit, pendingcancel
        deadline = time.time() + ENTRY_POLL_SECONDS
        while time.time() < deadline:
            self._ib.sleep(0.5)
            result = self._trade_to_result(trade, client_order_id)
            status_norm = result.get("status", "").strip().lower()
            if status_norm in _BROKER_TERMINAL_FAILURE_STATUSES:
                return None
            if status_norm == "filled":
                return result
        # Still indeterminate → flag _working so caller can decide
        result["_working"] = True
        return result
    return result

except Exception:
    if order_submitted:
        # Exception fired AFTER the order was sent. The order may be live
        # at IBKR even though we have no Trade object. Flush callbacks,
        # then search open orders for the client_order_id.
        self._ib.sleep(0.5)
        recovered = self._find_open_order_by_ref(client_order_id)
        if recovered is not None:
            return recovered
    return None
finally:
    # Always flush async callbacks for consistent state.
    self._ib.sleep(0.5)
```

The genius is the `order_submitted` flag and the post-exception recovery. Async event-driven libraries can throw exceptions *after* state has already changed at the broker. Without `_find_open_order_by_ref`, the caller would see "order failed" and resubmit — creating duplicates.

`verify_legs_flat()` (lines 305–342) confirms broker-side zero-quantity for every leg before the journal is marked CLOSED. This caught the A018 ghost-position incident — the close had reported success, but verify_legs_flat said otherwise.

`reconcile_ghost_positions()` in `position_monitor.py:878` handles three failure modes:

- **True ghost** — broker has a position, journal has no reference at all
- **Journal lie** — journal claims CLOSED but the legs are still on the broker
- **Entry phantom** — journal says OPEN but broker has none of the legs

All three trigger Discord alerts with 60-min/symbol cooldown.

### §4.3 — Alpaca fallback

- **Library**: Alpaca paper REST API (`paper-api.alpaca.markets`)
- **Why kept**: the migration to IBKR was a one-way bet. Keeping Alpaca code paths exercised means we have a tested fallback if IBKR goes down for an extended period.
- **Why demoted**: Alpaca paper rejects index options (SPX, XSP, VIX) with HTTP 422 ("invalid underlying"). ETFs only.
- **Two Alpaca-specific gotchas**:
  - mleg orders **REQUIRE** top-level `"qty": "1"` in the payload
  - mleg orders **REJECT** `"position_intent"` — must be omitted
- **Options chain endpoint**: `paper-api.alpaca.markets/v2/options/contracts`

### §4.4 — IBKR paper combo MarketOrder limitation

Discovered today (2026-05-06) during a manual unwind attempt:

The default `MarketOrder("BUY", qty)` for a multi-leg combo in IBKR paper trading **does not always fill**, even on liquid underlyings, when the option chain quotes are returning `None` (paper data feed not warmed up). Orders sit at `Submitted` or `PendingSubmit` indefinitely until liquidity emerges.

Workaround: submit as a `LimitOrder` with an explicit price. Choose the price from intrinsic + a buffer (since bid/ask may not be available).

This is not a bug in the trading path — `place_mleg_order` always uses MarketOrder, which usually fills. It's a paper-trading-specific issue when trying to manually close stale positions.

### Good

- One env var swaps brokers
- Phase 5b partial-fill safeguard is layered defense at its finest
- `verify_legs_flat()` catches the worst class of bug (journal lies)
- Index-option chain auto-discovery (no hardcoded SPX vs SPXW)

### Bad

- IBKR Gateway is a 373 MB persistent process with a daily restart requirement
- ib_insync is async/event-driven — exceptions can arrive after state changes (the very reason we needed Phase 5b)
- Alpaca two gotchas (qty top-level + no position_intent) are easy to forget
- IBKR paper combo MarketOrder limitation (§4.4) — not a bug per se, but a sharp edge

### Could be better

- Combo LimitOrder fallback when MarketOrder hasn't filled in N seconds (with an intelligent price pick)
- Paper-data-feed warmup on broker connect (a no-op quote subscribe to all option roots before first order)
- Centralized retry/backoff config (today's values are scattered across the file)

---

## §5 — The pipeline

### ELI10

Every 15 minutes during market hours, a robot wakes up, checks the weather (market), looks for opportunities, debates them, and either places one trade or goes back to sleep.

### How it actually works

The "pipeline" specifically means Alpha. Beta has its own cron that runs in parallel; Gamma has its own two-phase cron. They share market intelligence cache files but don't gate on each other.

**Alpha pipeline order (run by `run_pipeline.py`):**

```
cron */15 13-20 * * 1-5
  ↓
run_pipeline.py
  ├─ Market hours check
  ├─ Daily budget check (count A### entries from journal today)
  ├─ market_intelligence.py     → cache/market_intelligence.json
  ├─ scan_options.py            → cache/scan_results.json
  ├─ debate_chamber.py          → cache/debate_output.json (per cycle)
  └─ autonomous_execution.py    → trades.jsonl (one A### entry per approved trade)
       └─ writes /tmp/quantai-heartbeats/pipeline.beat (always)
```

**EOD path (`run_pipeline.py eod` at 16:05 ET):**

```
run_pipeline.py eod
  ├─ Aggregate today's closed trades
  ├─ Compute day-level P&L, win rate
  └─ Post summary to Discord #alerts
```

#### Daily budget guard — silent-failure-proof

The most important detail. Counts entries from the **journal**, not from "did the script reach the end."

```python
# in run_pipeline.py
todays_alpha_entries = count_journal_entries_today(prefix="A", journal_path=JOURNAL)
if todays_alpha_entries >= 2:
    log("daily budget reached; exiting")
    return
```

Why this matters: imagine an order is submitted, the broker confirms a fill, the journal write succeeds, but then `autonomous_execution.py` crashes. If the budget were tracked in memory or in a separate counter file, the crash recovery could double-count or zero-count. The journal is the only thing the broker confirmed against, so it's the only thing the budget counts.

#### Heartbeat regardless of outcome

Every Alpha cycle ends with a `pipeline.beat` write. The heartbeat monitor (§8.1) reads that file every 2 minutes; if it's older than 20 minutes during market hours, it pages Discord.

#### Why "condition-triggered" not "schedule-triggered"

Cron fires every 15 minutes, but most ticks don't trade. They run the conditions (regime, VIX, daily-budget), and most exit early. This is intentional — agents enter when conditions allow, not on a schedule.

### Good

- Cron is dumb but reliable
- Each script runs to completion and exits — no long-lived process to leak memory
- Cache files between stages mean each stage is independently runnable for debugging
- Daily-budget check via journal-grow is silent-failure-proof
- EOD path keeps the operator informed without being part of the trading-decision path

### Bad

- 15-minute granularity means a sudden regime shift between ticks isn't reacted to
- No cross-agent gating (Alpha doesn't know Beta just entered the same delta)
- Sequential pipeline — if `scan_options.py` is slow (10–15 min on a wide universe), the cycle can spill into the next tick

### Could be better

- 5-minute pipeline cycles in volatile regimes (config-driven), 15 in calm regimes
- Cross-agent portfolio-level Greeks check before each entry
- Async `scan_options.py` (parallel ticker quotes)

---

## §6 — Debate chamber

### ELI10

When Alpha picks candidate trades, three little Claude voices in its head argue about each one:

1. A **Proposal Claude** picks the top candidates and writes a thesis
2. A **Bull voice** lists every reason it's a good idea
3. A **Bear voice** lists every reason it's a terrible idea
4. A **Judge Claude** reads everything and votes 0–100

If the Judge gives ≥60, the trade goes through. The whole conversation costs about a third of a cent.

### How it actually works

- **File**: `v2/shared-data/scripts/debate_chamber.py`
- **Helper**: `v2/shared-data/scripts/_debate_cases.py` — pure-Python Bull/Bear case templates (this is the secret to the low cost)
- **Models**:
  - Proposal: `claude-sonnet-4-6`
  - Bull/Bear: **deterministic Python templates** (no LLM call)
  - Judge: `claude-sonnet-4-6`

#### The flow

```
1. Proposal Agent (Sonnet)
   Input:  ~2,500 tokens (system prompt + market context + candidates)
   Output: Top 2–3 candidates with thesis paragraphs
   Cost:   ~$0.001

2. _debate_cases.build_case(strategy, candidate, regime)
   Bull case → markdown bullets (deterministic, e.g., for iron_condor:
       "Range-bound structure collects premium from both sides;
        underlying needs only to stay within wings")
   Bear case → markdown bullets (deterministic, max-loss callouts,
       regime risk, earnings proximity)
   Cost: $0.00 (no LLM call)

3. Judge Agent (Sonnet)
   Input:  ~2,000 tokens (system prompt + each candidate's bull+bear text)
   Output: 0–100 score per candidate + one-line rationale
   Cost:   ~$0.001

   Threshold: score >= 60 → approve
```

#### Why Python templates for Bull/Bear

Until 2026-05-03 these were two Haiku calls each — eliminated to save ~80 Haiku calls/day at full Alpha cadence. The Bull/Bear cases are mostly mechanical: same strategy + same regime → same rationale. The Judge benefits from LLM judgment because it has to weigh competing arguments. The Bull/Bear cases benefit from determinism because they're audit trails.

The templates live in `_debate_cases._STRATEGY_BULL` and `_STRATEGY_BEAR` dicts plus per-regime modifiers. The template output is the same markdown-bullet format the Judge used to receive from the LLMs, so the Judge's prompt didn't have to change.

#### Cost discipline

- **Per debate cycle**: ~$0.002–0.003 raw token cost
- **Per trading day** (assuming 6 cycles produce a candidate set): ~$0.012–0.018
- **Per month**: ~$0.30 (often the operator sees ~$0.10/cycle on the dashboard — that's amortized, including ClawRoute infra overhead)

Compared to a single bad trade (typical max loss $500–$1000 on Alpha), the cost discipline is irrelevant in dollar terms but immensely valuable in *audit* terms: every trade has a written record of why it was approved.

#### Constitution: in prompt AND in code

The Judge's system prompt enumerates the constitutional gates ("never approve trades within 14 days of earnings," "never approve when VIX ≥ 35," etc.). But these are also enforced before the order is placed:

- Earnings blackout — checked in `scan_options.py` (eligibility) AND in `autonomous_execution.py` (final guard)
- VIX gate — checked in `run_pipeline.py` (regime) AND in `risk_engine` (final guard)

Even if a future LLM model decided to be "creative" and approve something inside an earnings blackout, the Python gate would refuse to place the order. This is the single most important pattern in the codebase: **rules in code, not in prompts**.

#### What the LLMs add over pure code

If everything were rule-based, the system would be deterministic but blind to:

- Macroeconomic context ("the Fed is meeting Wednesday — narrow your strikes")
- Earnings proximity for indirect tickers ("AAPL earnings shortens IV crush window for QQQ")
- News synthesis ("XLE just took a 4% drop on commodity headlines — the bull put spread is still technically valid but the regime story has changed")
- Cross-strategy ranking ("the iron condor scored 65 but the diagonal scored 72 — pick diagonal")

The LLMs do the synthesis; the code does the gating.

### Good

- Two Sonnets give Alpha the ability to synthesize narratives
- Two Python templates eliminate the cost without losing the audit trail
- Constitutional rules belt-and-suspenders enforcement
- Per-trade rationale is human-readable and stored in the journal `decision` field

### Bad

- No prompt caching — every cycle re-processes the system prompt (~800 tokens worth)
- Bull/Bear templates are strategy-only, not regime-aware (they include regime as text, but the rationale doesn't shift)
- Judge can be slightly anchored to the proposal (single Sonnet writes both)

### Could be better

- Anthropic prompt caching on the system prompts → ~80% savings on repeated calls
- Regime-specific Bull/Bear modifiers (e.g., HIGH_VOL changes the bull case for credit spreads)
- A blind judge — a separate model (or different temperature/seed) writes the proposal vs the judge

---

## §7 — Journal — sacred source of truth

### ELI10

The journal is the diary. Every trade is one line. Once a line is written, it's never erased — only updated. Everyone reads it; only two people are allowed to write it (the agent that opens trades, and the position monitor that closes them). The journal is the truth. The broker is reality, but the journal is what we *believe* about reality.

### How it actually works

- **Path**: `/root/quantai-v2/shared-data/journal/paper/trades.jsonl`
- **Format**: JSONL. One JSON object per line. Newline-terminated. Append-only at the file level (the rewrite-once-then-rename pattern is used for in-place updates of existing entries).
- **Single-writer rule per stage**:
  - **Open**: only `autonomous_execution.py`, `beta_agent.py`, `gamma_agent.py` insert new lines
  - **Close**: only `position_monitor.py` updates existing lines (status: OPEN → CLOSED, fields: exit_pnl, close_reason, etc.)
  - **Annotate**: `agent_self_diagnosis.py` and `trade_reviewer.py` add post-close fields (`capability_diagnosis`, `post_trade`)
  - **Reconcile**: manual scripts like `close_intc_ghosts.py` for ghost recovery (rare; emits a `.bak.YYYY-MM-DD-HHMMSS-pre-{reason}` backup before any rewrite)
  - Everyone else is read-only — including all dashboard collectors, sheets_sync, weekly_synthesis, error_learner, and the workspace agents.

#### Trade-ID prefixes

| Prefix | Agent | Counter source |
|---|---|---|
| `A###` | Alpha | sequential per cron tick that successfully writes |
| `B###` | Beta | sequential |
| `G###` | Gamma | **max-based** (max existing G-id + 1; gap-bug-proof) |

Why max-based for Gamma: an early bug in Gamma counted only "today's" entries and could collide with yesterday's IDs after a journal restore. Max-based is monotonic.

#### Sample entry shape (representative)

```json
{
  "trade_id": "B042",
  "id": "B042",
  "source": "agent_beta",
  "agent": "agent_beta",
  "strategy": "broken_wing_butterfly",
  "symbol": "XSP",
  "status": "OPEN",
  "entry_timestamp": "2026-05-04T13:48:21.501821-04:00",
  "entry_price": 0.85,
  "estimated_credit": 0.85,
  "qty": 1,
  "max_loss": 215.00,
  "max_gain": 85.00,
  "legs": [
    {"symbol": "XSP260520P00610000", "action": "buy",  "side": "buy",  "ratio_qty": 1, "strike": 610},
    {"symbol": "XSP260520P00605000", "action": "sell", "side": "sell", "ratio_qty": 2, "strike": 605},
    {"symbol": "XSP260520P00595000", "action": "buy",  "side": "buy",  "ratio_qty": 1, "strike": 595}
  ],
  "regime_at_entry": "HIGH_VOL",
  "regime_data": { "vix": 22.1, "iv_rank": 68.4, "adx": 19.2 },
  "exit_rules": {
    "take_profit_pct": 50,
    "stop_loss_x_credit": 2,
    "time_dte": 1,
    "valley_danger_threshold": 0.45,
    "weekend_close": false
  },
  "simulated_slippage": 0.10,
  "net_delta": -3.2,
  "net_vega": -8.5,
  "decision": {
    "model": "deterministic",
    "regime": "HIGH_VOL",
    "rationale": "broken_wing_butterfly chosen by HIGH_VOL → BWB priority chain",
    "candidate_score": null
  }
}
```

#### Beta-only fields

Fields that only Beta entries will have:

- `regime_at_entry` — the 12-regime classifier output
- `regime_data` — the numeric inputs to that classification
- `exit_rules` — per-trade exit logic; `position_monitor` reads this and applies the named rules
- `simulated_slippage` — flat fee-per-leg estimate (today: $0.10 × num_legs); kept as a record of Beta's pre-execution P&L assumption
- `net_delta`, `net_vega` — portfolio-Greeks sanity (Beta tracks these as part of its risk gates)

#### Alpha-only fields

- `decision.bull_case` and `decision.bear_case` — the markdown bullets the Judge saw
- `decision.judge_score` (0–100)
- `decision.judge_rationale` — the Judge's one-liner

#### Post-close annotations

After a trade closes, `position_monitor.py` writes:

- `status: "CLOSED"`
- `close_timestamp`, `exit_timestamp`
- `exit_price`, `exit_pnl`, `pnl`
- `close_reason` — one of: `take_profit`, `stop_loss`, `time_dte`, `weekend_close`, `valley_danger`, `expiry`, `hard_close_330`, `manual`, `phantom_never_filled`, `ghost_unwound_YYYY-MM-DD`

Then `agent_self_diagnosis.py` (Haiku) appends:

- `capability_diagnosis` — `{ gaps_identified[], no_gaps_note, priority, dimension, evidence }`

Then `trade_reviewer.py` (Haiku) appends:

- `post_trade` — `{ thesis_outcome, regime_assessment, greeks_notes, timing, lessons, parameter_suggestions }`

These two are inline (not async) — `position_monitor` calls them with 20-second timeouts after the close.

#### `PHANTOM_NEVER_FILLED` status (added 2026-05-05)

Status added after the A021/A022 entry-phantom incident. When an entry order is rejected by the broker but the journal accidentally records it as OPEN, manual reconciliation rewrites those entries to `PHANTOM_NEVER_FILLED` with `pnl=0.00`. This status is *not* CLOSED (because there was no exit) and *not* OPEN (because there's nothing on the broker). The dashboard's open-trades collector filters `status == "OPEN"`, so phantoms drop off automatically.

#### Why JSONL not a database

Trade-offs considered:

| Property | JSONL | SQLite | Postgres |
|---|---|---|---|
| Atomic append | POSIX write | INSERT | INSERT |
| grep-friendly | yes | no | no |
| `git diff` friendly | yes | no | no |
| Schema migration | manual (just add field) | ALTER TABLE | ALTER TABLE |
| Multi-writer safe | careful (single-writer rule) | yes | yes |
| Query language | jq / Python | SQL | SQL |
| Backup | `cp file file.bak` | dump | pg_dump |
| Crash recovery | last line may be partial; just truncate | WAL/journal | WAL |

For a single-operator system with one writer per stage and ~5–10 trades per day, JSONL is the right call. The most-asked questions ("show me last week's Alpha trades", "what was the average PnL for B-series this month") are five lines of jq or three lines of Python.

### Good

- Atomic, append-only, version-control-friendly, no schema migration cost
- Single-writer rule keeps the contract simple
- `.bak.YYYY-MM-DD-HHMMSS-pre-{reason}` backups before any rewrite
- The journal *is* the audit trail — no separate logging layer

### Bad

- The "rewrite-once-then-rename" pattern for in-place updates means the entire file is read into memory on every update. At 10k+ entries this could matter; we're at ~50.
- Manual journal corrections (like today's A021/A022 phantom rewrite) are scary because they touch the source of truth
- No automated schema validation — a typo in field name slips through

### Could be better

- A tiny schema validator that runs on every journal write (Pydantic or jsonschema)
- Read-only file-system mount of the journal for non-writer scripts (defense-in-depth on the single-writer rule)
- Periodic jsonl-to-parquet snapshot for fast historical analytics

---

## §8 — Monitoring

### ELI10

QuantAI's immune system. Five independent watchers, none of which trust the others' state. When any of them sees something wrong, they alert. The watchers run on different schedules so a single failure doesn't blind everyone.

### §8.1 — Heartbeat monitor

- **File**: `v2/shared-data/scripts/heartbeat_monitor.py`
- **Cron**: `*/2 * * * *` (every 2 min, all hours, all days)
- **Reads**: `/tmp/quantai-heartbeats/pipeline.beat` (and similar files for other agents)
- **Logic**:
  - During market hours (9:30 AM–4:00 PM ET, Mon–Fri): if pipeline.beat is older than 20 minutes, alert Discord
  - Outside market hours: no alerts (the pipeline isn't supposed to be running)
  - 30-minute Discord cooldown — `alert_cooldown.json` tracks last-alert timestamp per signature
- **Also probes**: IBKR port 4002 (`socket.connect_ex` — read-only, no credentials), writes status to `/var/dashboard/state/quantai-heartbeats.json`
- **What it catches**: pipeline crashed mid-cycle, cron skipped, ibgateway down, network partition

### §8.2 — Position monitor

- **File**: `v2/shared-data/scripts/position_monitor.py`
- **Cron**: `*/2 13-20 * * 1-5` (every 2 min during market hours, weekdays)
- **What it does**: read open journal entries, fetch broker positions, apply exit rules, close on trigger.

#### Exit rule logic

For Alpha (no `exit_rules` field in journal):

1. **3:30 PM hard close** — any Alpha leg with same-day expiration closes at 3:30 PM ET unconditionally
2. **Expiry today/tomorrow** — close if 0-DTE or 1-DTE
3. **P&L ≤ -2× credit** — stop loss
4. **P&L ≥ 50% of credit** — take profit
5. Otherwise → hold

For Beta and Gamma (per-trade `exit_rules` field):

- `take_profit_pct` — close at this fraction of `max_gain`
- `stop_loss_x_credit` — close when loss reaches N × entry credit
- `time_dte` — close when DTE drops to this value
- `valley_danger_threshold` — Beta-specific; close if intra-spread Greek goes below threshold
- `weekend_close` — Friday EOD close

#### Ghost-position detection

`reconcile_ghost_positions(broker_positions, open_trades, all_trades)` (line 878) catches three failure modes:

| Mode | Pattern | Action |
|---|---|---|
| **True ghost** | Broker has position; no journal reference | Discord alert (60-min cooldown per symbol) |
| **Journal lie** | Journal CLOSED; broker still has legs | Discord alert |
| **Entry phantom** | Journal OPEN; broker has none of the legs | Discord alert |

This is what fired during the 2026-05-05 INTC mismatch investigation that triggered the cron halt.

#### `verify_legs_flat()` before CLOSED

After a `broker.close_position()` reports success, `position_monitor` calls `broker.verify_legs_flat(legs)` to confirm broker-side zero-quantity. Only then does it mark the journal `CLOSED`. This caught the A018 ghost-position incident where the close had reported "Cancelled" status but the journal had been updated to CLOSED anyway (pre-Phase-5 bug, fixed 2026-05-04).

#### Self-learning hooks

After a successful close, `position_monitor` calls (inline, with 20s timeouts):

- `agent_self_diagnosis.py` — writes `capability_diagnosis`
- `trade_reviewer.py` — writes `post_trade` and a markdown review file

If either times out, the close is still recorded; the annotation just doesn't happen. Logged at WARNING.

#### What it catches

- Stop-loss triggers
- Take-profit triggers
- Time-based exits (DTE, hard close)
- Ghost positions (real-time during market hours)
- Stale broker connections (errors propagate from broker.get_positions)

### §8.3 — System monitor

- **File**: `v2/shared-data/scripts/system_monitor.py`
- **Cron**: `*/5 * * * *` (every 5 min)
- **Probes** (port-only, no credentials):
  - LiteLLM `:4000`
  - ClawRoute `:18790`
  - IBKR `:4002`
- Writes consecutive-failure counts; thresholds for warning/error
- Does NOT alert directly; results feed `system-health-report.json` (the 13-check report) which Sentinel reads

### §8.4 — System test (43-point pre-market)

- **File**: `v2/shared-data/scripts/system_test.py`
- **Cron**: `30 13 * * 1-5` (9:30 AM ET pre-market, weekdays) via `pre_trade_check.py`
- **Checks** (43 total, includes):
  - Broker connection (IBKR and Alpaca paths)
  - LLM ingress (ClawRoute)
  - Journal writability
  - Cache freshness (market_intelligence, event_moves, scan_results)
  - Path existence (all critical scripts, all logs, all state files)
  - Cron file integrity (no parse errors)
  - Discord webhook reachability
  - Disk free
  - Memory available
  - VIX threshold sanity
- **Output**: `cache/pre_trade_check.json` — `{ go: true|false, checks_passed: 43, checks_failed: 0 }`
- **Discord**: posts `19/19 GO` (the most-watched-by-Alpha 19 critical checks) on success, or list of failures on red

### §8.5 — Error detector (catalog match)

- **File**: `v2/shared-data/scripts/legacy/error_detector.py` (note: legacy/ subdirectory; the path is historical)
- **Cron**: `*/5 * * * *` (every 5 min)
- **Reads** (last 500 lines per file):
  - `/root/quantai-v2/shared-data/logs/pipeline.log`
  - `/root/quantai-v2/shared-data/logs/heartbeat.log`
  - `/root/quantai-v2/shared-data/logs/position_monitor.log`
  - (skips `error_detector.log` itself to avoid feedback loop)
- **Catalog**: `docs/error-catalog.json` (62 entries at the time of writing)

#### Match algorithm

```python
for entry in catalog["errors"]:
    if entry["is_regex"]:
        if re.search(entry["pattern"], line, re.IGNORECASE):
            match!
    else:
        if entry["pattern"].lower() in line.lower():
            match!
```

A single line can match multiple catalog entries (multiple alerts emitted for one log line if they overlap).

#### Auto-action dispatch

- `retry` — sleep 60s, run `entry.retry_command`, post Discord
- `restart_service` — `sudo -n systemctl restart <entry.restart_target>`, post Discord
- `skip` — log locally only (intentional noise)
- `none` — post runbook link to `#alerts`; no automated action

#### Dedup

60-minute window per (signature_hash, source). Prevents the same error from posting 12 times in an hour.

#### Unknown lines

If a line contains ERROR_TOKENS (configurable: `ERROR`, `CRITICAL`, `Traceback`, etc.) but matches no catalog entry, it gets a signature_hash and is posted to `#karna-alerts` as "novel error".

### §8.6 — Error learner

- **File**: `v2/shared-data/scripts/error_learner.py`
- **Cron**: `0 22 * * 5` (Fri 22:00 UTC = 6:00 PM ET)
- **Reads**: `/var/dashboard/errors.db` (populated every 2 min by `collect_errors.py`)
- **Lookback**: 7 days
- **Logic**:
  - Group events by `signature_hash`
  - Patterns ≥ 3 occurrences AND not already in catalog → append as `category=recurring` with stub runbook
  - Patterns ≥ 2 occurrences (`MIN_NOVEL_THRESHOLD`) → append as `category=novel` (no runbook yet)
  - Patterns already in catalog → refresh `last_seen`, bump `occurrence_count`
- **Auto-classification**: pure Python signature matching, no LLM
- **Atomic writes**: `.tmp` + `os.replace()` + `.bak` backup before each catalog update
- **Discord**: weekly summary post via `_discord.post_to_channel()`

This is the rare cron that *modifies* the codebase (`docs/error-catalog.json`). Sentinel's NEVER lists exclude it from auto-modifications, but the learner itself is in the trusted zone for catalog writes.

### §8.7 — `errors.db` SQLite

- **Path**: `/var/dashboard/errors.db` (3 MB at the time of writing)
- **Populated by**: `collect_errors.py` (every 2 min)
- **Sources ingested**:
  - `journalctl` (systemd)
  - `docker logs` (the legacy trader-* containers)
  - `syslog`
  - `pyapp_inbox` events (anything written to `/var/log/quantai/pyapp_inbox/`)
  - Text logs (pipeline, heartbeat, position_monitor)

#### Schema

```sql
CREATE TABLE events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  source TEXT NOT NULL,
  severity TEXT NOT NULL,
  message TEXT NOT NULL,
  signature TEXT NOT NULL,
  signature_hash TEXT NOT NULL,
  count INTEGER NOT NULL DEFAULT 1,
  catalog_id TEXT,
  runbook TEXT,
  resolved_at TEXT,
  resolved_by TEXT
);
```

`error_learner` reads from this; `Sentinel` reads from this; the dashboard's Errors tab reads from this. Operator can mark a row resolved with `resolved_at` and `resolved_by` via the dashboard UI or a small CLI.

### §8.8 — Runbooks

`docs/runbooks/` — 12 markdown files:

- `runbook-alpaca-connection.md`
- `runbook-auto-heal.md` (legacy reference; Sentinel replaced auto_heal but this doc still describes the underlying flow)
- `runbook-centralized-logger.md`
- `runbook-chain-404.md`
- `runbook-close-order-failed.md`
- `runbook-debit-credit-rejection.md`
- `runbook-ibkr-connection.md`
- `runbook-ibkr-nightly-restart.md`
- `runbook-market-intelligence-fail.md`
- `runbook-mleg-order-fail.md`
- `runbook-pipeline-silent-fail.md`
- `runbook-scan-timeout.md`

Each catalog entry has a `runbook` field pointing to one of these. When `error_detector` posts to Discord, the message includes the runbook path. The operator can also `cat` the runbook from a phone via Discord file commands.

### Good

- Five independent monitors, no single point of failure in detection
- The 60-min dedup keeps Discord usable
- The 13-check pre-market test catches infrastructure problems before any trade
- `verify_legs_flat()` and `reconcile_ghost_positions()` together close the entire ghost-position class of failures
- Auto-actions are *named* and *finite* (no LLM picks the action; only catalog tags do)

### Bad

- The pipeline-silent-stale alert fires repeatedly during the current cron halt — no halt-aware mode (§18). The 60-min dedup helps but doesn't fully suppress.
- `error_detector` lives under `legacy/` — naming is confusing; it's the active script
- 60-min dedup window is per signature, not per cluster — three near-identical errors with one-character differences = three alerts
- 7-day lookback for the learner means anything that surged on day 8 is missed in this week's roll-up

### Could be better

- Halt-aware mode (a `/tmp/quantai-heartbeats/halt_mode.json` flag that monitors check before alerting)
- Cluster-based dedup using edit-distance (or even just "first 80 chars hash")
- Replace `legacy/` with a clean module path
- Live "incident" page on the dashboard that consolidates all open errors with severity into one timeline

---

## §9 — Self-learning loop

### ELI10

Every time a trade closes, two short Claude reviews happen automatically. One says "what *capability* would have helped here?" (e.g., "needs better earnings detection"). The other says "what about *this specific trade* — was the thesis right? was the timing right? what would I do differently?" Both write notes. Once a week, a longer Claude reads all the notes and writes a synthesis report.

### How it actually works

The flow:

```
Trade closes
   ↓
position_monitor.py inline calls (20s timeouts each):
   ├─ agent_self_diagnosis.py (Haiku)
   │     └─ writes journal `capability_diagnosis` field
   │     └─ writes capability_requests/{agent}/{trade_id}.json
   │
   └─ trade_reviewer.py (Haiku)
         └─ writes journal `post_trade` field
         └─ writes trade_reviews/{agent}/{trade_id}.md
   ↓
collect_learning.py (cron */5)
   ↓ groups by (week, agent, dimension) and (week, agent, parameter)
   ↓ checks learning_tracker.json for resolved items
   ↓
/var/dashboard/state/quantai-learning.json
   ↓
Dashboard "Self-Learning" tab
   ↑
Operator marks item resolved via:
   resolve_item.py --id <id> --note "..."
   ↓
learning_tracker.json (fcntl-locked)

Friday 4:45 PM ET:
weekly_synthesis.py (Sonnet)
   ↓ aggregates capability_requests + trade_reviews from past week
   ↓ produces weekly_reports/YYYY-MM-DD_synthesis.md
   ↓ posts ~1900-char per-agent summaries to Discord #alerts
```

### §9.1 — `agent_self_diagnosis.py`

- **Trigger**: inline by `position_monitor.py` after every close
- **Model**: `claude-haiku-4-5-20251001`
- **Timeout**: 20 seconds
- **Inputs**: closed trade record, original decision rationale, market context at close
- **Output schema**:
  ```json
  {
    "gaps_identified": [
      { "dimension": "scanning", "description": "...", "evidence": "...", "priority": "high|medium|low" }
    ],
    "no_gaps_note": "<string>",
    "primary_dimension": "<string>"
  }
  ```
- **Persisted to**:
  - Journal entry `capability_diagnosis` field
  - `/root/quantai-v2/shared-data/capability_requests/{agent}/{trade_id}.json`
- **Manual invocation**: `python3 agent_self_diagnosis.py --trade-id A015 [--dry-run]`

### §9.2 — `trade_reviewer.py`

- **Trigger**: inline by `position_monitor.py` after `agent_self_diagnosis`
- **Model**: `claude-haiku-4-5-20251001`
- **Timeout**: 20 seconds
- **Max tokens**: 2000 (bumped from 1200 on 2026-05-03 — short reviews lost regime nuance)
- **Output schema**:
  ```json
  {
    "thesis_outcome": "validated|invalidated|partially",
    "regime_assessment": "...",
    "greeks_notes": "...",
    "timing": "good|acceptable|poor",
    "lessons": ["..."],
    "parameter_suggestions": [
      { "parameter": "credit_pct", "current_value": "X", "suggested_value": "Y", "rationale": "..." }
    ]
  }
  ```
- **Persisted to**:
  - Journal entry `post_trade` field
  - `/root/quantai-v2/shared-data/trade_reviews/{agent}/{trade_id}.md` (full markdown review)

### §9.3 — `weekly_synthesis.py`

- **Schedule**: `45 20 * * 5` (Friday 16:45 ET)
- **Model**: `claude-sonnet-4-6`
- **Timeout**: 90 seconds
- **Inputs**: aggregated `capability_requests/` + `trade_reviews/` from the past 7 days
- **Output**:
  - Markdown: `/root/quantai-v2/shared-data/weekly_reports/{YYYY-MM-DD}_synthesis.md`
  - Discord posts: per-agent summary, ~1900 chars each, to `DISCORD_CHANNEL_ALERTS`
- **Run-lock**: `/tmp/weekly_synthesis.lock` (prevents double-posting if cron fires twice)
- **Synthesis dimensions** (per the prompt):
  - "What pattern repeated this week"
  - "What single change would have improved 2+ trades"
  - "What new signal should we track"
  - "What rule should be tightened or loosened"
  - "What's the operator action item this week"

### §9.4 — `collect_learning.py`

- **Cron**: `*/5 * * * *`
- **Reads**: `capability_requests/`, `trade_reviews/`, `learning_tracker.json`, latest `weekly_reports/*.md`
- **Groups**:
  - Capability gaps by (week, agent, dimension)
  - Parameter suggestions by (week, agent, parameter)
- **Filters**: items in `learning_tracker.json["resolved"]` are moved from `open_items` to `resolved_items`
- **Writes**: `/var/dashboard/state/quantai-learning.json`

#### `quantai-learning.json` schema (top-level)

```json
{
  "last_updated": "ISO",
  "status": "ok|idle|warning",
  "data": {
    "open_items": [
      {
        "id": "stable hash",
        "date": "ISO",
        "agent": "alpha|beta|gamma",
        "type": "capability_request|parameter_suggestion",
        "dimension": "scanning|debate|risk|...",
        "title": "...",
        "summary": "...",
        "frequency": <int>,
        "estimated_impact": "high|medium|low",
        "priority": "high|medium|low",
        "source_trades": ["A012", "A015"],
        "status": "open"
      }
    ],
    "resolved_items": [
      { "id": "...", "agent": "...", "type": "...", "title": "...",
        "resolved_date": "ISO", "resolution_note": "..." }
    ],
    "stats": {
      "total_open": <int>,
      "total_resolved": <int>,
      "by_dimension": { "scanning": <int>, ... },
      "by_agent": { "alpha": <int>, ... }
    }
  }
}
```

### §9.5 — `resolve_item.py`

- **CLI**:
  ```
  python3 resolve_item.py --id "<item-id>" --note "<resolution note>"
  python3 resolve_item.py --id "<item-id>" --unresolve
  python3 resolve_item.py --list
  python3 resolve_item.py --list-resolved
  ```
- **Locking**: exclusive `fcntl.flock` on `learning_tracker.json.lock`
- **Effect**: writes/removes entry in `learning_tracker.json["resolved"]`
- **Dashboard reflection**: ≤5 min (next `collect_learning.py` cycle moves the item)

### §9.6 — Dashboard "Self-Learning" tab

Reads `quantai-learning.json` and renders:

- Open items by priority + dimension
- Resolved items with resolution notes
- Stats (totals, by-dimension, by-agent)
- Link to latest `weekly_reports/{date}_synthesis.md`

### Good

- Per-trade reviews are immediate (inline post-close) so context is fresh
- Three-tiered models (Haiku for per-trade, Sonnet for weekly) match cost to depth
- Operator can resolve items from a CLI without touching the dashboard
- Run-lock on weekly synthesis prevents double-posting
- Markdown reviews are stored separately so they're greppable / git-friendly

### Bad

- 20s timeouts on Haiku calls means slow LLM responses just drop the review (not retried)
- No automated escalation when the same `dimension` shows up 5+ weeks running
- Operator has to remember to mark items resolved (no auto-resolve based on subsequent trade outcomes)
- `learning_tracker.json` is a single file — at scale this becomes contention

### Could be better

- Auto-resolve items when the underlying gap stops appearing in 4 consecutive weekly syntheses
- Trend visualization on the dashboard ("scanning gaps over time")
- Direct PR-suggestion mode where the weekly synthesis proposes a code diff (Sentinel-routed for review)
- Per-tag SLA tracking ("medium-priority items should be resolved within 30 days")

---

## §10 — KARNA, OpenClaw, Discord, workspaces

### ELI10

KARNA is the 24/7 Claude voice that the operator talks to over Discord. OpenClaw is the engine that runs that voice. The four workspaces are KARNA's four "modes" — co-pilot, builder, journal-keeper, researcher — each with its own personality and its own Discord channel.

### §10.1 — OpenClaw runtime (the engine)

- **Service**: `/etc/systemd/system/openclaw.service`
- **Type**: `simple` — *do not change* (one previous accidental change cost 18 hours of debugging)
- **User**: `root`
- **ExecStart**: `/usr/bin/openclaw gateway`
- **EnvironmentFile**: `/etc/karna/secrets.env` (credentials live here, never in unit file or repo)
- **Dependencies**:
  - `After=network.target clawroute.service tailscaled.service`
  - `Wants=clawroute.service`
- **Restart**: `on-failure`, `RestartSec=10`

OpenClaw is a small Rust harness that hosts a Claude session. It mediates tools (file system, shell, Discord), manages conversation context, and exposes the Claude model behind a stable interface. KARNA = the running OpenClaw instance with the QuantAI Sonnet 4.6 configuration.

### §10.2 — KARNA = the 24/7 supervisor

Roles:

- **Co-pilot in `#chat`** — operator's first stop for "is the system OK," "show me last week's performance," "explain why A015 closed at -2× credit"
- **Tool runner** — KARNA can run `system_test.py`, query the journal, post a summary, etc. (path-allowlisted; no trading)
- **Drafting partner** — when the operator says "draft a fix for the close-order-failed runbook", KARNA writes the draft. Sentinel is the executor; KARNA is the writer.

KARNA does *not*:

- Trade. Even if asked, it refuses.
- Edit `.env`, openclaw.service, or the journal.
- Bypass the Sentinel approval flow. If a fix needs operator ✅, KARNA queues it.

### §10.3 — Discord channels

| Channel | Audience | Source |
|---|---|---|
| `#karna-command` | Operator → KARNA | KARNA listens for slash commands and natural-language asks |
| `#karna-approvals` | Sentinel → operator | Where ✅ approval cards land (48h TTL) |
| `#system-health` | All monitors | Heartbeat, position monitor, system_test status |
| `#alerts` | Trade execution + EOD + weekly synthesis | autonomous_execution, eod_summary, weekly_synthesis |
| `#chat` | workspace-orchestrator | Operator's daily co-pilot |
| `#infra` | workspace-infra | System health questions, fix authoring |
| `#journal` | workspace-journal | Trade-history queries, stats |
| `#research` | workspace-research | Daily research briefs (6:45 AM ET) |

All channels use `_discord.py` `post_to_channel()` (no webhooks — webhook URLs were decommissioned because they leak channel identity). Bot token: `DISCORD_BOT_TOKEN` env var (in `/etc/karna/secrets.env`).

### §10.4 — Approval flow (Sentinel ✅)

```
Sentinel apply slot fires
   ↓
For each propose_wait fix:
   ↓
   Post embed to #karna-approvals:
     • title (e.g. "Restart collect_clawroute — clawroute.json stale 18 min")
     • fix_id (sha1 short)
     • diff (truncated to 80 lines)
     • shell commands proposed
   ↓
   React with ✅
   ↓
Operator reads on phone, taps ✅
   ↓
Next apply slot:
   ↓
   Poll Discord reactions on cards <48h old
   ↓
   For ✅'d cards:
     • Run validate_proposal() Python gate (NEVER lists)
     • Apply diff (.bak first)
     • Run shell commands (timeout)
     • Record receipt → /var/dashboard/auto_heal_data/receipts/<fix_id>.json
   ↓
Tile updated → quantai-sentinel.json → dashboard
```

Cards expire after 48h. Cards that re-appear (because Sentinel re-proposes the same fix-id with a new id, or because the underlying issue persists) get a fresh card. There is no "snoozed for 24h" memory — see §18.

### §10.5 — Four workspaces

Each workspace is a directory containing:

- `AGENTS.md` — operating manual (what this agent is allowed to do, what it isn't)
- `SOUL.md` — personality (tone, defaults, style)

The agents are not separate processes — they're conversation contexts within KARNA's OpenClaw instance, switched by Discord channel. KARNA loads the right `AGENTS.md` + `SOUL.md` based on which channel the message came in on.

#### `workspace-orchestrator` (`#chat`)

- **Mandate**: trading co-pilot. Knows the agents (Alpha/Beta/Gamma), can run scans on command, reports market conditions, agent stats, position monitor status.
- **Soul**: sharp, direct, senior trading desk analyst. Lead with answer, then reasoning. Push back on emotional decisions. Don't ramble.

#### `workspace-infra` (`#infra`)

- **Mandate**: system's immune system. The hands that build, fix, maintain. Methodical, paranoid about breaking things.
- **Soul**: methodical, careful, thorough. Diagnose before act. Reliability is the product. When in doubt, don't change it.

#### `workspace-journal` (`#journal`)

- **Mandate**: read-side system of record for every trade. Lives in `#journal`. Reads `trades.jsonl`, never mutates it.
- **Soul**: precise, organized, reliable. Spot patterns humans miss. Never judge trades. Ask before assuming.

#### `workspace-research` (`#research`)

- **Mandate**: intelligence layer. Daily credit-spread briefs (6:45 AM ET). Regime briefs. Performance summaries. Never trades.
- **Soul**: data-first, opinion second. Concise for mobile consumption. Think like an options seller. Honest about uncertainty.

### §10.6 — Workspace sync

The workspaces live in two places:

- **Canonical**: `/home/trader/QuantAI/v2/workspace-{orchestrator,infra,journal,research}/{AGENTS,SOUL}.md`
- **Runtime**: `/root/quantai-v2/workspace-*/...` (the path that OpenClaw loads from)

Sync is manual: `bash /home/trader/QuantAI/scripts/sync_workspaces.sh` after any change to the canonical files. There is no git hook. This is a known wart (§18).

### Good

- KARNA gives the operator a single mental model: "the AI on Discord knows everything"
- Channel-keyed personality switching is simple and effective
- Workspaces in two files (`AGENTS.md` + `SOUL.md`) is enough structure without ceremony
- Discord ✅ approvals are a great mobile UX
- Path allowlists in code, not in prompts

### Bad

- `Type=simple` is a footgun (§18)
- Manual `sync_workspaces.sh` — easy to forget; no auto-detection of drift
- KARNA can't see the dashboard — it can describe what should be there but can't render it
- Discord rate limits become real if too many alerts fire in a window

### Could be better

- Auto-sync workspace edits via post-commit git hook
- Dashboard screenshots (or a small embed) into KARNA's context for visual debugging
- Per-channel rate-limit awareness so monitors don't all fire at the same minute
- Weekly KARNA "office hours" digest summarizing what was asked vs not (could surface gaps)

---

## §11 — The dashboard — command center

### ELI10

The dashboard is a single web page that shows everything: positions, agent stats, errors, the cron schedule. It's deliberately boring tech — no fancy framework, no build step. Collectors write JSON files; the page reads them.

### §11.1 — The three-layer stack

```
Layer 1: DATA
  10 collectors on cron (1–15 min cadence)
    ↓ write
  /var/dashboard/state/*.json   ← versioned snapshots, last_updated stamp

Layer 2: PRESENTATION
  /var/dashboard/index.html  (single hand-edited React SPA, ~98 KB, no build)
    React 18 + Recharts + Mermaid + Tailwind (all CDN)
    Babel transpiles JSX in browser
    fetch(state/*.json) every 30 sec via useEffect
    9 hardcoded tabs (Live, Agents, Trades, Performance, Self-Learning,
                      System, Workflows, Errors, History)

Layer 3: TRANSPORT
  python3 -m http.server 8080 --bind 127.0.0.1
    Auto-restart via dashboard-http.service (RestartSec=10s)
    Bound to localhost — Tailscale handles external exposure
  ↓
  Tailscale serve --bg --https=443 http://127.0.0.1:8080
    Re-asserted every minute by quantai-tailscale-serve.timer
  ↓
  https://quantai.tail1465ff.ts.net/   ← what you visit
```

### §11.2 — The 10 collectors

All write to `/var/dashboard/state/*.json` with the schema `{last_updated, status, data, [alerts]}`:

| Collector | State files | Source | Cron |
|---|---|---|---|
| `collect_system.py` | `system.json` | systemctl, docker, tailscale CLI | `* * * * *` |
| `collect_karna.py` | `karna-status.json`, `karna-cost.json`, `karna-background.json` | OpenClaw session DB, ClawRoute usage | `* * * * *` |
| `collect_quantai.py` | `quantai-positions.json`, `quantai-metrics.json`, `quantai-alerts.json`, `quantai-timeline.json`, `quantai-window-current.json`, `quantai-data-status.json` | `trades.jsonl` | `* * * * *` |
| `collect_alpaca.py` | `alpaca-account.json`, appends to `equity_history.jsonl` | `broker.get_broker()` (broker-aware: hits IBKR if `BROKER_TYPE=ibkr`) | `* * * * *` |
| `collect_history.py` | `quantai-history.json` | closed-trade aggregate | `*/5 * * * *` |
| `collect_cron.py` | `cron-status.json` | log file mtimes for ~21 jobs | `* * * * *` |
| `collect_beta.py` | `agent-beta-state.json` | beta journal + regime cache | `* * * * *` |
| `collect_gamma.py` | `agent-gamma-state.json` | gamma scan + journal | `* * * * *` |
| `collect_clawroute.py` | `clawroute.json` | ClawRoute SQLite DB | `*/15 * * * *` |
| `collect_trade_analytics.py` | `trade-analytics.json` | strategy/agent breakdowns | `*/5 * * * *` |

Plus `errors.db` (SQLite, separate path; `lib_errors.py` is the shared parser used by `collect_errors.py` and the dashboard's Errors tab).

### §11.3 — The 9 tabs

Hardcoded as an array in `index.html`:

1. **Live** — system health, KARNA status, position P&L, real-time alerts
2. **Agents** — Alpha / Beta / Gamma cards with stats, regime, decision history
3. **Trades** — open + recent fills
4. **Performance** — equity curve, win rate, daily/weekly summaries
5. **Self-Learning** — error learner feedback loop, capability requests, parameter suggestions
6. **System** — VPS resources, cron job health, pipeline status, Sentinel tile, ClawRoute costs
7. **Workflows** — Mermaid diagrams of every major flow
8. **Errors** — error timeline, severity filtering, learner queue
9. **History** — all-time trade ledger, cumulative equity

Adding a new agent (e.g., "Delta") means hand-editing `index.html` to add to the `tabs[]` array AND adding a new card section. There is no auto-generation. This is the structural-drift gotcha (§11.6).

### §11.4 — Dashboard editing pitfall

There used to be a `generate.py` that built `index.html` from a template every 30 seconds. It was retired on 2026-05-04 — the systemd unit `dashboard-generator.service` is masked, the source is `generate.py.retired.2026-05-04`.

So today, **editing `/var/dashboard/generate.py` does nothing**. The canonical source is `/home/trader/dashboard/index.html`; deploy with:

```bash
sudo cp /home/trader/dashboard/index.html /var/dashboard/index.html
```

### §11.5 — Authentication

Tailscale mesh identity. The HTTP server is bound to `127.0.0.1`; the only way in is via `quantai.tail1465ff.ts.net/` which only people on the operator's tailnet can reach. There is no app-level login. This is appropriate for a single-operator system; would not be appropriate for multi-tenant.

### §11.6 — Failure mode: structural drift

**The data layer auto-syncs. The structural layer drifts silently.**

When Gamma was added on 2026-04-29, the dashboard's `index.html` did not learn about Gamma. Six days later, on 2026-05-05, a human edited the HTML to add a Gamma card. During those six days, the dashboard was a lie — it showed Alpha and Beta as the only agents.

There is no detector for this. Sentinel is path-allowlisted away from `index.html` (it can write `state/*.json` only). No CI checks the agent list against the identity files. No git hook flags structural drift.

This is the worst thing about the dashboard. See §19 for the proposed fix.

### Good

- Three-layer separation is clean: data, presentation, transport
- No build step, no Node toolchain, no upgrade treadmill
- JSON state files are the API; trivially queryable from any script
- Tailscale auth is set-and-forget
- Collectors fail independently — one stale collector doesn't break the rest

### Bad

- Hand-edited HTML for structural changes (§11.6)
- No drift detector
- No app-level auth means a compromised tailnet device exposes the dashboard fully
- Babel-in-browser means slow first paint (~1–2 sec)
- 30-second polling means real-time visibility lags by up to 30 sec

### Could be better

- A drift detector that runs daily: compare `tabs[]` against `AGENT_*_IDENTITY.md` and the journal's distinct `source` values
- Sentinel read-access to `index.html` so it can *propose* drift fixes (still propose-wait — humans approve)
- Optional Tailscale-funnel access (no client install) for non-tailnet visitors
- WebSocket push for sub-second updates on the Live tab
- Lazy-load Recharts/Mermaid (cut first-paint by ~60%)

---

## §12 — Cron — the metronome

### ELI10

Cron is the heartbeat. Every entry is "at this time, run this script." There's no orchestrator, no queue, no state machine. Cron fires; the script runs to completion; cron fires again at its next time.

### How it actually works

The VPS root crontab is in **UTC**. Market hours 9 AM–4 PM ET = 13–20 UTC during DST (which is the current state). After DST ends (Nov 1, 2026), the crons need updating to 14–21 UTC.

Below is the full cron schedule grouped by purpose. Items marked **HALTED** are temporarily commented out due to the 2026-05-05 INTC-mismatch investigation (see §20).

#### Trading (currently HALTED)

```cron
# HALTED 2026-05-05: */15 13-20 * * 1-5  python3 .../run_pipeline.py        >> .../pipeline.log
# HALTED 2026-05-05: 5 20 * * 1-5         python3 .../run_pipeline.py eod    >> .../pipeline.log
# HALTED 2026-05-05: 30 13 * * 1-5        python3 .../pre_trade_check.py     >> .../pipeline.log
# HALTED 2026-05-05: */15 13-20 * * 1-5   python3 .../beta_agent.py          >> .../beta.log
# HALTED 2026-05-05: 0 6 * * 0            python3 .../beta/event_moves_seeder.py
# HALTED 2026-05-05: 30 20 * * 1-5        python3 .../gamma_agent.py --scan
# HALTED 2026-05-05: 33 13 * * 1-5        python3 .../gamma_agent.py --execute
# HALTED 2026-05-05: */2 13-20 * * 1-5    python3 .../position_monitor.py    >> .../position_monitor.log
```

When trading resumes (post-May-15 expiration of the INTC ghosts), the halt prefix is removed in stages per §20.

#### Monitoring (always running)

```cron
*/2 * * * *           python3 .../heartbeat_monitor.py                   >> .../heartbeat.log
*/5 * * * *           python3 .../legacy/error_detector.py
*/2 * * * *           python3 .../collect_errors.py                      >> /root/logs/collect_errors.log
0 22 * * 5            python3 .../error_learner.py                       >> .../error_learner.log
```

#### Sentinel (replaces auto_heal)

```cron
*/15 * * * *          python3 .../sentinel_agent.py --auto                >> .../sentinel.log
```

The `--auto` wrapper reads the ET clock and dispatches to: 8:30 AM apply, 10/11/12/1/2/3 PM observe, 4:15 PM apply, 9 PM observe (weekday); 10 AM observe (weekend). Non-slot ticks exit silently.

#### Dashboard collectors

```cron
* * * * *             python3 /var/dashboard/collect_system.py
* * * * *             python3 /var/dashboard/collect_karna.py
* * * * *             python3 /var/dashboard/collect_quantai.py
* * * * *             python3 /var/dashboard/collect_alpaca.py
*/5 * * * *           python3 /var/dashboard/collect_history.py
* * * * *             python3 /var/dashboard/collect_cron.py
* * * * *             python3 /var/dashboard/collect_beta.py
* * * * *             python3 /var/dashboard/collect_gamma.py
*/15 * * * *          python3 /var/dashboard/collect_clawroute.py
*/5 * * * *           python3 /var/dashboard/collect_trade_analytics.py
```

#### Self-learning

```cron
*/5 * * * *           python3 .../collect_learning.py
45 20 * * 5           python3 .../weekly_synthesis.py                    # Friday 4:45 PM ET
```

#### Other

```cron
*/5 * * * *           systemctl is-active ibgateway >/dev/null 2>&1 || systemctl start ibgateway
0 2 * * *             /root/scripts/karna-backup.sh                      >> /root/logs/backup.log
```

The `ibgateway` watchdog is a belt-and-suspenders restart. The daily `karna-backup.sh` at 2 AM UTC age-encrypts the state files and pushes to GitHub.

### DST gotcha

Cron is UTC. During DST (March–November), market hours 9 AM–4 PM ET = 13–20 UTC. After DST (November–March), market hours 9 AM–4 PM ET = 14–21 UTC. The cron entries hardcode `13-20` because we're currently in DST. They will need updating on the next DST transition (Nov 1, 2026).

### Good

- Cron is dumb but reliable — 50 years of POSIX-tested behavior
- Each entry is independently runnable for debugging (`python3 /path/to/script.py`)
- Halt prefix is a clean comment-out — easy to re-enable in stages
- Sentinel wrapper centralizes the apply/observe logic instead of multiple cron lines
- Watchdog for ibgateway means we recover from gateway crashes within 5 min

### Bad

- DST transitions are manual edits (Nov 1, 2026 is a known TODO)
- No cron syntax validation — a typo silently disables a job
- No alert when cron itself is dead (chicken-and-egg; the heartbeat monitor would catch the *symptom* but not "cron daemon stopped")
- Cron logs go to multiple files; debugging cross-job timing is awkward

### Could be better

- Switch to systemd timers (per-unit logging via journalctl, native dependency support, no UTC/local confusion)
- Pre-deploy cron syntax check in CI
- Daily cron-health check that verifies every job has run within its expected window

---

## §13 — LLM cost discipline / ClawRoute

### ELI10

Every Claude call goes through one mailbox (ClawRoute). The mailbox picks the cheapest model that can handle the job. It also writes down what every call cost. Total bill: about $4–$5 a month.

### How it actually works

- **Single ingress**: every LLM call from any QuantAI script routes through `localhost:18790` (ClawRoute), NOT direct Anthropic.
- **NOT LiteLLM**: `localhost:4000` is the legacy LiteLLM proxy. It still runs (Docker container `litellm`) but is no longer the QuantAI ingress. CLAUDE.md still mentions it; this is doc drift (§18).
- **Tier routing** (cheap-first):
  - **HEARTBEAT** — Gemini Flash-Lite (95% cheaper than Haiku) — used for trivial yes/no checks
  - **SIMPLE** — DeepSeek V4-Flash — used for status summaries
  - **MEDIUM** — Claude Haiku 4.5 — used for `agent_self_diagnosis`, `trade_reviewer`, `error_learner` summaries
  - **COMPLEX** — Claude Sonnet 4.6 — used for debate `Proposal` + `Judge`, weekly synthesis, Sentinel apply
  - **FRONTIER** — Claude Opus 4.x — deferred (no current use case justifies the cost)

### `_llm_client.py` — the shim

- **File**: `v2/shared-data/scripts/_llm_client.py`
- **Default routing**: `http://127.0.0.1:18790/v1/chat/completions` (line 42)
- **Two interfaces**:
  - SDK-shaped: `Client()` with `messages.create()` (Anthropic-SDK compatible)
  - Functional: `chat(model, messages, ...)` (lighter wrapper)
- **Two ClawRoute quirks documented inline**:
  - No `Authorization` header (ClawRoute sets it server-side from its own keychain)
  - Raw byte reads for gzip responses (httpx auto-decompression breaks ClawRoute's streaming)
- **No prompt caching** in the shim today — every call is fresh

### Escape valve

Setting `LLM_BYPASS_CLAWROUTE=1` reverts the script to direct Anthropic-SDK calls (using `ANTHROPIC_API_KEY` from the environment). Used for:

- Local development without ClawRoute running
- Emergency fallback if ClawRoute itself is broken

### Cost tracking

- **Collector**: `collect_clawroute.py` runs every 15 min
- **Reads**: ClawRoute's SQLite DB
- **Writes**: `/var/dashboard/state/clawroute.json` (today's spend, 7-day rolling, per-tier breakdown, per-script attribution)
- **Dashboard**: ClawRoute card on the System tab shows daily and 7-day totals against the budget
- **Spend-spike alert**: if today's spend exceeds 2× the 7-day average, post to `#alerts`

### Budget

- **Target**: ~$4–$5/month total
- **Where it goes**:
  - Sentinel apply (twice/weekday × Sonnet, ~2k input tokens/run) → ~$0.50/month
  - Weekly synthesis (1×/week × Sonnet, ~10k input tokens) → ~$0.50/month
  - Per-trade Haiku reviews (×2 per close, ~2k input tokens each) → variable, ~$1–$2/month at 5–10 trades/day
  - Debate chamber (Proposal + Judge Sonnet, ~5k tokens/cycle, 6 cycles/day) → ~$1/month
  - Misc (Sentinel observe Haiku, error_learner Discord post) → ~$0.50/month

### Good

- One ingress = one cost surface
- Tier routing means we don't pay Sonnet rates for Haiku-class tasks
- Audit trail (every call logged with model, tokens, cost, script attribution)
- Escape valve means we're never locked in
- Budget is small enough to not be a recurring decision

### Bad

- ClawRoute is a SPOF for LLM traffic — if the proxy dies, every script that doesn't set the bypass flag breaks. (Mitigation: monitor probes the port every 5 min.)
- No prompt caching in the shim (would save another ~80% on repeated system prompts)
- The two ClawRoute quirks (no auth header, gzip raw reads) are tribal knowledge — documented in code comments but easy to miss if porting
- LiteLLM still running creates confusion (§18)

### Could be better

- Anthropic prompt caching enabled in the shim — instant 80% reduction on repeat-prompt calls
- Per-tier daily budget with a kill switch (e.g., "if FRONTIER spend > $1 today, refuse new Frontier requests")
- ClawRoute health probe with auto-bypass when proxy is down (today: probe alerts; doesn't auto-bypass)
- Decommission LiteLLM unless we're keeping it for a reason

---

## §14 — Graphify

### ELI10

Graphify is a tool that turns the codebase into a queryable map. Like Google Maps for source code: "what calls this function," "what's the shortest path from this file to the broker," etc.

### How it actually works

- **Install**: `pipx install graphify` (operator-installed; not part of the QuantAI repo)
- **Output directory**: `/home/trader/QuantAI/graphify-out/`
  - `graph.json` — node + edge data
  - `graph.html` — interactive viewer
  - `GRAPH_REPORT.md` — auto-generated overview (god nodes, communities)
  - `wiki/index.md` — if generated, auto-curated overview
- **Current size**: 2800 nodes, 4752 edges, 249 communities

### Two update modes

- **AST-only rebuild** (free, every commit) via post-commit hook:
  - `graphify update .`
  - Re-extracts code structure (function defs, calls, imports)
  - No LLM cost
  - Fast (seconds)
- **Full LLM-driven rebuild** (monthly) via cron:
  - First Monday of the month, 6 AM UTC
  - Runs `monthly_graph_refresh.py` which calls `claude -p "/graphify --update"` headless
  - 30-min budget on Sonnet
  - Picks up doc semantic edges (markdown → code references)
  - On failure → Discord `#alerts` + dashboard error tile

### Why graphify is useful

- **Architecture queries**: "what calls `place_mleg_order`?" — graphify returns the 5 callers across all agents
- **Cross-module impact**: "if I change `_broker_ibkr.py`, which scripts feel it?" — graphify gives the BFS subtree
- **Reading docs**: when a question spans multiple files (e.g., "how does the journal interact with the position monitor?"), `graphify query` traverses extracted+inferred edges instead of grepping
- **Token reduction**: ADR-001 measured a 44.8× token reduction per query when Claude uses graphify vs raw-grep

### MCP server

Graphify exposes an MCP server that Claude can call directly:

- `graphify_query <natural-language>` — semantic search across the graph
- `graphify_path <a> <b>` — shortest path between two nodes
- `graphify_explain <concept>` — community-bucketed explanation

### Good

- Free incremental rebuilds keep the graph current without recurring LLM cost
- Token reduction means more architecture context fits in a Claude session
- The HTML viewer is independently useful for new contributors
- Monthly LLM rebuild catches doc-driven semantic edges

### Bad

- Manual `graphify update .` after doc edits (only AST is auto)
- The `graph.json` file is 1.1 MB — version-controlling it adds noise to PRs
- No drift alert if `graph.json` is older than N days
- Monthly LLM rebuild can fail silently (mitigated by Discord alert, but still)

### Could be better

- Auto LLM rebuild on doc commits (not just monthly)
- Smaller `graph.json` (e.g., split into per-community shards)
- Drift indicator on the dashboard

---

## §15 — Architecture decision records (ADRs)

The full text lives in `docs/00-ARCHITECTURE-DECISIONS.md`. Below: one paragraph each + an interview-ready talking point.

### ADR-001 — Adopt Graphify (2026-04-25)

**Decision**: install graphify (pipx); build graph at `graphify-out/graph.json`; run as MCP server; install commit hook for AST rebuilds.

**Why**: an 80-file codebase imposes a re-read tax on every Claude session. A queryable graph cuts that tax.

**Consequences**: (+) 44.8× token reduction per query; (+) free incremental rebuilds; (–) manual LLM rebuild after doc changes; (–) graph.json version-control noise.

**Interview talking point**: "We measured the token cost of Claude reading 30 files for an architecture question vs. graphify-querying — 44.8× reduction. That's not a marginal optimization; it changes what's feasible in one session."

### ADR-002 — ClawRoute as single LLM ingress (2026-04-25)

**Decision**: route every LLM call through `localhost:18790` via the `_llm_client.py` shim. Provide an escape valve.

**Why**: 15 LLM call sites bypassed any cost control; routing log was empty for 2.5 weeks; needed a single audit surface and a tier-routing pivot point.

**Consequences**: (+) single cost surface; (+) ~95% savings on small-task calls via cheaper-tier routing; (–) ClawRoute becomes a SPOF (mitigated by bypass flag and port probe); (–) two shim-side quirks are tribal knowledge.

**Interview talking point**: "Before this, we couldn't tell what was costing money. After it, every call is in one DB. That's the prerequisite for *any* cost-discipline conversation."

### ADR-003 — (absent)

The repo skips from 002 to 004 — no ADR-003 exists. (A retired draft on a tooling choice that never shipped.)

### ADR-004 — Migrate from Alpaca to IBKR (2026-04-26)

**Decision**: IBKR via IB Gateway 10.37 (paper account `DUP851506`, port `4002`); ib_insync 0.9.86 client; `BrokerBase` adapter with `BROKER_TYPE` env var; `BROKER_TYPE=ibkr` is the default since 2026-04-27.

**Why**: Alpaca paper rejects index options (SPX/XSP/VIX) with HTTP 422; Beta strategy needs XSP for European exercise + cash settlement + Section 1256 tax treatment.

**Consequences**: (+) XSP/SPX/VIX chains accessible; (+) Section 1256 (60/40 tax); (+) European exercise (zero early-assignment risk); (+) cash settlement (no share delivery); (–) Gateway is a 373 MB persistent process with daily restart; (–) ib_insync is async/event-driven (the very reason Phase 5b safeguard exists); (–) credentials in `.env` (mitigated by IBC injection at runtime).

**Interview talking point**: "Alpaca returns 422 on index options. The strategy was theoretically valid but practically un-runnable. The migration unblocked the *actual* edge — Section 1256 plus European exercise — that the strategy depended on."

### ADR-005 — Agent Beta as separate pipeline (2026-04-27)

**Decision**: Beta runs on its own cron (`*/15 13-20 * * 1-5 beta_agent.py`), shares journal + broker adapter, has independent risk gates, uses zero LLM.

**Why**: Beta and Alpha have different risk profiles, different universes, and different decision logics. Coupling them into one pipeline would mean a Beta failure halts Alpha (and vice versa). Decoupling is cheaper than the coupling tax.

**Consequences**: (+) two agents coexist on one account, independent budgets; (+) Beta refuses to run on Alpaca (forces correct broker); (–) Finnhub free tier limits historical event seed (only ~3 weeks back); (–) IV Rank is a 21-day realized-vol proxy.

**Interview talking point**: "The shared journal + shared broker adapter let two agents share infrastructure without sharing fate. Beta's regime classifier could go off the rails tomorrow and Alpha would keep trading. That's the architecture: shared substrate, isolated logic."

---

## §16 — Safety culture: 12 hard rules in code

Every rule below is enforced in Python — not in prompts. If the LLMs go rogue, the rules still apply. This is the if-you-remember-nothing-else list.

### Rule 1 — Hard caps in code, not in prompts

Path allowlists (Sentinel `WRITE_ALLOWLIST_PREFIXES`), NEVER lists (`NEVER_MODIFY_PATHS`, `NEVER_TOUCH_PATHS`, `NEVER_RESTART_SERVICES_BLANKET`), 80-line diff caps. All checked in `is_command_safe()` and `validate_proposal()` before any execution.

**Where**: `sentinel_agent.py:130–135` (allowlist), `is_command_safe()`.

### Rule 2 — Single journal writer per stage

Open: `autonomous_execution.py`, `beta_agent.py`, `gamma_agent.py`. Close: `position_monitor.py`. Annotate: `agent_self_diagnosis.py`, `trade_reviewer.py`. Reconcile: manual scripts only. Everyone else read-only.

**Where**: contractual (no Python enforcement); deviations get caught in code review and by the `verify_legs_flat()` check.

### Rule 3 — Daily budget counts JOURNAL writes, not order attempts

```python
todays_alpha_entries = count_journal_entries_today(prefix="A", journal_path=JOURNAL)
if todays_alpha_entries >= 2: exit
```

Silent-failure-proof. If `autonomous_execution` crashes mid-cycle, the journal still reflects truth.

**Where**: `run_pipeline.py` (Alpha), analogous in `beta_agent.py` and `gamma_agent.py`.

### Rule 4 — `place_mleg_order` returns None on failure, never raises

The caller doesn't have to wrap in try/except. A None return triggers reconciliation paths (`get_open_orders`, journal-vs-broker check).

**Where**: `_broker_ibkr.py:603–767`. Phase 5b safeguard.

### Rule 5 — `verify_legs_flat()` before marking CLOSED

After `broker.close_position()` reports success, the position_monitor calls `broker.verify_legs_flat(legs)` to confirm broker-side zero-quantity. Only then writes status=CLOSED.

**Where**: `position_monitor.py` close path; `_broker_ibkr.py:305–342`.

### Rule 6 — Effective sizing cap decoupled from drawdown gates

Beta's sizing uses `min(broker_equity, 50000)`. Drawdown gates use real `broker_equity`. The asymmetry is intentional — caps single-trade risk while still tracking real account drawdown.

**Where**: `beta/risk_engine.py`.

### Rule 7 — 3:30 PM hard close on Alpha trades

Any Alpha leg with same-day expiration closes at 3:30 PM ET unconditionally. No regime override, no operator approval needed.

**Where**: `position_monitor.py` exit-rule logic; alert mirror in `autonomous_execution.py`.

### Rule 8 — Earnings blackout absolute

14d for Alpha, 7d for Gamma. No exceptions. Checked at scan eligibility AND at execution-time guard.

**Where**: `scan_options.py`, `autonomous_execution.py`, `gamma/earnings.py`.

### Rule 9 — VIX ≥ 35 → HALT regime, no trading

No manual override. If the operator wants to trade in a CRISIS, they have to edit code (which is correct — that's a deliberate decision, not a click).

**Where**: `regime_detector.py`, `run_pipeline.py`.

### Rule 10 — IB Gateway nightly restart 23:30–00:15 ET → connection circuit breaker

`_broker_ibkr.connect()` refuses to attempt during this window. Returns False, exits cleanly.

**Where**: `_broker_ibkr.py:143–150, 174–177`.

### Rule 11 — 3-attempt budget per fix in Sentinel

Same fix-id (sha1 of diff + files + commands) failing 3 times → quarantine + Discord escalation. Prevents Sentinel from hammering.

**Where**: `sentinel_agent.py:1009–1052`.

### Rule 12 — `Type=simple` for openclaw.service

NEVER change. One previous accidental change cost 18 hours of debugging.

**Where**: `/etc/systemd/system/openclaw.service`. Documented in CLAUDE.md as a hard rule.

### Why hard rules in code, not prompts

LLMs are creative. Creative + safety-critical is dangerous. By moving the gates into Python, we get:

- **Determinism** — same input always blocks
- **Audit trail** — Python stack trace shows the gate
- **No prompt-injection escape** — even if an LLM sees a "user" message saying "bypass the safety gate," the Python gate still runs
- **Fast iteration on prompts** without risking safety regressions

This is the single most important pattern in QuantAI. If you take one thing from this document, take this.

---

## §17 — What's good

A list of things that work well, in rough priority order. These are the patterns to keep when refactoring.

### Layered defense

Five independent monitors (heartbeat, position, system, error_detector, error_learner) plus Sentinel. None of them trust the others' state. A single failure in one doesn't blind the rest. This is the immune-system pattern: redundancy is a feature.

### Cost discipline as architecture

Tier routing through ClawRoute (§13) is a *structural* cost control, not a per-script discipline. Adding a new script automatically inherits the tier policy. New developers can't accidentally bill us into a wall.

### Determinism wins

Beta is zero-LLM. Gamma is zero-LLM. The trading-path safety gates (§16) are zero-LLM. The LLMs only show up where they add narrative judgment that pure rules can't. This makes the system reproducible, auditable, and predictable — exactly what you want in a money-handling system.

### Adapter pattern (broker)

The `BROKER_TYPE` env var is the only change required to swap brokers. Phase 4 of the IBKR migration was a one-week project precisely because the adapter let us keep Alpaca code paths exercised. If IBKR has an outage, the fallback isn't theoretical.

### Journal-as-truth

Append-only JSONL with a single-writer rule per stage. The journal *is* the audit trail. There is no separate logging layer to keep in sync. `git diff trades.jsonl` shows what happened. `cp trades.jsonl trades.jsonl.bak` is a backup. No schema migration cost.

### Mobile-first ops (Discord ✅)

Sentinel's approval flow is "card on phone, tap ✅." The operator can run trades, fix infrastructure, and respond to alerts without opening SSH. This is the difference between "can be on call" and "is on call."

### Self-learning loop

Every closed trade gets a Haiku diagnosis + a Haiku review automatically. Once a week, Sonnet synthesizes everything. The operator marks items resolved via a CLI. The system improves itself one trade at a time, with a human in the loop for resolution decisions.

### Section 1256 edge

XSP/SPX options qualify for Section 1256 tax treatment (60/40 long/short) regardless of holding period. European exercise means no early-assignment risk. Cash settlement means no share delivery surprises. These three structural advantages are *real* alpha vs equivalent SPY trades — and they're the reason ADR-004 happened.

### Honest "what I won't do"

Every agent identity file ends with a "what I won't do" section. This forces clarity at design time and is loaded into the LLM prompts at runtime. The LLM is constrained by its own constitution. It's not a perfect defense — but it adds an extra layer beyond the code-enforced gates.

### Graphify token reduction

ADR-001 measured 44.8× token reduction per architecture query. That's not an optimization; it's an enabler. Without it, certain Claude conversations were prohibitively long. With it, "explain the broker adapter" is one query.

### Phase 5b partial-fill safeguard

The `order_submitted` flag + `_find_open_order_by_ref` + `verify_legs_flat` triplet closes the entire ghost-position class of bugs. It came from incidents (A018 close, A021/A022 entries) and the design lessons-learned are baked into the code. Layered defense at its best.

### Single-VPS simplicity

We chose to run everything on one VPS with cron. No Kubernetes, no message bus, no orchestrator. That's a pro: operational complexity is minimal. (It's also a con — see §18.)

### `verify_legs_flat()` before journal CLOSED

Broker-confirmed flat-state is required before the journal accepts a CLOSED status. This caught the A018 ghost. It's a one-line addition to the close path that prevents the worst class of journal-vs-broker drift.

---

## §18 — What's bad / weak / risky

A list of known weaknesses, in rough priority order. Calling them out is honest; pretending they don't exist is not.

### Documentation drift

`CLAUDE.md` still describes auto_heal as active, but auto_heal was retired on 2026-05-03 and replaced by Sentinel. CLAUDE.md still names LiteLLM as the LLM ingress, but the actual ingress is ClawRoute. Multiple `.bak` and `.retired` files clutter `scripts/`. New contributors will read these and get a wrong mental model. This document is part of the fix.

### Single-VPS SPOF

One VPS. One IB Gateway. One Tailscale relay. If any of them dies, QuantAI dies. There is no HA, no failover, no backup region.

Mitigations today:

- ibgateway watchdog cron (every 5 min) — recovers from gateway crashes
- `dashboard-http.service` auto-restart
- Daily backups via `karna-backup.sh` (age-encrypted, GitHub-pushed)

What's still uncovered: VPS host failure, Tailscale outage, IBKR-side network issues that the watchdog can't fix.

### OpenClaw `Type=simple` is a foot-gun

The systemd unit type is `simple` for hard-won reasons. One accidental change to `Type=forking` cost 18 hours of debugging. The unit file is path-allowlisted out of Sentinel's reach, and CLAUDE.md flags it as a hard rule. But it's still a single line that, if changed, breaks everything.

### Manual workspace sync

`bash /home/trader/QuantAI/scripts/sync_workspaces.sh` must be human-run after any change to `v2/workspace-*/AGENTS.md` or `SOUL.md`. There is no git hook. The runtime path (`/root/quantai-v2/workspace-*/...`) silently drifts from the canonical.

### IV Rank is a 21-day realized-vol proxy

The numerator is 21-day realized volatility, not chain-derived implied volatility. The denominator is a 21-day rolling percentile of the same. This is a *cheap* proxy that correlates ~0.7 with true IV Rank. Beta's strategy gates that depend on IV Rank are operating on a noisy signal.

### ClawRoute is a SPOF for LLM traffic

If ClawRoute dies, every script that doesn't set `LLM_BYPASS_CLAWROUTE=1` breaks at the next LLM call. Mitigations: port probe every 5 min, escape valve in the shim. Not mitigated: auto-bypass when probe fails (the operator has to flip the env var manually).

### Finnhub free tier limits event_moves_seeder

The historical event-move data for Beta's `PRE_EVENT` regime is seeded from Finnhub. Free tier limits us to ~3 weeks of history, accumulated weekly. The 8-event window for back-testing event behavior takes 8 weeks to fill. The strategy works without it (uses heuristic defaults), but accuracy improves as the window fills.

### Limited live data

QuantAI's first real (non-dry-run) IBKR trade was 2026-04-27. Beta and Gamma went live 2026-04-27 / 2026-04-29. At the time of this document, that's about 9 trading days of live data. Strategy-level insights need 30+ days minimum. We're operating on backtests + paper-trade evidence; the live distribution is still forming.

### Docker legacy bypass

The `docker-compose.yml` containers (trader-orchestrator, trader-discord, trader-cto, trader-guards) are still running but mostly superseded by v2 cron + OpenClaw. They're dead weight — they don't break, but they don't help either. Untangling them is a project that hasn't been prioritized.

### GitHub remote stale

Local `main` on the VPS is the source of truth. `origin/main` on GitHub is frozen — diverged 42 local vs 40 remote at common ancestor `c91c801`. ~75 files differ. The two histories cannot be reconciled automatically. The remote is effectively a backup, not a sync target.

### Trading currently HALTED

Since 2026-05-05, all trading agents have a `# HALTED 2026-05-05 INTC-mismatch-investigation:` prefix on their cron lines. A018 + A020 (8 INTC option legs total) are sitting on the broker, holding to May 15 expiration. A021 + A022 were corrected to `PHANTOM_NEVER_FILLED` on 2026-05-06. See §20.

This is a halt of a currently-investigating issue, not a permanent state — but it's the current state.

### No price re-validation in Alpha or Beta

Between debate-time / strategy-selection time and execution time, prices can move. Today, Alpha and Beta submit at whatever the broker's chain query gives at execution. Only Gamma re-validates (RSI < 35 + above 200-SMA) before execute. This is the biggest gap in the trading path.

### IBKR paper combo MarketOrder doesn't fill when quotes are None

Discovered 2026-05-06. Even on liquid underlyings, if the IBKR paper data feed isn't warmed up (option quotes return `None`), a multi-leg combo MarketOrder sits at `Submitted`/`PendingSubmit` indefinitely. Workaround: use LimitOrder with intrinsic + buffer. The trading path uses MarketOrder by default; only manual unwinds hit this.

### Dashboard structural drift

Adding a new agent requires hand-editing `index.html`. There is no detector for this. When Gamma was added 2026-04-29, the dashboard didn't learn about Gamma until 2026-05-05 (six days). For those six days, the dashboard was a lie. Sentinel cannot fix this — `index.html` is outside its write allowlist.

### Sentinel pending-approval cards re-post each apply slot

If the operator doesn't tap ✅ within an apply slot, the card re-posts at the next apply slot until 48h elapses. There is no "snoozed for 24h" memory. A vacationing operator returns to a Discord channel full of duplicates.

### Status check on ibgateway leaks credentials

`systemctl status ibgateway` and `ps aux | grep ibgateway` print credentials to terminal output. Mitigation: CLAUDE.md documents the safe alternatives (`systemctl is-active ibgateway`; `journalctl -u ibgateway -n 30 --no-pager | grep -v -i 'pass\|pw=\|--user='`). New operators are at risk of using the unsafe form.

### No backtest harness in trading path

`services/backtester.py` exists but isn't wired into CI. New strategies are paper-tested live. There is no historical-replay gate before going live. Gamma went live with a backtest done outside the repo; it's correct, but we couldn't re-run it from CI today.

### Agent identity files in two places

Canonical: `/home/trader/QuantAI/v2/shared-data/agents/AGENT_*_IDENTITY.md`. Runtime: `/root/quantai-v2/.../AGENT_*_IDENTITY.md` (loaded by OpenClaw at agent boot). Sync is human responsibility. Same root cause as workspace sync (§18). Different sync script.

### Cron is fragile to DST transitions

The crontab hardcodes `13-20` UTC for market hours, which is correct during DST. After Nov 1, 2026, market hours move to `14-21` UTC. The transition is a manual edit. Forgetting it = pipeline runs an hour off, which is silently broken.

### error_detector lives in `legacy/`

Path is `v2/shared-data/scripts/legacy/error_detector.py`. It's the active script — `legacy/` is historical naming from before a refactor. Confusing for new contributors.

---

## §19 — What could be better

Concrete, actionable improvements in priority order. None of these are required for the system to work; all of them would make it better.

### High-impact (next 30 days)

#### HA / DR plan

Secondary VPS in a different region, warm IB Gateway with separate IP whitelist. Tailscale's mesh handles the routing transparently. Failover is manual today; could be automated with a watchdog that flips `BROKER_TYPE` and DNS.

#### CI for trading path

Pre-commit syntax check on every `.py` in `v2/shared-data/scripts/`. Smoke test on broker dry-run (`BROKER_DRY_RUN=1` mode). Block merges that break either. Today: tests exist (`v2/shared-data/tests/`) but aren't run on commit.

#### Halt-aware monitoring

A `/tmp/quantai-heartbeats/halt_mode.json` flag with `{halted: true, reason: "...", until: "ISO"}`. Heartbeat monitor and error_detector check it before alerting. Suppresses pipeline-silent-stale alerts during planned halts.

#### Update CLAUDE.md

Replace LiteLLM mentions with ClawRoute. Replace auto_heal mentions with Sentinel. Update cron schedule to reflect halt state. Add a one-liner pointing to `docs/architecture.md` as the canonical doc.

#### Price re-validation in Alpha and Beta

Before `place_mleg_order`, re-query the chain and verify the credit/cost is within ±10% of the debate-time value (Alpha) or strategy-selection value (Beta). Abort if drifted. Today, only Gamma re-validates.

### Medium-impact (next 90 days)

#### True chain-derived IV Rank

Snapshot option chains daily, compute true IV from chain mids, build a rolling percentile. Replace the 21-day realized-vol proxy. Beta's HIGH_VOL regime gate becomes more accurate.

#### Auto workspace sync

Git post-commit hook that runs `sync_workspaces.sh` on any change to `v2/workspace-*/`. Removes the manual step.

#### Daily LLM spend cap with kill switch

Per-tier daily budget. If FRONTIER spend > $1 today, refuse new Frontier requests. Phase B4 of ADR-002 explicitly deferred this; time to ship.

#### Backtest in CI

Any new strategy must pass historical N-month replay before going live. Wire `services/backtester.py` into a CI job that runs on PR.

#### Combo LimitOrder fallback

When `place_mleg_order` MarketOrder hasn't filled in 30 seconds, cancel and resubmit as LimitOrder with an intelligent price (mid-quote if available, intrinsic + buffer otherwise). Today: stuck orders sit until the operator notices.

### Low-impact (someday)

#### Docker legacy purge

Retire `trader-orchestrator`, `trader-discord`, `trader-cto`, `trader-guards`. Consolidate any remaining functionality into v2 cron. Smaller surface area, less confusion.

#### Kill the GitHub remote (or hard reconcile)

Living with two divergent histories is a footgun. Either treat GitHub as a frozen archive (current state, but less explicitly), or do a one-time hard reconciliation. Pick one and document it.

#### Strategy-level position grouping

In `position_monitor.py`, group legs by trade_id when applying exit rules. Today, each leg is independent — an iron condor with both wings touched is treated as four separate decisions. Already noted in CLAUDE.md as deferred.

#### Per-tier daily budget alerts

Not just "total spend spike" but "FRONTIER spend > $X today" or "Sonnet calls > N today." Catches cost regressions before they become bills.

#### Drift detector for the dashboard

Daily job that compares `tabs[]` and Mermaid agent names against `AGENT_*_IDENTITY.md` files and the journal's distinct `source` values. Alerts if drift detected. Could be a Sentinel responsibility (read-only access to `index.html` would suffice).

#### Sentinel snooze memory

Per-fix-id "asked recently, ignored" state in `state.json`. If the same fix-id is dismissed twice in 24h, suppress for 24h after the second dismissal. Eliminates the duplicate-card problem (§18).

#### Replace `legacy/` paths

Move `v2/shared-data/scripts/legacy/error_detector.py` to `v2/shared-data/scripts/error_detector.py`. Update all references. Cosmetic but reduces new-contributor confusion.

#### Per-agent rate-limit awareness in Discord posting

If multiple monitors all fire in the same minute, Discord throttles. A small in-process rate limiter on `_discord.post_to_channel()` would smooth the bursts.

#### Lazy-load Recharts/Mermaid in dashboard

Cut first-paint time by ~60%. Today: full bundle loads on every visit.

---

## §22 — Glossary

Terms used throughout this document, with one-line definitions.

### Options-specific

- **OCC symbol** — the standard option symbol format: `<ROOT><YYMMDD><C|P><STRIKE×1000 padded to 8 digits>`. Example: `INTC260515P00094000` = INTC put expiring 2026-05-15, strike $94.00.
- **Bag / ComboLeg** — IBKR's representation of a multi-leg option order as a single security with sub-legs. Submitting a Bag means all legs fill or none do (no leg risk).
- **IV (implied volatility)** — the market-implied volatility of an option, derived from the option price and the Black-Scholes model.
- **IV Rank** — IV percentile ranking over a lookback window. 0 = lowest IV in window, 100 = highest. QuantAI uses a 21-day realized-vol proxy today (§18).
- **Delta** — first derivative of option price with respect to underlying price. ATM ≈ 0.50.
- **Gamma** — second derivative; rate of change of delta. Highest at ATM near expiry.
- **Theta** — first derivative with respect to time; daily decay. Negative for long options, positive for short.
- **Vega** — first derivative with respect to IV. Positive for long options, negative for short.
- **Section 1256** — US tax treatment for index options: 60% long-term capital gains, 40% short-term, regardless of holding period. Applies to SPX, XSP, VIX (not SPY, QQQ).
- **European exercise** — option can only be exercised at expiry (no early assignment). XSP, SPX, VIX are European.
- **American exercise** — option can be exercised any day before expiry. Equity options (SPY, AAPL) are American.
- **Cash settlement** — at expiry, settlement is in cash (the difference between strike and underlying), no share delivery. SPX, XSP, VIX are cash-settled.
- **Contango / backwardation** — futures-curve shapes; contango = front below back, backwardation = front above back. Affects VIX-related strategies.

### Indicators

- **ADX** (Average Directional Index) — trend strength, 0–100. > 25 = trending; < 20 = range-bound.
- **RSI** (Relative Strength Index) — momentum oscillator, 0–100. > 70 overbought, < 30 oversold. Connors RSI(10) is a 10-period Wilder RSI.
- **EMA** (Exponential Moving Average) — weighted moving average favoring recent prices.
- **SMA** (Simple Moving Average) — equal-weight moving average. Gamma uses 200-day SMA as the long-term trend filter.
- **Bollinger Band width percentile** — standardized BB width; lowest decile = squeeze (Beta SQUEEZE regime).

### Strategies

- **mleg order** — multi-leg option order, submitted as one combo.
- **Debit spread** — net premium paid; long the higher-priced option, short the lower.
- **Credit spread** — net premium received; short the higher-priced option, long the lower.
- **Iron condor** — two credit spreads (bear call above, bull put below); range-bound thesis.
- **Broken-wing butterfly (BWB)** — modified butterfly with wings of unequal width; lopsided risk profile.
- **Ratio backspread** — long more contracts of one strike than short of another; high vega.
- **Calendar spread** — short near-term + long far-term, same strike; theta + vega harvest.
- **Diagonal spread** — like a calendar but different strikes; mixed theta + delta.
- **Jade lizard** — short put + short call spread above; no upside risk in a specific construction.

### System concepts

- **regime** — Beta's market-state classification (one of 12). Drives strategy selection.
- **circuit breaker** — automatic halt after N consecutive losses (Beta: 5).
- **propose-wait** — Sentinel fix class requiring operator ✅ approval.
- **safe-auto** — Sentinel fix class auto-applied without approval (post Python NEVER-list gate).
- **never-touch** — Sentinel fix class refusing observation (path-allowlisted out).
- **ghost position** — journal-vs-broker mismatch. Three flavors: true ghost, journal lie, entry phantom.
- **paper trading** — broker-simulated trades with no real money. IBKR paper account `DUP851506`; Alpaca paper.
- **dry run** — code path that simulates an order without actually placing it. Triggered by `BROKER_DRY_RUN=1`.
- **fix-id** — sha1 hash of (diff + files + commands) for a Sentinel proposal. 3-attempt budget per fix-id.
- **JSONL** — JSON Lines format. One JSON object per line; newline-terminated.
- **atomic write** — write to `.tmp` then `os.replace()`; prevents partial reads.
- **MCP** — Model Context Protocol; way to expose tools to Claude (graphify uses this).
- **ADR** — Architecture Decision Record; a numbered, dated document recording an architectural choice.
- **SPOF** — Single Point of Failure. We have several.
- **HA / DR** — High Availability / Disaster Recovery. We have neither.

### Tools and services

- **Tailscale** — mesh VPN with identity-based auth. Fronts the dashboard.
- **OpenClaw** — Rust runtime hosting a 24/7 Claude session. Implements KARNA.
- **KARNA** — name for the OpenClaw-hosted Claude Sonnet 4.6 instance that supervises the system.
- **ClawRoute** — local LLM-call proxy on `:18790`. Tier routing + cost tracking.
- **LiteLLM** — legacy LLM proxy on `:4000`. Still running; no longer the QuantAI ingress.
- **ib_insync** — Python async wrapper for IBKR's API. Used by `_broker_ibkr.py`.
- **IBC** — IB Controller. Wraps IB Gateway startup; injects credentials at runtime.
- **IB Gateway** — IBKR's lightweight FIX/Java gateway. Runs on `:4002` (paper), `:4001` (live).
- **graphify** — codebase knowledge graph tool. Provides MCP queries.
- **yfinance** — Python wrapper for Yahoo Finance data. Used by `market_intelligence.py` and Gamma's scanner.
- **Finnhub** — financial data API (free tier). Source for `event_moves_seeder`.
- **Recharts** — React charting library used by the dashboard.
- **Mermaid** — Markdown-flavored diagram-as-text renderer; dashboard's Workflows tab uses this.
- **Tailwind CSS** — utility-first CSS framework; dashboard styling.

---

*End of document. Total length is intentionally bounded: see §0 for the structure, and §20 for what's expected to be stale next time you read this.*
