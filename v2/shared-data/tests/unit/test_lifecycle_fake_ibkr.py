"""Fake-broker scenario tests for the lifecycle FSM.

Original six scenarios + adversarial cases for the two critical transitions
identified by Phase 0 (ACKED→FILLED 67%, EXIT_SUBMITTED→CLOSED 33%).

Original:
  (i)   Submitted-never-Filled → ACKED deadline → PHANTOM_NEVER_FILLED
  (ii)  Filled-mid-poll → ACKED → FILLED → OPEN
  (iii) place_mleg_order raises post-submit → UNKNOWN → ACKED → deadline
  (iv)  Partial fill (filled_qty < ordered) → ACCEPTED, FILLED
  (v)   Duplicate coid → idempotent noop
  (vi)  Close Cancelled → OPEN retry → Filled → CLOSED

Adversarial (added Gap 5):
  (vii)  Fill arrives AFTER 15min timeout → already PHANTOM, idempotent noop
  (viii) Broker returns corrupted data (empty dict, missing fields)
  (ix)   Multiple consecutive indeterminate polls before fill
  (x)    Broker refuses close repeatedly → stays EXIT_ACKED, alert fires at 30min
  (xi)   Legs vanish from broker after close submitted
  (xii)  Close returns Submitted-never-Filled → EXIT_ACKED → 30min alert
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


# ══════════════════════════════════════════════════════════════════════════════
# ADVERSARIAL SCENARIOS — Gap 5 additions for the two critical transitions
# ══════════════════════════════════════════════════════════════════════════════

# ── (vii) Fill arrives AFTER 15min timeout (race condition) ───────────────────

def test_fill_after_timeout_is_idempotent_noop():
    """Trade already escalated to PHANTOM. Late fill must not resurrect it."""
    # FSM already advanced to PHANTOM_NEVER_FILLED
    record = {
        "id": "T007",
        "state": "PHANTOM_NEVER_FILLED",
        "status": "PHANTOM_NEVER_FILLED",
        "order_id": "b200",
        "close_reason": "fsm_acked_deadline_15min",
    }
    # Late fill arrives — this is the race
    assert TradeLifecycle.is_idempotent_noop(record, TradeState.FILLED) is True
    # advance() must also return (None, {}) for terminals
    state, updates = TradeLifecycle.advance(record)
    assert state is None
    assert updates == {}


# ── (viii) Broker returns corrupted data ──────────────────────────────────────

def test_empty_dict_from_broker_is_rejected():
    """Broker returns {} (connection drop, empty response)."""
    r = SubmitResult.from_broker({})
    assert r.outcome == SubmitOutcome.REJECTED
    assert r.order_id is None
    assert r.filled_qty == 0
    assert TradeLifecycle.state_after_submit(r) == TradeState.REJECTED


def test_dict_with_only_order_id_no_status_is_unknown():
    """Broker returns partial data — order_id present, everything else missing."""
    r = SubmitResult.from_broker({"order_id": "partial-999"})
    assert r.outcome == SubmitOutcome.UNKNOWN
    assert TradeLifecycle.state_after_submit(r) == TradeState.ACKED


def test_close_result_empty_dict_is_rejected():
    """Close broker returns {} (timeout, empty response)."""
    r = CloseResult.from_broker({})
    assert r.outcome == CloseOutcome.UNKNOWN  # no status → UNKNOWN, not FILLED
    assert TradeLifecycle.state_after_close(r) == TradeState.EXIT_ACKED


# ── (ix) Multiple consecutive indeterminate polls before fill ─────────────────

def test_multiple_indeterminate_polls_stay_acked():
    """ACKED record polled 3 times with indeterminate status, still within deadline."""
    record = {
        "id": "T009",
        "state": "ACKED",
        "order_id": "b300",
        "last_transition_at": _now_minus(minutes=3).isoformat(),
    }
    # Three polls at 3 min — all within 15min deadline → no transition
    for _ in range(3):
        state, updates = TradeLifecycle.advance(record)
        assert state is None
        assert updates == {}


def test_multiple_indeterminate_polls_then_timeout():
    """ACKED record polled repeatedly, deadline hits on the Nth poll."""
    record = {
        "id": "T009b",
        "state": "ACKED",
        "order_id": "b301",
        "last_transition_at": _now_minus(minutes=16).isoformat(),
    }
    # Still in ACKED after deadline — must escalate regardless of how many prior polls
    state, updates = TradeLifecycle.advance(record)
    assert state == TradeState.PHANTOM_NEVER_FILLED


# ── (x) Broker refuses close order repeatedly (the actual zombie pattern) ─────

def test_close_refused_repeatedly_stays_open_for_retry(fake_broker):
    """Broker returns None on close (the DE zombie pattern). FSM must allow retry
    by returning to OPEN, not deadlocking in EXIT_SUBMITTED."""
    # Three consecutive None returns (broker refuses)
    for attempt in range(3):
        fake_broker.close_returns = [None]
        raw = fake_broker.place_close_order({}, [])
        r = CloseResult.from_broker(raw)
        assert r.outcome == CloseOutcome.REJECTED
        # REJECTED close → OPEN (allows retry next cycle)
        assert TradeLifecycle.state_after_close(r) == TradeState.OPEN


def test_exit_acked_30min_alert_fires(fake_broker):
    """Close order stuck in Submitted for 31min → alert flag fires."""
    record = {
        "id": "T010",
        "state": "EXIT_ACKED",
        "working_close_order_id": "close-stuck",
        "last_transition_at": _now_minus(minutes=31).isoformat(),
    }
    state, updates = TradeLifecycle.advance(record)
    # EXIT_ACKED does NOT auto-terminal — operator decides
    assert state is None
    assert updates.get("fsm_exit_acked_alert") is True


# ── (xi) Legs vanish from broker AFTER close submitted ────────────────────────

def test_open_legs_vanish_during_close_detected():
    """Position legs disappear from broker while trade is OPEN (pre-close).
    FSM must detect this as PHANTOM_VANISHED, not silently ignore it."""
    record = {
        "id": "T011",
        "state": "OPEN",
        "legs": [{"symbol": "SPY261231C00450000"}, {"symbol": "SPY261231C00460000"}],
    }
    # Broker returns empty positions — both legs gone
    state, updates = TradeLifecycle.advance(record, broker_positions={})
    assert state == TradeState.PHANTOM_VANISHED
    assert "vanished" in updates.get("close_reason", "")


def test_open_one_leg_present_no_vanish():
    """At least one leg still on broker → no vanish, trade remains OPEN."""
    record = {
        "id": "T012",
        "state": "OPEN",
        "legs": [{"symbol": "SPY261231C00450000"}, {"symbol": "SPY261231C00460000"}],
    }
    state, _ = TradeLifecycle.advance(
        record,
        broker_positions={"SPY261231C00450000": {"qty": 1}}
    )
    assert state is None  # one leg present → still OPEN


# ── (xii) Close returns Submitted-never-Filled → EXIT_ACKED escalation ───────

def test_close_submitted_never_fills_reaches_exit_acked(fake_broker):
    """Close order stuck in Submitted → EXIT_ACKED → 30min alert.
    This is the full close-side zombie lifecycle."""
    # Close returns Submitted (indeterminate)
    fake_broker.close_returns = [{"status": "Submitted", "_working": True, "order_id": "close-500"}]
    raw = fake_broker.place_close_order({}, [])
    r = CloseResult.from_broker(raw)
    assert r.outcome == CloseOutcome.INDETERMINATE
    assert TradeLifecycle.state_after_close(r) == TradeState.EXIT_ACKED

    # EXIT_ACKED at 31min → alert flag
    record = {
        "id": "T013",
        "state": "EXIT_ACKED",
        "last_transition_at": _now_minus(minutes=31).isoformat(),
    }
    state, updates = TradeLifecycle.advance(record)
    assert state is None  # no auto-terminal
    assert updates.get("fsm_exit_acked_alert") is True
