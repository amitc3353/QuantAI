# QuantAI Knowledge Base
Last updated: 2026-04-15 by Amit + Claude

## What QuantAI is
Autonomous options trading system. Paper trading on Alpaca ($99,368 equity). Two agent strategies (Alpha, Beta) plus manual SOFI collar. Goal: $3-10k/month income at $150k deployed. Currently in paper trading validation phase — 0/10 pre-live checklist items complete.

## Architecture (what's actually running)

### Two coexisting systems
1. **v2 Pipeline (active, does the work):** Python cron at `/home/trader/QuantAI/v2/shared-data/scripts/`, runs every 15 min during market hours (9-16 ET Mon-Fri). Entry point: `run_pipeline.py`. Runtime data at `/root/quantai-v2/shared-data/`.
2. **docker-compose (legacy, unclear role):** `trader-orchestrator`, `trader-discord`, `trader-cto`, `trader-guards` containers. All "healthy" but trader-guards only serves /health pings. No evidence these containers do meaningful work. Reconciliation deferred — not causing harm.

### v2 Pipeline flow
```
Cron (every 15m) → run_pipeline.py
  → market_intelligence.py (VIX, prices, technicals via yfinance)
  → scan_options.py "all" (78 tickers × 4 strategy types — SLOW, 10-15 min)
  → debate_chamber.py (Bull/Bear/Judge LLM debate, produces approved trades)
  → autonomous_execution.py (builds mleg orders, submits to Alpaca, journals)
  → sheets_sync.py (syncs journal to Google Sheets)
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
- `DISCORD_WEBHOOK_CHAT`: Trade alerts to Discord
- `GOOGLE_SHEET_ID`: Journal sync target
- `ANTHROPIC_API_KEY`: Debate chamber LLM calls
- Loaded from `/home/trader/QuantAI/.env` (never cat this in chat)

## Alpaca API gotchas
- **mleg orders require top-level `qty`** — not just `ratio_qty` on legs
- **mleg orders reject `position_intent`** — don't include it
- **Options chain endpoint:** `paper-api.alpaca.markets/v2/options/contracts` (not data.alpaca.markets/v1beta1)
- **Paper account equity:** ~$99,368 (as of April 15)
- **Chain 404 on some tickers:** Fallback to proposed strikes works but skips validation

## Dashboard
- **Location:** `/home/trader/dashboard/` (KARNA-level, not project-scoped)
- **Access:** `https://quantai.tail1465ff.ts.net/` via Tailscale
- **Services:** `dashboard-generator` (HTML every 30s), `dashboard-http` (Python HTTP on 127.0.0.1:8080)
- **Extension model:** Write a JSON state file to `/home/trader/dashboard/state/`, add a render block in `generate.py`
- **Collectors:** `collect_system.py`, `collect_karna.py`, `collect_quantai.py` — run every minute via cron

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
