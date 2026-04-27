# Plan: Agent Beta Implementation

> **HISTORICAL — SUPERSEDED 2026-04-27** by `docs/2026-04-26-agent-beta-implementation-plan-v2.md`. This version assumed Alpaca SPY/VXX proxies because index options were thought unavailable. The IBKR broker adapter shipped 2026-04-26 (ADR-004 phase 3) and is verified for native XSP/SPX/VIX chains, so Beta now targets index options directly. Kept for the audit trail.

## Context

Agent Beta is a new autonomous options trading agent that coexists with Agent Alpha (the current v2 pipeline). Per the spec, Beta is **regime-driven, deterministic, zero-LLM**: every cycle it reads market data, classifies the current regime (one of 12), selects an appropriate strategy (one of 8), validates against risk rules, and submits a multi-leg order. It introduces a step-change in sophistication — long-vol strategies (event strangles, ratio backspreads), broken-wing butterflies, calendar spreads, VIX hedges — alongside Alpha's existing equity-credit-spread approach.

**The spec is the authoritative source.** Section 1-13 of the user's prompt define every entry condition, strike-selection algorithm, exit rule, and journal field. This plan does not relitigate strategy logic — it's purely about how to wire Beta into the existing codebase without breaking Alpha.

**What Beta needs that doesn't exist today** (from cross-referencing spec § 3 against the current `market_intelligence.py`):

| Data Beta needs | Status today | Phase |
|---|---|---|
| ADX(14) for SPX | ❌ missing | 1 |
| BB width percentile (126d) | ❌ have `bb_width` decimal, not percentile | 1 |
| IV Rank (252d) | ⚠️ `get_iv_rank()` exists in `scan_options.py` but isn't surfaced in `market_intelligence.json` | 1 |
| Put-call skew (25-delta) | ❌ missing | 1 |
| VIX 1-day change for emergency override | ❌ missing | 1 |
| SPX ATM straddle price + implied move % | ❌ missing | 1 |
| SPX ATM bid-ask spread (microstructure) | ❌ missing | 1 |
| `vix`, `vix_3m`, `vix_term_structure` | ✅ already in `market_intelligence.json` | — |
| FOMC/CPI/NFP dates + days_away | ✅ already in market_intelligence.json | — |
| RSI/MACD/EMA20/EMA50 for SPX | ✅ already calculated for symbol universe (need SPX added if not there) | 1 |
| Event move database (historical % moves) | ❌ no file exists | 2 |

**Existing infrastructure Beta will reuse, not rebuild:**
- `autonomous_execution.py:place_mleg_order()` — order placement (lines 267-309)
- `autonomous_execution.py` journal write path → `/root/quantai-v2/shared-data/journal/paper/trades.jsonl`
- `position_monitor.py` core close-order logic (single-leg fallback, market-hours guard, retry cap, broker-already-closed recovery — all from earlier sessions)
- `_logger.py` for structured error logging
- `_discord.py` for bot-token alerts
- `lib_errors.resolve_catalog()` for closing the loop on fixed errors
- The dashboard's existing Agents tab (`/var/dashboard/index.html:268-396`) — Beta card already exists at lines 299-325 (currently mis-described as iron-condor; we replace its content)

**Confirmed decisions (from review + live probe):**
1. **Index options** → **PROBED LIVE 2026-04-26**: All 5 index symbols (XSP, SPXW, SPX, VIX, MXSP) returned `HTTP 422 invalid underlying symbols` from Alpaca paper. Only equity/ETF options work. **Final INSTRUMENT_MAP**: `{"SPX": "SPY", "XSP": "SPY", "VIX": "VXX"}`. SPY = 1/10 SPX notional (matches XSP equivalence). VXX is the chosen VIX proxy; UVXY is a fallback (also has options); SVXY/VIXY have no active contracts. Document as ADR-004 with the implications below.

   **Implications of using SPY/VXX proxies** (must inform strategy modules):
   - **American-style early assignment risk**: ratio backspreads' short ATM legs can be assigned early on dividend days. Monitor short-leg delta; close if pushed deep ITM. Paper trading rarely simulates early assignment, so this is mostly a live-trading concern documented for migration.
   - **Share settlement**: SPY options settle in shares not cash. Strict adherence to `time_exit_dte ≥ 1` (close before expiry) is non-negotiable to avoid stock positions.
   - **VXX decay**: VXX rolls VIX futures daily, drifts down in contango. "VIX < 20" threshold doesn't translate 1:1 — Phase 4 vix_calls module needs proxy-tuned thresholds.
   - **Notional sizing**: position sizing math uses standard 100x equity-option multiplier (no change from existing code).
