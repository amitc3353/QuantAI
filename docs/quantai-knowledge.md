# QuantAI Knowledge Base
Last updated: 2026-04-27 by Amit + Claude

## What QuantAI is
Autonomous options trading system. Paper trading via broker adapter (`BROKER_TYPE=ibkr` default; Alpaca paper fallback). $1M IBKR paper equity (account DUP851506). Two autonomous agents — **Alpha** (defined-risk ETF spreads, LLM-driven debate) and **Beta** (regime-driven SPX/XSP/VIX index options, deterministic, zero-LLM) — plus manual SOFI collar. Goal: $3-10k/month income at $150k deployed. Currently in paper trading validation phase.

## Architecture (what's actually running)

### Two coexisting systems
1. **v2 Pipeline (active, does the work):** Python cron at `/home/trader/QuantAI/v2/shared-data/scripts/`, runs every 15 min during market hours (9-16 ET Mon-Fri). Entry point: `run_pipeline.py`. Runtime data at `/root/quantai-v2/shared-data/`.
2. **docker-compose (legacy, unclear role):** `trader-orchestrator`, `trader-discord`, `trader-cto`, `trader-guards` containers. All "healthy" but trader-guards only serves /health pings. No evidence these containers do meaningful work. Reconciliation deferred — not causing harm.

### v2 Pipeline flow

**Agent Alpha** (LLM debate-driven, runs via `run_pipeline.py`):
```
Cron (every 15m) → run_pipeline.py
  → market_intelligence.py (VIX, prices, technicals via yfinance + SPX fields for Beta)
  → scan_options.py "all" (78 tickers × 4 strategy types — SLOW, 10-15 min)
  → debate_chamber.py (Bull/Bear/Judge LLM debate, produces approved trades)
  → autonomous_execution.py (builds mleg orders, submits via broker.place_mleg_order
    [IBKR by default], journals as A###)
  → sheets_sync.py (syncs journal to Google Sheets)
```

**Agent Beta** (regime-driven, runs via `beta_agent.py` on a separate cron):
```
Cron (every 15m) → beta_agent.py
  → load market_intelligence.json + event_moves.json
  → require BROKER_TYPE=ibkr (refuses otherwise)
  → regime_detector.classify_regime (12 regimes, first-match-wins)
  → pick primary strategy (+ fallback) from regime → strategy map
  → strategy.can_enter / risk_engine.check_risk / strategy.select_strikes via broker chain
  → broker.place_mleg_order (IBKR), journal as B### with full exit_rules
```

**Agent Gamma** (single-strategy Connors RSI(10) pullback, two-phase cron):
```
Cron (4:30 PM ET) → gamma_agent.py --scan
  → fetch ~252 daily bars × 27 symbols (4 indices + 3 ETFs + 20 stocks)
  → compute Wilder RSI(10) + SMA(200)
  → filters: trend (price > SMA200), oversold (RSI<30), 7-day earnings blackout, liquidity
  → write gamma_pending_entries.json + gamma_indicator_cache.json

Cron (9:33 AM ET, next morning) → gamma_agent.py --execute
  → require BROKER_TYPE=ibkr; soft-fail if IBKR connect down (preserves pending)
  → re-validate (RSI<35 soft bound; SMA200 still holding)
  → strike_selector builds 14-21 DTE bull-call debit spread (0.50/0.27 deltas)
  → broker.place_mleg_order (IBKR), journal as G### with exit_rules
  → exit_rules: rsi_exit=40, time_stop=10 trading days, trend_break=SMA200, ±50/+150% PnL
```

### Key paths
- **Journal (source of truth):** `/root/quantai-v2/shared-data/journal/paper/trades.jsonl`
- **Cache (scan results, debate, intel):** `/root/quantai-v2/shared-data/cache/`
- **Pipeline log:** `/root/quantai-v2/shared-data/logs/pipeline.log` (942KB+, grows fast)
- **Scripts:** `/home/trader/QuantAI/v2/shared-data/scripts/`
- **Workspace sync:** `bash /home/trader/QuantAI/scripts/sync_workspaces.sh` (copies workspace files to `/root/quantai-v2/` where OpenClaw reads them)
- **Symlink bridge:** `/projects/quantai → /home/trader/QuantAI`
- **Dashboard:** `/home/trader/dashboard/` (KARNA-level, not project-scoped)

### Cron schedule
```
*/15 9-16 * * 1-5   run_pipeline.py          # Main pipeline
5 16 * * 1-5        run_pipeline.py eod      # EOD summary
30 9 * * 1-5        pre_trade_check.py       # Pre-market
*/2 * * * *         heartbeat_monitor.py     # Pipeline liveness check
* * * * *           collect_system.py        # Dashboard collectors
* * * * *           collect_karna.py
* * * * *           collect_quantai.py
```

## Strategies

### Agent Alpha (agent_alpha)
- Bull put spreads, bear call spreads, diagonal spreads
- Any strategy that isn't iron condors
- Defined-risk, no shares required

### Agent Beta (agent_beta)
- Iron condors, butterflies
- Range-bound premium collection
- Submitted as 4-leg mleg orders

### Agent Gamma (agent_gamma)
- Connors RSI(10) pullback, single-strategy mean reversion
- Universe: 27 symbols (XSP/SPX/NDX/RUT, SPY/QQQ/IWM, 20 mega-cap stocks)
- Bull call debit spreads only (always long-direction in confirmed uptrend)
- Two-phase cron: 4:30 PM scan, 9:33 AM execute (next day)
- Risk: 1% per trade, max 3 open / 2 daily / 2 per sector, 3-loss circuit breaker (48h)

