"""Integration test: full Sentinel apply cycle with mocked LLM.

Plants a synthetic system-health-report.json + errors.db with known patterns,
runs sentinel_agent.run_apply() with a mocked Client, verifies:
  - reclassify_catalog_noise() actually mutates the DB
  - LLM-proposed safe_auto fix gets validated and queued
  - Discord posts go through (mocked)
  - Dashboard tile is written with correct shape
  - --dry-run produces no DB or filesystem mutations
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import sentinel_agent as SA


def _create_errors_db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
            source TEXT NOT NULL, severity TEXT NOT NULL,
            message TEXT NOT NULL, signature TEXT NOT NULL,
            signature_hash TEXT NOT NULL, count INTEGER NOT NULL DEFAULT 1,
            catalog_id TEXT, runbook TEXT,
            resolved_at TEXT, resolved_by TEXT
        );
    """)
    con.commit()
    con.close()


def _seed_health_report(path: Path, *, status="ok", checks=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "last_updated": "2026-05-03T18:00:00Z",
        "status": status,
        "data": {"checks": checks or {}},
    }
    path.write_text(json.dumps(payload))


def _seed_positions(path: Path, count=0):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "last_updated": "2026-05-03T18:00:00Z",
        "status": "ok",
        "data": {"count": count, "positions": []},
    }
    path.write_text(json.dumps(payload))


@pytest.fixture
def sentinel_env(tmp_path, monkeypatch):
    """Re-route all Sentinel paths under tmp_path."""
    data_dir = tmp_path / "auto_heal_data"
    state_dir = tmp_path / "dashboard_state"
    db_path = state_dir / "errors.db"
    health_path = state_dir / "system-health-report.json"
    sentinel_tile = state_dir / "quantai-sentinel.json"
    positions = state_dir / "quantai-positions.json"
    log_path = tmp_path / "sentinel.log"

    monkeypatch.setattr(SA, "DATA_DIR", data_dir)
    monkeypatch.setattr(SA, "PENDING_DIR", data_dir / "pending_fixes")
    monkeypatch.setattr(SA, "APPLIED_DIR", data_dir / "applied")
    monkeypatch.setattr(SA, "DIGEST_DIR", data_dir / "digest_buffer")
    monkeypatch.setattr(SA, "FIX_HISTORY_DIR", data_dir / "fix_history")
    monkeypatch.setattr(SA, "STATE_FILE", data_dir / "state.json")
    monkeypatch.setattr(SA, "LOCK_FILE", tmp_path / "sentinel.lock")
    monkeypatch.setattr(SA, "LOG_PATH", log_path)
    monkeypatch.setattr(SA, "DASHBOARD_STATE_DIR", state_dir)
    monkeypatch.setattr(SA, "HEALTH_REPORT_PATH", health_path)
    monkeypatch.setattr(SA, "SENTINEL_TILE_PATH", sentinel_tile)
    monkeypatch.setattr(SA, "POSITIONS_PATH", positions)
    monkeypatch.setattr(SA, "ERRORS_DB", db_path)
    monkeypatch.setattr(SA, "TEST_RESULTS_PATH", state_dir / "quantai-test-results.json")
    # Sandbox runtime paths so tests don't touch root-owned production dirs
    monkeypatch.setattr(SA, "WEEKLY_REPORTS_DIR", tmp_path / "weekly_reports")
    monkeypatch.setattr(SA, "JOURNAL_PATH", tmp_path / "trades.jsonl")
    monkeypatch.setattr(SA, "CATALOG_PATH", tmp_path / "missing-catalog.json")
    monkeypatch.setattr(SA, "SENTINEL_IDENTITY", tmp_path / "missing-identity.md")

    state_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "pending_fixes").mkdir(exist_ok=True)
    (data_dir / "applied").mkdir(exist_ok=True)
    (data_dir / "digest_buffer").mkdir(exist_ok=True)
    (data_dir / "fix_history").mkdir(exist_ok=True)

    _create_errors_db(db_path)

    return {
        "tmp": tmp_path, "data_dir": data_dir, "state_dir": state_dir,
        "db": db_path, "health": health_path, "tile": sentinel_tile,
        "positions": positions,
    }


