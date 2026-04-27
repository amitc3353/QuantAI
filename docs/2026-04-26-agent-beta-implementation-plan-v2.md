# Plan: Agent Beta Implementation (IBKR-Native)

## Context

Agent Beta is a new autonomous options trading agent that will coexist with Agent Alpha in the QuantAI v2 pipeline. Per the user spec, Beta is **regime-driven, deterministic, zero-LLM**: every cycle it reads market data, classifies the current regime (one of 12), selects a strategy (one of 8), validates against risk rules, and submits a multi-leg order via `broker.py`. Strategies span long-vol (event strangles, ratio backspreads), broken-wing butterflies, calendars, and VIX hedges — a step-change beyond Alpha's iron-condor-and-spreads playbook.

**Why now, why this shape:**
- The IBKR broker adapter (`broker.py` + `_broker_ibkr.py`) is **verified working** on XSP/SPX/VIX chains as of 2026-04-26 (ADR-004 phase 3 just landed). The old proxy plan (SPY/VXX) is obsolete — Beta can target true index options natively.
- Beta's strategies require Section 1256 60/40 tax treatment + European/cash-settled mechanics, which only SPX/XSP/VIX provide. ETF proxies were always a workaround.
- A prior plan doc (`docs/2026-04-26-agent-beta-implementation-plan.md`) was written **before** the IBKR work and bakes in SPY/VXX proxies. It will be marked historical, superseded by this plan.

**Big architectural shift:** With user approval, the **entire pipeline migrates to BROKER_TYPE=ibkr**. Alpha runs on IBKR too, not just Beta. This requires re-verifying Alpha's iron-condor flow on IBKR before Beta goes live (Phase 0).

---

## What can be reused vs new

### Reused (no changes)
- `broker.py` / `_broker_ibkr.py` — order submission, chain fetch, positions, account. Beta calls `get_broker()` like Alpha.
- `_logger.py` — structured logging to dashboard SQLite. Beta calls `setup("beta_agent")` per script.
- `_llm_client.py` — ClawRoute shim. Only needed if Beta wants LLM interpretation (not required by spec).
- `_discord.py` — bot-token alert poster. Same channel as Alpha (`DISCORD_CHANNEL_ALERTS`).
- `lib_errors.resolve_catalog()` — close-the-loop on fixed errors (used by position_monitor).
- Journal at `/root/quantai-v2/shared-data/journal/paper/trades.jsonl` — schema is agent-agnostic; `source: "agent_beta"` distinguishes from Alpha. Existing readers use `.get()`, so adding `exit_rules`, `regime_at_entry`, `regime_data`, `simulated_slippage` is non-breaking.
- Dashboard frontend (`/var/dashboard/index.html`) already has Beta card scaffolding (lines 299-325 with `sourceFilter: s => s.startsWith("agent_beta")`). Card content needs replacement, no JS rewrite.
- `market_intelligence.json` cache structure — additive.
- Finnhub event calendar already cached via `market_intelligence.py`.

### Reused with small extensions
- `autonomous_execution.py` — Beta calls `place_mleg_order` here (which itself calls `broker.get_broker()`). Three surgical edits (Phase 7).
- `position_monitor.py` — Hardcoded 4-rule exit logic gets a "consult `trade["exit_rules"]` first" prefix; Alpha trades fall through unchanged (Phase 8).
- `market_intelligence.py` — Add 11 new SPX-derived fields (Phase 1). All additive.

### New
- `v2/shared-data/scripts/beta/` package — regime detector, risk engine, 8 strategy modules, chain helpers, event-moves seeder.
- `v2/shared-data/scripts/beta_agent.py` — main cron entry point.
- `/var/dashboard/collect_beta.py` — per-cycle dashboard state collector.
- Cache files: `event_moves.json`, `beta_regime_state.json`, `agent-beta-state.json`.

---

## Phases (10, each ≈1 session)

### Phase 0 — IBKR migration of Alpha + supersede old plan (~1-2h)
The whole pipeline flips to IBKR before Beta ships. Beta is blocked until Alpha proves green on IBKR.

