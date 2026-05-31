"""Append-only sidecar writer for state transition history.

Writes one JSON line per transition to state_transitions.jsonl.
Never read on the hot path — only used by replay tests and dashboards.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Optional

TRANSITION_LOG = (
    os.environ.get("LIFECYCLE_TRANSITION_LOG")
    or "/root/quantai-v2/shared-data/journal/paper/state_transitions.jsonl"
)


def record(
    trade_id: str,
    from_state: str,
    to_state: str,
    broker_status: Optional[str] = None,
    coid: Optional[str] = None,
    duration_ms: Optional[int] = None,
    extra: Optional[dict] = None,
) -> None:
    """Append one transition record to the sidecar log. Silent on failure."""
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "trade_id": trade_id,
        "from": from_state,
        "to": to_state,
    }
    if broker_status is not None:
        entry["broker_status"] = broker_status
    if coid is not None:
        entry["coid"] = coid
    if duration_ms is not None:
        entry["duration_ms"] = duration_ms
    if extra:
        entry.update(extra)
    try:
        with open(TRANSITION_LOG, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass  # sidecar write failure must never crash the hot path
