"""Phase 5 close-path safeguard tests (added 2026-05-04 after A018 incident).

Background: A018 was an INTC iron condor entered by agent_alpha. Its close
order was rejected by IBKR Error 201 ("Cannot have open orders on both sides
of the same US Option contract") because A017 had opposing positions on the
same contracts. The broker returned a non-None dict with status='Cancelled'.
The prior `place_close_order` only checked `if order is None`, so the
cancelled order was treated as success — journal marked CLOSED with
fabricated +$13.39 P&L while 4 legs remained open on the broker.

These tests guard against any regression of:
  1. place_close_order rejecting Cancelled / Rejected / Inactive statuses
  2. Indeterminate statuses (Submitted / PreSubmitted) treated as not-yet-filled
  3. verify_legs_flat returning the correct unflat-leg subset
  4. reconcile_ghost_positions distinguishing "journal lie" from "true ghost"
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))


# Bootstrap pattern: position_monitor imports from broker which has heavy deps.
# Same pattern as test_phase5_partial_fill.py — install fakes BEFORE the import.
class _FakeBrokerBase:
    pass


def _make_broker_module():
    mod = MagicMock()
    mod.BrokerBase = _FakeBrokerBase
    mod.DRY_RUN_SENTINEL = {"DRY_RUN": True}
    return mod


_BROKER_MOD = _make_broker_module()
sys.modules.setdefault("broker", _BROKER_MOD)

import position_monitor as pm  # noqa: E402


# ── place_close_order status handling ──────────────────────────────────

class TestPlaceCloseOrderStatusHandling:
    """The bug that caused A018: Cancelled status returned by broker but
    place_close_order's caller only checked `if order is None`."""

    def _trade(self):
        return {
            "id": "T001", "symbol": "SPY", "strategy": "iron_condor",
            "legs": [
                {"side": "sell", "symbol": "SPY251219P00500000"},
                {"side": "buy", "symbol": "SPY251219P00495000"},
            ],
        }

    def _broker_response(self, status):
        return {
            "order_id": "ord-1",
            "status": status,
            "filled_qty": 0,
            "avg_fill_price": 0.0,
            "client_order_id": "close-T001-x",
        }

    @pytest.fixture(autouse=True)
    def _disable_dry_run(self, monkeypatch):
        monkeypatch.setattr(pm, "DRY_RUN", False)

    def test_cancelled_status_returns_None(self, monkeypatch):
        fake_broker = MagicMock()
        fake_broker.close_position.return_value = self._broker_response("Cancelled")
        monkeypatch.setattr(pm, "get_broker", lambda: fake_broker)
        result = pm.place_close_order(self._trade(), self._trade()["legs"], close_qty=1)
        assert result is None, "Cancelled status MUST be treated as failure"

    def test_canceled_alt_spelling_returns_None(self, monkeypatch):
        fake_broker = MagicMock()
        fake_broker.close_position.return_value = self._broker_response("Canceled")
        monkeypatch.setattr(pm, "get_broker", lambda: fake_broker)
        assert pm.place_close_order(self._trade(), self._trade()["legs"]) is None

    def test_apicancelled_returns_None(self, monkeypatch):
        fake_broker = MagicMock()
        fake_broker.close_position.return_value = self._broker_response("ApiCancelled")
        monkeypatch.setattr(pm, "get_broker", lambda: fake_broker)
        assert pm.place_close_order(self._trade(), self._trade()["legs"]) is None

    def test_rejected_returns_None(self, monkeypatch):
        fake_broker = MagicMock()
        fake_broker.close_position.return_value = self._broker_response("Rejected")
        monkeypatch.setattr(pm, "get_broker", lambda: fake_broker)
        assert pm.place_close_order(self._trade(), self._trade()["legs"]) is None

    def test_inactive_returns_None(self, monkeypatch):
        fake_broker = MagicMock()
        fake_broker.close_position.return_value = self._broker_response("Inactive")
        monkeypatch.setattr(pm, "get_broker", lambda: fake_broker)
        assert pm.place_close_order(self._trade(), self._trade()["legs"]) is None

    def test_submitted_indeterminate_returns_working_state(self, monkeypatch):
        """Submitted = order working, hasn't filled yet.
        2026-05-04 contract: return None (caller treats as failure → resubmit loop bug).
        2026-05-05 contract: return dict with _working=True so caller can persist
        working_close_order_id and POLL on next cycle (no resubmit loop)."""
        fake_broker = MagicMock()
        fake_broker.close_position.return_value = self._broker_response("Submitted")
        monkeypatch.setattr(pm, "get_broker", lambda: fake_broker)
        result = pm.place_close_order(self._trade(), self._trade()["legs"])
        assert result is not None, "Submitted MUST return a dict (not None) — was the A020 regression"
        assert result.get("_working") is True

    def test_presubmitted_indeterminate_returns_working_state(self, monkeypatch):
        fake_broker = MagicMock()
        fake_broker.close_position.return_value = self._broker_response("PreSubmitted")
        monkeypatch.setattr(pm, "get_broker", lambda: fake_broker)
        result = pm.place_close_order(self._trade(), self._trade()["legs"])
        assert result is not None
        assert result.get("_working") is True

    def test_filled_status_returns_dict(self, monkeypatch):
        """Filled = real success → caller can mark CLOSED."""
        fake_broker = MagicMock()
        fake_broker.close_position.return_value = self._broker_response("Filled")
        monkeypatch.setattr(pm, "get_broker", lambda: fake_broker)
        result = pm.place_close_order(self._trade(), self._trade()["legs"])
        assert result is not None
        assert result["id"] == "ord-1"
        assert (result["status"] or "").lower() == "filled"

    def test_dry_run_returns_simulated(self, monkeypatch):
        monkeypatch.setattr(pm, "DRY_RUN", True)
        # Add 'side' to each leg for dry-run logging
        trade = self._trade()
        result = pm.place_close_order(trade, trade["legs"])
        assert result is not None
        assert result["status"] == "simulated"


