"""Backwards-compatibility tests for the per-arm journal additions.

The 4-arm test introduces:
  - 4 new per-arm journal files (gamma_arm_<id>_trades.jsonl)
  - A new `arm_id` field on Gamma trade entries
  - New trade ID prefixes (Ga###/Gb###/Gc###/Gd###) replacing legacy G###

This file verifies:
  1. Legacy G001-G003 entries (pre-experiment, no arm_id) still parse via
     existing tooling.
  2. New per-arm entries have arm_id and a Ga/Gb/Gc/Gd prefix.
  3. The union trades.jsonl includes entries from all arms (via append_arm_trade).
  4. Existing tools that filter on source.startswith("agent_gamma") still
     find entries from all arms (source = "agent_gamma_arm_a/b/c/d").
  5. The arm_id field is purely additive — entries without it (legacy)
     parse cleanly, entries with it don't break any existing assumption.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from gamma.arm_state import (  # noqa: E402
    append_arm_trade,
    arm_open_positions,
    init_all_arms,
    load_arm_journal,
    next_arm_trade_id,
)


@pytest.fixture
def tmp_journal(tmp_path):
    j = tmp_path / "journal"
    j.mkdir()
    return j


# ─────────────────────────────────────────────────────────
# Legacy compatibility (pre-experiment Gamma trades)
# ─────────────────────────────────────────────────────────


class TestLegacyGammaTrades:
    """Pre-experiment Gamma trades have id ``G001``, ``G002``, ``G003`` and
    no ``arm_id`` field. These must remain parseable by every tool that
    reads ``trades.jsonl``."""

    def test_legacy_g001_parses_as_json(self):
        """A legacy entry must JSON-deserialize cleanly."""
        legacy_line = json.dumps({
            "id": "G001",
            "timestamp": "2026-04-30T09:33:42-04:00",
            "mode": "paper",
            "source": "agent_gamma",
            "symbol": "AAPL",
            "strategy": "rsi_pullback_debit_spread",
            "status": "CLOSED",
            "pnl": 87.50,
        })
        parsed = json.loads(legacy_line)
        assert parsed["id"] == "G001"
        assert parsed.get("arm_id") is None  # not present
        assert parsed["source"] == "agent_gamma"

    def test_legacy_trade_in_per_arm_journal_is_skipped_by_filter(self, tmp_journal):
        """If someone writes a legacy entry to a per-arm journal by mistake,
        load_arm_journal returns it (we don't filter by arm_id at load
        time) — but downstream code that filters by arm_id sees no match."""
        # Manually write a legacy entry to gamma_arm_a_trades.jsonl
        path = tmp_journal / "gamma_arm_a_trades.jsonl"
        path.write_text(json.dumps({
            "id": "G001",
            "source": "agent_gamma",
            "symbol": "AAPL",
            "status": "OPEN",
        }) + "\n")
        loaded = load_arm_journal("a", base_dir=tmp_journal)
        assert len(loaded) == 1
        # Filtering by arm_id sees nothing
        with_arm_id = [t for t in loaded if t.get("arm_id") == "a"]
        assert with_arm_id == []

    def test_existing_filter_pattern_finds_arm_entries(self, tmp_journal):
        """Existing tooling typically filters trades by
        ``source.startswith("agent_gamma")``. After the experiment, sources
        are ``agent_gamma_arm_<id>`` — confirm the prefix-startswith still
        catches them."""
        init_all_arms(cache_dir=tmp_journal.parent / "cache",
                       journal_dir=tmp_journal)

        # Append entries from each arm
        for aid in ("a", "b", "c", "d"):
            append_arm_trade(aid, {
                "id": f"G{aid}001",
                "arm_id": aid,
                "source": f"agent_gamma_arm_{aid}",
                "symbol": "AAPL",
                "status": "OPEN",
                "max_risk": 100.0,
            }, journal_dir=tmp_journal)

        # Read the union file
        union = tmp_journal / "trades.jsonl"
        union_entries = [
            json.loads(l) for l in union.read_text().splitlines() if l
        ]
        # Existing-tool pattern: filter by source startswith
        gamma_entries = [
            e for e in union_entries
            if (e.get("source") or "").startswith("agent_gamma")
        ]
        assert len(gamma_entries) == 4
        assert {e["arm_id"] for e in gamma_entries} == {"a", "b", "c", "d"}


# ─────────────────────────────────────────────────────────
# Per-arm journal entries have arm_id
# ─────────────────────────────────────────────────────────


class TestPerArmJournalArmId:
    def test_append_sets_arm_id_if_missing(self, tmp_journal):
        """append_arm_trade injects arm_id if caller forgot to set it.
        Defensive — callers shouldn't rely on this, but it prevents silent
        corruption if they do."""
        init_all_arms(cache_dir=tmp_journal.parent / "cache",
                       journal_dir=tmp_journal)
        trade = {"id": "Ga001", "source": "agent_gamma_arm_a", "symbol": "AAPL"}
        append_arm_trade("a", trade, journal_dir=tmp_journal)

        loaded = load_arm_journal("a", base_dir=tmp_journal)
        assert len(loaded) == 1
        assert loaded[0]["arm_id"] == "a"

    def test_append_overrides_mismatched_arm_id(self, tmp_journal):
        """If caller passes arm_id='x' but appends to Arm A's journal,
        we set arm_id='a' on the entry to match the destination file."""
        init_all_arms(cache_dir=tmp_journal.parent / "cache",
                       journal_dir=tmp_journal)
        trade = {"id": "Ga001", "arm_id": "x_wrong", "symbol": "AAPL"}
        append_arm_trade("a", trade, journal_dir=tmp_journal)

        loaded = load_arm_journal("a", base_dir=tmp_journal)
        assert loaded[0]["arm_id"] == "a"

    def test_per_arm_journal_only_has_own_arm(self, tmp_journal):
        """Each per-arm file should only contain that arm's entries."""
        init_all_arms(cache_dir=tmp_journal.parent / "cache",
                       journal_dir=tmp_journal)

        append_arm_trade("a", {"id": "Ga001", "symbol": "AAPL", "status": "OPEN"},
                          journal_dir=tmp_journal)
        append_arm_trade("b", {"id": "Gb001", "symbol": "TMO", "status": "OPEN"},
                          journal_dir=tmp_journal)

        a_journal = load_arm_journal("a", base_dir=tmp_journal)
        b_journal = load_arm_journal("b", base_dir=tmp_journal)
        assert len(a_journal) == 1 and a_journal[0]["arm_id"] == "a"
        assert len(b_journal) == 1 and b_journal[0]["arm_id"] == "b"