2. **Pipeline integration** → New separate cron entry, parallel to Alpha. Beta crashes don't block Alpha. Both read shared `market_intelligence.json`, write to shared `trades.jsonl`.
3. **Exit rules location** → On the journal entry (`exit_rules` field). Existing readers (Alpha, sheets sync, dashboard) use `.get()` so they ignore extra fields.
4. **Trade ID prefix** → Conditional in `autonomous_execution.py`: `B###` for `source=='agent_beta'`, `A###` for everyone else. Counters maintained separately per agent.

---

## Reality checks (verified during exploration)

- **Alpaca paper does not currently support index options** in active code: 78 tickers in `scan_options.py:71-92` are all equities/ETFs. No SPX/SPXW/XSP/VIX in any executor function. Existing equity-options OCC builder at `autonomous_execution.py:148` (`build_occ_symbol`) would need extension for index symbol formats.
- **`source: "agent_beta"` already exists** in current code (`autonomous_execution.py:650`). It's assigned for the iron_condor strategy. Beta-spec strategies redefine what "agent_beta" means.
- **Trade ID at line 555**: `f"A{len(existing)+1:03d}"` — applies to all agents. Hardcoded prefix.
- **Dashboard Beta card (lines 299-325) describes iron condors** — must be replaced with regime-driven strategy list.
- **`position_monitor.py` exit logic is hardcoded** (lines 270-297) — 4 rules baked in (hard close 3:30, expiry, stop loss, profit target). No per-trade rules. Beta's spec needs additional rule types stored on trade.
- **`market_intelligence.py` output** at `/root/quantai-v2/shared-data/cache/market_intelligence.json`, 90-min cache, callable as a script (writes file). Adding new fields is additive.
- **Constants in autonomous_execution**: `MAX_LOSS_PCT=2`, `MAX_OPEN=3`, `EARNINGS_BLACKOUT=14`, `MIN_CREDIT=0.30`, `VIX_HALT=35`. These apply to ALL trades. Beta's stricter risk rules (circuit breaker on 5 losses, daily/weekly drawdown halt, correlation check) will be Beta-specific.
- **`TradeProposal` is a god node** (23 edges per graphify). Beta must produce trade proposals in this same shape for downstream compatibility.

---

## Plan (13 phases, each a separate session unless explicitly combined)

### Phase 0 — Lock in INSTRUMENT_MAP + ADR (~20 min, combine with Phase 1)

Probe already done (see Reality checks above). Just need to formalize:

1. Create `v2/shared-data/scripts/beta/__init__.py` with the constant:
   ```python
   INSTRUMENT_MAP = {
       "SPX": "SPY",   # 1/10 SPX notional, american-style equity option
       "XSP": "SPY",   # XSP already 1/10 SPX; SPY is the equivalent on Alpaca paper
       "VIX": "VXX",   # iPath Series B VIX futures ETF — has option chain
   }
   ```
2. Write **ADR-004** in `docs/00-ARCHITECTURE-DECISIONS.md` capturing the probe findings, the proxy choices, and the early-assignment / share-settlement / VXX-decay implications above.
3. Update `docs/quantai-knowledge.md` "Alpaca API gotchas" section with: "Index options (SPX/XSP/SPXW/VIX/MXSP) NOT supported on Alpaca paper as of 2026-04-26. Use SPY/QQQ/VXX equity options instead."

**Deliverable**: ADR-004 + INSTRUMENT_MAP constant available for import.

### Phase 1 — Extend `market_intelligence.py` with Beta's data needs (~2-3 hours)

Single file modification. All additions are additive — Alpha consumers use `.get()` and ignore new fields.

