"""Six scenario tests with fake broker — lifecycle FSM integration.

Tests the full sequence: SubmitResult.from_broker → state_after_submit
→ advance (with time injection) → final state.

Phase 0 evidence shapes scenarios:
  (i)  Submitted-never-Filled → ACKED deadline → PHANTOM_NEVER_FILLED  (67% case)
  (ii) Filled-mid-poll → ACKED → FILLED → OPEN                         (happy path)
  (iii) place_mleg_order raises post-submit → UNKNOWN → ACKED → deadline
  (iv) Partial fill (filled_qty < ordered) → ACCEPTED, FILLED           (still counts as fill)
  (v)  Duplicate coid → idempotent noop
  (vi) Close Cancelled → OPEN retry → Filled → CLOSED                   (33% guard)
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from lifecycle.states import TradeState
from lifecycle.broker_adapter import SubmitResult, CloseResult, SubmitOutcome, CloseOutcome
from lifecycle.trade_lifecycle import TradeLifecycle


def _now_minus(minutes=0, seconds=0) -> datetime:
    return datetime.now(timezone.utc) - timedelta(minutes=minutes, seconds=seconds)


# ── (i) Submitted-never-Filled ────────────────────────────────────────────────

def test_submitted_never_filled_becomes_phantom(fake_broker):
    # Broker returns Submitted (indeterminate), order never fills
    fake_broker.submit_returns = [
        {"status": "Submitted", "order_id": "b123", "filled_qty": 0}
    ]
    raw = fake_broker.place_mleg_order([], client_order_id="coid-001")
    result = SubmitResult.from_broker(raw)

    assert result.outcome == SubmitOutcome.INDETERMINATE
    initial_state = TradeLifecycle.state_after_submit(result)
    assert initial_state == TradeState.ACKED

    # 16 minutes later — past 15min ACKED deadline
    record = {
        "id": "T001",
        "state": "ACKED",
        "order_id": "b123",
        "last_transition_at": _now_minus(minutes=16).isoformat(),
        "timestamp": _now_minus(minutes=16).isoformat(),
    }
    state, updates = TradeLifecycle.advance(record)
    assert state == TradeState.PHANTOM_NEVER_FILLED
    assert "close_reason" in updates


# ── (ii) Filled mid-poll ──────────────────────────────────────────────────────

def test_filled_mid_poll_reaches_open(fake_broker):
    # First poll: still Submitted. Second poll: Filled.
    fake_broker.submit_returns = [
        {"status": "Submitted", "order_id": "b124", "filled_qty": 0}
    ]
    raw = fake_broker.place_mleg_order([], client_order_id="coid-002")
    result = SubmitResult.from_broker(raw)
    assert result.outcome == SubmitOutcome.INDETERMINATE
    assert TradeLifecycle.state_after_submit(result) == TradeState.ACKED

    # FSM sees fill arrive: submit a "Filled" result
    filled_raw = {"status": "Filled", "order_id": "b124", "filled_qty": 1, "avg_fill_price": 1.0}
    fill_result = SubmitResult.from_broker(filled_raw)
    assert fill_result.outcome == SubmitOutcome.ACCEPTED
    assert TradeLifecycle.state_after_submit(fill_result) == TradeState.FILLED

    # advance from FILLED with leg present in broker positions
    record = {
        "id": "T002",
        "state": "FILLED",
        "legs": [{"symbol": "SPY261231C00450000"}],
        "last_transition_at": _now_minus(seconds=10).isoformat(),
    }
    state, _ = TradeLifecycle.advance(
        record,
        broker_positions={"SPY261231C00450000": {"qty": 1}}
    )
    assert state == TradeState.OPEN


# ── (iii) place_mleg_order raises post-submit ─────────────────────────────────

def test_post_submit_exception_yields_unknown(fake_broker):
    # Simulate broker returning a dict with order_id but no status
    # (what happens when broker module catches exception after dispatch)
    fake_broker.submit_returns = [{"order_id": "b125", "status": None}]
    raw = fake_broker.place_mleg_order([], client_order_id="coid-003")
    result = SubmitResult.from_broker(raw)
    assert result.outcome == SubmitOutcome.UNKNOWN
    # UNKNOWN → ACKED (broker saw the order, status lost in transit)
    assert TradeLifecycle.state_after_submit(result) == TradeState.ACKED

    # ACKED + 16 min → PHANTOM (same deadline as indeterminate)
    record = {
        "id": "T003",
        "state": "ACKED",
        "order_id": "b125",
        "last_transition_at": _now_minus(minutes=16).isoformat(),
    }
    state, _ = TradeLifecycle.advance(record)
    assert state == TradeState.PHANTOM_NEVER_FILLED


# ── (iv) Partial fill ────────────────────────────────────────────────────────

def test_partial_fill_counts_as_accepted(fake_broker):
    # filled_qty=1 out of ordered_qty=2 — still ACCEPTED (has a fill)
    fake_broker.submit_returns = [
        {"status": "Filled", "order_id": "b126", "filled_qty": 1, "avg_fill_price": 0.55}
    ]
    raw = fake_broker.place_mleg_order([], qty=2, client_order_id="coid-004")
    result = SubmitResult.from_broker(raw)
    assert result.outcome == SubmitOutcome.ACCEPTED
    assert result.filled_qty == 1
    assert TradeLifecycle.state_after_submit(result) == TradeState.FILLED


# ── (v) Duplicate coid → idempotent ──────────────────────────────────────────

def test_duplicate_coid_idempotent_noop():
    # Record is already OPEN — a second submit with same coid is a noop
    record = {"id": "T005", "state": "OPEN", "order_id": "b127"}
    assert TradeLifecycle.is_idempotent_noop(record, TradeState.OPEN) is True
    assert TradeLifecycle.is_idempotent_noop(record, TradeState.ACKED) is False

    # Terminal state is always past any non-terminal target
    closed = {"id": "T005", "state": "CLOSED"}
    assert TradeLifecycle.is_idempotent_noop(closed, TradeState.OPEN) is True


# ── (vi) Close Cancelled → retry → Filled → CLOSED ───────────────────────────

def test_close_cancelled_retries_then_fills(fake_broker):
    # First close attempt: Cancelled
    fake_broker.close_returns = [{"status": "Cancelled"}]
    raw1 = fake_broker.place_close_order({}, [])
    r1 = CloseResult.from_broker(raw1)
    assert r1.outcome == CloseOutcome.REJECTED
    # Rejected close → OPEN (allows retry)
    assert TradeLifecycle.state_after_close(r1) == TradeState.OPEN

    # Second close attempt: Filled
    fake_broker.close_returns = [{"status": "Filled", "order_id": "c999"}]
    raw2 = fake_broker.place_close_order({}, [])
    r2 = CloseResult.from_broker(raw2)
    assert r2.outcome == CloseOutcome.FILLED
    assert TradeLifecycle.state_after_close(r2) == TradeState.CLOSED
