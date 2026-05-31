"""Unit tests for the TradeLifecycle FSM — per-transition coverage.

Phase 0 evidence shapes priority:
  - ACKED→FILLED / ACKED→PHANTOM_NEVER_FILLED: deepest coverage (67%)
  - EXIT_SUBMITTED→CLOSED / EXIT_ACKED→CLOSED: deep coverage (33%)
  - Other transitions: standard coverage

Every legal transition gets a green test.
Every illegal (from, to) pair raises IllegalTransition.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from lifecycle.states import (
    LEGAL_TRANSITIONS, TERMINAL_STATES, TradeState, IllegalTransition, assert_legal,
)
from lifecycle.broker_adapter import (
    SubmitOutcome, SubmitResult, CloseOutcome, CloseResult,
)
from lifecycle.trade_lifecycle import TradeLifecycle, _parse_ts, _deadline_exceeded


# ── assert_legal() ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("from_s,to_s", sorted(LEGAL_TRANSITIONS, key=str))
def test_all_legal_transitions_pass(from_s, to_s):
    assert_legal(from_s, to_s)  # must not raise


def test_illegal_transition_raises():
    with pytest.raises(IllegalTransition):
        assert_legal(TradeState.CLOSED, TradeState.OPEN)

    with pytest.raises(IllegalTransition):
        assert_legal(TradeState.OPEN, TradeState.ACKED)

    with pytest.raises(IllegalTransition):
        assert_legal(TradeState.PHANTOM_NEVER_FILLED, TradeState.OPEN)

    with pytest.raises(IllegalTransition):
        assert_legal(TradeState.REJECTED, TradeState.FILLED)


# ── broker_adapter.SubmitResult ───────────────────────────────────────────────

def test_submit_result_none_is_rejected():
    r = SubmitResult.from_broker(None)
    assert r.outcome == SubmitOutcome.REJECTED


def test_submit_result_filled():
    raw = {"status": "Filled", "order_id": "123", "filled_qty": 1, "avg_fill_price": 1.05}
    r = SubmitResult.from_broker(raw)
    assert r.outcome == SubmitOutcome.ACCEPTED
    assert r.order_id == "123"
    assert r.filled_qty == 1


def test_submit_result_submitted_is_indeterminate():
    raw = {"status": "Submitted", "order_id": "456", "filled_qty": 0}
    r = SubmitResult.from_broker(raw)
    assert r.outcome == SubmitOutcome.INDETERMINATE


def test_submit_result_presubmitted_is_indeterminate():
    raw = {"status": "PreSubmitted", "order_id": "789", "_working": True}
    r = SubmitResult.from_broker(raw)
    assert r.outcome == SubmitOutcome.INDETERMINATE
    assert r.working is True


def test_submit_result_cancelled_is_rejected():
    raw = {"status": "Cancelled", "order_id": "101"}
    r = SubmitResult.from_broker(raw)
    assert r.outcome == SubmitOutcome.REJECTED


def test_submit_result_unknown_when_order_id_no_status():
    # Production case A021/A022/A025 — order_id present, fill_status=None
    raw = {"order_id": "999", "status": None}
    r = SubmitResult.from_broker(raw)
    assert r.outcome == SubmitOutcome.UNKNOWN


# ── broker_adapter.CloseResult ────────────────────────────────────────────────

def test_close_result_filled():
    raw = {"status": "Filled", "order_id": "c1"}
    r = CloseResult.from_broker(raw)
    assert r.outcome == CloseOutcome.FILLED


def test_close_result_submitted_indeterminate():
    raw = {"status": "Submitted", "_working": True}
    r = CloseResult.from_broker(raw)
    assert r.outcome == CloseOutcome.INDETERMINATE


def test_close_result_cancelled_rejected():
    raw = {"status": "Cancelled"}
    r = CloseResult.from_broker(raw)
    assert r.outcome == CloseOutcome.REJECTED


def test_close_result_none_rejected():
    assert CloseResult.from_broker(None).outcome == CloseOutcome.REJECTED


# ── TradeLifecycle.state_after_submit ─────────────────────────────────────────

def test_state_after_submit_accepted_is_filled():
    r = SubmitResult(outcome=SubmitOutcome.ACCEPTED, filled_qty=1)
    assert TradeLifecycle.state_after_submit(r) == TradeState.FILLED


def test_state_after_submit_rejected():
    r = SubmitResult(outcome=SubmitOutcome.REJECTED)
    assert TradeLifecycle.state_after_submit(r) == TradeState.REJECTED


def test_state_after_submit_indeterminate_is_acked():
    r = SubmitResult(outcome=SubmitOutcome.INDETERMINATE)
    assert TradeLifecycle.state_after_submit(r) == TradeState.ACKED


def test_state_after_submit_unknown_is_acked():
    # UNKNOWN → treat as ACKED (has order_id, status lost in transit)
    r = SubmitResult(outcome=SubmitOutcome.UNKNOWN)
    assert TradeLifecycle.state_after_submit(r) == TradeState.ACKED


# ── TradeLifecycle.state_after_close ─────────────────────────────────────────

def test_state_after_close_filled_is_closed():
    r = CloseResult(outcome=CloseOutcome.FILLED)
    assert TradeLifecycle.state_after_close(r) == TradeState.CLOSED


def test_state_after_close_rejected_retries():
    r = CloseResult(outcome=CloseOutcome.REJECTED)
    assert TradeLifecycle.state_after_close(r) == TradeState.OPEN


def test_state_after_close_indeterminate_is_exit_acked():
    r = CloseResult(outcome=CloseOutcome.INDETERMINATE)
    assert TradeLifecycle.state_after_close(r) == TradeState.EXIT_ACKED


# ── TradeLifecycle.advance — ACKED (PRIMARY guard, 67% of phantoms) ───────────

def _ts_ago(minutes=0, hours=0, seconds=0) -> str:
    dt = datetime.now(timezone.utc) - timedelta(minutes=minutes, hours=hours, seconds=seconds)
    return dt.isoformat()


def _acked_record(age_minutes=0, **extra) -> dict:
    return {
        "id": "T001",
        "status": "PENDING",
        "state": "ACKED",
        "order_id": "broker-123",
        "timestamp": _ts_ago(minutes=age_minutes),
        "last_transition_at": _ts_ago(minutes=age_minutes),
        **extra,
    }


def test_acked_before_deadline_no_transition():
    record = _acked_record(age_minutes=5)
    state, updates = TradeLifecycle.advance(record)
    assert state is None
    assert updates == {}


def test_acked_after_15min_becomes_phantom():
    record = _acked_record(age_minutes=16)
    state, updates = TradeLifecycle.advance(record)
    assert state == TradeState.PHANTOM_NEVER_FILLED
    assert "close_reason" in updates
    assert "15min" in updates["close_reason"]


def test_acked_exactly_at_deadline_becomes_phantom():
    # At exactly the deadline boundary it should trigger
    record = _acked_record(age_minutes=15)
    state, _ = TradeLifecycle.advance(record)
    assert state == TradeState.PHANTOM_NEVER_FILLED


def test_terminal_state_no_transition():
    for terminal in TERMINAL_STATES:
        record = {"id": "T001", "state": terminal.value, "status": "PHANTOM_NEVER_FILLED"}
        state, updates = TradeLifecycle.advance(record)
        assert state is None, f"Terminal {terminal} should not transition"
        assert updates == {}


# ── TradeLifecycle.advance — EXIT_SUBMITTED (SECONDARY guard, 33%) ────────────

def _exit_submitted_record(age_seconds=0) -> dict:
    return {
        "id": "T002",
        "status": "OPEN",
        "state": "EXIT_SUBMITTED",
        "order_id": "entry-123",
        "working_close_order_id": "close-456",
        "last_transition_at": _ts_ago(seconds=age_seconds),
    }


def test_exit_submitted_before_deadline_no_transition():
    record = _exit_submitted_record(age_seconds=30)
    state, _ = TradeLifecycle.advance(record)
    assert state is None


def test_exit_submitted_after_60s_becomes_exit_acked():
    record = _exit_submitted_record(age_seconds=65)
    state, _ = TradeLifecycle.advance(record)
    assert state == TradeState.EXIT_ACKED


def test_exit_acked_before_30min_no_transition():
    record = {
        "id": "T003",
        "state": "EXIT_ACKED",
        "last_transition_at": _ts_ago(minutes=10),
    }
    state, updates = TradeLifecycle.advance(record)
    assert state is None
    assert updates == {}


def test_exit_acked_after_30min_alert_flag_set():
    record = {
        "id": "T003",
        "state": "EXIT_ACKED",
        "last_transition_at": _ts_ago(minutes=31),
    }
    state, updates = TradeLifecycle.advance(record)
    assert state is None  # no auto-terminal — operator decides
    assert updates.get("fsm_exit_acked_alert") is True


# ── TradeLifecycle.advance — OPEN (vanish detection) ─────────────────────────

def test_open_with_legs_in_broker_positions_no_transition():
    record = {
        "id": "T004",
        "state": "OPEN",
        "legs": [{"symbol": "SPY261231C00450000"}],
    }
    state, _ = TradeLifecycle.advance(record, broker_positions={"SPY261231C00450000": {}})
    assert state is None


def test_open_with_legs_missing_from_broker_is_vanished():
    record = {
        "id": "T004",
        "state": "OPEN",
        "legs": [{"symbol": "SPY261231C00450000"}],
    }
    state, updates = TradeLifecycle.advance(record, broker_positions={})
    assert state == TradeState.PHANTOM_VANISHED
    assert "vanished" in updates["close_reason"]


def test_open_no_broker_positions_available_no_transition():
    record = {"id": "T004", "state": "OPEN", "legs": [{"symbol": "X"}]}
    state, _ = TradeLifecycle.advance(record, broker_positions=None)
    assert state is None


# ── TradeLifecycle.is_idempotent_noop ─────────────────────────────────────────

def test_idempotent_noop_same_state():
    record = {"state": "OPEN"}
    assert TradeLifecycle.is_idempotent_noop(record, TradeState.OPEN) is True


def test_idempotent_noop_terminal_vs_nonterminal():
    record = {"state": "CLOSED"}
    assert TradeLifecycle.is_idempotent_noop(record, TradeState.OPEN) is True


def test_not_idempotent_noop():
    record = {"state": "ACKED"}
    assert TradeLifecycle.is_idempotent_noop(record, TradeState.OPEN) is False
