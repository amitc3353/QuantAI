"""Unit tests: Sentinel built-in safe_auto actions (pytest + graphify).

Tests cover:
  1. pytest blocked during market hours / trading window / cooldown
  2. pytest runs when stale + off-hours
  3. pytest timeout handled gracefully
  4. graphify blocked during market hours / cooldown
  5. graphify runs when stale + off-hours
  6. graphify timeout handled gracefully
  7. Context enrichment: top_criticals in errors.db summary
  8. flush_expired_pending removes old proposals
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

SCRIPTS = str(Path(__file__).resolve().parents[2] / "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import sentinel_agent as SA


class TestPytestSafeAuto:
    """Verify run_pytest_if_stale() respects all safety guards."""

    def test_pytest_blocked_during_market_hours(self):
        """Market hours -> skipped."""
        state = {}
        with patch.object(SA, "is_market_hours", return_value=True), \
             patch.object(SA, "is_trading_window", return_value=False):
            result = SA.run_pytest_if_stale(dry_run=False, state=state)
        assert result["ran"] is False
        assert "market hours" in result["reason"]

    def test_pytest_blocked_during_trading_window(self):
        """Trading window -> skipped."""
        state = {}
        with patch.object(SA, "is_market_hours", return_value=False), \
             patch.object(SA, "is_trading_window", return_value=True):
            result = SA.run_pytest_if_stale(dry_run=False, state=state)
        assert result["ran"] is False
        assert "market hours" in result["reason"] or "trading window" in result["reason"]

    def test_pytest_blocked_during_cooldown(self, tmp_path):
        """Last run 12h ago, cooldown=24h -> skipped."""
        state = {
            "last_pytest_run": (SA.now_utc() - timedelta(hours=12)).isoformat(),
        }
        with patch.object(SA, "is_market_hours", return_value=False), \
             patch.object(SA, "is_trading_window", return_value=False):
            result = SA.run_pytest_if_stale(dry_run=False, state=state)
        assert result["ran"] is False
        assert "cooldown" in result["reason"]

    def test_pytest_skipped_when_fresh(self, tmp_path):
        """Results 3 days old, threshold 7 -> skipped."""
        test_results = tmp_path / "test-results.json"
        # Touch file with recent mtime
        test_results.write_text('{"last_run": "recent"}')

        state = {}
        with patch.object(SA, "is_market_hours", return_value=False), \
             patch.object(SA, "is_trading_window", return_value=False), \
             patch.object(SA, "TEST_RESULTS_PATH", test_results):
            result = SA.run_pytest_if_stale(dry_run=False, state=state)
        assert result["ran"] is False
        assert "fresh" in result["reason"]

    def test_pytest_runs_when_stale_and_offhours(self, tmp_path):
        """Stale + off-hours + no cooldown -> ran=True."""
        test_results = tmp_path / "test-results.json"
        # Create file with old mtime (10 days ago)
        test_results.write_text('{"last_run": "old"}')
        old_time = time.time() - (10 * 24 * 3600)
        os.utime(test_results, (old_time, old_time))

        dashboard_dir = tmp_path / "dashboard"
        dashboard_dir.mkdir()

        state = {}
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "100 passed in 5.0s\n"

        with patch.object(SA, "is_market_hours", return_value=False), \
             patch.object(SA, "is_trading_window", return_value=False), \
             patch.object(SA, "TEST_RESULTS_PATH", tmp_path / "results.json"), \
             patch.object(SA, "DASHBOARD_STATE_DIR", dashboard_dir), \
             patch("subprocess.run", return_value=mock_proc):
            result = SA.run_pytest_if_stale(dry_run=False, state=state)

        assert result["ran"] is True
        assert result["returncode"] == 0
        assert "last_pytest_run" in state

    def test_pytest_dry_run_no_execution(self, tmp_path):
        """Dry run should not execute pytest."""
        test_results = tmp_path / "test-results.json"
        test_results.write_text('{"last_run": "old"}')
        old_time = time.time() - (10 * 24 * 3600)
        os.utime(test_results, (old_time, old_time))

        state = {}
        with patch.object(SA, "is_market_hours", return_value=False), \
             patch.object(SA, "is_trading_window", return_value=False), \
             patch.object(SA, "TEST_RESULTS_PATH", test_results), \
             patch("subprocess.run") as mock_run:
            result = SA.run_pytest_if_stale(dry_run=True, state=state)

        assert result["ran"] is False
        assert "dry-run" in result["reason"]
        mock_run.assert_not_called()

    def test_pytest_timeout_handled(self, tmp_path):
        """subprocess.TimeoutExpired -> graceful failure."""
        test_results = tmp_path / "test-results.json"
        test_results.write_text('{"last_run": "old"}')
        old_time = time.time() - (10 * 24 * 3600)
        os.utime(test_results, (old_time, old_time))

        state = {}
        with patch.object(SA, "is_market_hours", return_value=False), \
             patch.object(SA, "is_trading_window", return_value=False), \
             patch.object(SA, "TEST_RESULTS_PATH", test_results), \
             patch("subprocess.run", side_effect=subprocess.TimeoutExpired("pytest", 300)):
            result = SA.run_pytest_if_stale(dry_run=False, state=state)

        assert result["ran"] is False
        assert "timed out" in result["reason"]


class TestGraphifySafeAuto:
    """Verify run_graphify_if_stale() respects all safety guards."""

    def test_graphify_blocked_during_market_hours(self):
        """Market hours -> skipped."""
        state = {}
        with patch.object(SA, "is_market_hours", return_value=True), \
             patch.object(SA, "is_trading_window", return_value=False):
            result = SA.run_graphify_if_stale(dry_run=False, state=state)
        assert result["ran"] is False
        assert "market hours" in result["reason"]

    def test_graphify_blocked_during_cooldown(self):
        """Last run 12h ago, cooldown=24h -> skipped."""
        state = {
            "last_graphify_run": (SA.now_utc() - timedelta(hours=12)).isoformat(),
        }
        with patch.object(SA, "is_market_hours", return_value=False), \
             patch.object(SA, "is_trading_window", return_value=False):
            result = SA.run_graphify_if_stale(dry_run=False, state=state)
        assert result["ran"] is False
        assert "cooldown" in result["reason"]

    def test_graphify_skipped_when_fresh(self, tmp_path):
        """graph.json 2 days old -> skipped."""
        graph_path = tmp_path / "graph.json"
        graph_path.write_text('{}')
        # Recent mtime — should be fresh

        state = {}
        with patch.object(SA, "is_market_hours", return_value=False), \
             patch.object(SA, "is_trading_window", return_value=False), \
             patch.object(SA, "GRAPHIFY_GRAPH_PATH", graph_path):
            result = SA.run_graphify_if_stale(dry_run=False, state=state)
        assert result["ran"] is False
        assert "fresh" in result["reason"]

    def test_graphify_runs_when_stale_and_offhours(self, tmp_path):
        """graph.json 6 days old + off-hours -> ran=True."""
        graph_path = tmp_path / "graph.json"
        graph_path.write_text('{}')
        old_time = time.time() - (6 * 24 * 3600)
        os.utime(graph_path, (old_time, old_time))

        state = {}
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "rebuilt graph\n"

        with patch.object(SA, "is_market_hours", return_value=False), \
             patch.object(SA, "is_trading_window", return_value=False), \
             patch.object(SA, "GRAPHIFY_GRAPH_PATH", graph_path), \
             patch("subprocess.run", return_value=mock_proc):
            result = SA.run_graphify_if_stale(dry_run=False, state=state)

        assert result["ran"] is True
        assert result["returncode"] == 0
        assert "last_graphify_run" in state

    def test_graphify_missing_graph_skipped(self, tmp_path):
        """graph.json does not exist -> skipped."""
        state = {}
        with patch.object(SA, "is_market_hours", return_value=False), \
             patch.object(SA, "is_trading_window", return_value=False), \
             patch.object(SA, "GRAPHIFY_GRAPH_PATH", tmp_path / "nope.json"):
            result = SA.run_graphify_if_stale(dry_run=False, state=state)
        assert result["ran"] is False
        assert "does not exist" in result["reason"]

    def test_graphify_timeout_handled(self, tmp_path):
        """subprocess.TimeoutExpired -> graceful failure."""
        graph_path = tmp_path / "graph.json"
        graph_path.write_text('{}')
        old_time = time.time() - (6 * 24 * 3600)
        os.utime(graph_path, (old_time, old_time))

        state = {}
        with patch.object(SA, "is_market_hours", return_value=False), \
             patch.object(SA, "is_trading_window", return_value=False), \
             patch.object(SA, "GRAPHIFY_GRAPH_PATH", graph_path), \
             patch("subprocess.run", side_effect=subprocess.TimeoutExpired("graphify", 120)):
            result = SA.run_graphify_if_stale(dry_run=False, state=state)

        assert result["ran"] is False
        assert "timed out" in result["reason"]

    def test_graphify_command_not_found_handled(self, tmp_path):
        """FileNotFoundError -> graceful failure."""
        graph_path = tmp_path / "graph.json"
        graph_path.write_text('{}')
        old_time = time.time() - (6 * 24 * 3600)
        os.utime(graph_path, (old_time, old_time))

        state = {}
        with patch.object(SA, "is_market_hours", return_value=False), \
             patch.object(SA, "is_trading_window", return_value=False), \
             patch.object(SA, "GRAPHIFY_GRAPH_PATH", graph_path), \
             patch("subprocess.run", side_effect=FileNotFoundError("graphify")):
            result = SA.run_graphify_if_stale(dry_run=False, state=state)

        assert result["ran"] is False
        assert "not found" in result["reason"]


class TestContextEnrichment:
    """Verify errors.db summary includes top_criticals."""

    def test_top_criticals_included_in_summary(self, tmp_path):
        """Query should return top_criticals key with data."""
        import sqlite3
        db_path = tmp_path / "errors.db"
        con = sqlite3.connect(db_path)
        con.execute("""
            CREATE TABLE events (
                id INTEGER PRIMARY KEY,
                severity TEXT,
                count INTEGER DEFAULT 1,
                signature TEXT,
                message TEXT,
                resolved_at TEXT,
                resolved_by TEXT
            )
        """)
        con.execute(
            "INSERT INTO events (severity, count, signature, message) "
            "VALUES ('critical', 5, 'credit-balance-low', 'Your credit balance is too low')"
        )
        con.execute(
            "INSERT INTO events (severity, count, signature, message) "
            "VALUES ('info', 100, 'ufw-block', '[UFW BLOCK] noise')"
        )
        con.commit()
        con.close()

        with patch.object(SA, "ERRORS_DB", db_path):
            result = SA.query_errors_db_summary()

        assert "top_criticals" in result
        assert len(result["top_criticals"]) == 1
        assert result["top_criticals"][0]["severity"] == "critical"
        assert result["top_criticals"][0]["count"] == 5

    def test_top_criticals_empty_when_none_exist(self, tmp_path):
        """No critical rows -> empty list."""
        import sqlite3
        db_path = tmp_path / "errors.db"
        con = sqlite3.connect(db_path)
        con.execute("""
            CREATE TABLE events (
                id INTEGER PRIMARY KEY,
                severity TEXT,
                count INTEGER DEFAULT 1,
                signature TEXT,
                message TEXT,
                resolved_at TEXT,
                resolved_by TEXT
            )
        """)
        con.execute(
            "INSERT INTO events (severity, count, signature, message) "
            "VALUES ('info', 50, 'ufw-block', 'noise')"
        )
        con.commit()
        con.close()

        with patch.object(SA, "ERRORS_DB", db_path):
            result = SA.query_errors_db_summary()

        assert "top_criticals" in result
        assert len(result["top_criticals"]) == 0


class TestFlushExpiredPending:
    """Verify flush_expired_pending removes old proposals."""

    def test_expired_proposals_removed(self, tmp_path):
        """Proposals past expires_at should be removed."""
        pending = tmp_path / "pending"
        pending.mkdir()
        expired_rec = {
            "fix_id": "old123",
            "expires_at": (SA.now_utc() - timedelta(hours=1)).isoformat(),
        }
        (pending / "old123.json").write_text(json.dumps(expired_rec))

        with patch.object(SA, "PENDING_DIR", pending):
            removed = SA.flush_expired_pending(dry_run=False)

        assert removed == 1
        assert not (pending / "old123.json").exists()

    def test_fresh_proposals_kept(self, tmp_path):
        """Proposals not yet expired should be kept."""
        pending = tmp_path / "pending"
        pending.mkdir()
        fresh_rec = {
            "fix_id": "new123",
            "expires_at": (SA.now_utc() + timedelta(hours=24)).isoformat(),
        }
        (pending / "new123.json").write_text(json.dumps(fresh_rec))

        with patch.object(SA, "PENDING_DIR", pending):
            removed = SA.flush_expired_pending(dry_run=False)

        assert removed == 0
        assert (pending / "new123.json").exists()

    def test_dry_run_no_deletion(self, tmp_path):
        """Dry run should count but not delete."""
        pending = tmp_path / "pending"
        pending.mkdir()
        expired_rec = {
            "fix_id": "old123",
            "expires_at": (SA.now_utc() - timedelta(hours=1)).isoformat(),
        }
        (pending / "old123.json").write_text(json.dumps(expired_rec))

        with patch.object(SA, "PENDING_DIR", pending):
            removed = SA.flush_expired_pending(dry_run=True)

        assert removed == 1
        # File should still exist in dry-run
        assert (pending / "old123.json").exists()
