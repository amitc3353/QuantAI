"""Unit tests for the circuit breaker added to /var/dashboard/collect_alpaca.py."""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

ET = ZoneInfo("America/New_York")

COLLECT_ALPACA_PATH = Path("/var/dashboard/collect_alpaca.py")


@pytest.fixture()
def ca(tmp_path, monkeypatch):
    """Load collect_alpaca with broker mocked and all file paths redirected to tmp_path.

    collect_alpaca does ``from broker import get_broker`` at module level. We
    pre-populate sys.modules with a MagicMock so no real broker connection is
    attempted during import or in test cases that don't call fetch_account().
    """
    mock_broker_mod = MagicMock()
    monkeypatch.setitem(sys.modules, "broker", mock_broker_mod)

    spec = importlib.util.spec_from_file_location("_ca_test", str(COLLECT_ALPACA_PATH))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # Redirect every file constant to tmp_path
    circuit = tmp_path / "broker-circuit.json"
    monkeypatch.setattr(mod, "CIRCUIT_FILE", circuit)
    monkeypatch.setattr(mod, "STATE_DIR", tmp_path)
    monkeypatch.setattr(mod, "STATE_FILE", tmp_path / "alpaca-account.json")
    monkeypatch.setattr(mod, "EQUITY_HISTORY", tmp_path / "equity_history.jsonl")

    return mod


# ── TestCircuitOpen ───────────────────────────────────────────────────────────

class TestCircuitOpen:
    def test_returns_false_when_no_file(self, ca):
        assert ca._circuit_open() is False

    def test_returns_true_within_5_min(self, ca, tmp_path, monkeypatch):
        """A failure timestamp 30 seconds ago means circuit is open."""
        ts = (datetime.now(ET) - timedelta(seconds=30)).isoformat()
        (tmp_path / "broker-circuit.json").write_text(json.dumps({"failed_at": ts}))
        assert ca._circuit_open() is True

    def test_returns_false_after_5_min_expires(self, ca, tmp_path):
        """A failure timestamp 6 minutes ago means circuit has reset."""
        ts = (datetime.now(ET) - timedelta(seconds=360)).isoformat()
        (tmp_path / "broker-circuit.json").write_text(json.dumps({"failed_at": ts}))
        assert ca._circuit_open() is False

    def test_returns_false_exactly_at_300s_boundary(self, ca, tmp_path):
        """At exactly CIRCUIT_OPEN_SEC elapsed the circuit is closed (not open)."""
        ts = (datetime.now(ET) - timedelta(seconds=ca.CIRCUIT_OPEN_SEC)).isoformat()
        (tmp_path / "broker-circuit.json").write_text(json.dumps({"failed_at": ts}))
        assert ca._circuit_open() is False

    def test_returns_false_on_corrupt_file(self, ca, tmp_path):
        (tmp_path / "broker-circuit.json").write_text("not valid json {")
        assert ca._circuit_open() is False

    def test_returns_false_on_missing_failed_at_key(self, ca, tmp_path):
        (tmp_path / "broker-circuit.json").write_text(json.dumps({"other": "key"}))
        assert ca._circuit_open() is False


# ── TestTripCircuit ────────────────────────────────────────────────────────────

class TestTripCircuit:
    def test_creates_file(self, ca, tmp_path):
        assert not (tmp_path / "broker-circuit.json").exists()
        ca._trip_circuit()
        assert (tmp_path / "broker-circuit.json").exists()

    def test_writes_valid_json(self, ca, tmp_path):
        ca._trip_circuit()
        data = json.loads((tmp_path / "broker-circuit.json").read_text())
        assert "failed_at" in data

    def test_failed_at_is_recent(self, ca, tmp_path):
        ca._trip_circuit()
        data = json.loads((tmp_path / "broker-circuit.json").read_text())
        ts = datetime.fromisoformat(data["failed_at"])
        # Convert to UTC for comparison
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=ET)
        from datetime import timezone
        age_s = (datetime.now(timezone.utc) - ts.astimezone(timezone.utc)).total_seconds()
        assert age_s < 5  # written within last 5 seconds

    def test_overrides_previous_trip(self, ca, tmp_path):
        """Calling _trip_circuit() twice updates the timestamp."""
        old_ts = (datetime.now(ET) - timedelta(minutes=2)).isoformat()
        (tmp_path / "broker-circuit.json").write_text(json.dumps({"failed_at": old_ts}))
        ca._trip_circuit()
        data = json.loads((tmp_path / "broker-circuit.json").read_text())
        new_ts = datetime.fromisoformat(data["failed_at"])
        old = datetime.fromisoformat(old_ts)
        # new timestamp must be after old timestamp
        assert new_ts > old

    def test_circuit_open_returns_true_after_trip(self, ca, tmp_path):
        ca._trip_circuit()
        assert ca._circuit_open() is True


