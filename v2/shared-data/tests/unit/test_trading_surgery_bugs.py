"""Test-first regression tests for the 2026-05-05 trading-path surgery.

Each test reproduces a real bug observed in the 2026-05-05 incident:
  Bug #1 — A021/A022 entry-path: Cancelled status from broker treated as success
  Bug #2 — A020 close-path: indeterminate Submitted status retried instead of polled
  Bug #3 — Reconcile: OPEN journal entry with NO broker positions (entry phantom) not detected
  Bug #4 — Pre-trade exposure: agent enters new trade overlapping existing position contracts

Tests written BEFORE the fix; expected to FAIL initially, PASS after each fix layer.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))


# ── Bootstrap pattern (matches test_phase5_partial_fill.py) ────────────

class _FakeBrokerBase:
    pass


def _make_broker_module():
    mod = MagicMock()
    mod.BrokerBase = _FakeBrokerBase
    mod.DRY_RUN_SENTINEL = {"DRY_RUN": True}
    return mod


_BROKER_MOD = _make_broker_module()
sys.modules.setdefault("broker", _BROKER_MOD)

import _broker_ibkr as _bib  # noqa: E402
import position_monitor as pm  # noqa: E402

IBKRBroker = _bib.IBKRBroker


# ─────────────────────────────────────────────────────────────────────────
# Bug #1 — entry-path status validation
# ─────────────────────────────────────────────────────────────────────────

class TestEntryPathStatusValidation:
    """place_mleg_order must reject Cancelled / Rejected / Inactive statuses
    from the broker. Otherwise journal records OPEN trades that aren't real
    (A021/A022 incident on 2026-05-05).
    """

    @pytest.fixture(autouse=True)
    def _disable_dry_run(self, monkeypatch):
        # The module-level BROKER_DRY_RUN flag short-circuits place_mleg_order
        # to a sentinel before reaching status validation. Disable for these tests.
        monkeypatch.setattr(_bib, "BROKER_DRY_RUN", False)

    def _make_broker_with_status(self, status: str, filled: int = 0):
        broker = object.__new__(IBKRBroker)
        broker._last_order_error = None
        broker._ib = MagicMock()
        # connect() should return True
        broker.connect = MagicMock(return_value=True)
        # Build a fake trade with the desired orderStatus
        fake_trade = MagicMock()
        fake_trade.orderStatus.status = status
        fake_trade.orderStatus.filled = filled
        fake_trade.orderStatus.avgFillPrice = 0.0
        fake_trade.order.permId = 999_999_999
        fake_trade.order.orderId = 99
        fake_trade.order.orderRef = "test-coid"
        broker._ib.placeOrder = MagicMock(return_value=fake_trade)
        broker._ib.sleep = MagicMock()
        # qualifyContracts returns the same contract back
        def fake_qualify(c):
            c.conId = 99999
            return [c]
        broker._ib.qualifyContracts = fake_qualify
        return broker

    def _legs(self):
        return [
            {"action": "sell", "side": "sell", "type": "put", "strike": 100,
             "expiry": "2026-05-15", "symbol": "INTC260515P00100000"},
            {"action": "buy",  "side": "buy",  "type": "put", "strike": 99,
             "expiry": "2026-05-15", "symbol": "INTC260515P00099000"},
        ]

    def test_cancelled_entry_returns_None(self):
        """A021/A022 reproduction: broker says Cancelled, place_mleg_order must
        return None so caller doesn't write a phantom journal entry."""
        broker = self._make_broker_with_status("Cancelled")
        result = broker.place_mleg_order(self._legs(), qty=1, client_order_id="test-coid")
        assert result is None, (
            "Cancelled status MUST return None — broker rejected the order, "
            "the journal entry would be a lie if we proceeded"
        )

    def test_rejected_entry_returns_None(self):
        broker = self._make_broker_with_status("Rejected")
        result = broker.place_mleg_order(self._legs(), qty=1, client_order_id="test-coid")
        assert result is None

    def test_inactive_entry_returns_None(self):
        broker = self._make_broker_with_status("Inactive")
        assert broker.place_mleg_order(self._legs(), qty=1) is None

    def test_apicancelled_entry_returns_None(self):
        broker = self._make_broker_with_status("ApiCancelled")
        assert broker.place_mleg_order(self._legs(), qty=1) is None

    def test_filled_entry_returns_dict(self):
        """Sanity: Filled status still works — happy path unchanged."""
        broker = self._make_broker_with_status("Filled", filled=1)
        result = broker.place_mleg_order(self._legs(), qty=1, client_order_id="test-coid")
        assert result is not None
        assert (result.get("status") or "").lower() == "filled"


# ─────────────────────────────────────────────────────────────────────────
# Bug #2 — close-path state machine (no resubmit on Submitted)
# ─────────────────────────────────────────────────────────────────────────

