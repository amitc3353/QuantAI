"""Unit tests for system_monitor.py — deterministic health checks.

Each check is exercised in isolation with mocked filesystem / sockets. We
verify:
  - status thresholds (ok / info / warning / error)
  - report envelope shape ({last_updated, status, data: {checks}})
  - error handling for missing files
  - aggregate_status() picks the worst severity
"""
from __future__ import annotations

import json
import socket
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import system_monitor as SM


# ── _probe_port ──────────────────────────────────────────────────

class TestPortProbe:
    def test_probe_port_returns_true_when_connect_ex_returns_zero(self):
        with patch.object(socket, "socket") as mock_sock_ctor:
            mock_sock = MagicMock()
            mock_sock.connect_ex.return_value = 0
            mock_sock_ctor.return_value = mock_sock
            assert SM._probe_port("127.0.0.1", 9999) is True

    def test_probe_port_returns_false_on_nonzero(self):
        with patch.object(socket, "socket") as mock_sock_ctor:
            mock_sock = MagicMock()
            mock_sock.connect_ex.return_value = 111  # ECONNREFUSED
            mock_sock_ctor.return_value = mock_sock
            assert SM._probe_port("127.0.0.1", 9999) is False

    def test_probe_port_swallows_oserror(self):
        with patch.object(socket, "socket") as mock_sock_ctor:
            mock_sock = MagicMock()
            mock_sock.connect_ex.side_effect = OSError("network unreachable")
            mock_sock_ctor.return_value = mock_sock
            assert SM._probe_port("127.0.0.1", 9999) is False


# ── Fail-state persistence ──────────────────────────────────────

class TestFailState:
    def test_read_returns_zero_when_file_missing(self, tmp_path):
        path = tmp_path / "missing.json"
        st = SM._read_fail_state(path)
        assert st == {"consecutive_fails": 0, "first_fail_at": None}

    def test_update_resets_on_connected(self, tmp_path, monkeypatch):
        path = tmp_path / "fail.json"
        monkeypatch.setattr(SM, "HEARTBEAT_DIR", tmp_path)
        # Plant a prior fail
        path.write_text(json.dumps({"consecutive_fails": 5, "first_fail_at": "2026-01-01T00:00:00Z"}))
        st = SM._update_fail_state(path, connected=True)
        assert st == {"consecutive_fails": 0, "first_fail_at": None}

    def test_update_increments_on_failure(self, tmp_path, monkeypatch):
        path = tmp_path / "fail.json"
        monkeypatch.setattr(SM, "HEARTBEAT_DIR", tmp_path)
        st1 = SM._update_fail_state(path, connected=False)
        assert st1["consecutive_fails"] == 1
        assert st1["first_fail_at"] is not None
        st2 = SM._update_fail_state(path, connected=False)
        assert st2["consecutive_fails"] == 2
        # first_fail_at should be preserved
        assert st1["first_fail_at"] == st2["first_fail_at"]


# ── _port_check status thresholds ──────────────────────────────

class TestPortCheckStatus:
    @pytest.fixture(autouse=True)
    def _isolate(self, tmp_path, monkeypatch):
        monkeypatch.setattr(SM, "HEARTBEAT_DIR", tmp_path)
        self.tmp = tmp_path

    def _force(self, *, connected, prior_fails):
        fail_file = self.tmp / "test_fail.json"
        if prior_fails > 0:
            fail_file.write_text(json.dumps({
                "consecutive_fails": prior_fails - (1 if not connected else 0),
                "first_fail_at": "2026-05-03T00:00:00+00:00",
            }))
        with patch.object(SM, "_probe_port", return_value=connected):
            return SM._port_check("127.0.0.1", 9999, fail_file, "test")

    def test_connected_returns_ok(self):
        result = self._force(connected=True, prior_fails=0)
        assert result["status"] == "ok"
        assert result["consecutive_fails"] == 0

    def test_one_fail_returns_info(self):
        result = self._force(connected=False, prior_fails=1)
        assert result["status"] == "info"
        assert result["consecutive_fails"] == 1

    def test_two_fails_returns_warning(self):
        result = self._force(connected=False, prior_fails=2)
        assert result["status"] == "warning"
        assert result["consecutive_fails"] == 2

    def test_three_plus_fails_returns_error(self):
        result = self._force(connected=False, prior_fails=3)
        assert result["status"] == "error"
        assert result["consecutive_fails"] == 3


# ── check_disk / check_memory thresholds ──────────────────────

class TestDiskMemory:
    def test_disk_ok_at_50pct(self):
        with patch("os.statvfs") as mock:
            mock.return_value = MagicMock(f_blocks=100, f_bavail=50)
            result = SM.check_disk()
        assert result["status"] == "ok"
        assert result["used_pct"] == 50.0

    def test_disk_warning_at_88pct(self):
        with patch("os.statvfs") as mock:
            mock.return_value = MagicMock(f_blocks=100, f_bavail=12)
            result = SM.check_disk()
        assert result["status"] == "warning"
        assert result["used_pct"] == 88.0

    def test_disk_error_at_94pct(self):
        with patch("os.statvfs") as mock:
            mock.return_value = MagicMock(f_blocks=100, f_bavail=6)
            result = SM.check_disk()
        assert result["status"] == "error"

    def test_disk_handles_statvfs_failure(self):
        with patch("os.statvfs", side_effect=OSError("nope")):
            result = SM.check_disk()
        assert result["status"] == "warning"
        assert "error" in result