# ─────────────────────────────────────────────────────────
# Union trades.jsonl includes all arms
# ─────────────────────────────────────────────────────────


class TestUnionJournal:
    def test_trades_jsonl_union_includes_all_arms(self, tmp_journal):
        """Every per-arm append also writes to trades.jsonl. Existing tools
        that read trades.jsonl see all arms in one stream."""
        init_all_arms(cache_dir=tmp_journal.parent / "cache",
                       journal_dir=tmp_journal)

        for aid in ("a", "b", "c", "d"):
            append_arm_trade(aid, {
                "id": f"G{aid}001",
                "arm_id": aid,
                "source": f"agent_gamma_arm_{aid}",
                "symbol": "AAPL",
                "status": "OPEN",
            }, journal_dir=tmp_journal)

        union_text = (tmp_journal / "trades.jsonl").read_text()
        union_entries = [json.loads(l) for l in union_text.splitlines() if l]
        assert len(union_entries) == 4
        arms_seen = {e["arm_id"] for e in union_entries}
        assert arms_seen == {"a", "b", "c", "d"}

    def test_union_preserves_pre_experiment_entries(self, tmp_journal):
        """If trades.jsonl already has legacy G001-G003 entries when the
        experiment begins, appending new per-arm entries doesn't disturb them."""
        union_path = tmp_journal / "trades.jsonl"
        # Pre-existing legacy content
        union_path.write_text(
            json.dumps({"id": "G001", "source": "agent_gamma", "symbol": "AAPL"}) + "\n"
            + json.dumps({"id": "G002", "source": "agent_gamma", "symbol": "TMO"}) + "\n"
        )
        init_all_arms(cache_dir=tmp_journal.parent / "cache",
                       journal_dir=tmp_journal)

        append_arm_trade("a", {
            "id": "Ga001", "arm_id": "a",
            "source": "agent_gamma_arm_a", "symbol": "JNJ", "status": "OPEN",
        }, journal_dir=tmp_journal)

        entries = [
            json.loads(l) for l in union_path.read_text().splitlines() if l
        ]
        assert len(entries) == 3
        assert entries[0]["id"] == "G001"  # legacy preserved
        assert entries[1]["id"] == "G002"
        assert entries[2]["id"] == "Ga001"  # new arm entry appended