### Manual (Amit)
- SOFI collar (P001, the only trade for weeks)
- Covered calls, CSPs — require shares, agents can't hold stock

## Guard rules (always enforced)
- Max loss per trade: 2% of account
- Earnings blackout: 14 days
- VIX ≥ 35: no new trades (HALT regime)
- No-trade windows: 9:30-9:45 AM, 3:45-4:00 PM ET
- Stop loss: 2x credit received
- Profit target: 50% of max profit
- Max agent positions: 3 simultaneously
- Entry cutoff: 3:00 PM ET
- Hard close: 3:30 PM ET
- Debit strategies (diagonal, calendar): skip credit floor check

## Bugs found and fixed (April 15, 2026)

### Bug 1: mleg order missing top-level `qty` (CRITICAL — fixed)
**Symptom:** Pipeline logs "Entry complete" but trades.jsonl never gets new entries. Alpaca rejects with "qty must be > 0".
**Root cause:** `place_mleg_order()` in autonomous_execution.py built the payload without a top-level `"qty": "1"` field. Alpaca's mleg API requires it — `ratio_qty` on legs is the ratio relative to this top-level qty.
**Fix:** Added `"qty": "1"` to the payload dict (line ~285).
**Lesson:** The Alpaca mleg API documentation is sparse. The `ratio_qty` on legs is NOT the order quantity — it's a multiplier. Top-level `qty` is mandatory.

### Bug 2: `position_intent` rejected by Alpaca (CRITICAL — fixed)
**Symptom:** After fixing qty, orders still rejected with "invalid legs: invalid position_intent".
**Root cause:** Every leg dict included `"position_intent": "open"`. Alpaca's mleg endpoint doesn't accept this field.
**Fix:** Removed `position_intent` from all leg dicts in all 5 strategy builders (bull put, bear call, iron condor, diagonal, generic).
**Gotcha:** The sed-based removal broke multi-line dict entries where `position_intent` was on its own line with `{"ratio_qty": "1", "side": "buy",` — deleting the line removed the dict opening too. Required Python-based string replacement to fix the structural damage, plus manual addition of missing `]` bracket on iron condor.
**Lesson:** Never use `sed -i` to delete lines from Python dicts. Use exact string replacement or edit in an editor.

### Bug 3: Options chain endpoint URL wrong (MEDIUM — fixed)
**Symptom:** `Chain query 404: Not Found` for XOM, CVX when building diagonal spreads.
**Root cause:** Code used `ALPACA_DATA` (`https://data.alpaca.markets`) + `/v1beta1/options/contracts`. The working endpoint is `ALPACA_BASE` (`https://paper-api.alpaca.markets`) + `/v2/options/contracts`.
**Fix:** Changed the URL in `get_available_strikes()` to use `ALPACA_BASE/v2/options/contracts`.
**Note:** The fallback to proposed strikes (when chain lookup fails) works — trades executed even with the broken chain query. But without chain validation, strikes might not match actual available contracts.

### Bug 4: scan_options.py timeout (MEDIUM — mitigated)
**Symptom:** Pipeline crashes every run with `TimeoutExpired` after 300 seconds at the scan step.
**Root cause:** 78 tickers × 4 scan types × multiple yfinance API calls per ticker = 10-15 minutes. The 300-second subprocess timeout was too tight.
**Fix:** Raised timeout to 900 seconds. Added 45-minute scan cache freshness check — if scan results exist and are < 45 min old, skip the scan and go straight to debate.
**Lesson:** The scan is the bottleneck. Future optimization: reduce ticker list, parallelize, or use Alpaca's own screener instead of yfinance.

### Bug 5: Market intelligence data fetch failures (LOW — transient)
**Symptom:** VIX: 0.0, most tickers returning `'NoneType' object is not subscriptable`, SPY curl timeout.
**Root cause:** Intermittent network failures during cron execution. yfinance works fine when called directly with `--force`.
**Impact:** Trades execute with `underlying_price: 0` and `vix_at_entry: 0` in journal entries. The debate chamber makes decisions on stale/garbage data when intelligence fails.
**Status:** Not a code bug. Self-heals. But journal entries from failed intel runs have garbage context.

## Known issues (not yet fixed)

### Git divergence
31 local commits + 40 remote commits diverged on main. Today's fixes committed on branch `fix/mleg-pipeline-2026-04-15`. Need careful reconciliation — inspect each modified file before merge/rebase.

### docker-compose containers (legacy?)
trader-orchestrator, trader-discord, trader-cto, trader-guards all running. trader-guards only serves /health. No evidence they interact with v2 pipeline. Probably safe to stop, but need to verify trader-discord isn't posting to Discord channels.

### system_test.py 42/43
Only failure: `run_pipeline.py runs — Timed out` (system_test uses its own shorter timeout for the pipeline check). All other tests pass.

### Journal P001 has null fields
P001 SOFI trade has `"strategy": null`, `"iv": null`, `"delta": null`. Code that calls `.replace()` on strategy must handle None. Fixed in dashboard collector with `(t.get("strategy") or "?")` pattern.

### Fear & Greed scrape broken
CNN endpoint returns HTTP 418 ("I'm a teapot" — bot detection). Code falls back to VIX-based proxy score. Works but less accurate.