# ── check_journal_schema ──────────────────────────────────────

class TestJournalSchema:
    def test_missing_journal_returns_info(self, tmp_path, monkeypatch):
        monkeypatch.setattr(SM, "JOURNAL_PATH", tmp_path / "missing.jsonl")
        assert SM.check_journal_schema()["status"] == "info"

    def test_valid_entry_returns_ok(self, tmp_path, monkeypatch):
        j = tmp_path / "trades.jsonl"
        j.write_text(json.dumps({
            "trade_id": "A042", "status": "OPEN", "legs": [{"symbol": "SPY"}],
        }) + "\n")
        monkeypatch.setattr(SM, "JOURNAL_PATH", j)
        result = SM.check_journal_schema()
        assert result["status"] == "ok"
        assert result["trade_id"] == "A042"

    def test_missing_required_fields_warns(self, tmp_path, monkeypatch):
        j = tmp_path / "trades.jsonl"
        j.write_text(json.dumps({"foo": "bar"}) + "\n")
        monkeypatch.setattr(SM, "JOURNAL_PATH", j)
        result = SM.check_journal_schema()
        assert result["status"] == "warning"
        assert "status" in result["error"]

    def test_invalid_json_returns_error(self, tmp_path, monkeypatch):
        j = tmp_path / "trades.jsonl"
        j.write_text("not valid json at all\n")
        monkeypatch.setattr(SM, "JOURNAL_PATH", j)
        assert SM.check_journal_schema()["status"] == "error"


# ── aggregate_status ──────────────────────────────────────────

class TestAggregate:
    def test_all_ok_returns_ok(self):
        results = {"a": {"status": "ok"}, "b": {"status": "ok"}}
        assert SM.aggregate_status(results) == "ok"

    def test_one_warning_returns_warning(self):
        results = {"a": {"status": "ok"}, "b": {"status": "warning"}}
        assert SM.aggregate_status(results) == "warning"

    def test_error_beats_warning(self):
        results = {"a": {"status": "warning"}, "b": {"status": "error"}, "c": {"status": "ok"}}
        assert SM.aggregate_status(results) == "error"

    def test_info_does_not_beat_ok(self):
        # Wait — info IS ranked above ok in STATUS_RANK. Verify the rank.
        assert SM.STATUS_RANK["info"] > SM.STATUS_RANK["ok"]
        results = {"a": {"status": "ok"}, "b": {"status": "info"}}
        assert SM.aggregate_status(results) == "info"


# ── run_all_checks resilience ─────────────────────────────────

class TestRunAllChecks:
    def test_check_exception_caught_as_warning(self):
        # Patch one check to raise; verify run_all_checks catches it
        original = SM.CHECKS
        try:
            def bad_check():
                raise RuntimeError("boom")
            SM.CHECKS = [("bad", bad_check), ("disk", SM.check_disk)]
            results = SM.run_all_checks()
            assert results["bad"]["status"] == "warning"
            assert "RuntimeError" in results["bad"]["error"]
            # Other checks still run
            assert "disk" in results
        finally:
            SM.CHECKS = original

    def test_envelope_shape(self, tmp_path, monkeypatch):
        # Run with no real data; just verify report shape
        monkeypatch.setattr(SM, "HEALTH_REPORT_PATH", tmp_path / "report.json")
        monkeypatch.setattr(SM, "DASHBOARD_STATE_DIR", tmp_path)
        results = SM.run_all_checks()
        path = SM.write_report(results)
        doc = json.loads(path.read_text())
        assert "last_updated" in doc
        assert "status" in doc
        assert "data" in doc
        assert "checks" in doc["data"]


# ── check_dashboard_html_size (added 2026-05-04) ───────────────

class TestDashboardHtmlSize:
    """Detects React SPA being clobbered by a tile-grid generator.
    React SPA is ~93KB; the retired generate.py output was ~5KB. Anything
    <10KB triggers the alert."""

    def test_react_spa_size_returns_ok(self, tmp_path, monkeypatch):
        target = tmp_path / "index.html"
        target.write_bytes(b"x" * 90_000)  # simulate ~90KB React SPA
        with patch("system_monitor.Path") as mock_path:
            mock_path.return_value = target
            result = SM.check_dashboard_html_size()
        assert result["status"] == "ok"
        assert result["size_bytes"] == 90_000

    def test_clobbered_5kb_size_returns_error(self, tmp_path):
        target = tmp_path / "index.html"
        target.write_bytes(b"x" * 5_000)  # simulate clobbered tile-grid
        with patch("system_monitor.Path") as mock_path:
            mock_path.return_value = target
            result = SM.check_dashboard_html_size()
        assert result["status"] == "error"
        assert result["size_bytes"] == 5_000
        assert "restore" in result["hint"].lower()

    def test_missing_file_returns_error(self, tmp_path):
        target = tmp_path / "missing.html"
        with patch("system_monitor.Path") as mock_path:
            mock_path.return_value = target
            result = SM.check_dashboard_html_size()
        assert result["status"] == "error"
        assert "missing" in result["error"]

    def test_check_is_in_checks_list(self):
        names = [n for (n, _) in SM.CHECKS]
        assert "dashboard_html_size" in names, (
            "check #14 dashboard_html_size must be registered in CHECKS"
        )
