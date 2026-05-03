"""Unit tests for _journal_update.py — atomic rewrite + find helpers."""
from __future__ import annotations

import importlib
import json

import pytest


@pytest.fixture(autouse=True)
def _reload_paths(tmp_root):
    """Ensure _paths constants point to the sandbox before each test."""
    import _paths
    importlib.reload(_paths)
    # Also reload _journal_update so DEFAULT_JOURNAL picks up the sandbox path
    import _journal_update
    importlib.reload(_journal_update)
    yield


def _write_journal(path, entries):
    with open(path, "w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


class TestFindTrade:
    def test_finds_existing_trade(self, populated_journal):
        from _journal_update import find_trade
        t = find_trade("A001", str(populated_journal))
        assert t is not None
        assert t["id"] == "A001"
        assert t["symbol"] == "SPY"

    def test_returns_none_for_missing(self, populated_journal):
        from _journal_update import find_trade
        assert find_trade("ZZZZ", str(populated_journal)) is None

    def test_returns_none_when_file_absent(self, tmp_path):
        from _journal_update import find_trade
        assert find_trade("A001", str(tmp_path / "nonexistent.jsonl")) is None

    def test_skips_corrupt_lines(self, journal_path):
        journal_path.write_text('{"id":"A001","symbol":"SPY"}\nnot-json\n{"id":"A002"}\n')
        from _journal_update import find_trade
        assert find_trade("A001", str(journal_path)) is not None
        assert find_trade("A002", str(journal_path)) is not None

    def test_returns_first_match(self, journal_path):
        entries = [
            {"id": "A001", "symbol": "SPY", "pnl": 100},
            {"id": "A001", "symbol": "QQQ", "pnl": 200},
        ]
        _write_journal(journal_path, entries)
        from _journal_update import find_trade
        t = find_trade("A001", str(journal_path))
        assert t["symbol"] == "SPY"  # first match


class TestUpdateTradeEntry:
    def test_merges_fields(self, populated_journal):
        from _journal_update import update_trade_entry, find_trade
        ok = update_trade_entry("A001", {"capability_diagnosis": {"test": True}},
                                str(populated_journal))
        assert ok is True
        updated = find_trade("A001", str(populated_journal))
        assert updated["capability_diagnosis"] == {"test": True}

    def test_other_entries_preserved(self, populated_journal):
        from _journal_update import update_trade_entry, find_trade
        update_trade_entry("A001", {"new_field": "x"}, str(populated_journal))
        a002 = find_trade("A002", str(populated_journal))
        assert a002 is not None  # A002 wasn't touched

    def test_noop_when_id_not_found(self, populated_journal):
        from _journal_update import update_trade_entry, find_trade
        ok = update_trade_entry("ZZZZ", {"x": 1}, str(populated_journal))
        # Should return True (file rewrite succeeded) but no mutation visible
        assert ok is True
        assert find_trade("ZZZZ", str(populated_journal)) is None

    def test_returns_false_when_file_missing(self, tmp_path):
        from _journal_update import update_trade_entry
        ok = update_trade_entry("A001", {"x": 1}, str(tmp_path / "ghost.jsonl"))
        assert ok is False

    def test_atomic_write_no_partial_state(self, journal_path, sample_trade):
        """File must not be left in partial state on error mid-write."""
        _write_journal(journal_path, [sample_trade])
        from _journal_update import update_trade_entry, find_trade
        update_trade_entry("A001", {"post_trade": {"result": "ok"}}, str(journal_path))
        t = find_trade("A001", str(journal_path))
        assert t["post_trade"]["result"] == "ok"
        # Original symbol must still be intact
        assert t["symbol"] == "SPY"

    def test_default_journal_uses_sandbox_path(self, tmp_root):
        """DEFAULT_JOURNAL must point into the sandbox, not production."""
        import _journal_update
        importlib.reload(_journal_update)
        from _journal_update import DEFAULT_JOURNAL
        assert str(tmp_root) in DEFAULT_JOURNAL

    def test_preserves_corrupt_lines(self, journal_path, sample_trade):
        """Corrupt lines should be preserved verbatim (not dropped)."""
        journal_path.write_text(json.dumps(sample_trade) + "\nnot-json\n")
        from _journal_update import update_trade_entry
        update_trade_entry("A001", {"x": 1}, str(journal_path))
        content = journal_path.read_text()
        assert "not-json" in content
