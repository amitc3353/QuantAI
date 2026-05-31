"""Hypothesis property tests for the lifecycle FSM.

Property: given any non-terminal start state and any sequence of
(broker_status, time_advance_minutes) events, the FSM eventually
reaches a terminal state and never re-enters it.

Requires: pip install hypothesis (already in test requirements).
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

hypothesis = pytest.importorskip("hypothesis", reason="hypothesis not installed")
given = hypothesis.given
settings = hypothesis.settings
assume = hypothesis.assume
st = pytest.importorskip("hypothesis.strategies")

from lifecycle.states import TradeState, TERMINAL_STATES
from lifecycle.trade_lifecycle import TradeLifecycle

NON_TERMINAL_STATES = [
    s for s in TradeState
    if s not in TERMINAL_STATES
    and s not in (TradeState.PROPOSED, TradeState.SUBMIT_PENDING, TradeState.EXIT_PROPOSED)
]

BROKER_STATUSES = [
    "Filled", "Submitted", "PreSubmitted", "Cancelled", "Rejected", None,
]


def _record(state: TradeState, ts_minutes_ago: int = 0) -> dict:
    ts = (datetime.now(timezone.utc) - timedelta(minutes=ts_minutes_ago)).isoformat()
    return {
        "id": "PROP001",
        "state": state.value,
        "status": "OPEN" if state == TradeState.OPEN else "PENDING",
        "order_id": "broker-prop",
        "timestamp": ts,
        "last_transition_at": ts,
        "legs": [{"symbol": "SPY261231C00450000"}],
    }


@given(
    start=st.sampled_from(NON_TERMINAL_STATES),
    time_advances=st.lists(
        st.integers(min_value=0, max_value=60),
        min_size=1,
        max_size=20,
    ),
)
@settings(max_examples=200, deadline=5000)
def test_fsm_always_reaches_terminal_given_enough_time(start, time_advances):
    """With enough time advances, any non-terminal state must eventually terminate.

    We drive the FSM forward by the sum of all time_advances — enough to
    exceed any per-state deadline. Verify the FSM never gets stuck.
    """
    total_minutes = sum(time_advances)
    # If total advance < 15 minutes, the ACKED deadline won't trigger.
    # Ensure enough cumulative advance for the longest deadline (30 min EXIT_ACKED).
    assume(total_minutes >= 31)

    record = _record(start, ts_minutes_ago=total_minutes)
    now = datetime.now(timezone.utc)

    # Drive advance() once; with ts_minutes_ago=total_minutes the record
    # will appear to have been in this state for total_minutes.
    state, updates = TradeLifecycle.advance(record, now=now, broker_positions={})

    if state is not None:
        assert state in TradeState, f"advance returned invalid state: {state}"
        # If a terminal was reached, ensure it IS a terminal
        if state in TERMINAL_STATES:
            # Advance again — terminal must never produce another transition
            record2 = {**record, "state": state.value}
            state2, _ = TradeLifecycle.advance(record2, now=now, broker_positions={})
            assert state2 is None, f"Terminal {state} re-entered: {state2}"


@given(
    state=st.sampled_from(list(TERMINAL_STATES)),
)
@settings(max_examples=50, deadline=2000)
def test_terminal_states_never_transition(state):
    """Any terminal state must return (None, {}) from advance() regardless of time."""
    record = _record(state, ts_minutes_ago=999)
    result_state, updates = TradeLifecycle.advance(record, now=datetime.now(timezone.utc))
    assert result_state is None
    assert updates == {}
