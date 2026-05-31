"""Trade lifecycle states, legal transitions, and per-state deadlines.

Phase 0 evidence (2026-05-30):
  - 67% O3_STUCK_INDETERMINATE (ACKED→FILLED path)
  - 33% O5_CLOSE_FAILURE (EXIT_SUBMITTED→CLOSED path)
Tight guards + heavy coverage concentrate on those two transitions.
"""
from __future__ import annotations

from datetime import timedelta
from enum import Enum


class TradeState(str, Enum):
    # ── Ephemeral (in-memory only, never persisted) ──────────────────────────
    PROPOSED        = "PROPOSED"       # agent minted coid, no broker call yet
    SUBMIT_PENDING  = "SUBMIT_PENDING" # placeOrder dispatched, awaiting ACK
    EXIT_PROPOSED   = "EXIT_PROPOSED"  # exit rule fired this tick

    # ── Durable (persisted in journal `state` field) ─────────────────────────
    ACKED           = "ACKED"          # broker returned non-failure status
    FILLED          = "FILLED"         # at least one leg filled
    OPEN            = "OPEN"           # legs reconciled vs broker positions()
    EXIT_SUBMITTED  = "EXIT_SUBMITTED" # close placeOrder dispatched
    EXIT_ACKED      = "EXIT_ACKED"     # close indeterminate; polling

    # ── Durable terminals ─────────────────────────────────────────────────────
    CLOSED              = "CLOSED"              # close filled AND legs gone from broker
    REJECTED            = "REJECTED"            # entry hit terminal-failure status
    PHANTOM_NEVER_FILLED = "PHANTOM_NEVER_FILLED"  # submitted/acked past deadline
    PHANTOM_VANISHED    = "PHANTOM_VANISHED"    # was OPEN, broker lost the position
    EXPIRED             = "EXPIRED"             # leg expiry reached without close


# States that must be persisted to the journal immediately on transition.
DURABLE_STATES = {
    TradeState.ACKED,
    TradeState.FILLED,
    TradeState.OPEN,
    TradeState.EXIT_SUBMITTED,
    TradeState.EXIT_ACKED,
    TradeState.CLOSED,
    TradeState.REJECTED,
    TradeState.PHANTOM_NEVER_FILLED,
    TradeState.PHANTOM_VANISHED,
    TradeState.EXPIRED,
}

TERMINAL_STATES = {
    TradeState.CLOSED,
    TradeState.REJECTED,
    TradeState.PHANTOM_NEVER_FILLED,
    TradeState.PHANTOM_VANISHED,
    TradeState.EXPIRED,
}

# Legacy status strings → canonical TradeState (used by backfill_state.py
# and by the FSM when reading pre-FSM journal records).
LEGACY_STATUS_MAP = {
    "OPEN":                 TradeState.OPEN,
    "CLOSED":               TradeState.CLOSED,
    "EXPIRED":              TradeState.EXPIRED,
    "PHANTOM_NEVER_FILLED": TradeState.PHANTOM_NEVER_FILLED,
    # PENDING with order_id → ACKED; without → SUBMIT_PENDING (handled in backfill)
    "PENDING":              None,  # needs order_id check; caller handles
}

# Per-state deadlines. Missing = no deadline.
# EXIT_ACKED is alert-only (no auto-terminal — operator decides close-side failures).
DEADLINES: dict[TradeState, timedelta] = {
    TradeState.SUBMIT_PENDING: timedelta(seconds=60),
    TradeState.ACKED:          timedelta(minutes=15),    # PRIMARY guard (67% of phantoms)
    TradeState.FILLED:         timedelta(minutes=5),
    TradeState.EXIT_SUBMITTED: timedelta(seconds=60),
    TradeState.EXIT_ACKED:     timedelta(minutes=30),    # SECONDARY guard (33% of phantoms)
}

# Legal transitions: (from_state, to_state).
# Every other (from, to) pair is illegal and raises IllegalTransition.
LEGAL_TRANSITIONS: set[tuple[TradeState, TradeState]] = {
    # Entry path
    (TradeState.PROPOSED,       TradeState.SUBMIT_PENDING),
    (TradeState.PROPOSED,       TradeState.REJECTED),      # instant reject at submit
    (TradeState.SUBMIT_PENDING, TradeState.ACKED),
    (TradeState.SUBMIT_PENDING, TradeState.FILLED),        # instant fill
    (TradeState.SUBMIT_PENDING, TradeState.REJECTED),
    (TradeState.SUBMIT_PENDING, TradeState.PHANTOM_NEVER_FILLED),  # 60s deadline
    (TradeState.ACKED,          TradeState.FILLED),
    (TradeState.ACKED,          TradeState.REJECTED),
    (TradeState.ACKED,          TradeState.PHANTOM_NEVER_FILLED),  # 15min deadline (PRIMARY)
    (TradeState.FILLED,         TradeState.OPEN),
    (TradeState.FILLED,         TradeState.PHANTOM_NEVER_FILLED),  # 5min deadline
    # Position monitoring
    (TradeState.OPEN,           TradeState.EXIT_PROPOSED),
    (TradeState.OPEN,           TradeState.PHANTOM_VANISHED),  # broker lost position
    (TradeState.OPEN,           TradeState.EXPIRED),
    # Exit path
    (TradeState.EXIT_PROPOSED,  TradeState.EXIT_SUBMITTED),
    (TradeState.EXIT_SUBMITTED, TradeState.CLOSED),
    (TradeState.EXIT_SUBMITTED, TradeState.EXIT_ACKED),
    (TradeState.EXIT_SUBMITTED, TradeState.OPEN),          # cancel → allow retry
    (TradeState.EXIT_ACKED,     TradeState.CLOSED),        # (SECONDARY guard — 33% of phantoms)
    (TradeState.EXIT_ACKED,     TradeState.OPEN),          # cancel → retry
    # Backfill-only: legacy PENDING maps to ACKED/SUBMIT_PENDING
    # No self-transitions; idempotency handled by caller (same coid → no-op)
}


class IllegalTransition(Exception):
    """Raised when an FSM transition is not in LEGAL_TRANSITIONS."""
    pass


def assert_legal(from_state: TradeState, to_state: TradeState) -> None:
    if (from_state, to_state) not in LEGAL_TRANSITIONS:
        raise IllegalTransition(
            f"Illegal transition: {from_state.value} → {to_state.value}"
        )
