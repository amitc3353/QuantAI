# Runbook: scan_options.py timeout

## Detection
- Pattern: `TimeoutExpired` in pipeline.log after a `scan_options.py` invocation
- Symptom: Pipeline aborts mid-cycle; no scan JSON written; debate chamber gets no input.

## Diagnosis
1. Check `/root/quantai-v2/shared-data/cache/` for the most recent scan file — if stale (>45 min), the scan never finished.
2. Run `time python3 /home/trader/QuantAI/v2/shared-data/scripts/scan_options.py all` manually and observe wall-clock.
3. 78 tickers × 4 strategies × multiple yfinance calls → expect 10-15 min under normal conditions. If it takes >15 min, yfinance is throttling or network is slow.

## Fix
- Subprocess timeout in `run_pipeline.py` is already 900s. If hitting 900s, lower the ticker list (`discover_tickers`) or increase timeout.
- Reuse cached scan: the pipeline skips scanning if a fresh scan (<45 min) exists. Don't delete cache during market hours.

## Auto-fixable?
**Yes — `retry`.** Next pipeline cycle runs again 15 min later with the cache present (if scan partial results were written). The detector's retry action waits 60s then re-invokes the failing cron target.

## Prevention
- Cache freshness gate (45 min) in `run_pipeline.py`.
- Split scan into smaller ticker batches if yfinance rate-limits.
