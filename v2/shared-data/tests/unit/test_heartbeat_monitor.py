"""Unit tests for IBKR port probe and state tracking added to heartbeat_monitor.py."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# heartbeat_monitor lives in scripts/ which conftest adds to sys.path.
# It has a module-level _logger_setup call; that's fine — logging is a no-op in tests.
import heartbeat_monitor as hm


# ── Autouse fixture: redirect all file I/O to tmp_path ───────────────────────

@pytest.fixture(autouse=True)
def _patch_hm_paths(tmp_path, monkeypatch):
    """Redirect every file path in heartbeat_monitor to tmp_path for isolation."""
    monkeypatch.setattr(hm, "BEAT_DIR",           tmp_path)
    monkeypatch.setattr(hm, "STATE_FILE",          tmp_path / "quantai-heartbeats.json")
    monkeypatch.setattr(hm, "COOLDOWN_FILE",       tmp_path / "alert_cooldown.json")
    monkeypatch.setattr(hm, "LOG_THROTTLE_FILE",   tmp_path / "hb-last-log.json")
    monkeypatch.setattr(hm, "IBKR_PROBE_FAIL_FILE", tmp_path / "ibkr_probe_fail.json")


def _write_probe_state(tmp_path, consecutive_fails: int, first_fail_at: str | None = None):
    """Helper: pre-populate the probe state file."""
    payload = {
        "consecutive_fails": consecutive_fails,
        "first_fail_at": first_fail_at or "2026-05-03T10:00:00+00:00",
    }
    (tmp_path / "ibkr_probe_fail.json").write_text(json.dumps(payload))


def _make_pipeline_beat(tmp_path, age_seconds: int = 30):
    """Write a fresh pipeline.beat so main() doesn't raise on a missing beat."""
    ts = (datetime.now(timezone.utc) - timedelta(seconds=age_seconds)).isoformat()
    (tmp_path / "pipeline.beat").write_text(ts)


def _make_socket_mock(connect_ex_result: int = 0, raise_exc=None) -> MagicMock:
    s = MagicMock()
    if raise_exc is not None:
        s.connect_ex.side_effect = raise_exc
    else:
        s.connect_ex.return_value = connect_ex_result
    return s


# ── TestProbeIbkrPort ─────────────────────────────────────────────────────────

class TestProbeIbkrPort:
    def test_returns_true_when_port_open(self):
        mock_s = _make_socket_mock(connect_ex_result=0)
        with patch("socket.socket", return_value=mock_s):
            assert hm.probe_ibkr_port() is True

    def test_returns_false_when_port_refused(self):
        mock_s = _make_socket_mock(connect_ex_result=111)
        with patch("socket.socket", return_value=mock_s):
            assert hm.probe_ibkr_port() is False

    def test_returns_false_on_non_zero_errno(self):
        mock_s = _make_socket_mock(connect_ex_result=61)  # ECONNREFUSED on macOS
        with patch("socket.socket", return_value=mock_s):
            assert hm.probe_ibkr_port() is False

    def test_returns_false_on_oserror(self):
        mock_s = _make_socket_mock(raise_exc=OSError("network unreachable"))
        with patch("socket.socket", return_value=mock_s):
            assert hm.probe_ibkr_port() is False

    def test_socket_always_closed_on_success(self):
        mock_s = _make_socket_mock(connect_ex_result=0)
        with patch("socket.socket", return_value=mock_s):
            hm.probe_ibkr_port()
        mock_s.close.assert_called_once()

    def test_socket_always_closed_on_exception(self):
        mock_s = _make_socket_mock(raise_exc=OSError("boom"))
        with patch("socket.socket", return_value=mock_s):
            hm.probe_ibkr_port()
        mock_s.close.assert_called_once()

    def test_settimeout_called_with_2(self):
        mock_s = _make_socket_mock()
        with patch("socket.socket", return_value=mock_s):
            hm.probe_ibkr_port()
        mock_s.settimeout.assert_called_once_with(2)

    def test_connects_to_configured_host_and_port(self):
        mock_s = _make_socket_mock()
        with patch("socket.socket", return_value=mock_s):
            hm.probe_ibkr_port()
        mock_s.connect_ex.assert_called_once_with((hm.IBKR_HOST, hm.IBKR_PORT))


