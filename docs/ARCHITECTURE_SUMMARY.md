# QuantAI — Architecture Summary

A short, public-facing tour of QuantAI. Read this first if you only have ten minutes. When you want depth, jump into [`architecture.md`](./architecture.md). For the dated "what's happening right now" view, see [`STATE.md`](./STATE.md).

---

## What this is

QuantAI is an autonomous, multi-agent options-trading system. It operates a $1,000,000 IBKR paper account around the clock, places carefully-controlled multi-leg option orders during US market hours, monitors its own positions, fixes its own infrastructure, and writes a weekly self-review of its own performance. The whole thing runs on a single Linux VPS, costs ~$4–5/month in LLM spend, and is operated by one person from their phone over Discord.

The single sentence that drives every design choice is this:

> **Use LLMs only where judgment is needed. Enforce all safety rules in code, not in prompts.**

Most of what makes QuantAI interesting follows from taking that sentence seriously.

---

## Four agents, one journal

QuantAI is four small, narrow Python programs, not one big system. Each agent has its own identity file, its own log, its own trade-ID prefix, and its own mandate. They share three things: the broker adapter, the journal, and the position monitor.

**Agent Alpha** trades defined-risk options spreads on a 78-ticker ETF/equity universe. Before placing an order, Alpha runs a "debate chamber" — a Sonnet-class LLM proposes the top candidates, Python templates generate Bull and Bear cases for each, and a second Sonnet judge votes 0–100. Trades scoring ≥ 60 go through. Maximum loss per trade is 2% of a $50k effective sizing cap; daily entries are capped at 2; positions close at 3:30 PM ET. Trade IDs start with `A`. Cost per debate cycle is around two-tenths of a cent.

**Agent Beta** trades native index options on SPX, XSP, and VIX through IBKR — not through equity ETF proxies. A 12-regime classifier (CRISIS, MEAN_REVERSION_OVERSOLD, HIGH_VOL, SQUEEZE, PRE_EVENT, TREND_UP, TREND_DOWN, LOW_VOL, RANGE, NORMAL, plus a HALT failsafe) maps to one of 8 strategies (event_strangle, broken_wing_butterfly, calendar_spread, ratio backspreads, debit/credit spreads, vix_calls). Beta is **fully deterministic** — zero LLM calls per cycle. Same inputs always produce the same trade. Trade IDs start with `B`. The reason for the IBKR-only path is structural: index options give Section 1256 tax treatment (60/40 long/short), European exercise (no early-assignment risk), and cash settlement (no share delivery). Equity-ETF options have none of those.

**Agent Gamma** is the specialist. One setup, one trade type. The setup: Connors RSI(10) below 30, price above the 200-day SMA, no earnings within seven days, sufficient liquidity. The trade: a 14–21 DTE bull call debit spread, sized to ≤1% risk. Two phases run on different crons — a 4:30 PM ET scan after market close, and a 9:33 AM ET execute the next morning that re-validates the signal on fresh data before submitting. Backtest evidence on the equivalent universe (1996–2019) is 88.89% win rate. The setup is intentionally rare; some weeks it does not trade at all, and that is correct behavior. Trade IDs start with `G`.

**Agent Sentinel** does not trade. It is the system's autonomous infrastructure operations agent — a peer of the three trading agents but with a completely different mandate. Sentinel reads logs, errors, and health snapshots; classifies what it sees as `safe_auto`, `propose_wait`, or `never_touch`; auto-applies the safe ones; and queues riskier ones to a Discord card the operator can approve with a single ✅ tap on their phone. Sentinel is path-allowlisted away from the trading code, the journal, the broker module, and the openclaw service file. Even if its underlying LLM tagged a fix as "safe to auto-apply," the Python NEVER-list gate would still refuse if the proposal touches any of those paths. Sentinel runs four times per weekday plus weekend observe slots, and self-downgrades to read-only if it ever fires inside the trading window.

All four agents append to a single JSONL file: `/root/quantai-v2/shared-data/journal/paper/trades.jsonl`. One trade per line, append-only, never mutated except by the agent that opened it (status: OPEN) and the position monitor that closes it (status: CLOSED, plus exit fields). Everyone else — collectors, the dashboard, the weekly synthesis — reads only. The journal is the single source of truth, the audit trail, and the input to every analytics question. There is no separate database.

---

## The safety surface

QuantAI's safety story is twelve hard rules, all in Python code, none in prompts.

The trading-path safeguards enforce the basics: 2% max loss per trade, 5% daily loss halts new entries, 14-day earnings blackouts (7 days for Gamma), VIX ≥ 35 routes to a HALT regime that stops all entries, the IB Gateway nightly restart window blocks broker connections from 23:30–00:15 ET to avoid retry storms, and a 3-attempt budget in Sentinel quarantines any fix that fails three times in a row.

The trading-path bug protections are subtler. Daily entry budgets count *journal-written* trades, not order *attempts*, so a script crashing mid-cycle never double-counts. The `place_mleg_order` function returns `None` on terminal failure rather than raising an exception, so callers never have to wrap broker calls in try/except. After a close order, `verify_legs_flat()` confirms the broker actually has zero quantity on each leg before the journal accepts CLOSED — this caught a real production bug where a `Cancelled` close was being treated as success while the legs stayed open. The Phase 5b partial-fill safeguard (added 2026-05-03 after two real incidents) detects when an exception fires after the order was already submitted and recovers the working order via `_find_open_order_by_ref`, rather than blindly resubmitting.