New fields to add to `market_intelligence.json` output:
- `spx_price` (yfinance `^GSPC`)
- `spx_rsi_14`, `spx_macd_signal`, `spx_ema_20`, `spx_ema_50`, `spx_ema_20_slope` — apply existing per-symbol logic to ^GSPC
- `spx_adx_14` — implement per spec (formula in spec § 3B)
- `spx_bb_width_percentile_126d` — implement per spec
- `spx_iv_rank` — extract `get_iv_rank()` from `scan_options.py` into a shared helper, call for SPX (or proxy SPY)
- `spx_atm_straddle_price`, `spx_implied_move_pct` — query options chain for nearest weekly ATM call+put, mid prices
- `spx_atm_bid_ask_spread` — same chain query
- `spx_put_call_skew` — query 25-delta put + 25-delta call IVs from chain, compute ratio
- `vix_1d_change` — `vix - vix_previous_close` (yfinance 2-day history)
- `vix_contango_pct` — `(vix3m - vix) / vix * 100` (already have vix3m)
- `event_within_3_days` — derived boolean from existing `*_days_away` fields

**Critical files modified**: `v2/shared-data/scripts/market_intelligence.py` only.

**Deliverable**: After running, `cat /root/quantai-v2/shared-data/cache/market_intelligence.json | jq '. | keys'` includes all new keys. Existing Alpha pipeline still runs (regression test: `pre_trade_check.py` should report 19/19 GO).

### Phase 2 — Event move database seeder (~1-2 hours)

New script: `v2/shared-data/scripts/beta/event_moves_seeder.py`.

- Hardcode (or fetch from Finnhub) historical FOMC/CPI/NFP/GDP dates over last 2 years.
- For each date, fetch SPX `(close - prev_close) / prev_close * 100` from yfinance — absolute value.
- Build the JSON shape from spec § 3D: `{"FOMC": {"moves": [...], "avg_8": ..., "last_updated": "..."}, ...}`.
- Write to `/root/quantai-v2/shared-data/cache/event_moves.json`.
- Cron entry: weekly Sunday morning refresh.

**Deliverable**: file populated with 8+ moves per event type, avg_8 computed. Beta can read this to compare current implied move % against historical.

### Phase 3 — Regime detector module + backtest (~2 hours)

New module: `v2/shared-data/scripts/beta/regime_detector.py`.

- Pure-Python `classify_regime(intel: dict) -> str` returning one of the 12 regime strings from spec § 4.
- Implements the priority chain literally — first match wins.
- `days_since_halt` tracked in a small persistent file `/root/quantai-v2/shared-data/cache/beta_regime_state.json` (most-recent HALT timestamp).
- A standalone backtest harness: replay last 90 days of market_intelligence.json snapshots (or rebuild from yfinance) and emit regime sequence. Sanity check: HALT should fire on known vol-spike days (e.g., Aug 5 2024 if data goes back); SQUEEZE should be rare; NORMAL should dominate.
- Outputs to `/var/dashboard/state/agent-beta-state.json` (regime + reason + history) for dashboard.

**Deliverable**: backtest log printed; current-day classification matches manual inspection of vix/iv_rank/adx values.

### Phase 4 — 8 strategy modules (~4-6 hours, can split into 2 sessions of 4 strategies each)

New directory: `v2/shared-data/scripts/beta/strategies/`. One file per strategy:

| File | Strategy | Spec section |
|---|---|---|
| `event_strangle.py` | Event Strangle | § 6 Strategy 1 |
| `call_ratio_backspread.py` | Call Ratio 1x2 | § 6 Strategy 2 |
| `put_ratio_backspread.py` | Put Ratio 1x2 | § 6 Strategy 3 |
| `broken_wing_butterfly.py` | BWB | § 6 Strategy 4 |
| `vix_calls.py` | VIX Calls | § 6 Strategy 5 |
| `debit_spread.py` | Debit Spread | § 6 Strategy 6 |
| `calendar_spread.py` | Calendar | § 6 Strategy 7 |
| `credit_spread_offset.py` | Credit Spread (theta offset, HIGH_VOL only) | § 6 Strategy 8 |

Each module exports the same interface:
```python
def can_enter(intel: dict, regime: str, journal: list) -> tuple[bool, str]:
    """Strategy-specific entry conditions. Returns (passed, reason)."""

def select_strikes(intel: dict, options_chain: dict) -> dict | None:
    """Returns leg specifications + estimated cost/credit, or None if no clean fit."""

def build_exit_rules(strikes: dict, intel: dict) -> dict:
    """Returns the exit_rules object that gets stored on the journal entry."""

def position_size(account_equity: float, max_risk: float, risk_pct: float) -> int:
    """Returns contract count; reuses a shared sizing helper."""
```