## Environment variables (purposes, not values)
- `ALPACA_API_KEY` / `ALPACA_SECRET_KEY`: Paper trading credentials
- `FINNHUB_API_KEY`: Economic calendar events
- `DISCORD_TOKEN_ORCHESTRATOR` + `DISCORD_CHANNEL_ALERTS`: bot-token Discord posting (sole path post-2026-04-26; webhooks decommissioned)
- `DISCORD_WEBHOOK_RESEARCH/PROPOSALS/EXECUTION`: legacy webhooks still referenced by trader-orchestrator (will retire alongside that container)
- `GOOGLE_SHEET_ID`: Journal sync target
- `ANTHROPIC_API_KEY`: Debate chamber LLM calls
- Loaded from `/home/trader/QuantAI/.env` (never cat this in chat)

## Alpaca API gotchas
- **mleg orders require top-level `qty`** — not just `ratio_qty` on legs
- **mleg orders reject `position_intent`** — don't include it
- **Options chain endpoint:** `paper-api.alpaca.markets/v2/options/contracts` (not data.alpaca.markets/v1beta1)
- **Paper account equity:** ~$99,368 (as of April 15)
- **Chain 404 on some tickers:** Fallback to proposed strikes works but skips validation
- **Index options unsupported:** SPX, XSP, VIX all return HTTP 422 — this is the migration driver for IBKR (see ADR-004)

## Broker adapter (broker.py — built 2026-04-26)

Pluggable abstraction so trading scripts can target Alpaca or IBKR transparently. Built in preparation for full migration off Alpaca; Alpaca paper stays live until callers (autonomous_execution, position_monitor) are wired through.

### Files
- `v2/shared-data/scripts/broker.py` — ABC, factory, OCC parser, AlpacaBroker (thin REST wrapper)
- `v2/shared-data/scripts/_broker_ibkr.py` — IBKRBroker (lazy-imported; pays ~200ms ib_insync cost only when needed)
- `v2/shared-data/scripts/test_broker.py` — print-based smoke test (42 checks, all passing 2026-04-26)

### Selection
- `BROKER_TYPE=alpaca` (default) | `ibkr`
- `BROKER_DRY_RUN=1` forces all order-placing methods to log payload and return a dry-run sentinel — no network POSTs
- `IBKR_HOST` / `IBKR_PORT` / `IBKR_CLIENT_ID` / `IBKR_ACCOUNT` env overrides (defaults: 127.0.0.1, 4002, 1, "")

### Interface (BrokerBase)
`connect`, `disconnect`, `get_account`, `get_positions`, `fetch_option_chain(symbol, dte_range, strike_range=None, include_quotes=False)`, `get_quote(symbol)` (underlying), `get_option_quote(occ)` (contract), `place_mleg_order(legs, qty, tif, client_order_id)`, `close_position(legs, qty, client_order_id)`, `get_order_status(order_id)`.

All methods return `None` / `[]` on failure — never raise into callers.

### Leg shape (boundary contract)
Both adapters accept the existing Alpaca-shaped legs:
```
[{"ratio_qty": "1", "side": "buy"|"sell", "symbol": <OCC>}, ...]
```
OCC is the lossless format already in `trades.jsonl`. IBKRBroker parses OCC internally via `_parse_occ()`.

### Index option routing (IBKR)
- XSP → `Option(..., exchange="CBOE", tradingClass="XSP")`
- SPX → `tradingClass="SPX"` (third-Friday monthlies) or `"SPXW"` (everything else); both surfaced from `reqSecDefOptParams`
- VIX → `tradingClass="VIX"` or `"VIXW"`; exchange CBOE
- Underlying lookup: `Index(symbol, "CBOE", "USD")` for index roots, `Stock(symbol, "SMART", "USD")` for equities

### IBKR lifecycle
- One `IB()` instance per process. Lazy-connect on first call. `atexit` handles disconnect.
- Sync API: plain `ib.connect()` / `ib.disconnect()` — do NOT use `util.startLoop` (Jupyter-only) or `asyncio.run`.
- Use `ib.sleep(N)` between `reqMktData` and reading tickers — `time.sleep()` won't pump the loop.
- Connect retry: 3 attempts, 5s backoff. Disconnects half-connected sockets between attempts so a stale clientId doesn't poison subsequent retries.
- 23:30–00:15 ET restart-window guard: refuses to connect during IB Gateway's nightly restart at 23:45 ET rather than retry-storming.
- Force live data via `reqMarketDataType(1)`; logs WARNING once if fallback to delayed (type 3) is detected.

### Order semantics
- `place_mleg_order` is **never auto-retried**. Pass a deterministic `client_order_id`; on timeout, callers reconcile via `get_order_status` before resubmitting (avoids duplicate fills on combos).
- AlpacaBroker preserves both quirks (top-level qty, no position_intent). 1-leg mleg falls back to a plain market order (Alpaca rejects 1-leg mleg).
- IBKRBroker builds a `Bag` (combo) contract with `ComboLeg` per leg and submits via `placeOrder`. `orderRef` carries the `client_order_id` round-trip.

### Normalized output shapes
```
account     {equity, buying_power, cash, options_buying_power, pattern_day_trader}
position    {symbol (OCC), qty, side, avg_cost, current_price, unrealized_pnl, market_value}
chain_entry {symbol (OCC), underlying, strike, expiry (YYYY-MM-DD), right (C|P),
             bid, ask, mid, last, delta, gamma, theta, vega, open_interest, volume}
quote       {bid, ask, last, mid}    # mid only when bid>0 and ask>bid
order       {order_id, status, filled_qty, avg_fill_price, client_order_id}
```
Greek/quote fields are `None` when the broker can't supply (Alpaca's `/v2/options/contracts` returns no Greeks).