# ── TestResetCircuit ───────────────────────────────────────────────────────────

class TestResetCircuit:
    def test_removes_file(self, ca, tmp_path):
        (tmp_path / "broker-circuit.json").write_text(json.dumps({"failed_at": "2026-01-01"}))
        ca._reset_circuit()
        assert not (tmp_path / "broker-circuit.json").exists()

    def test_no_error_when_no_file(self, ca, tmp_path):
        """_reset_circuit() must not raise if the file doesn't exist."""
        assert not (tmp_path / "broker-circuit.json").exists()
        ca._reset_circuit()  # should not raise

    def test_circuit_open_returns_false_after_reset(self, ca, tmp_path):
        ca._trip_circuit()
        assert ca._circuit_open() is True
        ca._reset_circuit()
        assert ca._circuit_open() is False


# ── TestMainCircuitIntegration ────────────────────────────────────────────────

class TestMainCircuitIntegration:
    """Test main() behavior when circuit is open vs. closed."""

    def test_main_skips_connect_when_circuit_open(self, ca, tmp_path, monkeypatch):
        """Circuit open → main() returns immediately without calling fetch_account."""
        ca._trip_circuit()  # open the circuit
        fetch_calls = []
        monkeypatch.setattr(ca, "fetch_account", lambda: fetch_calls.append(1) or {})
        ca.main()
        assert fetch_calls == [], "fetch_account must not be called when circuit is open"

    def test_main_calls_fetch_when_circuit_closed(self, ca, tmp_path, monkeypatch):
        """Circuit closed → main() calls fetch_account."""
        fetch_calls = []
        def mock_fetch():
            fetch_calls.append(1)
            return {
                "equity": 1000.0, "last_equity": None, "cash": 500.0,
                "buying_power": 2000.0, "portfolio_value": 1000.0,
                "long_market_value": 0.0, "short_market_value": 0.0,
                "account_status": "ACTIVE",
            }
        monkeypatch.setattr(ca, "fetch_account", mock_fetch)
        ca.main()
        assert fetch_calls == [1]

    def test_main_resets_circuit_on_success(self, ca, tmp_path, monkeypatch):
        """Successful fetch resets the circuit breaker."""
        ca._trip_circuit()
        # Manually expire the circuit so the call goes through
        old_ts = (datetime.now(ET) - timedelta(seconds=400)).isoformat()
        (tmp_path / "broker-circuit.json").write_text(json.dumps({"failed_at": old_ts}))

        monkeypatch.setattr(ca, "fetch_account", lambda: {
            "equity": 1000.0, "last_equity": None, "cash": 500.0,
            "buying_power": 2000.0, "portfolio_value": 1000.0,
            "long_market_value": 0.0, "short_market_value": 0.0,
            "account_status": "ACTIVE",
        })
        ca.main()
        assert ca._circuit_open() is False

    def test_main_trips_circuit_on_failure(self, ca, tmp_path, monkeypatch):
        """Failed fetch trips the circuit breaker."""
        def failing_fetch():
            raise RuntimeError("ibkr broker connect failed")
        monkeypatch.setattr(ca, "fetch_account", failing_fetch)
        ca.main()
        assert ca._circuit_open() is True

    def test_main_writes_circuit_open_flag_to_state_on_failure(self, ca, tmp_path, monkeypatch):
        """State JSON includes circuit_open=True after a broker failure."""
        monkeypatch.setattr(ca, "fetch_account", lambda: (_ for _ in ()).throw(
            RuntimeError("ibkr broker connect failed")
        ))
        ca.main()
        state_file = tmp_path / "alpaca-account.json"
        if state_file.exists():
            state = json.loads(state_file.read_text())
            assert state["data"].get("circuit_open") is True

    def test_main_writes_ok_status_on_success(self, ca, tmp_path, monkeypatch):
        """State JSON shows status=ok after a successful broker call."""
        monkeypatch.setattr(ca, "fetch_account", lambda: {
            "equity": 998000.0, "last_equity": None, "cash": 100000.0,
            "buying_power": 200000.0, "portfolio_value": 998000.0,
            "long_market_value": 0.0, "short_market_value": 0.0,
            "account_status": "ACTIVE",
        })
        ca.main()
        state = json.loads((tmp_path / "alpaca-account.json").read_text())
        assert state["status"] == "ok"
        assert state["data"]["equity"] == 998000.0
