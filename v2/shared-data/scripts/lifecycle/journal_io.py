"""State-aware journal reader/writer for the lifecycle FSM.

Wraps the existing rewrite_journal_atomic pattern from position_monitor.py.
Reads the `state` field if present; derives it from legacy `status` if not.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Optional

from lifecycle.states import TradeState, LEGACY_STATUS_MAP, TERMINAL_STATES

JOURNAL = (
    os.environ.get("QUANTAI_JOURNAL")
    or "/root/quantai-v2/shared-data/journal/paper/trades.jsonl"
)


def load_all() -> list[dict]:
    """Load every record from the journal (all statuses). Returns []  if missing."""
    if not os.path.exists(JOURNAL):
        return []
    records = []
    with open(JOURNAL) as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                records.append(json.loads(raw))
            except json.JSONDecodeError:
                continue
    return records


def get_state(record: dict) -> Optional[TradeState]:
    """Return the FSM state for a journal record.

    Priority:
    1. `state` field (set by FSM)
    2. Derived from `status` field (legacy backcompat)
    """
    if "state" in record and record["state"]:
        try:
            return TradeState(record["state"])
        except ValueError:
            pass

    status = record.get("status") or ""
    mapped = LEGACY_STATUS_MAP.get(status)
    if mapped is not None:
        return mapped
    if status == "PENDING":
        # PENDING with order_id → ACKED; without → SUBMIT_PENDING
        return TradeState.ACKED if record.get("order_id") else TradeState.SUBMIT_PENDING
    return None


def is_terminal(record: dict) -> bool:
    state = get_state(record)
    return state in TERMINAL_STATES if state else False


def rewrite_journal_atomic(updates: dict) -> bool:
    """Merge updates into matching journal entries, rewrite atomically.

    updates: {trade_id: {field: value, ...}}
    Returns True on success, False on any error (original untouched on failure).
    Mirrors position_monitor.rewrite_journal_atomic — kept separate so
    the lifecycle package has no import dependency on position_monitor.
    """
    tmp_path = JOURNAL + ".tmp"
    try:
        lines = []
        if os.path.exists(JOURNAL):
            with open(JOURNAL) as f:
                for raw in f:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        t = json.loads(raw)
                        if t.get("id") in updates:
                            t.update(updates[t["id"]])
                        lines.append(json.dumps(t))
                    except Exception:
                        lines.append(raw)
        with open(tmp_path, "w") as f:
            f.write("\n".join(lines) + "\n")
        os.replace(tmp_path, JOURNAL)
        return True
    except Exception:
        return False


def persist_transition(trade_id: str, to_state: TradeState, extra: Optional[dict] = None) -> bool:
    """Atomically write a single state field update to the journal."""
    now = datetime.now(timezone.utc).isoformat()
    update = {
        "state": to_state.value,
        "last_transition_at": now,
    }
    if extra:
        update.update(extra)
    # Also mirror state into legacy status field for backward compat
    # so position_monitor's load_journal() (filters by status) still works.
    _STATUS_MIRROR = {
        TradeState.OPEN:                "OPEN",
        TradeState.CLOSED:              "CLOSED",
        TradeState.REJECTED:            "PHANTOM_NEVER_FILLED",   # closest legacy
        TradeState.PHANTOM_NEVER_FILLED: "PHANTOM_NEVER_FILLED",
        TradeState.PHANTOM_VANISHED:    "PHANTOM_NEVER_FILLED",
        TradeState.EXPIRED:             "EXPIRED",
        TradeState.EXIT_SUBMITTED:      "OPEN",   # still an open trade for position_monitor
        TradeState.EXIT_ACKED:          "OPEN",
        TradeState.ACKED:               "PENDING",
        TradeState.FILLED:              "PENDING",
    }
    if to_state in _STATUS_MIRROR:
        update["status"] = _STATUS_MIRROR[to_state]
    return rewrite_journal_atomic({trade_id: update})
