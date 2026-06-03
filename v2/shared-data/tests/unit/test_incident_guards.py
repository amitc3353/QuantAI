"""Tests for the three 2026-06-01/02 incident guards.

1. next_arm_trade_id dedupe: counter must not reuse an id that survives in
   the union journal after a per-arm reset.
2. _detect_resurrected_pending: PENDING entry with a live broker position is
   flagged + skipped, not auto-promoted or phantom-escalated.
3. _duplicate_ids / position_monitor halt: duplicate journal ids stop the
   close loop entirely.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

# Bootstrap fake broker before importing position_monitor
_BROKER_MOD = MagicMock()
_BROKER_MOD.BrokerBase = type("FakeBrokerBase", (), {})
_BROKER_MOD.DRY_RUN_SENTINEL = {"DRY_RUN": True}
sys.modules.setdefault("broker", _BROKER_MOD)


# ── Fix 1: next_arm_trade_id dedupe against union journal ─────────────────────

from gamma.arm_state import next_arm_trade_id, _max_counter_for_prefix


def test_max_counter_for_prefix():
    journal = [{"id": "Ga001"}, {"id": "Ga005"}, {"id": "Gb003"}, {"id": "X"}]
    assert _max_counter_for_prefix(journal, "Ga") == 5
    assert _max_counter_for_prefix(journal, "Gb") == 3
    assert _max_counter_for_prefix(journal, "Gc") == 0


def test_next_id_no_union_uses_arm_journal(tmp_path):
    nonexist = tmp_path / "no_union.jsonl"
    arm_journal = [{"id": "Ga005"}]
    assert next_arm_trade_id("a", arm_journal, union_journal_path=nonexist) == "Ga006"


def test_next_id_after_reset_does_not_collide_with_union(tmp_path):
    """THE INCIDENT CASE: per-arm journal truncated (empty) by reset, but the
    union journal still has Ga001-Ga006. Next id must be Ga007, not Ga001."""
    union = tmp_path / "trades.jsonl"
    union_records = [
        {"id": "Ga001"}, {"id": "Ga002"}, {"id": "Ga006"},  # survive reset
        {"id": "Gb001"},
    ]
    union.write_text("\n".join(json.dumps(r) for r in union_records) + "\n")

    empty_arm_journal = []  # reset truncated it
    next_id = next_arm_trade_id("a", empty_arm_journal, union_journal_path=union)
    assert next_id == "Ga007", f"expected Ga007 (no collision), got {next_id}"


def test_next_id_takes_max_across_both_journals(tmp_path):
    union = tmp_path / "trades.jsonl"
    union.write_text(json.dumps({"id": "Ga003"}) + "\n")
    arm_journal = [{"id": "Ga005"}]  # arm journal is ahead
    # max(5, 3) + 1 = 6
    assert next_arm_trade_id("a", arm_journal, union_journal_path=union) == "Ga006"


def test_next_id_union_unreadable_falls_back(tmp_path):
    # Point at a directory (open() raises) — must fall back to arm journal
    bad = tmp_path  # a directory
    arm_journal = [{"id": "Gb002"}]
    assert next_arm_trade_id("b", arm_journal, union_journal_path=bad) == "Gb003"


# ── Fix 2: resurrection guard ────────────────────────────────────────────────

import position_monitor as pm


def test_resurrection_guard_flags_pending_with_broker_position(monkeypatch):
    broker = MagicMock()
    broker.get_positions.return_value = [{"symbol": "WMT260618C00115000", "qty": 4}]
    trades = [
        {"id": "Ga101", "status": "PENDING", "source": "agent_gamma_arm_a",
         "legs": [{"symbol": "WMT260618C00115000"}, {"symbol": "WMT260618C00119000"}]},
    ]
    monkeypatch.setattr(pm, "post_discord", lambda *a, **k: None)
    resurrected = pm._detect_resurrected_pending(broker, trades)
    assert "Ga101" in resurrected


def test_resurrection_guard_ignores_pending_without_broker_position(monkeypatch):
    broker = MagicMock()
    broker.get_positions.return_value = [{"symbol": "SOMETHING_ELSE", "qty": 1}]
    trades = [
        {"id": "Ga101", "status": "PENDING", "source": "agent_gamma_arm_a",
         "legs": [{"symbol": "WMT260618C00115000"}]},
    ]
    monkeypatch.setattr(pm, "post_discord", lambda *a, **k: None)
    resurrected = pm._detect_resurrected_pending(broker, trades)
    assert resurrected == set()


def test_resurrection_guard_handles_broker_error(monkeypatch):
    broker = MagicMock()
    broker.get_positions.side_effect = RuntimeError("broker down")
    trades = [{"id": "Ga101", "status": "PENDING", "source": "agent_gamma_arm_a",
               "legs": [{"symbol": "X"}]}]
    # Must not raise
    resurrected = pm._detect_resurrected_pending(broker, trades)
    assert resurrected == set()


def test_resurrection_guard_only_pending_not_open(monkeypatch):
    broker = MagicMock()
    broker.get_positions.return_value = [{"symbol": "WMT260618C00115000"}]
    trades = [
        {"id": "Ga101", "status": "OPEN", "source": "agent_gamma_arm_a",
         "legs": [{"symbol": "WMT260618C00115000"}]},  # OPEN, not PENDING
    ]
    monkeypatch.setattr(pm, "post_discord", lambda *a, **k: None)
    assert pm._detect_resurrected_pending(broker, trades) == set()


# ── Fix 3: duplicate-id guard ────────────────────────────────────────────────

def test_duplicate_ids_detects_collision():
    trades = [{"id": "Ga101"}, {"id": "Ga101"}, {"id": "Gb101"}]
    assert pm._duplicate_ids(trades) == {"Ga101"}


def test_duplicate_ids_clean_journal():
    trades = [{"id": "Ga101"}, {"id": "Gb101"}, {"id": "Gc101"}]
    assert pm._duplicate_ids(trades) == set()


def test_duplicate_ids_ignores_missing_id():
    trades = [{"id": "Ga101"}, {"foo": "bar"}, {"id": None}]
    assert pm._duplicate_ids(trades) == set()


def test_duplicate_ids_multiple_collisions():
    trades = [{"id": "A"}, {"id": "A"}, {"id": "B"}, {"id": "B"}, {"id": "C"}]
    assert pm._duplicate_ids(trades) == {"A", "B"}
