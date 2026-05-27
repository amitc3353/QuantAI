"""Unit tests: Nightly expired-options sweep in reflection_reconciler.

Tests cover:
  1. OPEN entries with all expired legs -> EXPIRED
  2. PENDING entries with all expired legs -> PHANTOM_NEVER_FILLED
  3. Entries with future legs unchanged
  4. Mixed expiry (some expired, some future) unchanged
  5. Edge cases: no legs, malformed dates, CLOSED entries skipped
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPTS = str(Path(__file__).resolve().parents[2] / "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)


def _make_trade(tid, status="OPEN", legs=None, symbol="XYZ"):
    """Build a minimal trade dict for testing."""
    if legs is None:
        legs = [
            {"action": "buy", "type": "call", "strike": 100,
             "expiry": "2026-06-01"},
            {"action": "sell", "type": "call", "strike": 105,
             "expiry": "2026-06-01"},
        ]
    return {
        "id": tid,
        "status": status,
        "source": "agent_gamma_arm_a",
        "symbol": symbol,
        "timestamp": datetime.now().isoformat(),
        "legs": legs,
    }


def _write_journal(path, trades):
    with open(path, "w") as f:
        for t in trades:
            f.write(json.dumps(t) + "\n")


def _read_journal(path):
    entries = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


@pytest.fixture
def journal_path(tmp_path):
    return str(tmp_path / "trades.jsonl")


class TestSweepExpiredEntries:
    """Test reflection_reconciler.sweep_expired_entries()."""

    def test_open_with_expired_legs_marks_expired(self, journal_path):
        """OPEN entry with all legs past expiry should become EXPIRED."""
        yesterday = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
        trades = [_make_trade("T001", status="OPEN", legs=[
            {"action": "buy", "type": "call", "strike": 100, "expiry": yesterday},
            {"action": "sell", "type": "call", "strike": 105, "expiry": yesterday},
        ])]
        _write_journal(journal_path, trades)

        with patch("reflection_reconciler.JOURNAL", journal_path):
            from reflection_reconciler import sweep_expired_entries
            scanned, expired = sweep_expired_entries()

        assert scanned == 1
        assert expired == 1
        result = _read_journal(journal_path)
        assert result[0]["status"] == "EXPIRED"
        assert "all_legs_expired" in result[0]["close_reason"]

    def test_open_with_future_legs_unchanged(self, journal_path):
        """OPEN entry with legs expiring in the future should stay OPEN."""
        future = (date.today() + timedelta(days=30)).strftime("%Y-%m-%d")
        trades = [_make_trade("T002", status="OPEN", legs=[
            {"action": "buy", "type": "call", "strike": 100, "expiry": future},
            {"action": "sell", "type": "call", "strike": 105, "expiry": future},
        ])]
        _write_journal(journal_path, trades)

        with patch("reflection_reconciler.JOURNAL", journal_path):
            from reflection_reconciler import sweep_expired_entries
            scanned, expired = sweep_expired_entries()

        assert scanned == 1
        assert expired == 0
        result = _read_journal(journal_path)
        assert result[0]["status"] == "OPEN"

    def test_mixed_expiry_unchanged(self, journal_path):
        """Entry with 1 expired leg and 1 future leg should stay OPEN."""
        past = (date.today() - timedelta(days=5)).strftime("%Y-%m-%d")
        future = (date.today() + timedelta(days=30)).strftime("%Y-%m-%d")
        trades = [_make_trade("T003", status="OPEN", legs=[
            {"action": "buy", "type": "call", "strike": 100, "expiry": past},
            {"action": "sell", "type": "call", "strike": 105, "expiry": future},
        ])]
        _write_journal(journal_path, trades)

        with patch("reflection_reconciler.JOURNAL", journal_path):
            from reflection_reconciler import sweep_expired_entries
            scanned, expired = sweep_expired_entries()

        assert scanned == 1
        assert expired == 0
        result = _read_journal(journal_path)
        assert result[0]["status"] == "OPEN"

    def test_pending_expired_marks_phantom(self, journal_path):
        """PENDING entry with all expired legs -> PHANTOM_NEVER_FILLED."""
        past = (date.today() - timedelta(days=10)).strftime("%Y-%m-%d")
        trades = [_make_trade("T004", status="PENDING", legs=[
            {"action": "buy", "type": "call", "strike": 100, "expiry": past},
            {"action": "sell", "type": "call", "strike": 105, "expiry": past},
        ])]
        _write_journal(journal_path, trades)

        with patch("reflection_reconciler.JOURNAL", journal_path):
            from reflection_reconciler import sweep_expired_entries
            scanned, expired = sweep_expired_entries()

        assert scanned == 1
        assert expired == 1
        result = _read_journal(journal_path)
        assert result[0]["status"] == "PHANTOM_NEVER_FILLED"
        assert "pending_and_all_legs_expired" in result[0]["close_reason"]

    def test_no_legs_unchanged(self, journal_path):
        """Entry without legs should be scanned but unchanged."""
        trades = [_make_trade("T005", status="OPEN", legs=[])]
        _write_journal(journal_path, trades)

        with patch("reflection_reconciler.JOURNAL", journal_path):
            from reflection_reconciler import sweep_expired_entries
            scanned, expired = sweep_expired_entries()

        assert scanned == 1
        assert expired == 0
        result = _read_journal(journal_path)
        assert result[0]["status"] == "OPEN"

    def test_closed_entries_skipped(self, journal_path):
        """CLOSED entries should not be scanned."""
        past = (date.today() - timedelta(days=10)).strftime("%Y-%m-%d")
        trades = [
            _make_trade("T006", status="CLOSED", legs=[
                {"action": "buy", "type": "call", "strike": 100, "expiry": past},
            ]),
            _make_trade("T007", status="OPEN", legs=[
                {"action": "buy", "type": "call", "strike": 100,
                 "expiry": (date.today() + timedelta(days=30)).strftime("%Y-%m-%d")},
            ]),
        ]
        _write_journal(journal_path, trades)

        with patch("reflection_reconciler.JOURNAL", journal_path):
            from reflection_reconciler import sweep_expired_entries
            scanned, expired = sweep_expired_entries()

        # Only the OPEN entry should be scanned
        assert scanned == 1
        assert expired == 0

    def test_malformed_expiry_date_skipped(self, journal_path):
        """Entry with malformed expiry date should be unchanged."""
        trades = [_make_trade("T008", status="OPEN", legs=[
            {"action": "buy", "type": "call", "strike": 100, "expiry": "not-a-date"},
            {"action": "sell", "type": "call", "strike": 105, "expiry": "2026-06-01"},
        ])]
        _write_journal(journal_path, trades)

        with patch("reflection_reconciler.JOURNAL", journal_path):
            from reflection_reconciler import sweep_expired_entries
            scanned, expired = sweep_expired_entries()

        assert scanned == 1
        assert expired == 0
        result = _read_journal(journal_path)
        assert result[0]["status"] == "OPEN"

    def test_expiry_today_not_yet_expired(self, journal_path):
        """Entry with legs expiring today should NOT be marked expired yet."""
        today = date.today().strftime("%Y-%m-%d")
        trades = [_make_trade("T009", status="OPEN", legs=[
            {"action": "buy", "type": "call", "strike": 100, "expiry": today},
        ])]
        _write_journal(journal_path, trades)

        with patch("reflection_reconciler.JOURNAL", journal_path):
            from reflection_reconciler import sweep_expired_entries
            scanned, expired = sweep_expired_entries()

        assert scanned == 1
        assert expired == 0
        result = _read_journal(journal_path)
        assert result[0]["status"] == "OPEN"
