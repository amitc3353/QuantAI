# Runbook: Close order failed for {trade_id}

**Catalog entries**: `recurring-3c6683b1` (root cause), `recurring-74290d3d`, `recurring-09cfb5d0`, `recurring-2cf36ff6` (per-symbol echoes)

## Symptom

`position_monitor.py` logs repeating lines like:

```
EXIT triggered: A008 (hard_close_15_30) — P&L $+36.00
  Close order FAILED 422: mleg orders must have at least 2 legs and at most 4 legs
  Close order failed for A008 (attempt N/5) — will retry next cycle
```

Trade stays `OPEN` in `/root/quantai-v2/shared-data/journal/paper/trades.jsonl` even though it should have closed.

## Root cause

The trade in the journal still says it has 2 legs (e.g., a diagonal spread), but on Alpaca only **one** of those legs is actually held — the other was assigned, expired, or closed outside the pipeline. `build_closing_legs()` returns just the surviving leg, and Alpaca's `mleg` order class requires 2-4 legs, so it 422s.

## Diagnosis

```bash
# 1. Find recent failures
sudo tail -500 /root/quantai-v2/shared-data/logs/position_monitor.log \
    | grep -B1 "Close order FAILED" | tail -30

# 2. Check journal vs Alpaca for the affected trade
TRADE_ID=A008
sudo python3 -c "
import json
trades = [json.loads(l) for l in open('/root/quantai-v2/shared-data/journal/paper/trades.jsonl') if l.strip()]
t = next(t for t in trades if t['id'] == '$TRADE_ID')
print(f\"Journal says: {len(t['legs'])} legs\")
for l in t['legs']: print(f\"  {l['action']} {l['type']} {l['strike']} {l['expiry']}\")
"

# 3. Check current Alpaca positions
curl -sS "https://paper-api.alpaca.markets/v2/positions" \
    -H "APCA-API-KEY-ID: $ALPACA_API_KEY" \
    -H "APCA-API-SECRET-KEY: $ALPACA_SECRET_KEY" \
    | python3 -c "import json,sys; ps=json.load(sys.stdin); [print(p['symbol'], p['qty']) for p in ps if p.get('asset_class')=='us_option']"
```

## Common causes + fixes

### A) Single leg remaining (most common)

The pipeline now handles this automatically:
- `position_monitor.py:place_close_order()` detects `len(legs) == 1` and falls back to a single-leg market order instead of mleg.
- After 5 failed attempts (`MAX_CLOSE_ATTEMPTS`), the trade is left OPEN, a Discord alert fires once, and retries stop until manual intervention.

If it still fails: check the Alpaca order rejection reason — most likely the long leg has zero bid (deep OTM or near-zero time value) and the market order can't fill.

### B) Zero legs remaining (broker-closed)

The pipeline now handles this too:
- `build_closing_legs()` returns `[]`.
- main() marks the journal entry `status: "CLOSED"`, `exit_reason: "closed_outside_pipeline"`, P&L preserved from the last live calculation.

### C) Close attempted after market hours

Was the dominant noise source before the fix. `position_monitor.py:main()` now calls `is_market_open()` and skips all close attempts outside 09:30–16:00 ET on weekdays. P&L still records; closes wait for the next session.

### D) Unbounded retries

Now bounded at 5 attempts per trade. Counter persisted at `/root/quantai-v2/shared-data/cache/close_attempts.json` and reset on successful close.

## Manual recovery (when the pipeline gives up)

If you see a Discord alert "Position close gave up after 5 attempts":

1. Inspect the trade in Alpaca dashboard: https://paper-app.alpaca.markets/positions
2. Manually close the residual leg(s).
3. Edit the journal to mark CLOSED:
   ```bash
   sudo python3 -c "
   import json
   path = '/root/quantai-v2/shared-data/journal/paper/trades.jsonl'
   trades = [json.loads(l) for l in open(path) if l.strip()]
   for t in trades:
       if t['id'] == 'AXXX':
           t['status'] = 'CLOSED'
           t['exit_reason'] = 'manual_close'
           t['exit_timestamp'] = '2026-XX-XXTXX:XX:00-04:00'
   with open(path, 'w') as f:
       for t in trades: f.write(json.dumps(t) + '\n')
   "
   ```
4. Reset the attempt counter: `sudo rm -f /root/quantai-v2/shared-data/cache/close_attempts.json`

## Prevention

- The tighter `error_learner.py` (post-2026-04-26) won't re-catalog these messages with `severity: "unknown"`. They land in the catalog with proper severity + this runbook reference.
- The `error_detector.py` only posts `severity: "warning"` or `"critical"` to Discord, throttled at 60 min per `eid`. The 70/hour flood is gone.
