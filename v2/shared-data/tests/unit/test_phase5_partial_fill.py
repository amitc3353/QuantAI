"""Unit tests: Phase 5 partial-fill safeguard.

Three areas under test:
  1. IBKRBroker._find_open_order_by_ref / get_open_orders
  2. IBKRBroker.place_mleg_order recovery path (exception after submit)
  3. position_monitor ghost-position reconciliation
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

# ── Bootstrap: stub out 'broker' so _broker_ibkr.IBKRBroker is a real class ──

class _FakeBrokerBase:
    pass


def _make_broker_module():
    mod = MagicMock()
    mod.BrokerBase = _FakeBrokerBase
    mod.DRY_RUN_SENTINEL = {"DRY_RUN": True}
    mod.BROKER_DRY_RUN = False
    mod._parse_occ = MagicMock(return_value=None)
    mod._safe_mid = MagicMock(return_value=0.0)
    return mod


# We must set the fake broker BEFORE importing _broker_ibkr.
# Use a fresh sys.modules slot keyed to "broker" for the duration.
_BROKER_MOD = _make_broker_module()
sys.modules.setdefault("broker", _BROKER_MOD)

# Now import (or re-use the already-imported) _broker_ibkr
import importlib
_bib = sys.modules.get("_broker_ibkr")
if _bib is None:
    sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))
    import _broker_ibkr as _bib

IBKRBroker = _bib.IBKRBroker

SCRIPTS_DIR = Path(__file__).parent.parent.parent / "scripts"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_trade_mock(order_ref: str, perm_id: int = 12345, status: str = "Submitted") -> MagicMock:
    """Build a minimal ib_insync Trade-like mock."""
    trade = MagicMock()
    trade.order.orderRef = order_ref
    trade.order.permId = perm_id
    trade.order.orderId = perm_id
    trade.orderStatus.status = status
    trade.orderStatus.filled = 0
    trade.orderStatus.avgFillPrice = 0.0
    return trade


def _make_broker(connected: bool = True, dry_run: bool = False) -> IBKRBroker:
    """Create an IBKRBroker with a mocked _ib and connect()."""
    broker = object.__new__(IBKRBroker)
    broker._ib = MagicMock()
    broker._last_order_error = None
    broker._connected = connected
    # connect() returns the connected flag
    broker.connect = MagicMock(return_value=connected)
    # Patch BROKER_DRY_RUN at module level for tests that need it
    return broker


# ══════════════════════════════════════════════════════════════════════════════
# 1. IBKRBroker.get_open_orders
# ══════════════════════════════════════════════════════════════════════════════

class TestGetOpenOrders:

    def test_returns_empty_when_not_connected(self):
        broker = _make_broker(connected=False)
        result = broker.get_open_orders()
        assert result == []

    def test_returns_all_open_trades_when_no_filter(self):
        broker = _make_broker()
        t1 = _make_trade_mock("coid-A", perm_id=1)
        t2 = _make_trade_mock("coid-B", perm_id=2)
        broker._ib.openTrades.return_value = [t1, t2]
        result = broker.get_open_orders()
        assert len(result) == 2

    def test_filters_by_client_order_id(self):
        broker = _make_broker()
        t1 = _make_trade_mock("coid-A", perm_id=1)
        t2 = _make_trade_mock("coid-B", perm_id=2)
        broker._ib.openTrades.return_value = [t1, t2]
        result = broker.get_open_orders(client_order_id="coid-A")
        assert len(result) == 1
        assert result[0]["client_order_id"] == "coid-A"

    def test_returns_empty_when_no_match(self):
        broker = _make_broker()
        t1 = _make_trade_mock("coid-A", perm_id=1)
        broker._ib.openTrades.return_value = [t1]
        result = broker.get_open_orders(client_order_id="coid-MISSING")
        assert result == []

    def test_result_shape_matches_trade_to_result(self):
        broker = _make_broker()
        trade = _make_trade_mock("coid-X", perm_id=9999, status="PreSubmitted")
        broker._ib.openTrades.return_value = [trade]
        result = broker.get_open_orders()
        r = result[0]
        assert "order_id" in r
        assert "status" in r
        assert "filled_qty" in r
        assert "avg_fill_price" in r
        assert "client_order_id" in r

    def test_returns_empty_on_exception(self):
        broker = _make_broker()
        broker._ib.openTrades.side_effect = RuntimeError("connection lost")
        result = broker.get_open_orders()
        assert result == []

    def test_does_not_raise_when_ib_is_unhealthy(self):
        broker = _make_broker()
        broker._ib.openTrades.side_effect = Exception("boom")
        # Must not propagate
        result = broker.get_open_orders()
        assert isinstance(result, list)


# ══════════════════════════════════════════════════════════════════════════════
# 2. IBKRBroker._find_open_order_by_ref
# ══════════════════════════════════════════════════════════════════════════════

class TestFindOpenOrderByRef:

    def test_returns_none_when_no_client_order_id(self):
        broker = _make_broker()
        assert broker._find_open_order_by_ref(None) is None
        assert broker._find_open_order_by_ref("") is None

    def test_finds_trade_in_open_trades(self):
        broker = _make_broker()
        trade = _make_trade_mock("coid-Z", perm_id=42)
        broker._ib.openTrades.return_value = [trade]
        broker._ib.trades.return_value = []
        result = broker._find_open_order_by_ref("coid-Z")
        assert result is not None
        assert result["client_order_id"] == "coid-Z"

    def test_falls_back_to_trades_if_not_in_open_trades(self):
        broker = _make_broker()
        trade = _make_trade_mock("coid-Z", perm_id=42)
        broker._ib.openTrades.return_value = []
        broker._ib.trades.return_value = [trade]
        result = broker._find_open_order_by_ref("coid-Z")
        assert result is not None
        assert result["client_order_id"] == "coid-Z"

    def test_returns_none_when_not_found_anywhere(self):
        broker = _make_broker()
        broker._ib.openTrades.return_value = []
        broker._ib.trades.return_value = []
        result = broker._find_open_order_by_ref("coid-GHOST")
        assert result is None

    def test_returns_none_on_exception(self):
        broker = _make_broker()
        broker._ib.openTrades.side_effect = Exception("network error")
        result = broker._find_open_order_by_ref("coid-Z")
        assert result is None

    def test_skips_trades_with_wrong_ref(self):
        broker = _make_broker()
        t_wrong = _make_trade_mock("wrong-ref", perm_id=1)
        t_right = _make_trade_mock("coid-Z", perm_id=2)
        broker._ib.openTrades.return_value = [t_wrong, t_right]
        broker._ib.trades.return_value = []
        result = broker._find_open_order_by_ref("coid-Z")
        assert result is not None
        assert result["client_order_id"] == "coid-Z"


# ══════════════════════════════════════════════════════════════════════════════
# 3. IBKRBroker.place_mleg_order recovery path
# ══════════════════════════════════════════════════════════════════════════════

class TestPlaceMlegOrderRecovery:
    """Tests for the 'order_submitted=True then exception' recovery path.

    We test the behaviour of the inner try/except directly by verifying
    the post-submit exception flow via a side_effect on _ib.sleep.
    """

    def _build_broker_for_submit(self, coid: str, crash_on_sleep: bool = False,
                                  recovered_trade=None):
        """Build a broker whose placeOrder() succeeds but sleep() raises."""
        broker = _make_broker()

        # _is_in_restart_window — always False in tests
        with patch.object(_bib, "_is_in_restart_window", return_value=False):
            pass  # just for warm-up; real patch applied in individual tests

        broker._ib.qualifyContracts.return_value = [MagicMock(conId=1, exchange="SMART")]
        fake_trade = _make_trade_mock(coid, perm_id=777)
        broker._ib.placeOrder.return_value = fake_trade

        if crash_on_sleep:
            # Crash on FIRST sleep (the post-placeOrder sleep(1));
            # subsequent calls (the recovery sleep(0.5)) should succeed.
            call_count = {"n": 0}

            def _sleep_side_effect(secs):
                call_count["n"] += 1
                if call_count["n"] == 1:
                    raise ConnectionResetError("connection reset by peer")

            broker._ib.sleep.side_effect = _sleep_side_effect

        if recovered_trade is not None:
            broker._ib.openTrades.return_value = [recovered_trade]
            broker._ib.trades.return_value = []
        else:
            broker._ib.openTrades.return_value = []
            broker._ib.trades.return_value = []

        return broker

    def test_successful_path_returns_result_dict(self):
        """Normal success: placeOrder works, sleep(1) works → result returned."""
        broker = _make_broker()
        fake_trade = _make_trade_mock("coid-ok", perm_id=100)
        broker._ib.placeOrder.return_value = fake_trade
        broker._ib.qualifyContracts.return_value = [MagicMock(conId=1, exchange="SMART")]

        legs = [{"symbol": "SPY251219C00600000", "side": "buy", "ratio_qty": 1}]

        with patch.object(_bib, "_is_in_restart_window", return_value=False), \
             patch.object(_bib, "BROKER_DRY_RUN", False), \
             patch.object(_bib, "_parse_occ") as mock_parse:
            from collections import namedtuple
            Spec = namedtuple("Spec", ["root", "expiry", "strike", "right"])
            mock_parse.return_value = Spec("SPY", "20251219", 600.0, "C")
            result = broker.place_mleg_order(legs, qty=1, client_order_id="coid-ok")

        assert result is not None
        assert result["order_id"] == "100"  # str(perm_id)

    def test_exception_after_submit_with_recovery_returns_order(self):
        """Exception fires after placeOrder; order found in open trades → recovered."""
        coid = "coid-recovery"
        recovered_trade = _make_trade_mock(coid, perm_id=888)

        broker = _make_broker()
        broker._ib.qualifyContracts.return_value = [MagicMock(conId=1, exchange="SMART")]
        broker._ib.placeOrder.return_value = recovered_trade

        call_count = {"n": 0}

        def _sleep_side_effect(secs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise ConnectionResetError("connection reset")

        broker._ib.sleep.side_effect = _sleep_side_effect
        broker._ib.openTrades.return_value = [recovered_trade]
        broker._ib.trades.return_value = []

        legs = [{"symbol": "SPY251219C00600000", "side": "buy", "ratio_qty": 1}]
        with patch.object(_bib, "_is_in_restart_window", return_value=False), \
             patch.object(_bib, "BROKER_DRY_RUN", False), \
             patch.object(_bib, "_parse_occ") as mock_parse:
            from collections import namedtuple
            Spec = namedtuple("Spec", ["root", "expiry", "strike", "right"])
            mock_parse.return_value = Spec("SPY", "20251219", 600.0, "C")
            result = broker.place_mleg_order(legs, qty=1, client_order_id=coid)

        assert result is not None, "Expected recovered result, got None"
        assert result["client_order_id"] == coid

    def test_exception_after_submit_no_recovery_returns_none(self):
        """Exception after placeOrder, order NOT in open trades → returns None."""
        coid = "coid-lost"
        broker = _make_broker()
        broker._ib.qualifyContracts.return_value = [MagicMock(conId=1, exchange="SMART")]
        fake_trade = _make_trade_mock(coid, perm_id=999)
        broker._ib.placeOrder.return_value = fake_trade

        call_count = {"n": 0}

        def _sleep_side_effect(secs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise ConnectionResetError("connection reset")

        broker._ib.sleep.side_effect = _sleep_side_effect
        broker._ib.openTrades.return_value = []  # not found
        broker._ib.trades.return_value = []

        legs = [{"symbol": "SPY251219C00600000", "side": "buy", "ratio_qty": 1}]
        with patch.object(_bib, "_is_in_restart_window", return_value=False), \
             patch.object(_bib, "BROKER_DRY_RUN", False), \
             patch.object(_bib, "_parse_occ") as mock_parse:
            from collections import namedtuple
            Spec = namedtuple("Spec", ["root", "expiry", "strike", "right"])
            mock_parse.return_value = Spec("SPY", "20251219", 600.0, "C")
            result = broker.place_mleg_order(legs, qty=1, client_order_id=coid)

        assert result is None, "Expected None when order not found in open trades"
        assert broker._last_order_error is not None

    def test_finally_flushes_async_on_success(self):
        """On success, finally block calls sleep(0.5) to flush callbacks."""
        broker = _make_broker()
        fake_trade = _make_trade_mock("coid-ok2", perm_id=200)
        broker._ib.placeOrder.return_value = fake_trade
        broker._ib.qualifyContracts.return_value = [MagicMock(conId=1, exchange="SMART")]

        sleep_args = []
        broker._ib.sleep.side_effect = lambda s: sleep_args.append(s)

        legs = [{"symbol": "SPY251219C00600000", "side": "buy", "ratio_qty": 1}]
        with patch.object(_bib, "_is_in_restart_window", return_value=False), \
             patch.object(_bib, "BROKER_DRY_RUN", False), \
             patch.object(_bib, "_parse_occ") as mock_parse:
            from collections import namedtuple
            Spec = namedtuple("Spec", ["root", "expiry", "strike", "right"])
            mock_parse.return_value = Spec("SPY", "20251219", 600.0, "C")
            broker.place_mleg_order(legs, qty=1, client_order_id="coid-ok2")

        # sleep(1) from normal path + sleep(0.5) from finally
        assert 0.5 in sleep_args, f"Expected sleep(0.5) in finally block; got {sleep_args}"

    def test_exception_before_submit_returns_none_no_recovery(self):
        """Exception before placeOrder (e.g. qualifyContracts) → None, no recovery attempt."""
        broker = _make_broker()
        broker._ib.qualifyContracts.return_value = []  # empty → early return None

        legs = [{"symbol": "SPY251219C00600000", "side": "buy", "ratio_qty": 1}]
        with patch.object(_bib, "_is_in_restart_window", return_value=False), \
             patch.object(_bib, "BROKER_DRY_RUN", False), \
             patch.object(_bib, "_parse_occ") as mock_parse:
            from collections import namedtuple
            Spec = namedtuple("Spec", ["root", "expiry", "strike", "right"])
            mock_parse.return_value = Spec("SPY", "20251219", 600.0, "C")
            result = broker.place_mleg_order(legs, qty=1, client_order_id="coid-qual-fail")

        assert result is None
        # openTrades should NOT have been called (no recovery path for pre-submit failure)
        broker._ib.openTrades.assert_not_called()


# ══════════════════════════════════════════════════════════════════════════════
# 4. position_monitor ghost-position reconciliation
# ══════════════════════════════════════════════════════════════════════════════

# Load position_monitor without importing broker or requests at module level
import importlib.util
import types

_PM_PATH = Path("/home/trader/QuantAI/v2/shared-data/scripts/position_monitor.py")


@pytest.fixture()
def pm(tmp_path, monkeypatch):
    """Load position_monitor with file I/O patched to tmp_path."""
    # Stub out heavy deps before loading
    fake_requests = MagicMock()
    fake_requests.post.return_value = MagicMock(status_code=200)
    monkeypatch.setitem(sys.modules, "requests", fake_requests)

    fake_broker_mod = MagicMock()
    monkeypatch.setitem(sys.modules, "broker", fake_broker_mod)

    spec = importlib.util.spec_from_file_location("_pm_test", str(_PM_PATH))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # Redirect file paths to tmp_path
    monkeypatch.setattr(mod, "DASH_FILE", tmp_path / "quantai-positions.json")
    monkeypatch.setattr(mod, "_GHOST_ALERT_FILE",
                        tmp_path / "ghost-positions-alerted.json")

    return mod


def _open_trade(trade_id: str, legs: list) -> dict:
    """Build a minimal OPEN journal trade entry."""
    return {
        "id": trade_id,
        "status": "OPEN",
        "source": "agent_alpha",
        "symbol": "SPY",
        "legs": legs,
    }


def _leg(occ: str) -> dict:
    return {"symbol": occ, "side": "buy", "ratio_qty": 1}


class TestReconcileGhostPositions:

    def test_no_ghosts_when_all_positions_in_journal(self, pm, tmp_path):
        """All broker positions appear in journal legs → no ghosts, no Discord."""
        discord_calls = []
        pm.post_discord = lambda msg: discord_calls.append(msg)

        broker_pos = {"SPY251219C00600000": {"qty": 1, "market_value": 100}}
        trades = [_open_trade("A001", [_leg("SPY251219C00600000")])]

        result = pm.reconcile_ghost_positions(broker_pos, trades)

        assert result["ghosts"] == set()
        assert discord_calls == []

    def test_ghost_detected_when_position_not_in_journal(self, pm, tmp_path):
        """Broker position not in any journal entry → ghost detected."""
        discord_calls = []
        pm.post_discord = lambda msg: discord_calls.append(msg)

        broker_pos = {"SPY251219C00600000": {"qty": 1, "market_value": 200}}
        trades = []  # no open journal entries

        result = pm.reconcile_ghost_positions(broker_pos, trades)

        assert "SPY251219C00600000" in result["ghosts"]
        assert any("Ghost position" in c for c in discord_calls)

    def test_ghost_alert_message_contains_symbol(self, pm, tmp_path):
        """Alert message must name the unknown symbol."""
        discord_calls = []
        pm.post_discord = lambda msg: discord_calls.append(msg)

        occ = "XSP251219P04500000"
        broker_pos = {occ: {"qty": -2, "market_value": -150}}
        trades = []

        pm.reconcile_ghost_positions(broker_pos, trades)

        assert any(occ in c for c in discord_calls)

    def test_zero_qty_positions_ignored(self, pm, tmp_path):
        """Zero-qty broker positions (stale API artefacts) are not flagged."""
        discord_calls = []
        pm.post_discord = lambda msg: discord_calls.append(msg)

        broker_pos = {"SPY251219C00600000": {"qty": 0, "market_value": 0}}
        trades = []

        result = pm.reconcile_ghost_positions(broker_pos, trades)

        assert result["ghosts"] == set()
        assert discord_calls == []

    def test_multiple_ghosts_all_alerted(self, pm, tmp_path):
        """Multiple unknown positions → each gets its own Discord alert."""
        discord_calls = []
        pm.post_discord = lambda msg: discord_calls.append(msg)

        broker_pos = {
            "SPY251219C00600000": {"qty": 1, "market_value": 100},
            "SPY251219P00550000": {"qty": -1, "market_value": -80},
        }
        trades = []

        result = pm.reconcile_ghost_positions(broker_pos, trades)

        assert len(result["ghosts"]) == 2
        assert len(discord_calls) == 2

    def test_cooldown_suppresses_repeat_alert(self, pm, tmp_path):
        """Alert already sent within cooldown window → no duplicate alert."""
        discord_calls = []
        pm.post_discord = lambda msg: discord_calls.append(msg)

        occ = "SPY251219C00600000"
        recent_ts = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        (tmp_path / "ghost-positions-alerted.json").write_text(json.dumps({occ: recent_ts}))

        broker_pos = {occ: {"qty": 1, "market_value": 100}}
        trades = []

        pm.reconcile_ghost_positions(broker_pos, trades)

        assert discord_calls == []

    def test_cooldown_expired_allows_repeat_alert(self, pm, tmp_path):
        """Previous alert was >60 min ago → cooldown expired, alert fires."""
        discord_calls = []
        pm.post_discord = lambda msg: discord_calls.append(msg)

        occ = "SPY251219C00600000"
        old_ts = (datetime.now(timezone.utc) - timedelta(minutes=70)).isoformat()
        (tmp_path / "ghost-positions-alerted.json").write_text(json.dumps({occ: old_ts}))

        broker_pos = {occ: {"qty": 1, "market_value": 100}}
        trades = []

        pm.reconcile_ghost_positions(broker_pos, trades)

        assert any("Ghost position" in c for c in discord_calls)

    def test_cooldown_record_written_after_alert(self, pm, tmp_path):
        """After alerting, timestamp is written to the cooldown file."""
        pm.post_discord = lambda msg: None  # consume

        occ = "SPY251219C00600000"
        broker_pos = {occ: {"qty": 1, "market_value": 100}}
        trades = []

        pm.reconcile_ghost_positions(broker_pos, trades)

        cooldown_file = tmp_path / "ghost-positions-alerted.json"
        assert cooldown_file.exists()
        data = json.loads(cooldown_file.read_text())
        assert occ in data

    def test_journal_position_not_flagged_as_ghost(self, pm, tmp_path):
        """Position that matches a journal leg is NOT a ghost."""
        discord_calls = []
        pm.post_discord = lambda msg: discord_calls.append(msg)

        occ_known = "SPY251219C00600000"
        occ_ghost = "SPY251219P00550000"

        broker_pos = {
            occ_known: {"qty": 1, "market_value": 100},
            occ_ghost: {"qty": -1, "market_value": -80},
        }
        trades = [_open_trade("A001", [_leg(occ_known)])]

        result = pm.reconcile_ghost_positions(broker_pos, trades)

        assert occ_ghost in result["ghosts"]
        assert occ_known not in result["ghosts"]
        assert len(discord_calls) == 1

    def test_returns_empty_set_when_no_positions(self, pm, tmp_path):
        pm.post_discord = lambda msg: None
        result = pm.reconcile_ghost_positions({}, [])
        assert result["ghosts"] == set()
        assert result["journal_lies"] == set()

    def test_ghost_alert_message_contains_fix_guidance(self, pm, tmp_path):
        """Alert message must tell operator what to check."""
        discord_calls = []
        pm.post_discord = lambda msg: discord_calls.append(msg)

        occ = "XSP251219C04600000"
        broker_pos = {occ: {"qty": 2, "market_value": 50}}
        trades = []

        pm.reconcile_ghost_positions(broker_pos, trades)

        assert discord_calls
        msg = discord_calls[0]
        assert "journal" in msg.lower() or "TWS" in msg


class TestGhostAlertHelpers:

    def test_ghost_alert_ok_when_no_file(self, pm, tmp_path):
        assert pm._ghost_alert_ok("SPY251219C00600000") is True

    def test_ghost_alert_ok_within_cooldown(self, pm, tmp_path):
        occ = "SPY251219C00600000"
        recent = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        (tmp_path / "ghost-positions-alerted.json").write_text(json.dumps({occ: recent}))
        assert pm._ghost_alert_ok(occ) is False

    def test_ghost_alert_ok_after_cooldown_expires(self, pm, tmp_path):
        occ = "SPY251219C00600000"
        old = (datetime.now(timezone.utc) - timedelta(minutes=65)).isoformat()
        (tmp_path / "ghost-positions-alerted.json").write_text(json.dumps({occ: old}))
        assert pm._ghost_alert_ok(occ) is True

    def test_ghost_alert_ok_returns_true_on_corrupt_file(self, pm, tmp_path):
        """Fail-open: corrupt cooldown file → allow alert."""
        (tmp_path / "ghost-positions-alerted.json").write_text("{not valid json!!!")
        assert pm._ghost_alert_ok("SPY251219C00600000") is True

    def test_record_ghost_alert_creates_file(self, pm, tmp_path):
        occ = "SPY251219C00600000"
        assert not (tmp_path / "ghost-positions-alerted.json").exists()
        pm._record_ghost_alert(occ)
        assert (tmp_path / "ghost-positions-alerted.json").exists()

    def test_record_ghost_alert_writes_recent_ts(self, pm, tmp_path):
        occ = "SPY251219C00600000"
        pm._record_ghost_alert(occ)
        data = json.loads((tmp_path / "ghost-positions-alerted.json").read_text())
        ts = datetime.fromisoformat(data[occ])
        age_s = (datetime.now(timezone.utc) - ts.astimezone(timezone.utc)).total_seconds()
        assert age_s < 5

    def test_record_ghost_alert_updates_existing_entry(self, pm, tmp_path):
        occ = "SPY251219C00600000"
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        (tmp_path / "ghost-positions-alerted.json").write_text(json.dumps({occ: old_ts}))
        pm._record_ghost_alert(occ)
        data = json.loads((tmp_path / "ghost-positions-alerted.json").read_text())
        new_ts = datetime.fromisoformat(data[occ])
        old = datetime.fromisoformat(old_ts)
        assert new_ts > old
