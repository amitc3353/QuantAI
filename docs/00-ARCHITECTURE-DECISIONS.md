# Architecture Decisions — QuantAI

## ADR-001: Adopt Graphify for codebase knowledge graphs
**Date:** 2026-04-25
**Status:** Accepted
**Context:** QuantAI's pipeline, guard engine, error catalog, and runbooks are
spread across ~80 files. Every Claude Code session pays a re-read tax to
rebuild structural context. Same problem on KARNA's surface (SOUL/AGENTS
files + OpenClaw config). Need a queryable, token-efficient knowledge graph
both Claude Code and OpenClaw can consume.

**Decision:** Install `graphifyy` via `pipx` for the `trader` user. Build a
graph of the QuantAI repo at `graphify-out/graph.json`. Expose it as an
on-demand MCP server registered in `.claude/settings.local.json`. Install
`graphify hook install` for AST-only rebuilds on commit (no LLM cost on
code-only commits). LLM extraction rebuilds remain human-initiated.

**Alternatives considered:**
- Roll our own ctags + grep workflow: rejected — no concept/doc layer, no
  cross-modal edges (code ↔ runbooks).
- Pure embedding-based RAG (e.g., LlamaIndex): rejected — graphify's
  topology-based clustering matches our needs better and gives us paths
  ("what calls this?") that vector search doesn't.
- Skip and keep grepping: rejected — re-read tax compounds as the codebase grows.

**Consequences:**
- (+) 44.8× token reduction per query vs reading raw files (measured on this corpus).
- (+) Free incremental AST rebuilds on every commit.
- (+) `post()` and `run_guard_pipeline()` identified as cross-community god nodes.
- (-) New tool dependency; pinned by `pipx` install, may need version bumps.
- (-) Graph staleness possible after doc changes — manual rebuild step required.
- (-) `graph.json` committed to repo (1.1 MB on initial build; monitor growth).

**Out of scope for this ADR:**
- KARNA self-knowledge graph (Use Case B — separate ADR when implemented).
- Cross-project combined graph (Use Case D — deferred indefinitely).

---

## ADR-002: Cost discipline via ClawRoute as the single LLM ingress
**Date:** 2026-04-25
**Status:** Accepted (Phase A1 cron-side shipped; Docker-side deferred to follow-up)