Each is pure-Python, deterministic, zero-LLM. Strike-selection algorithms are exactly as specified (no improvisation).

**Critical reuse**: existing options-chain fetcher in `autonomous_execution.py` (lines 192-256). May need to extract into a shared helper module `beta/_chain_helpers.py`.

**Deliverable**: each module unit-testable in isolation. `python3 -c "from beta.strategies.event_strangle import can_enter; print(can_enter(fake_intel, 'PRE_EVENT', []))"` works.

### Phase 5 — Risk engine (~1-2 hours)

New module: `v2/shared-data/scripts/beta/risk_engine.py`.

Implements spec § 5:
- `MAX_OPEN_POSITIONS=3`, `MAX_TRADES_PER_DAY=2`, `CIRCUIT_BREAKER_LOSSES=5`, `DRAWDOWN_HALT_DAILY=0.02`, `DRAWDOWN_HALF_SIZE_WEEKLY=0.05`.
- Single function `check_risk(new_trade, intel, account, journal) -> tuple[bool, str, dict]` — third element is the (possibly-modified) trade with `risk_pct` halved if weekly drawdown threshold crossed.
- Reads journal directly via the existing pattern (`open(JOURNAL).readlines() | json.loads`).
- Counts ONLY trades with `source == 'agent_beta'` for these checks (Beta's risk rules don't apply to Alpha).
- Correlation check via summing `net_delta` and `net_vega` from open Beta positions — those fields will be added to journal entries when Beta opens a trade.

**Deliverable**: unit-testable; ten or so synthetic journal scenarios cover the 6 risk paths.

### Phase 6 — Beta entry engine (~2-3 hours)

New file: `v2/shared-data/scripts/beta_agent.py` — the main Beta cron entry point.

Flow:
1. Load `market_intelligence.json` and `event_moves.json`.
2. `regime = regime_detector.classify_regime(intel)`.
3. Per spec § 4 regime-strategy mapping, pick primary strategy.
4. `risk_engine.check_risk(...)` → bail if blocked.
5. `strategy.can_enter(...)` → bail if any condition fails.
6. Fetch options chain for the chosen instrument (from `INSTRUMENT_MAP[underlying]`).
7. `strategy.select_strikes(...)` → if `None`, try secondary strategy; else bail.
8. `strategy.position_size(...)` → contract count.
9. Build the trade proposal dict (matching `TradeProposal` god-node shape so existing infra works).
10. Submit via reused `autonomous_execution.place_mleg_order()` with `source='agent_beta'`.
11. Journal write with full Beta schema (spec § 9): `regime_at_entry`, `event_type`, `regime_data`, `exit_rules`, `simulated_slippage`.
12. Update `/var/dashboard/state/agent-beta-state.json`.
13. Discord alert via `_discord.post_to_channel`.
14. Log via `_logger.setup('beta_agent')` for centralized error logger integration.

`--dry-run` flag: emits the trade proposal but doesn't place an order or write to journal.

**Deliverable**: dry-run produces a fully-formed trade proposal for the current regime; one happy-path cycle confirms end-to-end reach to Alpaca.

### Phase 7 — Order execution support edits (~1-2 hours)

Modify `v2/shared-data/scripts/autonomous_execution.py`:
- **Trade ID prefix**: at line 555, change to `prefix = "B" if source == "agent_beta" else "A"; new_id = f"{prefix}{count_for_prefix:03d}"` where `count_for_prefix` counts only trades sharing the same prefix.
- **`exit_rules` passthrough**: when writing journal entry, include `trade.get("exit_rules", {})` as a top-level field.
- **Index options support** (conditional on Phase 0 outcome): if `INSTRUMENT_MAP` resolves to SPXW, extend `build_occ_symbol()` to handle the SPXW format. If proxies, no change needed.
- **Slippage simulation** per spec § 7 — add `simulate_slippage()` helper, call on every execution, log to journal entry.

These changes are surgical. Existing Alpha trades still get `A###` and continue to work unchanged.