Sentinel's safety is enforced by three NEVER lists checked in `is_command_safe()` and `validate_proposal()`: `NEVER_MODIFY_PATHS` (trading code), `NEVER_TOUCH_PATHS` (.env, openclaw, ibgateway, journal), and `NEVER_RESTART_SERVICES_BLANKET`. The position-aware ibgateway guard refuses to restart the broker connection if either market hours are open or any positions exist — both must be false for a restart to proceed.

Finally, the system's stickiest hard rule, learned the painful way: `Type=simple` for `openclaw.service`. Changing it once caused 18 hours of debugging.

---

## How the moving parts fit together

The metronome is cron. There is no message bus, no orchestrator, no long-lived process to leak memory. Every script runs to completion and exits; state lives on disk. Most ticks don't trade — they run the conditions (regime, daily-budget, market hours) and exit early. Agents enter when conditions allow, not on a schedule.

The LLM ingress is **ClawRoute**, a single proxy on `localhost:18790` that every Anthropic call routes through. ClawRoute provides tier routing (Gemini Flash-Lite for cheap heartbeat-class calls, Haiku for medium, Sonnet for complex, Opus deferred), centralized cost tracking, and an audit trail. An escape valve (`LLM_BYPASS_CLAWROUTE=1`) lets any script bypass it if the proxy goes down. Total LLM spend across the whole system: ~$4–5/month.

The dashboard is a deliberately minimal three-layer stack. Cron-driven Python collectors write versioned JSON state files into `/var/dashboard/state/`. A single hand-edited React SPA at `/var/dashboard/index.html` polls those files every 30 seconds and renders nine tabs (Live, Agents, Trades, Performance, Self-Learning, System, Workflows, Errors, History). The whole UI is fronted by `python3 -m http.server` bound to `127.0.0.1`, with Tailscale serve handling external exposure. There is no app-level auth — Tailscale's mesh identity is the auth boundary.

A self-learning loop runs after every closed trade. Two Haiku-class LLM passes — `agent_self_diagnosis` and `trade_reviewer` — write back to the journal entry and to per-trade markdown files. Every Friday at 4:45 PM ET, a Sonnet-class `weekly_synthesis` reads the week's diagnoses and reviews and posts a per-agent summary to Discord. The operator can mark items resolved with a small CLI; the dashboard's Self-Learning tab tracks open vs. resolved.

KARNA — the 24/7 Claude Sonnet 4.6 instance hosted by the OpenClaw runtime — is the operator's co-pilot, drafting partner, and Discord interface. KARNA does not trade. Four "workspaces" (`workspace-orchestrator`, `workspace-infra`, `workspace-journal`, `workspace-research`) keyed by Discord channel give KARNA different personalities and mandates depending on which channel the operator is talking to.

Knowledge graph queries go through **graphify**, which builds a queryable map of the codebase (currently 2,800 nodes, 4,752 edges, 249 communities). AST-only rebuilds run free on every commit; a monthly Sonnet-driven refresh picks up doc-semantic edges. The measured token cost reduction per architecture query is ~44.8×.

---

## What's good, what isn't

The strengths of QuantAI follow directly from the thesis. Layered defense (five independent monitors, none of which trust each other's state). Cost discipline as architecture (tier routing means we don't pay Sonnet rates for Haiku-class tasks). Determinism wins where it can (Beta and Gamma are zero-LLM; safety gates are zero-LLM). The broker adapter is a one-env-var swap. The journal is the truth. Mobile-first ops via Discord ✅ approvals. Section 1256 tax treatment is real structural alpha vs. equivalent SPY trades.

The weaknesses are honest about themselves. There is no high-availability — one VPS, one IB Gateway, one Tailscale relay. Workspace sync is manual (no git hook). The IV Rank used by Beta is a 21-day realized-vol proxy, not chain-derived true IV. ClawRoute is a single point of failure for LLM traffic. Live trading data is thin — only a few weeks. There is no CI gate before changes hit the trading path. The dashboard's structural layer drifts from the code's structural layer (the data layer auto-syncs; the React tabs are hand-edited). And — most importantly today — Alpha and Beta do not re-validate prices between the debate-time decision and the execution-time order; only Gamma does. That is the single highest-leverage fix on the backlog.

---

## Where to go next

- **Want the deep dive?** [`architecture.md`](./architecture.md) — every meaningful component, with file paths, line numbers, ELI10 explanations, and good/bad/could-be-better callouts.
- **Want today's snapshot?** [`STATE.md`](./STATE.md) — halt status, open positions, trade counts, recovery plan. Expected to age fast.
- **Want a specific topic?** Jump straight from the [`architecture.md` table of contents](./architecture.md#0-table-of-contents) — agents are §3, broker adapter is §4, debate chamber is §6, journal is §7, monitoring is §8, self-learning is §9, dashboard is §11, ADRs are §15, the 12 safety rules are §16, and the honest "what could be better" is §19.
