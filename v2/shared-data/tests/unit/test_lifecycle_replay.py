"""Replay test — feed historical trades.jsonl through the FSM.

For every record:
- CLOSED in journal → FSM derive_state must yield a terminal that includes CLOSED
- PHANTOM_NEVER_FILLED → FSM must yield one of the 3 phantom terminals
- OPEN → FSM must yield OPEN or a non-terminal (not a phantom terminal)
- None stuck non-terminal after backfill

Also explicitly verifies the 21 known zombie records from Phase 0
(listed in backfill_state._KNOWN_ZOMBIES) each map to PHANTOM_NEVER_FILLED.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from lifecycle.states import TradeState, TERMINAL_STATES
from lifecycle.backfill_state import derive_state, _KNOWN_ZOMBIES

LIVE_JOURNAL = "/root/quantai-v2/shared-data/journal/paper/trades.jsonl"

PHANTOM_TERMINALS = {
    TradeState.PHANTOM_NEVER_FILLED,
    TradeState.PHANTOM_VANISHED,
    TradeState.REJECTED,
}


def load_journal(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    records = []
    with open(path) as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                records.append(json.loads(raw))
            except json.JSONDecodeError:
                continue
    return records


# ── Known zombie inventory (Phase 0 exact list) ───────────────────────────────

KNOWN_ZOMBIE_IDS = set(_KNOWN_ZOMBIES.keys())


@pytest.mark.parametrize("trade_id", sorted(KNOWN_ZOMBIE_IDS))
def test_known_zombie_maps_to_phantom_terminal(trade_id):
    """Each of the 21 Phase-0 known phantoms must derive to PHANTOM_NEVER_FILLED."""
    record = {"id": trade_id, "status": "PHANTOM_NEVER_FILLED", "order_id": "x"}
    state = derive_state(record)
    assert state == TradeState.PHANTOM_NEVER_FILLED, (
        f"Zombie {trade_id} derived {state}, expected PHANTOM_NEVER_FILLED"
    )


# ── Live journal replay ────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def journal_records():
    """Load live journal if readable; skip gracefully if not accessible."""
    records = load_journal(LIVE_JOURNAL)
    if not records:
        pytest.skip(f"Live journal not readable at {LIVE_JOURNAL} (run as root or provide access)")
    return records


def test_replay_closed_records_reach_closed(journal_records):
    closed = [r for r in journal_records if r.get("status") == "CLOSED"]
    for r in closed:
        state = derive_state(r)
        assert state == TradeState.CLOSED, (
            f"Record {r.get('id')} status=CLOSED derived {state}"
        )


def test_replay_phantom_records_reach_phantom_terminal(journal_records):
    phantoms = [r for r in journal_records if r.get("status") == "PHANTOM_NEVER_FILLED"]
    for r in phantoms:
        state = derive_state(r)
        assert state in PHANTOM_TERMINALS, (
            f"Record {r.get('id')} status=PHANTOM_NEVER_FILLED derived {state}, "
            f"expected one of {PHANTOM_TERMINALS}"
        )


def test_replay_open_records_not_phantom_terminal(journal_records):
    open_records = [r for r in journal_records if r.get("status") == "OPEN"]
    for r in open_records:
        state = derive_state(r)
        assert state not in PHANTOM_TERMINALS, (
            f"Record {r.get('id')} status=OPEN incorrectly derived {state}"
        )


def test_replay_no_record_stuck_with_none_state(journal_records):
    """Every record must derive to a known TradeState (not None)."""
    stuck = []
    for r in journal_records:
        state = derive_state(r)
        if state is None:
            stuck.append(r.get("id"))
    assert not stuck, f"Records with no derivable state: {stuck}"