**Deliverable**: regression test: trigger a synthetic Alpha trade in dry-run, confirm it gets `A###`. Trigger a Beta proposal in dry-run, confirm it gets `B001`. Both have `exit_rules` field present (Alpha's empty `{}`, Beta's populated).

### Phase 8 — Position monitor extensions (~2-3 hours)

Modify `v2/shared-data/scripts/position_monitor.py`:
- Replace hardcoded `check_exit_threshold()` logic with: **first** consult `trade.get("exit_rules")` if present, **else** fall through to the existing 4-rule logic (Alpha compat).
- Implement the new exit rule types from spec § 8: `valley_danger`, `delta_exit`, `post_event`, `weekend_close`, `scale_out_at` (with peak-tracking + trailing stop), `trend_reversal`.
- For `scale_out` + `trailing_stop`, persist `peak_pnl_pct` and `scaled_at_*` flags by writing them back to the journal entry on each cycle (atomic rewrite — already supported).
- Beta-specific data needs in monitor: net_delta, net_vega, current xsp_price, ADX, EMA — read from `market_intelligence.json` (already cached; no extra fetch).

The existing improvements stay (single-leg fallback, market-hours guard, MAX_CLOSE_ATTEMPTS=5, broker-already-closed recovery, code-resolve via lib_errors). These apply to Beta automatically.

**Deliverable**: feed the monitor a fake Beta trade with each exit_rule type, confirm correct exit decision. Alpha trades behavior unchanged.

### Phase 9 — Dashboard updates (~1-2 hours)

Two changes:
1. **New collector**: `/var/dashboard/collect_beta.py` (cron every minute). Reads journal + Beta state file + account, computes win rate / avg R:R / drawdown / circuit-breaker status / open Beta positions / current regime. Writes `/var/dashboard/state/agent-beta-state.json` per spec § 10.
2. **Update `/var/dashboard/index.html` Agents tab** (lines 299-325): replace the iron-condor description with the regime-driven Beta layout. Show: current regime + reason, active strategy, open positions vs max, today's trades, win rate, avg R:R, daily/weekly P&L, circuit breaker status.

**Deliverable**: dashboard renders a new Beta card. State JSON updates within 1 min of Beta running.

### Phase 10 — Pipeline integration (~30 min)

- Add cron entry: `*/15 13-20 * * 1-5  python3 /home/trader/QuantAI/v2/shared-data/scripts/beta_agent.py >> /root/quantai-v2/shared-data/logs/beta.log 2>&1`
- Add cron entry for the Beta dashboard collector: `* * * * * python3 /var/dashboard/collect_beta.py 2>/dev/null`
- Add cron entry for event_moves_seeder: `0 6 * * 0  python3 /home/trader/QuantAI/v2/shared-data/scripts/beta/event_moves_seeder.py >> /root/quantai-v2/shared-data/logs/beta.log 2>&1` (Sundays 6 AM UTC)
- Add `beta.log` to the textfile ingestor list in `/var/dashboard/collect_errors.py`
- Add `beta.log` to logrotate config

**Deliverable**: `crontab -l | grep beta` shows 3 entries. Beta logs are captured by the centralized error logger.

### Phase 11 — Integration test (~1-2 hours)

Run `beta_agent.py --dry-run` for each of the 12 regimes by mocking `market_intelligence.json` to force each regime in turn:
- Each regime triggers the right primary strategy
- Strike selection succeeds for the regime's typical conditions
- Exit rules are well-formed
- Risk checks correctly block when limits would be hit (e.g., 6 consecutive losses → CIRCUIT_BREAKER blocks)

Walk Alpha through one cycle in parallel — confirm it still runs, still produces trade IDs `A###`, still writes journal entries the dashboard understands.

**Deliverable**: integration_test_log.md showing all 12 regime branches walked + Alpha-still-works confirmation.

### Phase 12 — Go live in paper (~ongoing)

- Enable Beta cron Monday morning before market open.
- Watch first 5 trading days closely:
  - Discord alerts make sense (no flood)
  - Trades land in journal with proper `B###` IDs
  - Dashboard Beta card updates correctly
  - position_monitor closes Beta trades using exit_rules
  - Centralized error logger flags any new Beta-specific failure modes
- Daily review: regime accuracy, strategy fit, any unexpected behaviors.
- After 30 days: revisit the spec § 13 validation milestones.

---

