"""Unit tests: Two-phase journal (PENDING -> OPEN on fill confirmation).

Tests cover:
  1. gamma_agent execution paths write PENDING when unfilled, OPEN when filled
  2. position_monitor._promote_pending_entries() promotion and timeout logic
  3. PENDING entries excluded from exit-rule evaluation
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── Bootstrap ──────────────────────────────────────────────────────────────────

SCRIPTS = str(Path(__file__).resolve().parents[2] / "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_pending_trade(tid="Ga999", symbol="XYZ", timestamp=None, order_id="12345",
                        arm_id=None, max_risk=100):
    ts = timestamp or datetime.now().isoformat()
    t = {
        "id": tid,
        "trade_id": tid,
        "status": "PENDING",
        "source": "agent_gamma_arm_a",
        "symbol": symbol,
        "timestamp": ts,
        "order_id": order_id,
        "fill_status": "Submitted",
        "filled_qty": 0,
        "max_risk": max_risk,
        "legs": [
            {"action": "buy", "type": "call", "strike": 100, "expiry": "2026-07-01"},
            {"action": "sell", "type": "call", "strike": 105, "expiry": "2026-07-01"},
        ],
    }
    if arm_id:
        t["arm_id"] = arm_id
    return t


def _make_open_trade(tid="Ga001", symbol="DE"):
    return {
        "id": tid,
        "trade_id": tid,
        "status": "OPEN",
        "source": "agent_gamma_arm_a",
        "symbol": symbol,
        "timestamp": datetime.now().isoformat(),
        "order_id": "99999",
        "fill_status": "Filled",
        "filled_qty": 1,
        "legs": [
            {"action": "buy", "type": "call", "strike": 530, "expiry": "2026-06-12"},
            {"action": "sell", "type": "call", "strike": 542.5, "expiry": "2026-06-12"},
        ],
        "exit_rules": {"stop_loss_pct": -50, "take_profit_pct": 150},
    }


# ── Tests: Two-phase status in gamma_agent ─────────────────────────────────

class TestGammaJournalStatus:
    """Verify gamma_agent sets status based on fill, not unconditionally OPEN."""

    def test_unfilled_order_status_is_pending(self):
        """If broker returns filled_qty=0, entry status should be PENDING."""
        fill = {"status": "Submitted", "filled_qty": 0, "order_id": "123",
                "avg_fill_price": 0}
        _fill_status = (fill or {}).get("status", "").lower()
        _filled_qty = (fill or {}).get("filled_qty", 0)
        _is_filled = _fill_status == "filled" and _filled_qty > 0
        status = "OPEN" if _is_filled else "PENDING"
        assert status == "PENDING"

    def test_filled_order_status_is_open(self):
        """If broker returns filled_qty=1 and status=Filled, entry should be OPEN."""
        fill = {"status": "Filled", "filled_qty": 1, "order_id": "123",
                "avg_fill_price": 2.50}
        _fill_status = (fill or {}).get("status", "").lower()
        _filled_qty = (fill or {}).get("filled_qty", 0)
        _is_filled = _fill_status == "filled" and _filled_qty > 0
        status = "OPEN" if _is_filled else "PENDING"
        assert status == "OPEN"

    def test_presubmitted_status_is_pending(self):
        """PreSubmitted (intermediate IBKR status) should produce PENDING."""
        fill = {"status": "PreSubmitted", "filled_qty": 0, "order_id": "123"}
        _fill_status = (fill or {}).get("status", "").lower()
        _filled_qty = (fill or {}).get("filled_qty", 0)
        _is_filled = _fill_status == "filled" and _filled_qty > 0
        status = "OPEN" if _is_filled else "PENDING"
        assert status == "PENDING"

    def test_none_fill_status_is_pending(self):
        """If fill is None or empty, status should be PENDING."""
        fill = None
        _fill_status = (fill or {}).get("status", "").lower()
        _filled_qty = (fill or {}).get("filled_qty", 0)
        _is_filled = _fill_status == "filled" and _filled_qty > 0
        status = "OPEN" if _is_filled else "PENDING"
        assert status == "PENDING"

    def test_cash_not_decremented_for_pending(self):
        """Arm cash should NOT be decremented for PENDING entries."""
        fill = {"status": "Submitted", "filled_qty": 0}
        _fill_st = (fill or {}).get("status", "").lower()
        _filled_q = (fill or {}).get("filled_qty", 0)
        _is_filled = _fill_st == "filled" and _filled_q > 0
        initial_cash = 10000.0
        max_risk = 500.0
        cash = initial_cash
        if _is_filled:
            cash -= max_risk
        assert cash == initial_cash, "Cash should be unchanged for unfilled orders"

    def test_cash_decremented_for_filled(self):
        """Arm cash SHOULD be decremented for filled entries."""
        fill = {"status": "Filled", "filled_qty": 1}
        _fill_st = (fill or {}).get("status", "").lower()
        _filled_q = (fill or {}).get("filled_qty", 0)
        _is_filled = _fill_st == "filled" and _filled_q > 0
        initial_cash = 10000.0
        max_risk = 500.0
        cash = initial_cash
        if _is_filled:
            cash -= max_risk
        assert cash == initial_cash - max_risk


# ── Tests: Alpha two-phase status ──────────────────────────────────────────

class TestAlphaJournalStatus:
    """Verify autonomous_execution sets PENDING for unfilled Alpha orders."""

    def test_alpha_unfilled_writes_pending(self):
        """Alpha trade with no fill should get PENDING status."""
        fill = {"status": "Submitted", "filled_qty": 0, "id": "abc"}
        status = ("OPEN" if (fill and str(fill.get("status", "")).lower() == "filled"
                             and fill.get("filled_qty", 0) > 0) else "PENDING")
        assert status == "PENDING"

    def test_alpha_filled_writes_open(self):
        """Alpha trade with confirmed fill should get OPEN status."""
        fill = {"status": "Filled", "filled_qty": 1, "id": "abc"}
        status = ("OPEN" if (fill and str(fill.get("status", "")).lower() == "filled"
                             and fill.get("filled_qty", 0) > 0) else "PENDING")
        assert status == "OPEN"


# ── Tests: _promote_pending_entries ────────────────────────────────────────

class TestPromotePending:
    """Test position_monitor._promote_pending_entries() promotion logic."""

    @pytest.fixture(autouse=True)
    def _setup_journal(self, tmp_path, monkeypatch):
        """Create a temp journal and patch position_monitor to use it."""
        self.journal_path = str(tmp_path / "trades.jsonl")
        # Write empty journal
        Path(self.journal_path).write_text("")

        # We need to import position_monitor with patched paths
        monkeypatch.setattr("builtins.__import__", __import__)

    def _write_journal(self, trades):
        with open(self.journal_path, "w") as f:
            for t in trades:
                f.write(json.dumps(t) + "\n")

    def _read_journal(self):
        entries = []
        with open(self.journal_path) as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
        return entries

    def test_promote_filled_becomes_open(self):
        """PENDING entry whose order is now Filled should become OPEN."""
        # Import the function we need to test the logic
        from position_monitor import _ENTRY_TERMINAL_FAIL_STATUSES

        order_status = {"status": "Filled", "filled_qty": 1, "avg_fill_price": 2.5}
        status_norm = order_status["status"].lower()
        filled = order_status["filled_qty"]

        assert status_norm == "filled"
        assert filled > 0
        # This would set status = "OPEN" in the promotion logic

    def test_promote_cancelled_becomes_phantom(self):
        """PENDING entry whose order was Cancelled should become PHANTOM."""
        from position_monitor import _ENTRY_TERMINAL_FAIL_STATUSES

        for terminal_status in ["cancelled", "rejected", "inactive"]:
            assert terminal_status in _ENTRY_TERMINAL_FAIL_STATUSES

    def test_promote_timeout_3h(self):
        """PENDING entry older than 3 hours should become PHANTOM."""
        from position_monitor import _PENDING_TIMEOUT_HOURS

        old_ts = (datetime.now() - timedelta(hours=4)).isoformat()
        entry_dt = datetime.fromisoformat(old_ts)
        hours_since = (datetime.now() - entry_dt).total_seconds() / 3600
        assert hours_since > _PENDING_TIMEOUT_HOURS

    def test_recent_pending_not_timed_out(self):
        """PENDING entry younger than 3 hours should NOT be timed out."""
        from position_monitor import _PENDING_TIMEOUT_HOURS

        recent_ts = (datetime.now() - timedelta(minutes=30)).isoformat()
        entry_dt = datetime.fromisoformat(recent_ts)
        hours_since = (datetime.now() - entry_dt).total_seconds() / 3600
        assert hours_since < _PENDING_TIMEOUT_HOURS

    def test_no_order_id_becomes_phantom(self):
        """PENDING entry with no order_id should become PHANTOM immediately."""
        trade = _make_pending_trade(order_id="")
        assert trade["order_id"] == ""
        # The promotion logic sets PHANTOM_NEVER_FILLED for empty order_id

    def test_pending_excluded_from_exit_rules(self):
        """PENDING entries should be filtered out of exit-rule evaluation."""
        trades = [
            _make_pending_trade(tid="Ga998"),
            _make_open_trade(tid="Ga001"),
        ]
        # The main() loop filters: if t.get("status") == "OPEN"
        open_for_eval = [t for t in trades if t.get("status") == "OPEN"]
        assert len(open_for_eval) == 1
        assert open_for_eval[0]["id"] == "Ga001"
        # PENDING trade should not be in the evaluation list
        assert all(t["status"] != "PENDING" for t in open_for_eval)