# ── TestIbkrProbeState ────────────────────────────────────────────────────────

class TestIbkrProbeState:
    """Tests for get_ibkr_probe_state() and update_ibkr_probe_state()."""

    # get_ibkr_probe_state ---

    def test_get_returns_defaults_when_no_file(self, tmp_path):
        state = hm.get_ibkr_probe_state()
        assert state == {"consecutive_fails": 0, "first_fail_at": None}

    def test_get_reads_existing_file(self, tmp_path):
        payload = {"consecutive_fails": 3, "first_fail_at": "2026-05-02T01:22:00+00:00"}
        (tmp_path / "ibkr_probe_fail.json").write_text(json.dumps(payload))
        state = hm.get_ibkr_probe_state()
        assert state["consecutive_fails"] == 3
        assert "2026-05-02" in state["first_fail_at"]

    def test_get_returns_defaults_on_corrupt_json(self, tmp_path):
        (tmp_path / "ibkr_probe_fail.json").write_text("{not valid json!!!}")
        state = hm.get_ibkr_probe_state()
        assert state == {"consecutive_fails": 0, "first_fail_at": None}

    def test_get_returns_defaults_on_empty_file(self, tmp_path):
        (tmp_path / "ibkr_probe_fail.json").write_text("")
        state = hm.get_ibkr_probe_state()
        assert state == {"consecutive_fails": 0, "first_fail_at": None}

    # update_ibkr_probe_state (connected=True) ---

    def test_update_connected_resets_fail_count(self, tmp_path):
        _write_probe_state(tmp_path, consecutive_fails=5)
        state = hm.update_ibkr_probe_state(connected=True)
        assert state["consecutive_fails"] == 0
        assert state["first_fail_at"] is None

    def test_update_connected_writes_reset_to_file(self, tmp_path):
        _write_probe_state(tmp_path, consecutive_fails=4)
        hm.update_ibkr_probe_state(connected=True)
        written = json.loads((tmp_path / "ibkr_probe_fail.json").read_text())
        assert written["consecutive_fails"] == 0

    # update_ibkr_probe_state (connected=False) ---

    def test_update_disconnected_increments_from_zero(self, tmp_path):
        state = hm.update_ibkr_probe_state(connected=False)
        assert state["consecutive_fails"] == 1
        assert state["first_fail_at"] is not None

    def test_update_disconnected_increments_from_existing(self, tmp_path):
        _write_probe_state(tmp_path, consecutive_fails=2)
        state = hm.update_ibkr_probe_state(connected=False)
        assert state["consecutive_fails"] == 3

    def test_update_disconnected_preserves_first_fail_at(self, tmp_path):
        first_fail = "2026-05-02T01:00:00+00:00"
        _write_probe_state(tmp_path, consecutive_fails=1, first_fail_at=first_fail)
        state = hm.update_ibkr_probe_state(connected=False)
        assert state["first_fail_at"] == first_fail

    def test_update_disconnected_sets_first_fail_at_on_first_failure(self, tmp_path):
        state = hm.update_ibkr_probe_state(connected=False)
        assert state["first_fail_at"] is not None
        # Should be a recent ISO timestamp
        dt = datetime.fromisoformat(state["first_fail_at"])
        age_s = (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds()
        assert age_s < 5  # set within last 5 seconds

    def test_update_disconnected_writes_to_file(self, tmp_path):
        hm.update_ibkr_probe_state(connected=False)
        written = json.loads((tmp_path / "ibkr_probe_fail.json").read_text())
        assert written["consecutive_fails"] == 1


# ── TestIbkrPortAlertInMain ───────────────────────────────────────────────────

class TestIbkrPortAlertInMain:
    """Test the IBKR port probe alert path wired into main()."""

    def _run_main(self, tmp_path, monkeypatch, port_open: bool) -> list[str]:
        """Run main() with mocked probe and collect Discord messages."""
        _make_pipeline_beat(tmp_path)
        discord_calls: list[str] = []
        monkeypatch.setattr(hm, "post_discord", lambda msg: discord_calls.append(msg))
        mock_s = _make_socket_mock(connect_ex_result=0 if port_open else 111)
        with patch("socket.socket", return_value=mock_s):
            hm.main()
        return discord_calls

    def test_no_alert_on_first_fail(self, tmp_path, monkeypatch):
        """consecutive_fails=1 is below threshold=2; no Discord message."""
        calls = self._run_main(tmp_path, monkeypatch, port_open=False)
        assert not any("REFUSED" in c for c in calls)

    def test_alert_fires_after_2_consecutive_fails(self, tmp_path, monkeypatch):
        """After 2 consecutive failures, a 🔴 Discord alert must fire."""
        _write_probe_state(tmp_path, consecutive_fails=1)  # 1 prior fail → 2nd triggers
        calls = self._run_main(tmp_path, monkeypatch, port_open=False)
        assert any("REFUSED" in c for c in calls), f"No REFUSED alert in: {calls}"

    def test_alert_message_contains_restart_command(self, tmp_path, monkeypatch):
        _write_probe_state(tmp_path, consecutive_fails=1)
        calls = self._run_main(tmp_path, monkeypatch, port_open=False)
        assert any("restart ibgateway" in c for c in calls)

    def test_no_alert_when_port_is_ok(self, tmp_path, monkeypatch):
        calls = self._run_main(tmp_path, monkeypatch, port_open=True)
        assert not any("REFUSED" in c for c in calls)

    def test_cooldown_suppresses_repeat_alert(self, tmp_path, monkeypatch):
        """An alert was sent 5 min ago (within 30-min cooldown) → no new alert."""
        recent_ts = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        (tmp_path / "alert_cooldown.json").write_text(json.dumps({"ibkr_port": recent_ts}))
        _write_probe_state(tmp_path, consecutive_fails=5)
        calls = self._run_main(tmp_path, monkeypatch, port_open=False)
        assert not any("REFUSED" in c for c in calls)

    def test_cooldown_expired_allows_repeat_alert(self, tmp_path, monkeypatch):
        """Previous alert was 35 min ago; cooldown (30 min) expired → alert fires."""
        old_ts = (datetime.now(timezone.utc) - timedelta(minutes=35)).isoformat()
        (tmp_path / "alert_cooldown.json").write_text(json.dumps({"ibkr_port": old_ts}))
        _write_probe_state(tmp_path, consecutive_fails=5)
        calls = self._run_main(tmp_path, monkeypatch, port_open=False)
        assert any("REFUSED" in c for c in calls)

    def test_state_json_includes_ibkr_port(self, tmp_path, monkeypatch):
        """main() must write ibkr_port to the dashboard state JSON."""
        self._run_main(tmp_path, monkeypatch, port_open=True)
        state = json.loads((tmp_path / "quantai-heartbeats.json").read_text())
        assert "ibkr_port" in state["data"]
        ibkr = state["data"]["ibkr_port"]
        assert "status" in ibkr
        assert "consecutive_fails" in ibkr
        assert "first_fail_at" in ibkr

    def test_state_ibkr_port_status_ok_when_connected(self, tmp_path, monkeypatch):
        self._run_main(tmp_path, monkeypatch, port_open=True)
        state = json.loads((tmp_path / "quantai-heartbeats.json").read_text())
        assert state["data"]["ibkr_port"]["status"] == "ok"

    def test_state_ibkr_port_status_refused_when_down(self, tmp_path, monkeypatch):
        self._run_main(tmp_path, monkeypatch, port_open=False)
        state = json.loads((tmp_path / "quantai-heartbeats.json").read_text())
        assert state["data"]["ibkr_port"]["status"] == "refused"

    def test_state_consecutive_fails_reflects_probe_count(self, tmp_path, monkeypatch):
        """consecutive_fails in state JSON matches the running count."""
        _write_probe_state(tmp_path, consecutive_fails=3)
        self._run_main(tmp_path, monkeypatch, port_open=False)
        state = json.loads((tmp_path / "quantai-heartbeats.json").read_text())
        assert state["data"]["ibkr_port"]["consecutive_fails"] == 4  # 3 prior + 1 this run
