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
