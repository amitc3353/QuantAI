# Runbook: mleg order fail

## Detection
- Pattern: `qty must be > 0` or `invalid position_intent` in pipeline.log
- Symptom: Pipeline logs "Entry complete" but trades.jsonl gets no new entry. Alpaca returns 422 Unprocessable.

## Diagnosis
1. Tail pipeline.log and find the Alpaca response body near the error.
2. Inspect `autonomous_execution.py` → `place_mleg_order()` → payload dict. Confirm both:
   - Top-level `"qty": "1"` is present (required by Alpaca mleg API).
   - No leg contains `"position_intent"` (Alpaca rejects it).
3. Confirm each leg has `"ratio_qty"`, `"side"`, `"symbol"`. Strategy-builder helpers (bull put, bear call, iron condor, diagonal) must all produce the same leg shape.

## Fix
Payload structure that Alpaca accepts:
```python
payload = {
    "qty": "1",
    "type": "market",
    "time_in_force": "day",
    "order_class": "mleg",
    "legs": [{"ratio_qty": "1", "side": "buy", "symbol": "..."}, ...]
}
```
If a previous sed edit damaged dict structure, rebuild the strategy-builder by hand — don't sed into Python dicts.

## Auto-fixable?
**No.** Payload bug indicates a code regression. Human review required so the fix lands in source, not just a retry.

## Prevention
- mleg API rules are documented in `docs/quantai-knowledge.md` § Alpaca API gotchas.
- Any change to `place_mleg_order()` or strategy-builders must be tested in `--dry-run` mode before merging.
