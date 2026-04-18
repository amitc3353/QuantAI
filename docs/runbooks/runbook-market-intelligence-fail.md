# Runbook: market_intelligence.py fail

## Detection
- Pattern: `VIX: 0.0`, `'NoneType' object is not subscriptable`, `F&G scrape failed (HTTP Error 418`, `Debate failed — skipping execution` in pipeline.log
- Symptom: Intel JSON has zeros/nulls; debate chamber decides on garbage data; trades journal entries have `underlying_price: 0`.

## Diagnosis
1. Run `python3 /home/trader/QuantAI/v2/shared-data/scripts/market_intelligence.py --force` manually. If it succeeds interactively, the cron failure was a transient network blip.
2. Check `/root/quantai-v2/shared-data/cache/market_intelligence.json` — is it fresh (<30 min) per `run_pipeline.intel_is_fresh()`?
3. Fear & Greed 418 is expected — CNN blocks bot-like requests. Code falls back to VIX-based proxy, so this is informational, not a failure.
4. For `Debate failed`: look for the immediately-preceding `anthropic.BadRequestError` or Anthropic API 401/403/429. Credit-balance-low → top up via console.

## Fix
- **Transient yfinance / connection failures:** self-heals. Next 15-min cycle usually recovers.
- **Anthropic credit balance zero:** pipeline cannot enter trades until credits are added. Update credits in Anthropic console; no code change.
- **F&G 418:** no action. VIX proxy is the documented fallback.

## Auto-fixable?
**Partial — `skip`.** The detector classifies these as transient and moves on; next cycle retries naturally. No retry is triggered automatically because the pipeline's own 15-min cadence is already the retry loop.

## Prevention
- Every caller of `market_intelligence.json` checks `data_quality` and regime before acting.
- Add monitoring: if debate fails N cycles in a row, escalate from `skip` to `none` + Discord escalation. Not yet implemented.
