# FSM Coverage Gap — Resurrected Phantoms

**Discovered:** 2026-06-01 during fsm_baseline_v1 reset.
**Severity:** Medium — silent journal-broker divergence on a path the FSM cannot detect.
**Status:** Documented, fix deferred to post-reset stabilisation.

## The bug

Eight Gamma journal entries (Ga004/Gb004/Gc005/Gd005 WMT spreads, Ga005/Gb005/Gc006/Gd006 APD spreads) were marked `status=PHANTOM_NEVER_FILLED` by `_escalate_stale_phantoms` after the 3-hour timeout. But the broker had **actually filled** those orders — they were live spreads totalling $3.84K of cost basis when the reset began.

`LIFECYCLE_FSM_MODE=enforce` was active. Position monitor ran every 2 minutes during market hours throughout the period. The FSM never noticed the divergence.

## Why the FSM missed it

`TradeLifecycle.advance(record, ...)` short-circuits at the top:

```python
current = get_state(record)
if current is None or current in TERMINAL_STATES:
    return None, {}
```

`PHANTOM_NEVER_FILLED` is in `TERMINAL_STATES` (correct — it IS a terminal). Once a record is marked, the FSM treats it as forensic data and never re-evaluates. So:

1. Order submitted → state=ACKED (correct)
2. 15-min ACKED deadline → FSM advances to PHANTOM_NEVER_FILLED (correct policy; broker hadn't reported fill yet)
3. Broker actually fills 30+ minutes later (long latency on combo orders during illiquid windows)
4. Position now exists at broker, but journal says terminal phantom
5. FSM's `advance()` skips terminal records → never reconciles → silent drift

The pre-existing `reconcile_ghost_positions()` in `position_monitor.py:1219` detects this case as `journal_lie` ("broker has position, journal says CLOSED for that contract") — but only for journal entries in **CLOSED** state, not PHANTOM_NEVER_FILLED. So the inverse-direction journal lie was invisible.

## Why the 3h deadline alone wasn't enough

The original `_escalate_stale_phantoms` (added 2026-05-29 in commit 21edf18) marks status to a terminal state **but does NOT call `broker.cancel_order`**. So if the order was sitting at IBKR in PreSubmit at the moment the deadline fired, IBKR could still fill it later. The FSM's per-state deadline (15min ACKED) is tighter than the 3h global escalator but has the same gap — neither cancels at the broker.

This is also a documented Phase 0 finding: "Phase 4 (close-path FSM) must include a `broker.cancel_order` call on escalation, not just a journal mark." That fix never landed in the close path proper — it was deferred for the reset.

## Fix design (deferred)

Add a `resurrection check` pass to `position_monitor.main()`, run AFTER `reconcile_ghost_positions`:

```python
def _detect_resurrected_phantoms(broker_positions: dict, all_trades: list) -> list[dict]:
    """For every PHANTOM_NEVER_FILLED record, check if any leg appears at broker.
    Returns the list of resurrected trade dicts."""
    resurrected = []
    for trade in all_trades:
        if trade.get("status") != "PHANTOM_NEVER_FILLED":
            continue
        legs = trade.get("legs", [])
        symbols = {str(lg.get("symbol", "")) for lg in legs}
        if symbols & set(broker_positions.keys()):
            resurrected.append(trade)
    return resurrected
```

On detection: post a Discord alert + flip journal to a NEW state (`PHANTOM_RESURRECTED`) so it shows up in dashboards and operator can decide:
- Close it via FSM exit path (treat as orphan OPEN trade)
- Or let it run if the operator wants the position

A second prevention layer: on `_escalate_stale_phantoms` (and the FSM's ACKED→PHANTOM_NEVER_FILLED transition), call `broker.cancel_order(order_id)` BEFORE marking the journal terminal. This eliminates the resurrection-by-late-fill case at the root.

## Workaround until the fix lands

Daily operational check at market close: `python3 -c "from _broker_ibkr import IBKRBroker; ..."` to enumerate broker positions and cross-reference against journal `status=OPEN` entries. Any mismatch is investigated manually.

The Phase 0 diagnostic `classify_phantoms.py` could be extended to flag this in the O4 bucket — currently it only catches the original phantom→vanish direction.

## Related

- `docs/2026-05-30-trade-lifecycle-fsm-plan.md` — Phase 0 evidence and FSM design
- `v2/shared-data/scripts/position_monitor.py:1219` — `reconcile_ghost_positions` (catches CLOSED-direction lies)
- `v2/shared-data/scripts/position_monitor.py:1048` — `_escalate_stale_phantoms` (no cancel call)
- `v2/shared-data/scripts/lifecycle/trade_lifecycle.py` — `advance()` terminal-state short-circuit