**Context:** Cost discipline ("LLMs only where judgment is needed; cheap models
for cheap tasks") was a stated principle but had never been audited. Phase-1
audit found that all 15 LLM call sites in QuantAI bypassed ClawRoute and
called Anthropic directly. ClawRoute's `routing_log` had been empty for the
2.5 weeks since service restart. The "ClawRoute will tier our calls" claim
was fiction. Separately, DeepSeek V3 (used in ClawRoute's MEDIUM tier
fallback) deprecates 2026-07-24, forcing a V4 migration regardless.

**Decision:** ClawRoute becomes the single LLM ingress for QuantAI. A shared
`_llm_client.py` shim at `v2/shared-data/scripts/_llm_client.py` exposes two
interfaces:
- `Client()` — drop-in replacement for `anthropic.Anthropic` with
  `.messages.create()` returning `.content[0].text`. Used by SDK-shaped
  callers (debate_chamber, self_evolution, etc.).
- `chat(messages, system, model, max_tokens, ...)` — functional helper
  returning a plain string. Used by aiohttp-shaped callers, async-safe
  via `await asyncio.to_thread(chat, ...)`.

The shim posts to `http://127.0.0.1:18790/v1/chat/completions` with two
ClawRoute-specific workarounds: (1) no `Authorization` header (the auth
middleware 500s on Bearer tokens it doesn't recognize); (2) raw byte reads
via `httpx.stream(...).iter_raw()` to bypass the lying upstream
`content-encoding: gzip` header. Both quirks documented inline in the shim.

A single env var `LLM_BYPASS_CLAWROUTE=1` reverts to direct Anthropic API as
an incident-response escape valve.

**Alternatives considered:**
- Keep direct-Anthropic, tune prompts: rejected — no audit trail, no
  per-tier routing, no daily-cap kill switch.
- Route through LiteLLM (already running at localhost:4000): rejected —
  duplicate router with a worse tier classifier than ClawRoute's, no
  integration with the existing dashboard.
- Per-script `ANTHROPIC_BASE_URL` env override: rejected — SDK doesn't
  expose the URL override cleanly across all 15 sites, and the shim is the
  same effort while giving us a single point of control for future logging
  / caching changes.

**Consequences:**
- (+) Single observable cost surface; daily caps enforceable in one place.
- (+) Tier-tuning data accumulates in one DB (`routing_log`).
- (+) DeepSeek V3→V4 migration handled centrally (no scripts to update).
- (+) Smoke tests showed ~95% savings vs direct Haiku on small requests
  (Gemini Flash-Lite via HEARTBEAT tier classification).
- (-) ClawRoute is a SPOF for LLM traffic. Mitigated by `LLM_BYPASS_CLAWROUTE=1`.
- (-) Two ClawRoute quirks live in our shim; if either is fixed upstream,
  remember to simplify.
- (-) The orchestrator / discord-bot / cto-listener Docker containers do
  not bind-mount their source from the host, so this migration was scoped
  to the cron-driven scripts (8 of 15 sites). The remaining 7 Docker-side
  sites are a separate task.

**Migration status (2026-04-25):**
- Cron-side (8 sites): migrated.
  - `v2/shared-data/scripts/debate_chamber.py` (4 sites: proposal, bull, bear, judge)
  - `v2/shared-data/scripts/self_evolution.py` (4 sites: consolidate, observe, critique, generate)
- Docker-side (7 sites): deferred to follow-up task.
  - `orchestrator/{agent1_iron_condor,agent2_covered_call,scheduler,self_improve}.py`
  - `services/{cto_agent,cto_report}.py`
  - `discord-bot/cogs/chat_agent.py`

**Out of scope for this ADR:**
- ClawRoute tier-threshold retuning (defer until 1+ week of populated data).
- Hardening the embedded API keys in `clawroute.service` / `openclaw.service`
  systemd units and the LiteLLM `docker run -e` flags (separate task).
- Daily spend cap + kill switch (Phase B4).

**Phase B2 (shipped 2026-04-25):** DeepSeek V3→V4 migrated. ClawRoute
`config/default.json` updated: SIMPLE primary is now `deepseek/deepseek-v4-flash`,
HEARTBEAT/FRONTIER fallbacks also switched to V4. Groq Llama 4 Scout
(`$0.11/$0.34/1M`) added to ClawRoute source + model registry (TypeScript
rebuild done). HEARTBEAT Groq swap pending `GROQ_API_KEY` in clawroute.service.

**Phase B5 (shipped 2026-04-25):** Cost cron + dashboard cards live.
`/var/dashboard/collect_clawroute.py` runs every 15 min, writes
`clawroute.json`, Discord-alerts on spend spikes.

---

## ADR-004: Migrate from Alpaca to IBKR for options execution
**Date:** 2026-04-26
**Status:** Accepted — Phases 1–4 complete 2026-04-27. Phase 5 (live trading flip) pending.

**Phase status (as of 2026-04-27):**

| Phase | What | Status |
|---|---|---|
| 1 | IB Gateway install + connection probe (XSP/SPX/VIX chains verified) | ✅ 2026-04-26 |
| 2 | Build pluggable `broker.py` (BrokerBase + AlpacaBroker + IBKRBroker via ib_insync) | ✅ 2026-04-26 |
| 3 | Wire `get_broker()` into 4 callers (autonomous_execution, position_monitor, pre_trade_check, collect_alpaca) and verify on IBKR | ✅ 2026-04-26 |
| 4 | System-wide flip: `BROKER_TYPE=ibkr` default in `.env`; close pre-existing Alpaca positions | ✅ 2026-04-27 |
| 5 | Live trading on IBKR (when paper validation milestones met) | ⏳ pending |

**Context:** QuantAI's original execution broker is Alpaca paper trading. A live
probe on 2026-04-26 confirmed that Alpaca paper returns HTTP 422 ("invalid
underlying symbol") for all index options — SPX, XSP, SPXW, VIX, and MXSP. The
planned strategy for both Agent Alpha and Agent Beta requires XSP (mini-SPX),
which offers: (1) European-style exercise — no early assignment risk, (2) cash
settlement — no share delivery risk, (3) Section 1256 tax treatment — 60/40
long/short-term capital gains regardless of holding period.

At $10k paper capital, one XSP contract ($50–$250 premium) fits the 1% risk rule.
SPY options are the nearest alternative but carry American-style exercise risk and
do not qualify for 1256 tax treatment.

IB Gateway 10.37 was installed at `/opt/ibgateway/` and configured for paper mode
(account DUP851506, port 4002) via IBC 3.23.0. The systemd unit
`ibgateway.service` is enabled. The IBKR password was rotated on 2026-04-26 and
is stored in `.env` as `IBKR_PASSWORD`, injected at runtime by
`/opt/ibc/quantai_gateway_start.sh` via IBC's `--pw` argument.

**Connection verified 2026-04-26:** ib_insync 0.9.86 connects to `localhost:4002`,
`managedAccounts()` returns `['DUP851506']`. Index option chains confirmed:
XSP (47 expiries / 482 strikes), SPX (20 / 590), VIX (9 / 70).

Root cause of initial failure: login username was misconfigured as the paper
account number (`DUP851506`) instead of the IBKR login username. Fixed by adding
`IBKR_USERNAME` to `.env` and reading it from the environment in the IBC wrapper.

**Decision:** IBKR (via IB Gateway + ib_insync) becomes the execution broker for
QuantAI. Alpaca paper remains the current active broker until a BrokerAdapter
abstraction layer is built and the IBKR connection is verified. Migration is
incremental:

1. Phase 1 (done 2026-04-26): Gateway installed, username fixed, connection verified.
   ib_insync connects, XSP/SPX/VIX chains confirmed.
2. Phase 2 (next session): Build `broker.py` — `AlpacaBroker` + `IBKRBroker` with
   `BROKER_TYPE=alpaca|ibkr` env var switch. Run paper trades on IBKR in parallel.
3. Phase 3: Validate IBKR paper execution in parallel with Alpaca for 1-2 weeks.
4. Phase 4: Validate IBKR paper execution in parallel with Alpaca for 1-2 weeks.
5. Phase 5 (when live trading approved): Switch `BROKER_TYPE` to `ibkr` on live
   account.

**Alternatives considered:**
- Keep Alpaca + use SPY/VXX as index proxies: rejected — SPY is American-style
  (early assignment on ex-div dates), VXX suffers contango decay (~10-15%/yr),
  neither qualifies for 1256 treatment. Proxies introduce tracking error that
  breaks the strategy's payoff math.
- Switch to tastytrade API: considered — has index options support, but API is
  less mature than ib_insync/TWS, and IBKR is already installed and partially
  configured.
- Build custom IBKR REST wrapper via TWS API (Java): rejected — ib_insync
  is a battle-tested Python client wrapping the same TWS socket API.

**Consequences:**
- (+) XSP, SPX, VIX option chains accessible — unblocks Agent Beta iron condors.
- (+) Section 1256 tax treatment: 60/40 long/short-term regardless of holding period.
- (+) European-style exercise: zero early-assignment risk on index options.
- (+) Cash settlement: no share delivery or margin call on expiration.
- (-) IB Gateway is a persistent process (~373 MB) requiring daily restart at
  23:45 ET (IBC handles this automatically via `AutoRestartTime=23:45`).
- (-) ib_insync is async/event-driven vs Alpaca's synchronous REST — `broker.py`
  must manage connect/disconnect lifecycle across cron invocations.
- (-) IBKR IP whitelist is a one-time manual setup step not captured in code.
- (-) Credential security: `IBKR_PASSWORD` must never appear in logs, ps, or
  systemctl output. Enforced via IBC wrapper + `EnvironmentFile` pattern.

**Security posture:**
- Password stored in `/home/trader/QuantAI/.env`, injected at runtime via
  `/opt/ibc/quantai_gateway_start.sh` using systemd `EnvironmentFile` directive.
- `config.ini` has blank `IbPassword=` intentionally; password injected via
  `--pw` arg into a 0600 temp copy of `gatewaystart.sh` at runtime.
- **NEVER run `systemctl status ibgateway` or `ps aux`** — use `systemctl is-active ibgateway`.

---

## ADR-005: Agent Beta — regime-driven, IBKR-native autonomous trader
**Date:** 2026-04-27
**Status:** Accepted — implemented and live in paper.

**Context.** Agent Alpha already runs end-to-end on the v2 pipeline (scan → debate → execute → journal) for ETF defined-risk spreads. The strategy spec for an additional agent (per the Beta specification document) calls for a fundamentally different shape: regime-driven, deterministic, zero-LLM, targeting native SPX/XSP/VIX index options for Section 1256 60/40 tax treatment, European exercise, and cash settlement. ADR-004 made this possible by adding IBKR support; this ADR formalizes how Beta is wired into the system.

**Decision.** Beta is built as a separate pipeline running parallel to Alpha:

- **Own cron entry** (`*/15 13-20 * * 1-5 beta_agent.py`) — Beta failures don't block Alpha.
- **Shared journal** (`trades.jsonl`) with `source == "agent_beta"` and `B###` IDs (per-prefix counters so Alpha's `A###` series is unaffected).
- **Shared broker adapter** (`broker.get_broker()` from ADR-004). Beta refuses to run if `BROKER_TYPE != ibkr` since its strategies require native index options.
- **Independent risk gates** scoped to `source == agent_beta` only (max 3 open, 2 entries/day, 5-loss circuit breaker, daily/weekly drawdown halts, correlation gates).
- **Per-trade `exit_rules`** stored on each journal entry. `position_monitor.py` consults `exit_rules` first when present (Beta path); otherwise falls through to the existing Alpha 4-rule logic. The 3:30 PM ET hard close still applies to all trades.
- **Zero-LLM**: regime classification, strategy selection, strike selection, and risk checks are all pure Python. ClawRoute / `_llm_client` is unused on the Beta path.
- **Twelve regimes × eight strategies**: HALT, CRISIS, MEAN_REVERSION_OVERBOUGHT/OVERSOLD, HIGH_VOL, SQUEEZE, PRE_EVENT, TREND_UP/DOWN, LOW_VOL, RANGE, NORMAL → mapped to event_strangle, call/put_ratio_backspread, broken_wing_butterfly, vix_calls, debit_spread, calendar_spread, credit_spread_offset.

**Consequences.**

- Two agents coexist on one broker, one journal, one account. Risk budgets are independent.
- Existing readers (sheets sync, dashboard, lib_errors) are journal-schema-agnostic; new Beta fields (`regime_at_entry`, `regime_data`, `exit_rules`, `simulated_slippage`, `net_delta`, `net_vega`) are additive — readers use `.get()`.
- `position_monitor.py` gains new exit rule types (valley_danger, weekend_close, scale_out, time_exit_dte, etc.) but Alpha behavior is unchanged.
- Beta requires `BROKER_TYPE=ibkr`; if that env var ever flips back to alpaca, Beta short-circuits with a clear log message and no trades are placed.
- Finnhub free tier limits the initial seed of `event_moves.json` to ~3 weeks of history; the weekly seeder (`0 6 * * 0`) accumulates the rolling 8-event window over time.
- IV Rank is currently a 21-day realized-vol percentile proxy (extracted from `scan_options.get_iv_rank`); upgrade to true chain-derived IV is on the deferred list.

**Reference:** Full design + phase-by-phase build log in `docs/2026-04-26-agent-beta-implementation-plan-v2.md`. Ten phases were committed in sequence on `feature/ibkr-broker-adapter` between 2026-04-26 and 2026-04-27.