1. Set `BROKER_TYPE=ibkr` in `/home/trader/QuantAI/.env` (and `/root/quantai-v2/.env` if it exists).
2. Run full verification suite (mirroring last session's IBKR verify):
   - `sudo BROKER_TYPE=ibkr python3 system_test.py` → expect 43/43
   - `sudo BROKER_TYPE=ibkr python3 pre_trade_check.py` → expect 19/19 GO
   - `sudo BROKER_TYPE=ibkr python3 autonomous_execution.py --check-only` → expect clean
   - `sudo BROKER_TYPE=ibkr python3 position_monitor.py --dry-run` → expect clean
3. **Synthetic Alpha trade dry-run on IBKR**: feed a fake debate output with an iron condor on a real IBKR-supported underlying (e.g., SPY equity option chain still works on IBKR; or a real index option). Confirm `place_mleg_order` builds the right Bag and returns a proper `order_id`. No real submission.
4. Watch one full pipeline cycle in dry-run mode (cron disabled) — confirm Alpha continues to function on IBKR.
5. Mark `docs/2026-04-26-agent-beta-implementation-plan.md` historical with a header note: `> SUPERSEDED by docs/2026-04-26-agent-beta-implementation-plan-v2.md (post-IBKR migration). Old proxy approach obsolete.`
6. Write the new plan to `docs/2026-04-26-agent-beta-implementation-plan-v2.md` (mirror of this file).

**Deliverable:** Alpha verified on IBKR with no regressions; old plan flagged historical.

### Phase 1 — Extend `market_intelligence.py` with Beta's data needs (~2-3h)
Single-file modification, additive. New fields:
- `spx_price` (yfinance `^GSPC` close)
- `spx_rsi_14`, `spx_macd_signal`, `spx_ema_20`, `spx_ema_50`, `spx_ema_20_slope` — apply existing per-symbol logic
- `spx_adx_14` — implement per spec § 3B formula
- `spx_bb_width_percentile_126d` — implement per spec § 3B formula
- `spx_iv_rank` — extract `get_iv_rank()` from `scan_options.py` into a shared helper; call for SPX
- `spx_atm_straddle_price`, `spx_implied_move_pct` — query nearest weekly ATM call+put via `broker.fetch_option_chain("SPX", dte_range=(0, 7), include_quotes=True)`
- `spx_atm_bid_ask_spread` — same chain query
- `spx_put_call_skew` — query 25-delta put + 25-delta call IVs from chain; compute ratio
- `vix_1d_change` — `vix - vix_previous_close` (yfinance 2-day history)
- `vix_contango_pct` — `(vix3m - vix) / vix * 100`
- `event_within_3_days` — derived boolean from existing `*_days_away` fields

**Critical files modified:** `v2/shared-data/scripts/market_intelligence.py` only.

**Deliverable:** `cat /root/quantai-v2/shared-data/cache/market_intelligence.json | jq 'keys'` includes all new keys; `pre_trade_check.py` still 19/19.

### Phase 2 — Event move database seeder (~1-2h)
New script `v2/shared-data/scripts/beta/event_moves_seeder.py`:
- Hardcode known FOMC/CPI/NFP/GDP dates from the last 2 years (or fetch from Finnhub historical endpoint).
- For each, fetch SPX `(close - prev_close) / prev_close * 100` from yfinance — absolute.
- Build the spec § 3D shape and write `/root/quantai-v2/shared-data/cache/event_moves.json`.
- Cron entry: weekly Sunday 06:00 UTC refresh (Phase 9).

**Deliverable:** `event_moves.json` populated with ≥8 moves per event type, `avg_8` computed.

### Phase 3 — Regime detector + 90-day backtest (~2h)
New `v2/shared-data/scripts/beta/regime_detector.py`:
- Pure-Python `classify_regime(intel: dict) -> str`. Implements the spec § 4 priority chain literally — first match wins.
- `days_since_halt` tracked in `/root/quantai-v2/shared-data/cache/beta_regime_state.json` (most recent HALT timestamp + decremented daily).
- Standalone backtest harness: replay last 90 days reconstructed from yfinance — print regime sequence. Sanity check distribution: NORMAL dominant, HALT only on real vol-spike days.
- Writes current regime + reason to `/var/dashboard/state/agent-beta-state.json`.

**Deliverable:** backtest log printed; current-day classification matches manual eyeball of vix/iv_rank/adx.

### Phase 4 — 8 strategy modules (~4-6h, can split across 2 sessions)
New directory `v2/shared-data/scripts/beta/strategies/`. One file per strategy with the same interface:

```python
def can_enter(intel: dict, regime: str, journal: list) -> tuple[bool, str]: ...
def select_strikes(intel: dict, broker, account_equity: float) -> dict | None: ...
def build_exit_rules(strikes: dict, intel: dict) -> dict: ...
def position_size(account_equity: float, max_risk: float, risk_pct: float) -> int: ...
```

| File | Strategy | Spec § |
|---|---|---|
| `event_strangle.py` | Event Strangle (SPX) | 6.1 |
| `call_ratio_backspread.py` | Call Ratio 1x2 (XSP) | 6.2 |
| `put_ratio_backspread.py` | Put Ratio 1x2 (XSP) | 6.3 |
| `broken_wing_butterfly.py` | BWB (XSP) | 6.4 |
| `vix_calls.py` | VIX Calls | 6.5 |
| `debit_spread.py` | Debit Spread (SPX) | 6.6 |
| `calendar_spread.py` | Calendar (SPX) | 6.7 |
| `credit_spread_offset.py` | Credit Spread (SPX, theta offset, HIGH_VOL only) | 6.8 |

**Each module:** pure-Python, deterministic, zero-LLM. Strike algorithms exactly per spec — no improvisation. Calls `broker.fetch_option_chain(symbol, dte_range, include_quotes=True)` for chain data — no direct REST. **Drop** all proxy-related caveats (early-assignment, share-settlement, VXX-decay) from prior plan — index options eliminate those concerns.

**Shared helper:** `v2/shared-data/scripts/beta/_chain_helpers.py` — `nearest_strike()`, `get_mid()`, `find_expiry_after()`, `find_nearest_expiry()`, OCC build helpers reused across strategies. Note `broker.py` already exports `_build_occ` and `_parse_occ` — reuse those rather than reimplementing.

**Deliverable:** each module unit-testable in isolation with synthetic chain fixtures.

### Phase 5 — Risk engine (~1-2h)
New `v2/shared-data/scripts/beta/risk_engine.py`. Implements spec § 5 verbatim:
- Constants: `MAX_OPEN_POSITIONS=3`, `MAX_TRADES_PER_DAY=2`, `CIRCUIT_BREAKER_LOSSES=5`, `DRAWDOWN_HALT_DAILY=0.02`, `DRAWDOWN_HALF_SIZE_WEEKLY=0.05`.
- `check_risk(new_trade, intel, account, journal) -> tuple[bool, str, dict]` — third element is the (possibly size-halved) trade.
- Counts only `source == 'agent_beta'` entries — Beta's risk rules don't apply to Alpha and vice versa.
- Correlation check sums `net_delta` and `net_vega` from open Beta positions (those fields populated at entry time by Phase 6).

**Deliverable:** unit-testable with ten synthetic journal scenarios covering all 6 risk paths.

### Phase 6 — Beta entry engine (~2-3h)
New `v2/shared-data/scripts/beta_agent.py` — main Beta cron entry point:

1. `_logger.setup("beta_agent")`.
2. Load `market_intelligence.json` and `event_moves.json`.
3. Refuse to run if `get_broker().name != "ibkr"` — log and exit cleanly. Beta's strategies require IBKR.
4. `regime = regime_detector.classify_regime(intel)`. Bail if HALT.
5. Per spec § 4 mapping, pick primary strategy (and secondary fallback).
6. `risk_engine.check_risk(...)` → bail if blocked, with reason.
7. `strategy.can_enter(...)` → bail if any condition fails.
8. `strategy.select_strikes(intel, broker, account_equity)` — calls `broker.fetch_option_chain(symbol, dte_range=..., include_quotes=True)`. If `None`, try secondary strategy; else bail.
9. `strategy.position_size(...)` → contract count.
10. Build the trade proposal dict (matching `TradeProposal` god-node shape so existing readers work).
11. Submit via reused `autonomous_execution.place_mleg_order()` with `source='agent_beta'`. (autonomous_execution itself uses `broker.get_broker()` already.)
12. Journal write with full Beta schema (spec § 9): `regime_at_entry`, `event_type`, `regime_data`, `exit_rules`, `simulated_slippage`, `net_delta`, `net_vega`.
13. Update `/var/dashboard/state/agent-beta-state.json` (Phase 9 collector also runs).
14. Discord alert via `_discord.post_to_channel`.

`--dry-run` flag emits the trade proposal but doesn't submit or journal.

**Deliverable:** dry-run produces fully-formed trade proposal for the current regime; one happy-path cycle reaches IBKR placeOrder.

### Phase 7 — `autonomous_execution.py` surgical edits (~1-2h)
Three changes:
- **Trade ID prefix**: at the existing ID generation site, change to `prefix = "B" if source == "agent_beta" else "A"; new_id = f"{prefix}{count_for_prefix:03d}"`. Counter scoped per prefix.
- **`exit_rules` passthrough**: when writing journal entry, include `trade.get("exit_rules", {})` as a top-level field.
- **Slippage simulation** per spec § 7 — add `simulate_slippage()` helper, call on every execution, log to journal entry.

No INSTRUMENT_MAP work — IBKR handles index options natively.

**Deliverable:** synthetic Alpha trade in dry-run still gets `A###`; synthetic Beta proposal gets `B001`; both have `exit_rules` field (Alpha's empty `{}`, Beta's populated).

### Phase 8 — `position_monitor.py` extensions (~2-3h)
- Wrap existing `check_exit_threshold()`: **first** consult `trade.get("exit_rules")` if present, else fall through to existing 4-rule logic (Alpha compat).
- Implement new exit rule types from spec § 8: `valley_danger`, `delta_exit`, `post_event`, `weekend_close`, `scale_out_at` (peak-tracking + trailing stop), `trend_reversal`, `time_exit_dte`, `take_profit_pct`, `stop_loss_pct`.
- For `scale_out` + `trailing_stop`, persist `peak_pnl_pct` and `scaled_at_*` flags by atomic journal rewrite (already supported).
- Read live data needed by Beta exit rules (net_delta, current xsp_price, ADX, EMA) from `market_intelligence.json` and `broker.get_positions()` — no extra fetch path needed.

Existing improvements (single-leg fallback, market-hours guard, MAX_CLOSE_ATTEMPTS=5, broker-already-closed recovery, lib_errors code-resolve) apply to Beta automatically.

**Deliverable:** synthetic Beta trade with each exit_rule type triggers correctly; Alpha trades behavior unchanged.

### Phase 9 — Dashboard updates (~1-2h)
1. **New collector** `/var/dashboard/collect_beta.py` (cron every minute):
   - Reads journal + Beta state file + account.
   - Computes: open Beta positions, today's trades, win rate, avg R:R, daily/weekly P&L, consecutive losses, circuit breaker status, current regime.
   - Writes `/var/dashboard/state/agent-beta-state.json` per spec § 10.
2. **Update `/var/dashboard/index.html` Agents tab** (lines 299-325): replace stale iron-condor description with regime-driven layout. Show: current regime + reason, active strategy, open positions vs max, today's trades, win rate, avg R:R, daily/weekly P&L, circuit breaker.

**Deliverable:** dashboard renders updated Beta card; state JSON updates within 1 min of Beta cron.

### Phase 10 — Cron, integration test, go-live (~2-3h, then ongoing)
1. Add cron entries (root crontab):
   ```
   */15 13-20 * * 1-5  python3 /home/trader/QuantAI/v2/shared-data/scripts/beta_agent.py >> /root/quantai-v2/shared-data/logs/beta.log 2>&1
   * * * * *           python3 /var/dashboard/collect_beta.py 2>/dev/null
   0 6 * * 0           python3 /home/trader/QuantAI/v2/shared-data/scripts/beta/event_moves_seeder.py >> /root/quantai-v2/shared-data/logs/beta.log 2>&1
   ```
2. Add `beta.log` to `/var/dashboard/collect_errors.py` ingestor list and to `logrotate.d` config.
3. **Integration test**: run `beta_agent.py --dry-run` for each of the 12 regimes by mocking `market_intelligence.json` to force each in turn. Confirm right strategy fires, strikes select cleanly, exit rules well-formed, risk gates trigger correctly.
4. **Alpha co-existence test**: walk Alpha through one cycle in parallel — confirm A### IDs, journal writes, dashboard render still work.
5. Enable Beta cron Monday morning. Watch closely for first 5 trading days.

**Deliverable:** integration test log; Beta live alongside Alpha on IBKR.

---

## Critical files

### New
- `v2/shared-data/scripts/beta/__init__.py`
- `v2/shared-data/scripts/beta/regime_detector.py`
- `v2/shared-data/scripts/beta/risk_engine.py`
- `v2/shared-data/scripts/beta/event_moves_seeder.py`
- `v2/shared-data/scripts/beta/_chain_helpers.py`
- `v2/shared-data/scripts/beta/strategies/{event_strangle,call_ratio_backspread,put_ratio_backspread,broken_wing_butterfly,vix_calls,debit_spread,calendar_spread,credit_spread_offset}.py`
- `v2/shared-data/scripts/beta_agent.py`
- `/var/dashboard/collect_beta.py`
- `docs/2026-04-26-agent-beta-implementation-plan-v2.md` (mirror of this plan)

### Modified
- `v2/shared-data/scripts/market_intelligence.py` — 11 new SPX fields (Phase 1)
- `v2/shared-data/scripts/autonomous_execution.py` — trade ID prefix, exit_rules passthrough, slippage sim (Phase 7)
- `v2/shared-data/scripts/position_monitor.py` — per-trade exit_rules consultation, new rule types (Phase 8)
- `/var/dashboard/index.html` — Agents tab Beta card content (Phase 9)
- `/var/dashboard/collect_errors.py` — add `beta.log` ingestor (Phase 10)
- `/etc/logrotate.d/quantai-errors` — add beta.log rotation (Phase 10)
- root crontab — 3 new entries (Phase 10)
- `/home/trader/QuantAI/.env` — `BROKER_TYPE=ibkr` (Phase 0)
- `docs/2026-04-26-agent-beta-implementation-plan.md` — header note marking historical (Phase 0)
- `docs/quantai-knowledge.md` — Beta architecture section (Phases 0 + 10)

### Auto-generated runtime files
- `/root/quantai-v2/shared-data/cache/event_moves.json`
- `/root/quantai-v2/shared-data/cache/beta_regime_state.json`
- `/var/dashboard/state/agent-beta-state.json`

---

## Reused functions / utilities (with paths)

- `broker.get_broker()` — `/home/trader/QuantAI/v2/shared-data/scripts/broker.py:576`
- `broker.fetch_option_chain(symbol, dte_range, strike_range, include_quotes)` — `broker.py:166` (BrokerBase)
- `broker.place_mleg_order(legs, qty, tif, client_order_id)` — `broker.py:181`
- `broker.close_position(legs, qty, client_order_id)` — `broker.py:189`
- `broker._build_occ(root, expiry, right, strike)` — `broker.py:115`
- `broker._parse_occ(occ)` — `broker.py:84`
- `_logger.setup(name)` — `/home/trader/QuantAI/v2/shared-data/scripts/_logger.py`
- `autonomous_execution.place_mleg_order(...)` — `/home/trader/QuantAI/v2/shared-data/scripts/autonomous_execution.py` (Beta calls this; it wraps broker)
- `scan_options.get_iv_rank(...)` — extract into shared helper in Phase 1
- `_discord.post_to_channel(...)` — existing Discord poster
- `lib_errors.resolve_catalog(...)` — used by position_monitor's close-the-loop

---

## Conflicts & ambiguities flagged

1. **Spec uses Alpaca payload shape (top-level `qty`, `ratio_qty`)** — `broker.py` accepts that shape and translates internally to IBKR Bag/ComboLeg. No spec change needed.
2. **Spec § 7 says "Use `paper-api.alpaca.markets/v2/orders` endpoint"** — overridden by user constraint (use broker.py). Spec language is descriptive of legacy; intent is clear.
3. **SPX vs SPXW disambiguation** — spec doesn't pick. IBKR adapter surfaces both via `reqSecDefOptParams`. Implementation choice: for short-DTE strategies (event strangle 3-7 DTE, debit spread 14-21 DTE), prefer SPXW (weekly); for ratio backspreads (21-30 DTE) and calendars (45-60 DTE long leg), fall through to whichever expiry is closest. Will document in Phase 4.
4. **Spec § 5 risk checks** mix Beta-only counts (5 consecutive losses, daily PnL) and account-wide counts. Plan: scope all risk-engine counts to `source == 'agent_beta'` only — Alpha and Beta have independent risk budgets. Spec is silent; this matches the "Alpha and Beta coexist" constraint.
5. **Spec § 6 Strategy 8 (credit spread offset)** has negative standalone EV — flagged in spec itself. Plan respects the warning: max 1 open at a time, never weekend, never through events, halved size. Track P&L separately in dashboard.
6. **Spec § 13 milestones** ("30 days running", "50 trades") may take 2-3 calendar months at strategy frequency. Treat as soft targets in Phase 10 monitoring; don't let them block parameter tuning.
7. **Position monitor Beta data freshness** — exit rules need ADX/EMA/net_delta refreshed at 2-min cadence, but `market_intelligence.json` refreshes at 90 min. **Mitigation**: position monitor reads cached intel for trend signals (acceptable lag for 2-week swing trades) but pulls live `broker.get_positions()` for net_delta. Document in Phase 8.

---

## Verification (end-to-end, after Phase 10)

1. **Alpha + Beta run independently on IBKR.** `grep '"id":"A0' trades.jsonl | wc -l` and `grep '"id":"B0' trades.jsonl | wc -l` both produce nonzero counts after a week. Neither blocks the other.
2. **Regime accuracy.** Spot-check 5 random Beta trades; `regime_at_entry` matches market data on that day.
3. **Exit rules fire.** At least one Beta trade closes via a new rule type (valley_danger, delta_exit, post_event, scale_out, etc.) within first 2 weeks.
4. **No Alpha regression.** `pre_trade_check.py` reports 19/19 GO daily. `system_test.py` reports 43/43 daily. Alpha continues placing iron condors / spreads on IBKR.
5. **Centralized error logger.** Any Beta-specific failure mode (e.g., chain unavailable, regime classifier exception) surfaces in dashboard ErrorsTab with proper severity, no flood.
6. **Dashboard.** Beta card renders current regime + reason + open positions + today P&L. Updates within 1 min of Beta cron.
7. **Backtest sanity.** Phase 3 90-day regime distribution: NORMAL dominant, HALT only on real vol-spike days, SQUEEZE rare.
8. **Risk gates work.** Trigger circuit breaker (synthetic 5 losses), confirm next entry blocked. Trigger daily drawdown halt, same. Confirm normal entry resumes after recovery.
9. **Per-phase verifications** are listed in each phase block above.

---

## Effort estimate

| Phase | Hours | Notes |
|---|---|---|
| 0: IBKR migration of Alpha + supersede old plan | 1-2 | Blocks everything else |
| 1: market_intelligence.py extensions | 2-3 | Single file |
| 2: Event move seeder | 1-2 | Combine with P3 |
| 3: Regime detector + backtest | 2 | |
| 4: 8 strategy modules | 4-6 | Split into 2 sessions |
| 5: Risk engine | 1-2 | Combine with P6 |
| 6: Beta entry engine | 2-3 | |
| 7: autonomous_execution edits | 1-2 | Combine with P8 |
| 8: position_monitor extensions | 2-3 | |
| 9: Dashboard | 1-2 | |
| 10: Cron + integration test + go-live | 2-3 | Then ongoing monitoring |

**Total:** ~20-30 focused hours, ~7-9 sessions if some are combined as suggested. Calendar: 2-3 weeks at a few sessions per week.

---

## What this plan does NOT do

- Does not change Alpha's strategy logic or risk rules — Alpha keeps its iron-condor / spread playbook.
- Does not implement live trading — paper only.
- Does not modify the journal schema in a non-additive way.
- Does not introduce new heavyweight dependencies — Python stdlib + existing `requests`/`yfinance`/`ib_insync`/`sqlite3`.
- Does not implement spec § 13 "Phase G live evaluation" — stops at paper go-live.
- Does not auto-tune any strategy parameters — spec defaults are taken literally.
- Does not create separate dashboard tabs for Beta — extends existing Agents tab.
- Does not touch Alpaca configuration — Alpaca remains a fallback adapter, but the system runs on IBKR.