# ─────────────────────────────────────────────────────────
# Schema additivity (arm_id doesn't break existing parsing)
# ─────────────────────────────────────────────────────────


class TestArmIdAdditive:
    def test_existing_tools_can_read_with_arm_id(self):
        """Simulate existing-tool parsing: load JSON line, access expected
        fields. The new arm_id field must not break field-access patterns."""
        with_arm_id = json.dumps({
            "id": "Ga001",
            "arm_id": "a",
            "ranker_used": "rsi_only",
            "rank_at_entry": 1,
            "timestamp": "2026-05-12T09:33:42-04:00",
            "mode": "paper",
            "source": "agent_gamma_arm_a",
            "symbol": "AAPL",
            "strategy": "rsi_pullback_debit_spread",
            "legs": [],
            "status": "OPEN",
            "max_risk": 185.0,
            "pnl": 0,
        })
        parsed = json.loads(with_arm_id)
        # All expected fields accessible
        assert parsed["id"] == "Ga001"
        assert parsed["source"] == "agent_gamma_arm_a"
        assert parsed["symbol"] == "AAPL"
        assert parsed["status"] == "OPEN"
        # The new fields are present but optional
        assert parsed["arm_id"] == "a"
        assert parsed["ranker_used"] == "rsi_only"

    def test_arm_id_absent_treated_as_legacy(self):
        """A trade dict with no arm_id is treated as legacy / pre-experiment.
        Code that filters by arm_id sees no match."""
        legacy = {"id": "G001", "source": "agent_gamma", "symbol": "AAPL"}
        assert legacy.get("arm_id") is None
        # Filter pattern used in collect_gamma / dashboards
        only_arm_a = legacy.get("arm_id") == "a"
        assert only_arm_a is False

    def test_open_positions_filter_per_arm(self, tmp_journal):
        """arm_open_positions(arm_id) returns only OPEN trades for that arm,
        scoped to the per-arm journal file."""
        init_all_arms(cache_dir=tmp_journal.parent / "cache",
                       journal_dir=tmp_journal)
        append_arm_trade("a", {"id": "Ga001", "arm_id": "a",
                                 "symbol": "AAPL", "status": "OPEN",
                                 "max_risk": 185.0},
                          journal_dir=tmp_journal)
        append_arm_trade("a", {"id": "Ga002", "arm_id": "a",
                                 "symbol": "TMO", "status": "CLOSED",
                                 "max_risk": 150.0, "pnl": 32.0},
                          journal_dir=tmp_journal)
        opens_a = arm_open_positions("a", journal_dir=tmp_journal)
        assert len(opens_a) == 1
        assert opens_a[0]["id"] == "Ga001"


# ─────────────────────────────────────────────────────────
# Trade ID prefix per arm (cross-cutting test)
# ─────────────────────────────────────────────────────────


class TestTradeIdPrefixPerArmEndToEnd:
    def test_trade_id_prefix_per_arm_via_append(self, tmp_journal):
        """End-to-end: append trades to each arm's journal using
        next_arm_trade_id(); confirm prefixes are correct and counters
        increment independently."""
        init_all_arms(cache_dir=tmp_journal.parent / "cache",
                       journal_dir=tmp_journal)

        for aid in ("a", "b", "c", "d"):
            for _ in range(2):
                journal = load_arm_journal(aid, base_dir=tmp_journal)
                tid = next_arm_trade_id(aid, journal)
                trade = {"id": tid, "arm_id": aid,
                          "source": f"agent_gamma_arm_{aid}",
                          "symbol": "AAPL", "status": "OPEN",
                          "max_risk": 100.0}
                append_arm_trade(aid, trade, journal_dir=tmp_journal)

        # Each arm should have Gx001 and Gx002
        for aid in ("a", "b", "c", "d"):
            j = load_arm_journal(aid, base_dir=tmp_journal)
            ids = sorted(t["id"] for t in j)
            assert ids == [f"G{aid}001", f"G{aid}002"]
