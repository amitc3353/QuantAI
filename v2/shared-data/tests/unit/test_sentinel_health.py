"""Unit tests: Sentinel dashboard truth + liveness monitoring.

Tests cover:
  1. write_dashboard() writes correct status based on LLM health
  2. system_monitor.check_sentinel_liveness() reads tile correctly
  3. Sentinel is no longer in the COLLECTOR_SKIP_FILES set
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

SCRIPTS = str(Path(__file__).resolve().parents[2] / "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)


class TestSentinelDashboardStatus:
    """Verify write_dashboard() reflects LLM health in the status field."""

    def test_dashboard_writes_error_on_llm_failure(self, tmp_path):
        """When llm_failed=True, dashboard status should be 'error'."""
        tile_path = tmp_path / "quantai-sentinel.json"
        pending_dir = tmp_path / "pending"
        pending_dir.mkdir()

        with patch("sentinel_agent.SENTINEL_TILE_PATH", tile_path), \
             patch("sentinel_agent.DASHBOARD_STATE_DIR", tmp_path), \
             patch("sentinel_agent.PENDING_DIR", pending_dir), \
             patch("sentinel_agent.next_scheduled_et", return_value="Thu 12:30 ET"):
            from sentinel_agent import write_dashboard

            state = {"quarantined": [], "last_run": {}}
            write_dashboard(state, "LLM call failed after retries",
                          "observe", 0, dry_run=False, llm_failed=True)

        data = json.loads(tile_path.read_text())
        assert data["status"] == "error"
        assert data["data"]["llm_healthy"] is False

    def test_dashboard_writes_ok_on_success(self, tmp_path):
        """When llm_failed=False and no quarantine, status should be 'ok'."""
        tile_path = tmp_path / "quantai-sentinel.json"
        pending_dir = tmp_path / "pending"
        pending_dir.mkdir()

        with patch("sentinel_agent.SENTINEL_TILE_PATH", tile_path), \
             patch("sentinel_agent.DASHBOARD_STATE_DIR", tmp_path), \
             patch("sentinel_agent.PENDING_DIR", pending_dir), \
             patch("sentinel_agent.next_scheduled_et", return_value="Thu 12:30 ET"):
            from sentinel_agent import write_dashboard

            state = {"quarantined": [], "last_run": {}}
            write_dashboard(state, "2 findings observed",
                          "observe", 2, dry_run=False, llm_failed=False)

        data = json.loads(tile_path.read_text())
        assert data["status"] == "ok"
        assert data["data"]["llm_healthy"] is True

    def test_dashboard_writes_warning_on_quarantine(self, tmp_path):
        """When quarantine present but LLM healthy, status should be 'warning'."""
        tile_path = tmp_path / "quantai-sentinel.json"
        pending_dir = tmp_path / "pending"
        pending_dir.mkdir()

        with patch("sentinel_agent.SENTINEL_TILE_PATH", tile_path), \
             patch("sentinel_agent.DASHBOARD_STATE_DIR", tmp_path), \
             patch("sentinel_agent.PENDING_DIR", pending_dir), \
             patch("sentinel_agent.next_scheduled_et", return_value="Thu 12:30 ET"):
            from sentinel_agent import write_dashboard

            state = {"quarantined": ["fix_001"], "last_run": {}}
            write_dashboard(state, "1 fix quarantined",
                          "apply", 0, dry_run=False, llm_failed=False)

        data = json.loads(tile_path.read_text())
        assert data["status"] == "warning"
        assert data["data"]["llm_healthy"] is True

    def test_llm_failure_overrides_quarantine_warning(self, tmp_path):
        """LLM failure (error) should override quarantine (warning)."""
        tile_path = tmp_path / "quantai-sentinel.json"
        pending_dir = tmp_path / "pending"
        pending_dir.mkdir()

        with patch("sentinel_agent.SENTINEL_TILE_PATH", tile_path), \
             patch("sentinel_agent.DASHBOARD_STATE_DIR", tmp_path), \
             patch("sentinel_agent.PENDING_DIR", pending_dir), \
             patch("sentinel_agent.next_scheduled_et", return_value="Thu 12:30 ET"):
            from sentinel_agent import write_dashboard

            state = {"quarantined": ["fix_001"], "last_run": {}}
            write_dashboard(state, "LLM 429 error",
                          "apply", 0, dry_run=False, llm_failed=True)

        data = json.loads(tile_path.read_text())
        assert data["status"] == "error"


class TestSentinelLivenessCheck:
    """Verify system_monitor.check_sentinel_liveness() reads the tile correctly."""

    def test_liveness_detects_error_status(self, tmp_path):
        """Tile with status='error' should return error."""
        tile = tmp_path / "quantai-sentinel.json"
        tile.write_text(json.dumps({
            "status": "error",
            "data": {"llm_healthy": False},
        }))

        with patch("system_monitor.DASHBOARD_STATE_DIR", tmp_path):
            from system_monitor import check_sentinel_liveness
            result = check_sentinel_liveness()

        assert result["status"] == "error"

    def test_liveness_detects_llm_unhealthy(self, tmp_path):
        """Tile with llm_healthy=False should return error even if status=ok."""
        tile = tmp_path / "quantai-sentinel.json"
        tile.write_text(json.dumps({
            "status": "ok",
            "data": {"llm_healthy": False},
        }))

        with patch("system_monitor.DASHBOARD_STATE_DIR", tmp_path):
            from system_monitor import check_sentinel_liveness
            result = check_sentinel_liveness()

        assert result["status"] == "error"

    def test_liveness_ok_when_healthy(self, tmp_path):
        """Tile with status=ok and llm_healthy=True should return ok."""
        tile = tmp_path / "quantai-sentinel.json"
        tile.write_text(json.dumps({
            "status": "ok",
            "data": {"llm_healthy": True},
        }))

        with patch("system_monitor.DASHBOARD_STATE_DIR", tmp_path):
            from system_monitor import check_sentinel_liveness
            result = check_sentinel_liveness()

        assert result["status"] == "ok"


class TestSentinelNotSkipped:
    """Verify Sentinel is no longer in COLLECTOR_SKIP_FILES."""

    def test_sentinel_not_in_skip_list(self):
        from system_monitor import COLLECTOR_SKIP_FILES
        assert "quantai-sentinel.json" not in COLLECTOR_SKIP_FILES

    def test_sentinel_in_market_hours_only(self):
        from system_monitor import COLLECTOR_MARKET_HOURS_ONLY
        assert "quantai-sentinel.json" in COLLECTOR_MARKET_HOURS_ONLY
