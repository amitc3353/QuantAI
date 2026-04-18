# Runbook: options chain 404

## Detection
- Pattern: `Chain query 404` in pipeline.log during scan_options.py execution.
- Symptom: A ticker's options chain lookup returns 404. Scan falls back to proposed strikes without validating them against the actual available contracts.

## Diagnosis
1. Which ticker? 404 can be:
   - Ticker has no listed options (rare — most tickers in the 78-ticker list do have options).
   - Endpoint URL typo. Correct URL is `paper-api.alpaca.markets/v2/options/contracts` (not `data.alpaca.markets/v1beta1/...`).
   - Expiry date parameter excludes all contracts.
2. Try manually: `curl -sI "https://paper-api.alpaca.markets/v2/options/contracts?underlying_symbols=XYZ" -H "APCA-API-KEY-ID: $ALPACA_API_KEY" -H "APCA-API-SECRET-KEY: $ALPACA_SECRET_KEY"`. 200 means the endpoint works; the code is at fault. 404 means the ticker really has no contracts.

## Fix
- Endpoint bug was fixed in commit `b42a4b8` — if it reappears, someone reverted that fix.
- If a specific ticker chronically 404s, remove it from the scan list. The fallback to proposed strikes works but produces lower-quality trades.

## Auto-fixable?
**Yes — `skip`.** The scanner already falls back; no retry needed. The detector logs and moves on.

## Prevention
- The endpoint URL is a constant in `scan_options.py`. Don't edit it without running the full pipeline in `--dry-run`.
- Weekly learner will flag if chain-404 occurrence_count spikes — might indicate a ticker list needs pruning.