class TestApplyCycle:
    def test_reclassify_runs_during_apply(self, sentinel_env, monkeypatch):
        """Apply cycle MUST reclassify known-noise events even with empty LLM plan."""
        # Seed: one fail2ban warning event (matches RECLASSIFY_PATTERNS)
        con = sqlite3.connect(sentinel_env["db"])
        con.execute(
            "INSERT INTO events (first_seen, last_seen, source, severity, message, "
            "signature, signature_hash, count) VALUES (?,?,?,?,?,?,?,?)",
            ("2026-05-01T00:00:00Z", "2026-05-03T00:00:00Z", "test", "warning",
             "noise", "fail2ban.filter sshd Found", "h1", 100),
        )
        con.commit()
        con.close()

        _seed_health_report(sentinel_env["health"])
        _seed_positions(sentinel_env["positions"], count=0)

        # Pretend it's outside the trading window
        with patch.object(SA, "is_trading_window", return_value=False), \
             patch.object(SA, "is_market_hours", return_value=False), \
             patch.object(SA, "call_llm", return_value={
                 "summary": "all quiet", "findings": [], "proposals": []}), \
             patch.object(SA, "post", return_value="msg-id"):
            state = {"attempts": {}, "quarantined": [], "last_run": {}}
            summary, counts = SA.run_apply(dry_run=False, state=state)

        # The reclassification should have flipped severity
        con = sqlite3.connect(sentinel_env["db"])
        cur = con.cursor()
        cur.execute("SELECT severity, resolved_at FROM events")
        sev, resolved_at = cur.fetchone()
        con.close()
        assert sev == "info"
        assert resolved_at is not None
        assert counts["reclassify"]["reclassified"] >= 1

    def test_dry_run_does_not_mutate(self, sentinel_env):
        """--dry-run plants no proposals, no DB writes, no Discord."""
        con = sqlite3.connect(sentinel_env["db"])
        con.execute(
            "INSERT INTO events (first_seen, last_seen, source, severity, message, "
            "signature, signature_hash, count) VALUES (?,?,?,?,?,?,?,?)",
            ("2026-05-01T00:00:00Z", "2026-05-03T00:00:00Z", "test", "warning",
             "noise", "fail2ban.filter sshd", "h2", 50),
        )
        con.commit()
        con.close()

        _seed_health_report(sentinel_env["health"])
        _seed_positions(sentinel_env["positions"])

        post_mock = MagicMock(return_value=None)
        with patch.object(SA, "is_trading_window", return_value=False), \
             patch.object(SA, "is_market_hours", return_value=False), \
             patch.object(SA, "post", post_mock):
            state = {"attempts": {}, "quarantined": [], "last_run": {}}
            SA.run_apply(dry_run=True, state=state)

        # DB unchanged
        con = sqlite3.connect(sentinel_env["db"])
        cur = con.cursor()
        cur.execute("SELECT severity, resolved_at FROM events")
        sev, resolved_at = cur.fetchone()
        con.close()
        assert sev == "warning"
        assert resolved_at is None
        # No Discord posts in dry-run
        post_mock.assert_not_called()
        # No pending proposals written
        assert not any(sentinel_env["data_dir"].joinpath("pending_fixes").glob("*.json"))

    def test_llm_safe_auto_proposal_validated_and_queued(self, sentinel_env):
        """LLM proposes a safe_auto collector restart; validation passes; queued."""
        _seed_health_report(sentinel_env["health"])
        _seed_positions(sentinel_env["positions"], count=0)

        plan = {
            "summary": "stale collector",
            "findings": [{"id": "f1", "severity": "low", "what": "stale",
                          "evidence": "collect_karna.json"}],
            "proposals": [{
                "id": "restart-karna",
                "fix_class": "safe_auto",
                "severity": "low",
                "description": "Restart collect_karna cron",
                "runbook": "",
                "target_files": [],
                "shell_commands": ["pkill -f collect_karna.py",
                                   "python3 /var/dashboard/collect_karna.py"],
                "diff": "",
            }],
        }
        with patch.object(SA, "is_trading_window", return_value=False), \
             patch.object(SA, "is_market_hours", return_value=False), \
             patch.object(SA, "call_llm", return_value=plan), \
             patch.object(SA, "post", return_value="msg-id"), \
             patch.object(SA, "react", return_value=True):
            state = {"attempts": {}, "quarantined": [], "last_run": {}}
            SA.run_observe(dry_run=False, state=state)

        # Proposal should be queued
        pending = list(sentinel_env["data_dir"].joinpath("pending_fixes").glob("*.json"))
        assert len(pending) == 1
        rec = json.loads(pending[0].read_text())
        assert rec["fix_class"] == "safe_auto"
        assert rec["auto_apply"] is True

    def test_llm_proposes_blocked_path_REJECTED(self, sentinel_env):
        """LLM tries to safe_auto-edit autonomous_execution.py — must be rejected."""
        _seed_health_report(sentinel_env["health"])
        _seed_positions(sentinel_env["positions"])

        plan = {
            "summary": "evil",
            "findings": [],
            "proposals": [{
                "id": "evil",
                "fix_class": "safe_auto",
                "severity": "high",
                "description": "Modify trading path",
                "target_files": ["v2/shared-data/scripts/autonomous_execution.py"],
                "shell_commands": [],
                "diff": "--- a/scripts/autonomous_execution.py\n+++ b/scripts/autonomous_execution.py\n",
            }],
        }
        with patch.object(SA, "is_trading_window", return_value=False), \
             patch.object(SA, "is_market_hours", return_value=False), \
             patch.object(SA, "call_llm", return_value=plan), \
             patch.object(SA, "post", return_value="msg-id"):
            state = {"attempts": {}, "quarantined": [], "last_run": {}}
            SA.run_observe(dry_run=False, state=state)

        # NO proposals queued — validation gate rejected
        pending = list(sentinel_env["data_dir"].joinpath("pending_fixes").glob("*.json"))
        assert len(pending) == 0


class TestDashboardTile:
    def test_tile_written_with_correct_shape(self, sentinel_env):
        state = {"attempts": {}, "quarantined": [], "last_run": {}}
        SA.write_dashboard(state, "test summary", "observe", 0, dry_run=False)

        tile = json.loads(sentinel_env["tile"].read_text())
        assert "last_updated" in tile
        assert tile["status"] in ("ok", "warning", "error")
        d = tile["data"]
        assert "mode" in d
        assert "actions_taken" in d
        assert "pending_count" in d
        assert "next_scheduled_run_et" in d