### Smoke-test results (2026-04-26)
- AlpacaBroker SPY chain (1-30 DTE): 3,874 contracts
- IBKRBroker SPY chain: 242,840 (cross-exchange duplicates), XSP: 20,244, SPX: 29,362 (both SPX and SPXW present), VIX: 680
- All 42 checks pass; managed account `DUP851506` confirmed

### Pipeline wiring (completed 2026-04-26)
`get_broker()` wired into all four callers:
- `autonomous_execution.py` — replaces direct Alpaca REST calls for orders
- `position_monitor.py` — positions + close orders go through broker adapter
- `pre_trade_check.py` — broker connect + account check
- `dashboard/collect_alpaca.py` — uses broker.get_account(); IBKR surfaces nulls for Alpaca-specific fields (last_equity, day_pnl)

### BROKER_TYPE=ibkr full verification (2026-04-26 → system-wide flip 2026-04-27)
All tests with `BROKER_TYPE=ibkr` (now the .env default; the system-wide flip happened 2026-04-27 alongside Beta launch):
- `system_test.py`: **43/43 passed**
- `pre_trade_check.py`: **19/19 — GO** (`Ibkr connected — equity $1,000,000`)
- `position_monitor.py --dry-run`: clean (no open positions)
- `autonomous_execution.py --check-only`: clean (market closed)
- `collect_alpaca.py`: state written, equity=1000000, day_pnl=null (IBKR expected)
- Dashboard errors: 0 broker-related errors
- `ibgateway.service`: active
- Pre-existing 9 Alpaca legs closed via `DELETE /v2/positions`, queued for Mon 9:30 ET fill; A008/A009/A010 marked CLOSED with `reason=ibkr_migration_reset`.

## Agent Beta (live 2026-04-27)

Beta is a **regime-driven, deterministic, zero-LLM** autonomous trader targeting native SPX/XSP/VIX index options via IBKR. Runs on its own cron entry parallel to Alpha.

