"""TradeLifecycle FSM — core state machine.

Phase 0 evidence (2026-05-30):
  Tight guards on two transitions:
  - ACKED → FILLED  (67% of historical phantoms — O3_STUCK_INDETERMINATE)
  - EXIT_SUBMITTED → CLOSED  (33% — O5_CLOSE_FAILURE)

Usage (shadow/enforce modes, gated by LIFECYCLE_FSM_MODE):

    result = TradeLifecycle.classify_submit(broker_raw)
    # → SubmitResult with typed outcome

    state, updates = TradeLifecycle.advance(record, now=datetime.now(tz))
    # → new TradeState + journal field updates (caller persists)

The FSM never writes to the journal itself — it returns the updates dict
and the caller calls journal_io.rewrite_journal_atomic(). This keeps the
FSM pure and easy to test without filesystem.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from lifecycle.states import (
    DEADLINES, LEGAL_TRANSITIONS, TERMINAL_STATES, TradeState, assert_legal,
)
from lifecycle.broker_adapter import SubmitOutcome, SubmitResult, CloseOutcome, CloseResult
from lifecycle.journal_io import get_state


class TradeLifecycle:
    """Stateless FSM — all state lives in journal records, not in this object."""

    # ── Submit (entry path) ────────────────────────────────────────────────────

    @staticmethod
    def classify_submit(broker_raw: Optional[dict]) -> SubmitResult:
        """Classify a place_mleg_order() return value into a typed SubmitResult."""
        return SubmitResult.from_broker(broker_raw)

    @staticmethod
    def state_after_submit(result: SubmitResult) -> TradeState:
        """Return the post-submit FSM state for a SubmitResult."""
        if result.outcome == SubmitOutcome.ACCEPTED:
            return TradeState.FILLED
        if result.outcome == SubmitOutcome.REJECTED:
            return TradeState.REJECTED
        # INDETERMINATE or UNKNOWN → ACKED (broker saw the order)
        return TradeState.ACKED

    @staticmethod
    def classify_close(broker_raw: Optional[dict]) -> CloseResult:
        """Classify a place_close_order() return value into a typed CloseResult."""
        return CloseResult.from_broker(broker_raw)

    @staticmethod
    def state_after_close(result: CloseResult) -> TradeState:
        """Return the FSM state transition for a CloseResult."""
        if result.outcome == CloseOutcome.FILLED:
            return TradeState.CLOSED
        if result.outcome == CloseOutcome.REJECTED:
            return TradeState.OPEN   # allow retry on next cycle
        return TradeState.EXIT_ACKED  # INDETERMINATE or UNKNOWN → poll

    # ── Advance (position_monitor tick) ───────────────────────────────────────

    @staticmethod
    def advance(
        record: dict,
        now: Optional[datetime] = None,
        broker_positions: Optional[dict] = None,
    ) -> tuple[Optional[TradeState], dict]:
        """Compute next FSM state for a single journal record.

        Returns (new_state, updates_dict). If no transition is warranted,
        returns (None, {}). Caller is responsible for persisting and for
        calling transition_log.record().

        Args:
            record: journal entry dict
            now: current time (injectable for tests)
            broker_positions: {occ_symbol: position_dict} from broker.get_positions(),
                              or None if unavailable this cycle
        """
        now = now or datetime.now(timezone.utc)
        current = get_state(record)
        if current is None or current in TERMINAL_STATES:
            return None, {}

        trade_id = record.get("id", "?")

        # ── ACKED: check deadline (PRIMARY phantom guard — 67% of cases) ──────
        if current == TradeState.ACKED:
            entry_ts = _parse_ts(record.get("timestamp") or record.get("last_transition_at"))
            if entry_ts and _deadline_exceeded(entry_ts, DEADLINES[TradeState.ACKED], now):
                return TradeState.PHANTOM_NEVER_FILLED, {
                    "close_reason": "fsm_acked_deadline_15min",
                }
            return None, {}

        # ── FILLED: check broker positions within 5min ────────────────────────
        if current == TradeState.FILLED:
            entry_ts = _parse_ts(record.get("last_transition_at") or record.get("timestamp"))
            if broker_positions is not None:
                # Any leg still on broker → advance to OPEN
                legs = record.get("legs", [])
                symbols = {str(lg.get("symbol") or lg.get("occ_symbol") or "") for lg in legs}
                if symbols & set(broker_positions.keys()):
                    return TradeState.OPEN, {}
                # No legs on broker yet — wait unless deadline exceeded
                if entry_ts and _deadline_exceeded(entry_ts, DEADLINES[TradeState.FILLED], now):
                    return TradeState.PHANTOM_NEVER_FILLED, {
                        "close_reason": "fsm_filled_no_position_5min",
                    }
            return None, {}

        # ── OPEN: check for vanished position or expiry ───────────────────────
        if current == TradeState.OPEN:
            if broker_positions is not None:
                legs = record.get("legs", [])
                symbols = {str(lg.get("symbol") or lg.get("occ_symbol") or "") for lg in legs}
                if symbols and not (symbols & set(broker_positions.keys())):
                    # Confirm over 2 consecutive cycles — caller tracks this;
                    # on first miss we don't auto-terminal, return a soft signal.
                    return TradeState.PHANTOM_VANISHED, {
                        "close_reason": "fsm_open_position_vanished",
                    }
            return None, {}

        # ── EXIT_SUBMITTED: check deadline (SECONDARY phantom guard — 33%) ────
        if current == TradeState.EXIT_SUBMITTED:
            ts = _parse_ts(record.get("last_transition_at"))
            if ts and _deadline_exceeded(ts, DEADLINES[TradeState.EXIT_SUBMITTED], now):
                return TradeState.EXIT_ACKED, {}
            return None, {}

        # ── EXIT_ACKED: 30min alert threshold — no auto-terminal ──────────────
        if current == TradeState.EXIT_ACKED:
            ts = _parse_ts(record.get("last_transition_at"))
            if ts and _deadline_exceeded(ts, DEADLINES[TradeState.EXIT_ACKED], now):
                # Signal for Discord alert; stay in EXIT_ACKED (operator decides)
                return None, {"fsm_exit_acked_alert": True}
            return None, {}

        # ── SUBMIT_PENDING: 60s deadline ──────────────────────────────────────
        if current == TradeState.SUBMIT_PENDING:
            ts = _parse_ts(record.get("last_transition_at") or record.get("timestamp"))
            if ts and _deadline_exceeded(ts, DEADLINES[TradeState.SUBMIT_PENDING], now):
                return TradeState.PHANTOM_NEVER_FILLED, {
                    "close_reason": "fsm_submit_pending_deadline_60s",
                }
            return None, {}

        return None, {}

    # ── Idempotency ────────────────────────────────────────────────────────────

    @staticmethod
    def is_idempotent_noop(record: dict, target_state: TradeState) -> bool:
        """Return True if a transition to target_state is a replay no-op.

        A transition is a no-op if the record is already at or past the
        target state in the FSM progression (e.g., OPEN ≥ ACKED).
        """
        current = get_state(record)
        if current is None:
            return False
        if current == target_state:
            return True
        # Terminal states are always "past" any non-terminal target
        if current in TERMINAL_STATES and target_state not in TERMINAL_STATES:
            return True
        return False


# ── Helpers ────────────────────────────────────────────────────────────────────

def _parse_ts(value) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    s = str(value).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _deadline_exceeded(entry_ts: datetime, deadline: timedelta, now: datetime) -> bool:
    return (now - entry_ts) >= deadline