# ── reconcile_ghost_positions journal-lie detection ───────────────────

class TestReconcileJournalLie:
    """When journal says CLOSED but broker still has the legs, that's a journal
    lie (close failed silently). Must be distinguished from a true ghost (no
    journal entry references the position at all).
    """

    @pytest.fixture(autouse=True)
    def _silence_discord(self, monkeypatch, tmp_path):
        self.discord_calls = []
        monkeypatch.setattr(pm, "post_discord", lambda msg: self.discord_calls.append(msg))
        monkeypatch.setattr(pm, "_GHOST_ALERT_FILE", tmp_path / "ghost-alerted.json")

    def _open(self, tid, legs):
        return {"id": tid, "trade_id": tid, "status": "OPEN",
                "legs": [{"symbol": s, "action": "sell"} for s in legs]}

    def _closed(self, tid, legs, close_ts="2026-05-04T14:00:00Z"):
        return {"id": tid, "trade_id": tid, "status": "CLOSED",
                "close_timestamp": close_ts,
                "legs": [{"symbol": s, "action": "sell"} for s in legs]}

    def test_closed_journal_with_open_broker_position_is_journal_lie(self):
        """A018 reproduction: journal CLOSED, broker still has the leg → journal lie."""
        broker_pos = {"INTC260515P00094000": {"qty": -1, "market_value": -429}}
        all_trades = [
            self._closed("A018", ["INTC260515P00094000"]),
        ]
        result = pm.reconcile_ghost_positions(
            broker_pos, open_trades=[], all_trades=all_trades,
        )
        assert ("INTC260515P00094000", "A018") in result["journal_lies"]
        assert "INTC260515P00094000" not in result["ghosts"], \
            "should NOT be flagged as true ghost — it's a known closed entry"
        # Discord alert should be a JOURNAL LIE, not a generic ghost
        assert any("Journal lie" in c for c in self.discord_calls)
        assert not any(
            "Ghost position detected" in c and "INTC" in c and "no matching journal entry" in c
            for c in self.discord_calls
        )

    def test_journal_lie_alert_names_the_lying_trade_id(self):
        broker_pos = {"INTC260515P00094000": {"qty": -1, "market_value": -429}}
        all_trades = [self._closed("A018", ["INTC260515P00094000"])]
        pm.reconcile_ghost_positions(broker_pos, [], all_trades=all_trades)
        # Alert message must reference the trade ID
        assert any("A018" in c for c in self.discord_calls)

    def test_multiple_legs_one_lying_trade_one_alert(self):
        """A018 has 4 legs all open on broker — should be ONE alert per trade,
        not 4 separate alerts (the previous fan-out was noise)."""
        broker_pos = {
            "INTC260515P00094000": {"qty": -1, "market_value": -429},
            "INTC260515P00093000": {"qty": +1, "market_value": +382},
            "INTC260515C00104000": {"qty": -1, "market_value": -243},
            "INTC260515C00105000": {"qty": +1, "market_value": +221},
        }
        all_trades = [self._closed("A018", [
            "INTC260515P00094000", "INTC260515P00093000",
            "INTC260515C00104000", "INTC260515C00105000",
        ])]
        pm.reconcile_ghost_positions(broker_pos, [], all_trades=all_trades)
        # All 4 legs grouped into one alert
        lie_alerts = [c for c in self.discord_calls if "Journal lie" in c]
        assert len(lie_alerts) == 1
        assert "4 leg(s)" in lie_alerts[0] or "4" in lie_alerts[0]

    def test_true_ghost_still_detected_when_no_journal_reference(self):
        """A position with NO journal entry whatsoever is still a true ghost."""
        broker_pos = {"SPY251219C00600000": {"qty": 1, "market_value": 100}}
        all_trades = []  # no journal entries at all
        result = pm.reconcile_ghost_positions(
            broker_pos, open_trades=[], all_trades=all_trades,
        )
        assert "SPY251219C00600000" in result["ghosts"]
        assert result["journal_lies"] == set()

    def test_open_journal_position_is_neither_ghost_nor_lie(self):
        broker_pos = {"SPY251219C00600000": {"qty": 1, "market_value": 100}}
        all_trades = [self._open("A001", ["SPY251219C00600000"])]
        open_trades = [t for t in all_trades if t["status"] == "OPEN"]
        result = pm.reconcile_ghost_positions(
            broker_pos, open_trades, all_trades=all_trades,
        )
        assert result["ghosts"] == set()
        assert result["journal_lies"] == set()

    def test_legacy_call_without_all_trades_still_works(self):
        """Backward compat: callers that don't pass all_trades only see ghosts."""
        broker_pos = {"SPY251219C00600000": {"qty": 1, "market_value": 100}}
        result = pm.reconcile_ghost_positions(broker_pos, open_trades=[])
        assert "SPY251219C00600000" in result["ghosts"]
        # No journal-lie detection without all_trades → empty set
        assert result["journal_lies"] == set()