class TestClosePathStateMachine:
    """A020 reproduction: close order returned status=Submitted (combo not yet
    filled). Old code (last night's fix): returned None → caller treated as
    failure → resubmitted next cycle → 5 reverse closes accumulated.

    Required behavior: when broker reports Submitted/PreSubmitted/PendingSubmit:
      1. place_close_order returns a 'working' state
      2. caller persists the working order_id on the journal entry
      3. next cycle POLLS the existing order_id instead of resubmitting
    """

    def _trade(self, working_close_order_id=None):
        t = {
            "id": "T001",
            "symbol": "SPY",
            "strategy": "iron_condor",
            "legs": [
                {"side": "sell", "symbol": "SPY251219P00500000"},
                {"side": "buy", "symbol": "SPY251219P00495000"},
            ],
        }
        if working_close_order_id:
            t["working_close_order_id"] = working_close_order_id
        return t

    @pytest.fixture(autouse=True)
    def _disable_dry_run(self, monkeypatch):
        monkeypatch.setattr(pm, "DRY_RUN", False)

    def test_submitted_status_returns_working_state_not_None(self, monkeypatch):
        """Submitted should be a 'working' state, NOT cause a resubmission loop."""
        fake_broker = MagicMock()
        fake_broker.close_position.return_value = {
            "order_id": "ord-sub-1",
            "status": "Submitted",
            "filled_qty": 0,
            "avg_fill_price": 0.0,
        }
        monkeypatch.setattr(pm, "get_broker", lambda: fake_broker)
        result = pm.place_close_order(self._trade(), self._trade()["legs"], close_qty=1)
        # CONTRACT: result must be either:
        #   None (if treating as failure — old buggy behavior)
        #   {"id": ..., "status": "Submitted", "_working": True} (new state-machine)
        # A020 retry loop happens if result is None AND no working_close_order_id
        # is persisted. The new contract REQUIRES result indicate a 'working'
        # state distinct from failure.
        assert result is not None, (
            "Submitted status must NOT return None — that would cause a resubmit "
            "loop (A020 reproduction). It should return a 'working' state instead."
        )
        # The working flag (or status preserved as Submitted) is the signal
        is_working = (
            result.get("_working") is True
            or (result.get("status") or "").lower() in {"submitted", "presubmitted", "pendingsubmit"}
        )
        assert is_working, (
            f"result must signal 'working' state, got {result}"
        )

    def test_filled_status_returns_filled(self, monkeypatch):
        fake_broker = MagicMock()
        fake_broker.close_position.return_value = {
            "order_id": "ord-fill-1",
            "status": "Filled",
            "filled_qty": 1,
            "avg_fill_price": 1.32,
        }
        monkeypatch.setattr(pm, "get_broker", lambda: fake_broker)
        result = pm.place_close_order(self._trade(), self._trade()["legs"], close_qty=1)
        assert result is not None
        assert (result.get("status") or "").lower() == "filled"
        # Must NOT be flagged as working
        assert not result.get("_working")

    def test_cancelled_status_returns_None(self, monkeypatch):
        """Cancelled is a terminal failure → None → caller can retry submission."""
        fake_broker = MagicMock()
        fake_broker.close_position.return_value = {
            "order_id": "ord-can-1", "status": "Cancelled",
            "filled_qty": 0, "avg_fill_price": 0.0,
        }
        monkeypatch.setattr(pm, "get_broker", lambda: fake_broker)
        result = pm.place_close_order(self._trade(), self._trade()["legs"], close_qty=1)
        assert result is None


# ─────────────────────────────────────────────────────────────────────────
# Bug #3 — entry phantom (OPEN journal + zero broker legs)
# ─────────────────────────────────────────────────────────────────────────