## Critical files

### New
| Path | Purpose | Phase |
|---|---|---|
| `v2/shared-data/scripts/beta/__init__.py` | INSTRUMENT_MAP constant + shared imports | 0 |
| `v2/shared-data/scripts/beta/regime_detector.py` | classify_regime() + backtest harness | 3 |
| `v2/shared-data/scripts/beta/risk_engine.py` | check_risk() with circuit breaker / drawdown / correlation | 5 |
| `v2/shared-data/scripts/beta/event_moves_seeder.py` | Builds historical event move database | 2 |
| `v2/shared-data/scripts/beta/_chain_helpers.py` | Extracted options-chain query helpers | 4 |
| `v2/shared-data/scripts/beta/strategies/event_strangle.py` | Strategy 1 | 4 |
| `v2/shared-data/scripts/beta/strategies/call_ratio_backspread.py` | Strategy 2 | 4 |
| `v2/shared-data/scripts/beta/strategies/put_ratio_backspread.py` | Strategy 3 | 4 |
| `v2/shared-data/scripts/beta/strategies/broken_wing_butterfly.py` | Strategy 4 | 4 |
| `v2/shared-data/scripts/beta/strategies/vix_calls.py` | Strategy 5 | 4 |
| `v2/shared-data/scripts/beta/strategies/debit_spread.py` | Strategy 6 | 4 |
| `v2/shared-data/scripts/beta/strategies/calendar_spread.py` | Strategy 7 | 4 |
| `v2/shared-data/scripts/beta/strategies/credit_spread_offset.py` | Strategy 8 | 4 |
| `v2/shared-data/scripts/beta_agent.py` | Main Beta cron entry point | 6 |
| `/var/dashboard/collect_beta.py` | Per-cycle Beta dashboard state collector | 9 |
| `/var/dashboard/state/agent-beta-state.json` | Beta state for dashboard (auto-generated) | 9 |
| `/root/quantai-v2/shared-data/cache/event_moves.json` | Historical event move database (auto-generated) | 2 |
| `/root/quantai-v2/shared-data/cache/beta_regime_state.json` | days_since_halt tracker (auto-generated) | 3 |
| `docs/00-ARCHITECTURE-DECISIONS.md` (append ADR-004) | Index option choice | 0 |

### Modified
| Path | Change | Phase |
|---|---|---|
| `v2/shared-data/scripts/market_intelligence.py` | Add 11 new SPX-related fields | 1 |
| `v2/shared-data/scripts/autonomous_execution.py` | Conditional B### trade ID, exit_rules passthrough, slippage simulation, INSTRUMENT_MAP support | 7 |
| `v2/shared-data/scripts/position_monitor.py` | Per-trade exit_rules consultation; new exit rule types (valley/delta/post-event/weekend/scale-out/trailing-stop) | 8 |
| `/var/dashboard/index.html` Agents tab (lines 299-325) | Replace stale Beta card content with regime-driven layout | 9 |
| `/var/dashboard/collect_errors.py` | Add `beta.log` to textfile ingestor list | 10 |
| `/etc/logrotate.d/quantai-errors` | Add beta.log rotation | 10 |
| Root crontab | 3 new entries (beta_agent every 15 min, collect_beta every min, event_moves_seeder weekly) | 10 |
| `docs/quantai-knowledge.md` | Document Beta architecture, INSTRUMENT_MAP decision | 0, 12 |

---

## Decision points (resolved)

1. ✅ **Instruments**: probe SPXW via Alpaca paper first (Phase 0), fall back to SPY/QQQ/VXX proxies if rejected
2. ✅ **Pipeline integration**: separate cron entry, parallel to Alpha
3. ✅ **Exit rules location**: on the journal entry (`exit_rules` field)
4. ✅ **Trade ID prefix**: conditional `B###` for `agent_beta`, `A###` for everyone else

**Decisions I'll make unilaterally** unless you push back:
- **One agent module path**: `v2/shared-data/scripts/beta/` directory (matches existing convention of flat scripts but groups Beta files together).
- **Logging convention**: Beta uses `_logger.setup('beta_agent')` and similar per-module names — same pattern as heartbeat_monitor / position_monitor / autonomous_execution.
- **Backtest depth**: Phase 3's regime backtest uses 90 days (matches spec § 12 "Validate detection on last 90 days").
- **No graphify update during Beta build** — graphify is a code-relationship snapshot, will refresh after a successful Phase 12 go-live.
- **Discord alerts via existing bot-token + DISCORD_CHANNEL_ALERTS** — same channel as everything else. No new Beta-specific channel.

