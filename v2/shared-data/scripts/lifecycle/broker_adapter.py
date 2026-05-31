"""Typed wrapper around _broker_ibkr.place_mleg_order / place_close_order.

Converts the raw dict (or None) returned by those functions into typed
SubmitResult / PollResult values so the FSM doesn't sprinkle raw string
comparisons against "submitted" / "presubmit" / "filled" everywhere.

_broker_ibkr.py is NOT edited — this module wraps it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class SubmitOutcome(str, Enum):
    ACCEPTED      = "ACCEPTED"     # broker returned Filled (instant)
    INDETERMINATE = "INDETERMINATE"# broker returned Submitted/PreSubmitted etc.
    REJECTED      = "REJECTED"     # broker terminal-failure or None returned
    UNKNOWN       = "UNKNOWN"      # order_id present but fill_status missing/unrecognised


class CloseOutcome(str, Enum):
    FILLED        = "FILLED"
    INDETERMINATE = "INDETERMINATE"
    REJECTED      = "REJECTED"
    UNKNOWN       = "UNKNOWN"


_TERMINAL_FAILURE = {
    "cancelled", "canceled", "apicancelled", "apicanceled",
    "rejected", "inactive",
}
_INDETERMINATE = {
    "submitted", "presubmitted", "pendingsubmit", "pendingcancel",
    "apipending",
}
_SUCCESS = {"filled", "simulated"}


@dataclass
class SubmitResult:
    outcome: SubmitOutcome
    order_id: Optional[str] = None
    fill_status: Optional[str] = None
    filled_qty: int = 0
    avg_fill_price: float = 0.0
    working: bool = False          # broker returned _working=True
    raw: dict = field(default_factory=dict)

    @staticmethod
    def from_broker(raw: Optional[dict]) -> "SubmitResult":
        if raw is None:
            return SubmitResult(outcome=SubmitOutcome.REJECTED, raw={})
        fs = (raw.get("status") or raw.get("fill_status") or "").lower()
        oid = raw.get("order_id") or raw.get("orderId")
        fq = raw.get("filled_qty") or raw.get("filledQty") or 0
        price = raw.get("avg_fill_price") or raw.get("avgFillPrice") or 0.0
        working = bool(raw.get("_working"))

        if fs in _SUCCESS and fq and fq > 0:
            outcome = SubmitOutcome.ACCEPTED
        elif fs in _TERMINAL_FAILURE:
            outcome = SubmitOutcome.REJECTED
        elif fs in _INDETERMINATE or working:
            outcome = SubmitOutcome.INDETERMINATE
        elif oid and not fs:
            outcome = SubmitOutcome.UNKNOWN
        else:
            outcome = SubmitOutcome.REJECTED

        return SubmitResult(
            outcome=outcome,
            order_id=str(oid) if oid else None,
            fill_status=raw.get("status") or raw.get("fill_status"),
            filled_qty=int(fq) if fq else 0,
            avg_fill_price=float(price) if price else 0.0,
            working=working,
            raw=raw,
        )


@dataclass
class CloseResult:
    outcome: CloseOutcome
    order_id: Optional[str] = None
    fill_status: Optional[str] = None
    working: bool = False
    raw: dict = field(default_factory=dict)

    @staticmethod
    def from_broker(raw: Optional[dict]) -> "CloseResult":
        if raw is None:
            return CloseResult(outcome=CloseOutcome.REJECTED, raw={})
        fs = (raw.get("status") or raw.get("fill_status") or "").lower()
        oid = raw.get("order_id") or raw.get("orderId")
        working = bool(raw.get("_working"))

        if fs in _SUCCESS:
            outcome = CloseOutcome.FILLED
        elif fs in _TERMINAL_FAILURE:
            outcome = CloseOutcome.REJECTED
        elif fs in _INDETERMINATE or working:
            outcome = CloseOutcome.INDETERMINATE
        else:
            outcome = CloseOutcome.UNKNOWN

        return CloseResult(
            outcome=outcome,
            order_id=str(oid) if oid else None,
            fill_status=raw.get("status") or raw.get("fill_status"),
            working=working,
            raw=raw,
        )