class TestEntryPhantomDetection:
    """A021/A022 reproduction: journal records trade as OPEN, but no broker
    legs match. reconcile_ghost_positions must alert this asymmetry too.
    """

    @pytest.fixture(autouse=True)
    def _silence_discord(self, monkeypatch, tmp_path):
        self.discord_calls = []
        monkeypatch.setattr(pm, "post_discord", lambda msg: self.discord_calls.append(msg))
        monkeypatch.setattr(pm, "_GHOST_ALERT_FILE", tmp_path / "ghost-alerted.json")

    def _open(self, tid, legs):
        return {
            "id": tid, "trade_id": tid, "status": "OPEN",
            "legs": [{"symbol": s, "action": "sell"} for s in legs],
        }

    def test_open_journal_with_no_broker_legs_is_entry_phantom(self):
        """A021 reproduction: journal says OPEN with 4 XOP legs, broker has nothing."""
        broker_pos = {}  # broker has zero positions
        all_trades = [
            self._open("A021", [
                "XOP260515P00171000", "XOP260515P00170000",
                "XOP260515C00190000", "XOP260515C00191000",
            ]),
        ]
        open_trades = [t for t in all_trades if t["status"] == "OPEN"]
        result = pm.reconcile_ghost_positions(
            broker_pos, open_trades, all_trades=all_trades,
        )
        # The new key 'entry_phantoms' should be populated
        assert "entry_phantoms" in result, (
            "reconcile must report entry_phantoms (OPEN journal + zero broker legs)"
        )
        assert "A021" in result["entry_phantoms"], (
            f"A021 must be flagged as entry phantom, got: {result['entry_phantoms']}"
        )
        # Discord alert must mention 'phantom' or 'never filled'
        assert any(
            ("phantom" in c.lower() or "never filled" in c.lower() or "never executed" in c.lower())
            for c in self.discord_calls
        ), f"alert must mention phantom/never-filled: {self.discord_calls}"

    def test_open_journal_with_partial_broker_legs_NOT_entry_phantom(self):
        """If at least one leg is on the broker, it's not a phantom — could be
        a partial fill or partial close. Different handling."""
        broker_pos = {
            "INTC260515P00094000": {"qty": -1, "market_value": -100},
        }  # Only 1 of 4 legs present
        all_trades = [
            self._open("A100", [
                "INTC260515P00094000", "INTC260515P00093000",
                "INTC260515C00104000", "INTC260515C00105000",
            ]),
        ]
        result = pm.reconcile_ghost_positions(
            broker_pos, [t for t in all_trades if t["status"] == "OPEN"],
            all_trades=all_trades,
        )
        # Not a phantom — at least one leg is on broker
        assert "A100" not in result.get("entry_phantoms", set())

    def test_open_journal_all_legs_on_broker_NOT_phantom(self):
        broker_pos = {
            "INTC260515P00094000": {"qty": -1, "market_value": -100},
            "INTC260515P00093000": {"qty": +1, "market_value": +50},
        }
        all_trades = [
            self._open("A101", ["INTC260515P00094000", "INTC260515P00093000"]),
        ]
        result = pm.reconcile_ghost_positions(
            broker_pos, [t for t in all_trades if t["status"] == "OPEN"],
            all_trades=all_trades,
        )
        assert "A101" not in result.get("entry_phantoms", set())
        assert result["ghosts"] == set()
        assert result["journal_lies"] == set()


# ─────────────────────────────────────────────────────────────────────────
# Bug #4 — pre-trade exposure dedupe
# ─────────────────────────────────────────────────────────────────────────

class TestPreTradeExposureDedupe:
    """Defense in depth: before submitting any new entry, check that none of
    the proposed legs' contracts are already in the portfolio. This prevents
    IBKR Error 201 ('Cannot have open orders on both sides of the same US
    Option contract') from surfacing as opaque Cancelled status.
    """

    @pytest.fixture(autouse=True)
    def _import_autonomous_execution(self, monkeypatch):
        """autonomous_execution.py does `os.makedirs("/root/quantai-v2/...")`
        at import. Tests run as `trader` and can't create dirs under /root.
        Stub os.makedirs and DISCORD env vars so import succeeds in test env.
        """
        # Provide harmless defaults for any required env
        monkeypatch.setenv("DISCORD_CHANNEL_ALERTS", "0")
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "0")
        # Stub os.makedirs to no-op for the protected /root path
        import os as _os
        original = _os.makedirs
        def safe_makedirs(path, *args, **kwargs):
            if str(path).startswith("/root/"):
                return None
            return original(path, *args, **kwargs)
        monkeypatch.setattr(_os, "makedirs", safe_makedirs)
        # Re-import module so import-time mkdir is skipped
        if "autonomous_execution" in sys.modules:
            del sys.modules["autonomous_execution"]
        import autonomous_execution  # noqa: F401
        self.ae = autonomous_execution

    def test_check_function_exists_in_autonomous_execution(self):
        assert hasattr(self.ae, "check_no_overlapping_positions"), (
            "autonomous_execution must expose check_no_overlapping_positions(legs, broker_positions)"
        )

    def test_overlap_returns_False(self):
        proposed_legs = [
            {"symbol": "INTC260515P00100000"},
            {"symbol": "INTC260515P00099000"},
        ]
        existing_positions = [
            {"symbol": "INTC260515P00100000", "qty": -1},
        ]
        ok, reason = self.ae.check_no_overlapping_positions(
            proposed_legs, existing_positions,
        )
        assert ok is False
        assert "INTC260515P00100000" in reason

    def test_no_overlap_returns_True(self):
        proposed_legs = [
            {"symbol": "INTC260515P00100000"},
            {"symbol": "INTC260515P00099000"},
        ]
        existing_positions = [
            {"symbol": "SPY260515C00500000", "qty": 1},
        ]
        ok, _ = self.ae.check_no_overlapping_positions(
            proposed_legs, existing_positions,
        )
        assert ok is True

    def test_zero_qty_existing_NOT_a_blocker(self):
        proposed_legs = [{"symbol": "INTC260515P00100000"}]
        existing_positions = [{"symbol": "INTC260515P00100000", "qty": 0}]
        ok, _ = self.ae.check_no_overlapping_positions(
            proposed_legs, existing_positions,
        )
        assert ok is True

    def test_empty_existing_returns_True(self):
        proposed_legs = [{"symbol": "INTC260515P00100000"}]
        ok, _ = self.ae.check_no_overlapping_positions(proposed_legs, [])
        assert ok is True