- **12 regimes** (priority chain, first-match-wins): HALT, CRISIS, MEAN_REVERSION_OVERBOUGHT/OVERSOLD, HIGH_VOL, SQUEEZE, PRE_EVENT, TREND_UP/DOWN, LOW_VOL, RANGE, NORMAL.
- **8 strategies** in `v2/shared-data/scripts/beta/strategies/`: event_strangle, call_ratio_backspread, put_ratio_backspread, broken_wing_butterfly, vix_calls, debit_spread, calendar_spread, credit_spread_offset (HIGH_VOL theta-offset only).
- **Independent risk gates** (`beta/risk_engine.py`): max 3 open positions, max 2 entries/day, 5-loss circuit breaker (24h cooldown), -2% daily / -5% weekly drawdown halts, ±0.5/1.0 net delta/vega correlation. Counts only `source == 'agent_beta'` entries.
- **Per-trade `exit_rules`** stored on the journal entry. `position_monitor.py` consults them first (take_profit_pct, stop_loss_pct, time_exit_dte, valley_danger, weekend_close, hard_time_exit_dte) and falls through to Alpha's 4-rule path for non-Beta trades. The 3:30 PM ET hard close still applies to all.
- **Trade IDs**: `B###` prefix for `agent_beta`, `A###` for Alpha, with per-prefix counters so the two series don't collide.
- **Refuses to run** if `BROKER_TYPE != ibkr` (Beta's strategies only work on native index options).
- **Cron entries** added 2026-04-27: `*/15 13-20 * * 1-5 beta_agent.py`, `0 6 * * 0 beta/event_moves_seeder.py`, `* * * * * /var/dashboard/collect_beta.py`.

Reference: `docs/2026-04-26-agent-beta-implementation-plan-v2.md` for full design and phase-by-phase build log.

### What's NOT done yet
- Real (non-dry-run) order submission via IBKRBroker (pending market hours)
- Strategy-level position grouping (stays in `position_monitor`'s journal logic)
- Beta first-week observation (Mon 2026-04-27 onwards — watch beta.log + dashboard)
- IV Rank surface uses 21-day realized-vol percentile (proxy); upgrade to true IV from chain greeks if needed

## Dashboard v2 (built 2026-04-17)
- **Location:** `/var/dashboard/index.html` (served), mirrored collectors in `/home/trader/dashboard/` (not served)
- **Access:** `https://quantai.tail1465ff.ts.net/` via Tailscale
- **Architecture:** Single-file React SPA — no build step. CDN-loaded React 18 + Tailwind + Recharts + Mermaid + Babel standalone. Browser transpiles JSX on load.
- **Polling:** client fetches `/state/*.json` every 30s via `setInterval`. No websockets, no server push.
- **Tabs:** Live, Agents, System, Workflows, Errors, History. Tab state is client-only (no routing).
- **Services:** `dashboard-http` (Python http.server on 127.0.0.1:8080) — untouched. `dashboard-generator` — **disabled** (the Python HTML generator is obsolete; index.html is static).
- **Backups kept:** `/var/dashboard/index.html.bak.2026-04-17`, `/var/dashboard/generate.py.bak.2026-04-17`.
- **Collectors (all via cron, every 1m unless noted):**
  - `collect_system.py` → `system.json` (VPS CPU/mem/disk, service health)
  - `collect_karna.py` → `karna-status.json`, `karna-cost.json`, `karna-background.json`
  - `collect_quantai.py` → `quantai-metrics.json`, `quantai-data-status.json`, `quantai-alerts.json`, `quantai-timeline.json`, `quantai-window-current.json`
  - `collect_alpaca.py` → `alpaca-account.json` (equity, day P&L, cash, buying power) + appends to `equity_history.jsonl`
  - `collect_cron.py` → `cron-status.json` (known job catalog → last-run from log mtime)
  - `collect_history.py` → `quantai-history.json` (every 5m — journal + equity curve + summary)
  - `position_monitor.py` owns `quantai-positions.json` (every 2m during market hours, not a "collector")
- **Env vars for Alpaca collector:** `ALPACA_API_KEY`, `ALPACA_SECRET_KEY` (NOT `APCA_API_KEY_ID`, despite Alpaca's own header naming).
- **Extension model:** Add a new collector → write JSON to `/var/dashboard/state/` → add a `fetchState` entry and a tab section in `index.html`. No regeneration required.

## Heartbeat monitoring (Phase B — built 2026-04-17)

### What it does
Catches silent pipeline failures within 2 minutes. Before this, the pipeline was broken for 2 weeks with no alert.

### Components
- **`write_heartbeat()`** in `run_pipeline.py` — writes UTC timestamp to `/tmp/quantai-heartbeats/pipeline.beat` at every successful market-hours execution exit point.
- **`v2/shared-data/scripts/heartbeat_monitor.py`** — reads the beat file, alerts Discord if stale, writes dashboard state.

### Behavior
- Monitor runs every 2 minutes (all day, all week)
- During market hours (9–16 ET Mon–Fri): alerts Discord if `pipeline.beat` is missing or older than 20 minutes
- Outside market hours: writes dashboard state but sends no alerts
- Alert cooldown: 30 minutes between Discord messages per beat name (prevents spam)

### Position monitor close-order behavior (post-2026-04-26)
- `is_market_open()` guard: close attempts skipped outside 09:30–16:00 ET on weekdays. P&L still recorded.
- 1-leg fallback: if `build_closing_legs()` returns exactly 1 leg, `place_close_order()` issues a single-leg market order (not mleg).
- Zero-leg recovery: if 0 active Alpaca legs found, the journal entry is auto-marked `status: "CLOSED"`, `exit_reason: "closed_outside_pipeline"` (no infinite retry loop).
- Bounded retries: 5 attempts per trade ID, persisted at `/root/quantai-v2/shared-data/cache/close_attempts.json`. After exhaustion, one Discord alert fires and the trade is left for manual review.
- Runbook: `docs/runbooks/runbook-close-order-failed.md`.
- Dashboard state: `/var/dashboard/state/quantai-heartbeats.json`
- Heartbeat log: `/root/quantai-v2/shared-data/logs/heartbeat.log`

### Beat file
- Path: `/tmp/quantai-heartbeats/pipeline.beat`
- Format: ISO 8601 UTC timestamp (e.g. `2026-04-17T18:31:54+00:00`)
- Written by: `run_pipeline.py` (runs as root via cron)
- Cooldown state: `/tmp/quantai-heartbeats/alert_cooldown.json`

### Dashboard state contract
```json
{
  "last_updated": "<ISO timestamp>",
  "status": "ok | idle | error",
  "data": {
    "market_hours": true,
    "pipeline": {
      "status": "ok | stale | missing | unknown",
      "age_min": 2.3,
      "last_beat": "<ISO timestamp or null>",
      "stale_threshold_min": 20
    }
  }
}
```

## Position threshold monitor (Slice D — built 2026-04-17)

### What it does
Monitors every open agent position every 2 minutes. Executes hard exits when thresholds are breached. Before this, open positions had no automated exit — only manual monitoring by Amit.

### Components
- **`v2/shared-data/scripts/position_monitor.py`** — standalone script, no dependency on run_pipeline.py
- **`collect_quantai.py`** (both copies) — `collect_positions()` call removed; position_monitor.py now owns `quantai-positions.json`

### Exit rules (in priority order)
1. **Hard close**: 3:30 PM ET → close all agent positions
2. **Expiry proximity**: any leg expires today or tomorrow → close
3. **Stop loss**: unrealized P&L < −(2 × abs(estimated_credit)) → close
4. **Profit target**: unrealized P&L ≥ 0.5 × abs(estimated_credit) → close

### How it works
1. Reads OPEN agent trades from journal
2. Calls Alpaca `GET /v2/positions` → builds `{occ_symbol: position}` dict
3. Constructs OCC symbol per leg: `{TICKER}{YYMMDD}{C|P}{strike×1000 zero-padded to 8}`
   - e.g. XOM 2026-06-18 Call 150.0 → `XOM260618C00150000`
4. Sums `unrealized_pl` across all matched legs = trade P&L
5. On exit trigger: builds reversed mleg order (buy↔sell), submits to Alpaca
6. **Only updates journal if close order succeeds** (atomic JSONL rewrite via `os.replace`)
7. Syncs Google Sheets, posts Discord alert per closed trade
8. Always writes `/var/dashboard/state/quantai-positions.json` with real P&L

### Key design decisions
- If Alpaca API is unavailable: skip cycle entirely (leaves old dashboard, no spurious closes)
- If close order fails: trade stays OPEN, auto-retried on next 2-min cycle
- `estimated_credit == 0`: skip credit-based thresholds (hard close and expiry still apply)
- Missing Alpaca legs (expired/assigned): skip that leg in close order; if all legs missing, skip close and log warning for manual review
- No `position_intent` on legs — Alpaca rejects it (Bug 2)
- DRY_RUN mode: `--dry-run` flag, no orders placed, no journal writes, Discord → stdout

### Dashboard state
File: `/var/dashboard/state/quantai-positions.json`
Written every cycle with fields: `id, symbol, strategy, source, entry_time, pnl, pnl_pct, status (ok/warning/critical), exit_reason`

### Logs
- `/root/quantai-v2/shared-data/logs/position_monitor.log`

## Self-learning error system (Phase E — built 2026-04-17)

**Goal:** detect errors in logs, classify against a catalog of known patterns, auto-fix the safe ones, route the rest to Discord with links to runbooks, and grow the catalog automatically each week.

### Components

**`docs/error-catalog.json`** — authoritative catalog of known error patterns.
Schema: `{id, pattern, is_regex, category, severity, auto_action, description, runbook, first_seen, last_seen, occurrence_count, source}`.
Optional: `retry_command`, `restart_target`.
Categories: `recurring` (3+ seen), `novel` (1 seen), `transient` (self-heals).
Auto-actions: `none` (human), `retry` (re-run retry_command after 60s), `skip` (log and move on), `restart_service` (systemctl restart).

**`docs/runbooks/*.md`** — one markdown file per known error family. Sections: Detection → Diagnosis → Fix → Auto-fixable? → Prevention. Seeded with 7 runbooks at launch.

**`v2/shared-data/scripts/error_detector.py`** — runs every 5 min via cron. Tails the last 500 lines of pipeline.log + heartbeat.log + position_monitor.log, matches against catalog, dispatches auto-actions, updates the catalog atomically, and writes `/var/dashboard/state/quantai-errors.json` for the dashboard. Deduplicates identical patterns within a 30-minute window via `/tmp/quantai-error-dedup.json`.

**`v2/shared-data/scripts/error_learner.py`** — runs Friday 22:00 UTC (6 PM ET). Scans the last 7 days of logs, counts signatures (with numeric/path/hex tokens stripped for stability), auto-appends recurring patterns (3+) as `recurring` and 2+ as `novel` with `severity: "info"` (post-2026-04-26: was "unknown", changed to suppress Discord noise). Skips lines starting with `[market_intelligence]`, `[scan_options]`, `[debate_chamber]`, etc. Bumps occurrence_count on known matches. Posts weekly digest via the bot-token helper `_discord.post_to_channel()`.

**`v2/shared-data/scripts/add_error.py`** — CLI for manual catalog additions. Atomic write with `.bak` backup. Rejects duplicate ids unless `--force`.

### Discord routing (post-2026-04-26)
All v2 cron scripts post via `_discord.post_to_channel(channel_id, msg)` using the bot token (`DISCORD_BOT_TOKEN`) and a single channel (`DISCORD_CHANNEL_ALERTS`). Severity gating happens inside `/var/dashboard/collect_errors.py`:
- `severity: "info"` → DB only, no Discord post.
- `severity: "warning"` → post once per `eid`, throttled at 60 minutes.
- `severity: "error"` → same as warning (60-min throttle).
- `severity: "critical"` → post immediately, throttled at 5 minutes per `eid`.

### Centralized error logger (post-2026-04-26)
All errors and warnings across the VPS — systemd journals (clawroute, litellm, openclaw), Docker containers (trader-orchestrator/discord/cto/guards/litellm), QuantAI text logs, /var/log/{auth,kern,fail2ban,ufw}, and Python apps that opt into the `_logger.setup()` handler — are ingested into a single SQLite DB at **`/var/dashboard/errors.db`** every 2 minutes by `collect_errors.py`. Dashboard surfaces this at the **Errors** tab. Catalog matching attaches `catalog_id` + runbook link automatically. See `docs/runbooks/runbook-centralized-logger.md` for query examples and operations.

The old `error_detector.py` (text-log-only) was archived to `v2/shared-data/scripts/legacy/`.

### Cron
```
*/5 * * * *  python3 /home/trader/QuantAI/v2/shared-data/scripts/error_detector.py >> /root/quantai-v2/shared-data/logs/error_detector.log 2>&1
0 22 * * 5   python3 /home/trader/QuantAI/v2/shared-data/scripts/error_learner.py >> /root/quantai-v2/shared-data/logs/error_learner.log 2>&1
```

### Dashboard
- **Errors tab**: 4 summary cards (auto-fixed / known-manual / unknown / catalog size), classified-errors list with severity + classification + action badges and the latest matching log line, plus the existing alerts timeline underneath.
- **System tab**: cron table auto-includes `error_detector.py` (every 5m) and `error_learner.py` (Fri 6 PM ET) — no dashboard change needed because `collect_cron.py` is catalog-driven.
- **Workflows tab**: new "Error Learning Loop" collapsible flowchart.

### Why this exists
Before Phase E, errors only surfaced in pipeline.log — detection required a human tailing logs. The self-learning loop turns log tailing into structured state with automatic remediation for known-safe actions, a runbook pointer for everything else, and a weekly learner that prevents the catalog from going stale as new error patterns appear. Pure Python, no LLM calls.

## Querying the architecture

A graphify knowledge graph is maintained at `graphify-out/graph.json` (976 nodes, 1727 edges, 56 communities, 44.8× token compression vs reading raw files).

### How to query

From a Claude Code session with the graph built:
```
/graphify query "what gates an order before submission?"
/graphify path "run_pipeline" "autonomous_execution"
/graphify explain "run_guard_pipeline"
```

Or via the MCP server (registered in `.claude/settings.local.json` as `graphify-quantai`), which exposes `query_graph`, `get_node`, `get_neighbors`, `shortest_path`.

### God nodes (highest cross-community centrality)
- `post()` — bridges 12 communities; every agent uses this for Discord output
- `run_guard_pipeline()` — core constraint enforcer across execution + monitoring
- `TradeProposal` — shared data structure linking scanner → debate → execution
- `build_context()` — pre-trade context builder, touched by Alpha and Beta

### Maintenance commands
- `graphify update .` — **default**: re-extract only changed files (AST-only for code, LLM for docs). Use after editing one or a few files.
- `graphify .` — **full rebuild**: only when structure has shifted significantly or `--update` produces stale results. Costs tokens.
- The git post-commit hook runs `graphify update .` automatically on code-only commits (free, no LLM).
- After editing runbooks, knowledge.md, or other doc files, run `graphify update .` manually to refresh concept edges.

### Rebuild on clone
`graph.json` is committed (1.1 MB). If it grows past 2 MB it will be gitignored — in that case rebuild with:
```bash
export PATH="$PATH:/home/trader/.local/bin"
graphify .   # run from inside a Claude Code session
```

## Cost discipline (Phase A — built 2026-04-25)

### What it does

Routes every cron-side LLM call through ClawRoute (`localhost:18790`), an
OpenAI-compatible proxy that classifies requests into 5 tiers and dispatches
to the cheapest provider that fits each tier. ClawRoute records cost,
savings, and the original-vs-routed model in `routing_log` (SQLite).

### Components

- `v2/shared-data/scripts/_llm_client.py` — shared shim. Two interfaces:
  - `Client()` — drop-in for `anthropic.Anthropic` (has `.messages.create()`).
  - `chat(messages, system, model, max_tokens, ...)` — functional helper.
    Async-safe via `await asyncio.to_thread(chat, ...)`.
- ClawRoute service — `systemctl status clawroute`, listens on
  `127.0.0.1:18790`, dashboard at `http://127.0.0.1:18790/dashboard`,
  stats JSON at `http://127.0.0.1:18790/stats`.
- Migrated callers (cron-side):
  - `debate_chamber.py` (4 sites: proposal, bull, bear, judge)
  - `self_evolution.py` (4 sites: consolidate, observe, critique, generate)

### Escape valve

Set `LLM_BYPASS_CLAWROUTE=1` in the environment to revert any callsite to
direct Anthropic API. Use only during incident response when ClawRoute is
down. The shim picks up the env var at module-import time, so the var must
be set in the cron entry / systemd unit / shell, not at runtime.

### Tier classification (current ClawRoute config — updated 2026-04-25)

| Tier | Primary | Fallback | Notes |
|---|---|---|---|
| HEARTBEAT | `gemini-2.5-flash-lite` | `deepseek-v4-flash` | <30 char messages, status pings |
| SIMPLE | `deepseek-v4-flash` | `gemini-2.5-flash` | 30-80 char questions (V4, migrated 2026-04-25) |
| MODERATE | `gemini-2.5-flash` | `claude-haiku-4-5` | default tier |
| COMPLEX | `claude-sonnet-4-6` | `gemini-2.5-flash` | tools present, analytical keywords |
| FRONTIER | `claude-sonnet-4-6` | `deepseek-v4-flash` | code blocks, tool_choice, >8K context |

**Groq Llama 4 Scout** is registered in ClawRoute's model registry as
`groq/meta-llama/llama-4-scout-17b-16e-instruct` ($0.11/$0.34 per 1M).
It is ready to use but HEARTBEAT primary swap to Groq is pending
`GROQ_API_KEY` being added to `/etc/systemd/system/clawroute.service`.
Once the key is added: restart clawroute, then update `config/default.json`
heartbeat primary to `groq/meta-llama/llama-4-scout-17b-16e-instruct`.

The classifier is in ClawRoute's source. Tier-threshold retuning deferred
until ≥1 week of populated `routing_log` data.

### ClawRoute quirks (worked around in `_llm_client.py`)

1. **Auth middleware 500s on Bearer tokens it doesn't recognize.** The shim
   omits the `Authorization` header entirely (ClawRoute is localhost-only).
2. **Lying `content-encoding: gzip` header.** ClawRoute forwards the
   upstream Google response header verbatim while its own middleware has
   already decompressed the body. Both `httpx` and `requests` trip on this.
   The shim uses `httpx.stream(...).iter_raw()` to bypass content-decoding.

If either is fixed in ClawRoute upstream, simplify the shim accordingly.

### Verifying it's working

```bash
# Stats endpoint shows cost / tier / savings
curl -s http://127.0.0.1:18790/stats | python3 -m json.tool | head -40

# Most recent routing decisions
sudo sqlite3 /home/openclaw/.openclaw/workspace/router/data/clawroute.db \
  "SELECT timestamp, original_model, routed_model, tier, classification_reason, actual_cost_usd, savings_usd FROM routing_log ORDER BY id DESC LIMIT 10"

# Confirm no remaining direct-Anthropic calls in cron-side scripts
grep -rn "anthropic.Anthropic\|api.anthropic.com" v2/shared-data/scripts/
# Should match only the bypass branch in _llm_client.py
```

### Known caveats

- The orchestrator/discord-bot/cto-listener Docker containers do NOT
  bind-mount their source from the host. Their 7 LLM call sites still call
  Anthropic directly. Migration is a separate task (image rebuilds + Docker
  network reconfig so containers can reach ClawRoute on the host).
- **Phase B4 shipped 2026-04-25** — daily spend cap, kill switch, and 80% Discord alert are live in ClawRoute.
- **Phase B3 shipped 2026-04-25** — `routing_log` now has `cache_creation_input_tokens` and `cache_read_input_tokens` columns. Caching savings are now tracked.
- **Phase A3 shipped 2026-04-25** — All Anthropic calls through ClawRoute now inject `cache_control: {type: "ephemeral"}` on the system message and send `anthropic-beta: prompt-caching-2024-07-31`. Cache tokens appear in `routing_log` once system prompts exceed Anthropic's 1024-token cache minimum.
- **ANTHROPIC_API_KEY in `/etc/karna/secrets.env`** was stale. Fixed 2026-04-25 — synced from QuantAI `.env`. ClawRoute now routes Anthropic COMPLEX/FRONTIER calls successfully.

---

## Cost observability (Phase B5 — built 2026-04-25)

### What it does

Reads ClawRoute's `routing_log` every 15 minutes, computes cost metrics,
and writes `/var/dashboard/state/clawroute.json`. The dashboard System tab
shows two new cards: **ClawRoute — Today** and **ClawRoute — 7-Day Rolling**.

Posts a Discord alert to `DISCORD_WEBHOOK_ALERTS` (falls back to
`DISCORD_WEBHOOK_CHAT`) when today's spend exceeds 2× the 7-day daily
average (once ≥3 days of baseline data exist), or when the daily budget
is ≥80% consumed.

### Components

- `/var/dashboard/collect_clawroute.py` — root cron, `*/15 * * * *`.
  Reads DB directly (no sudo needed since it runs as root).
  Source copy at `/home/trader/dashboard/collect_clawroute.py`.
- `/var/dashboard/state/clawroute.json` — state file shape:
  ```json
  {
    "last_updated": "...", "status": "ok|warning|error|idle",
    "data": {
      "clawroute_up": true, "daily_budget_usd": 5.0,
      "today": {"spend_usd": 0.000017, "savings_usd": 0.000326,
                "calls": 11, "by_tier": {"heartbeat": 0.000012},
                "escalation_rate_pct": 0.0, "avg_confidence": 0.805,
                "avg_response_ms": 430, "budget_pct": 0.0},
      "7d": {"spend_usd": ..., "daily_avg_usd": ...,
             "calls": ..., "days_with_data": 1}
    }, "alerts": []
  }
  ```
- Dashboard `index.html` — `ClawRouteCostCard` component in System tab.

### Verifying

```bash
sudo python3 /var/dashboard/collect_clawroute.py
cat /var/dashboard/state/clawroute.json | python3 -m json.tool
# Dashboard System tab at https://quantai.tail1465ff.ts.net/
```

### Daily budget

`DAILY_BUDGET_USD = 5.0` hardcoded (paper mode). Change to `20.0` when
live trading begins. See Phase B4 section below.

---

## Daily budget cap + kill switch (Phase B4 — built 2026-04-25)

Three mechanisms in ClawRoute's request handler (`src/server.ts`):

### 1. Operator kill switch
Create the file `/home/openclaw/.openclaw/KILL_SWITCH` → every LLM call
returns HTTP 503 immediately (no LLM contact). Remove the file to resume.
```bash
sudo touch /home/openclaw/.openclaw/KILL_SWITCH   # halt
sudo rm   /home/openclaw/.openclaw/KILL_SWITCH   # resume
```

### 2. Daily spend cap
`budget.dailyUsd = 5.0` in `config/default.json` (paper mode).
When today's `SUM(actual_cost_usd)` in `routing_log` ≥ cap, every
request returns HTTP 429 with `code: daily_budget_exhausted`.
Resets automatically at UTC midnight.

**To change the cap:**
- Edit `/home/openclaw/.openclaw/workspace/router/config/default.json`
  → `"budget": {"dailyUsd": 20.0, ...}`
- Or set env var `CLAWROUTE_DAILY_BUDGET_USD=20.0` in
  `/etc/karna/secrets.env` (no rebuild needed for env var change, just `systemctl restart clawroute`)
- Rebuild required for config.json change: `cd /home/openclaw/.openclaw/workspace/router && sudo -u openclaw npm run build && sudo systemctl restart clawroute`

### 3. 80% Discord alert
When today's spend crosses 80% of the daily cap, ClawRoute posts to
`DISCORD_WEBHOOK_ALERTS` (loaded from `/etc/karna/secrets.env`).
At most one alert per UTC day.

### How it was tested
- Kill switch: `sudo touch KILL_SWITCH` → verified HTTP 503 → `sudo rm KILL_SWITCH`
- Budget config: verified via `/api/config` endpoint shows `dailyUsd: 5`
- Discord: `discordWebhook: SET` confirmed after DISCORD_WEBHOOK_ALERTS added to secrets.env
- Normal routing: still routes to Groq HEARTBEAT post-deployment