**Open questions** (non-blocking; can be deferred to mid-build):
- Section 13 validation milestones say "30 days running" and "50 trades" — at the spec's strategy frequency (Event Strangle only fires near events; ratio backspreads only in clear trends), 50 trades may take 2-3 months. **Calibration suggestion**: track these as soft targets; don't let them block parameter tuning during the first 30 days if obvious issues surface.
- The spec mentions a "Phase G (live evaluation)" milestone — out of scope; this plan stops at paper-trading go-live.

---

## Verification (end-to-end)

Per-phase verification is in each phase. Final end-to-end checks after Phase 12:

1. **Beta + Alpha run independently**: Both write to `trades.jsonl`. `grep "A0" trades.jsonl | wc -l` and `grep "B0" trades.jsonl | wc -l` both produce nonzero counts after a week. Neither blocks the other.
2. **Regime accuracy**: Spot-check 5 random trades; the `regime_at_entry` should make sense given the market data on that day.
3. **Exit rules fire**: At least one Beta trade closes via a Beta-specific rule (valley_danger, delta_exit, post_event, scale_out, etc.) — confirms the new monitor logic works.
4. **No Alpha regression**: `pre_trade_check.py` reports 19/19 GO. Alpha continues placing iron condors, etc. as before.
5. **Centralized error logger**: any Beta-specific failure mode (e.g., index option chain unavailable, regime classifier exception) shows in the dashboard ErrorsTab with proper severity, no flood.
6. **Dashboard**: Beta card shows current regime + reason + open positions + today's P&L. Updates within 1 min of Beta cron firing.
7. **Backtest sanity**: Regime distribution over 90 days looks plausible — NORMAL dominant, HALT only on real vol-spike days, SQUEEZE rare, RANGE/TREND_UP/TREND_DOWN distributed roughly evenly.
8. **Risk gates work**: Trigger circuit breaker (synthetic 5 losses), confirm next entry blocked. Trigger daily drawdown halt, same. Confirm normal entry resumes after time/account recovery.

---

## What this plan does NOT do

- Does not change Alpha's strategy logic, risk rules, or trade selection (Alpha's iron condor / debit spread paths untouched).
- Does not implement live trading — paper only, hardcoded.
- Does not retire any existing infrastructure (orchestrator, error_learner, ClawRoute, etc.).
- Does not modify the journal schema in a non-additive way (existing readers continue working with `.get()`).
- Does not introduce new heavyweight dependencies — stays within Python stdlib + existing `requests`/`yfinance`/`sqlite3`.
- Does not implement spec § 13's "Phase G live evaluation" — stops at paper-trading go-live.
- Does not auto-tune any strategy parameters — the spec's defaults are taken literally.
- Does not run a hidden mock-broker for testing strikes — uses real Alpaca paper for all order paths from Phase 6 onward.
- Does not create separate dashboard tabs or pages for Beta — extends the existing Agents tab.

---

## Effort estimate (focused work hours)

| Phase | Hours | Session count |
|---|---|---|
| 0: Probe + ADR | 0.5 | 1 (combine with P1) |
| 1: Data pipeline | 2-3 | 1 |
| 2: Event move seeder | 1-2 | 1 (combine with P3) |
| 3: Regime detector + backtest | 2 | 1 |
| 4: 8 strategy modules | 4-6 | 2 |
| 5: Risk engine | 1-2 | 1 (combine with P6) |
| 6: Beta entry engine | 2-3 | 1 |
| 7: autonomous_execution mods | 1-2 | 1 (combine with P8) |
| 8: position_monitor mods | 2-3 | 1 |
| 9: Dashboard | 1-2 | 1 |
| 10: Pipeline integration | 0.5 | 1 (combine with P11) |
| 11: Integration test | 1-2 | 1 |
| 12: Go-live monitoring | ongoing | continuous |

**Total**: ~20-30 hours of focused work, ~6-9 sessions if some are combined as suggested. Calendar time: 2-3 weeks at a few sessions per week.
