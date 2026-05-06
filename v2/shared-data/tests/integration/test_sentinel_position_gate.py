"""Integration test: position-gate safety on ibgateway restart.

The most important test in the suite. Verifies that even when the LLM tags
a restart-ibgateway proposal as fix_class="safe_auto", the validation gate
rejects it whenever positions > 0 OR market_open. This is the gap in
auto_heal that the 2026-05-02 outage exposed.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import sentinel_agent as SA


@pytest.fixture
def env(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "pending_fixes").mkdir()

    monkeypatch.setattr(SA, "DATA_DIR", data_dir)
    monkeypatch.setattr(SA, "PENDING_DIR", data_dir / "pending_fixes")
    monkeypatch.setattr(SA, "APPLIED_DIR", data_dir / "applied")
    monkeypatch.setattr(SA, "DIGEST_DIR", data_dir / "digest_buffer")
    monkeypatch.setattr(SA, "FIX_HISTORY_DIR", data_dir / "fix_history")
    monkeypatch.setattr(SA, "DASHBOARD_STATE_DIR", state_dir)
    monkeypatch.setattr(SA, "HEALTH_REPORT_PATH", state_dir / "health.json")
    monkeypatch.setattr(SA, "POSITIONS_PATH", state_dir / "positions.json")
    monkeypatch.setattr(SA, "ERRORS_DB", state_dir / "missing.db")  # ensures no DB read
    monkeypatch.setattr(SA, "TEST_RESULTS_PATH", state_dir / "tests.json")
    monkeypatch.setattr(SA, "LOG_PATH", tmp_path / "sentinel.log")
    monkeypatch.setattr(SA, "WEEKLY_REPORTS_DIR", tmp_path / "weekly_reports")
    monkeypatch.setattr(SA, "JOURNAL_PATH", tmp_path / "trades.jsonl")
    monkeypatch.setattr(SA, "CATALOG_PATH", tmp_path / "missing-catalog.json")
    monkeypatch.setattr(SA, "SENTINEL_IDENTITY", tmp_path / "missing-identity.md")
    monkeypatch.setattr(SA, "STATE_FILE", data_dir / "state.json")
    monkeypatch.setattr(SA, "LOCK_FILE", tmp_path / "sentinel.lock")

    state_dir.joinpath("health.json").write_text(json.dumps({
        "last_updated": "2026-05-03T00:00:00Z", "status": "error",
        "data": {"checks": {"ibkr_port": {"status": "error", "consecutive_fails": 5}}},
    }))
    return {"state_dir": state_dir, "data_dir": data_dir, "tmp": tmp_path}


def _ibgw_proposal(fix_class="safe_auto"):
    return {
        "id": "ibgw-restart",
        "fix_class": fix_class,
        "severity": "critical",
        "description": "IBKR port refused for 6+ minutes; restart gateway",
        "target_files": [],
        "shell_commands": ["systemctl restart ibgateway"],
        "diff": "",
    }


class TestPositionGate:
    def test_proposal_with_positions_and_market_OPEN_rejected(self, env):
        """Worst case: market open AND positions exist → reject."""
        env["state_dir"].joinpath("positions.json").write_text(json.dumps({
            "last_updated": "x", "status": "ok",
            "data": {"count": 3, "positions": [{}, {}, {}]},
        }))

        plan = {
            "summary": "broker down",
            "findings": [{"id": "ibkr", "severity": "critical", "what": "port refused"}],
            "proposals": [_ibgw_proposal(fix_class="safe_auto")],
        }
        with patch.object(SA, "is_trading_window", return_value=True), \
             patch.object(SA, "is_market_hours", return_value=True), \
             patch.object(SA, "call_llm", return_value=plan), \
             patch.object(SA, "post", return_value=None):
            state = {"attempts": {}, "quarantined": [], "last_run": {}}
            SA.run_observe(dry_run=False, state=state)

        # NO pending proposal — safety gate rejected
        assert not list(env["data_dir"].joinpath("pending_fixes").glob("*.json"))

    def test_proposal_with_positions_and_market_CLOSED_rejected(self, env):
        """Off-hours but positions exist → still reject (reconciliation risk)."""
        env["state_dir"].joinpath("positions.json").write_text(json.dumps({
            "last_updated": "x", "status": "ok",
            "data": {"count": 1, "positions": [{}]},
        }))
        plan = {"summary": "x", "findings": [], "proposals": [_ibgw_proposal()]}
        with patch.object(SA, "is_trading_window", return_value=False), \
             patch.object(SA, "is_market_hours", return_value=False), \
             patch.object(SA, "call_llm", return_value=plan), \
             patch.object(SA, "post", return_value=None):
            state = {"attempts": {}, "quarantined": [], "last_run": {}}
            SA.run_observe(dry_run=False, state=state)
        assert not list(env["data_dir"].joinpath("pending_fixes").glob("*.json"))

    def test_proposal_no_positions_market_OPEN_rejected(self, env):
        """Market open + 0 positions → still reject (market hours rule)."""
        env["state_dir"].joinpath("positions.json").write_text(json.dumps({
            "last_updated": "x", "status": "ok", "data": {"count": 0, "positions": []},
        }))
        plan = {"summary": "x", "findings": [], "proposals": [_ibgw_proposal()]}
        with patch.object(SA, "is_trading_window", return_value=True), \
             patch.object(SA, "is_market_hours", return_value=True), \
             patch.object(SA, "call_llm", return_value=plan), \
             patch.object(SA, "post", return_value=None):
            state = {"attempts": {}, "quarantined": [], "last_run": {}}
            SA.run_observe(dry_run=False, state=state)
        assert not list(env["data_dir"].joinpath("pending_fixes").glob("*.json"))

    def test_proposal_no_positions_market_CLOSED_OK(self, env):
        """The only allowed combination: 0 positions AND market closed."""
        env["state_dir"].joinpath("positions.json").write_text(json.dumps({
            "last_updated": "x", "status": "ok", "data": {"count": 0, "positions": []},
        }))
        plan = {"summary": "x", "findings": [], "proposals": [_ibgw_proposal()]}
        with patch.object(SA, "is_trading_window", return_value=False), \
             patch.object(SA, "is_market_hours", return_value=False), \
             patch.object(SA, "call_llm", return_value=plan), \
             patch.object(SA, "post", return_value="msg-id"), \
             patch.object(SA, "react", return_value=True):
            state = {"attempts": {}, "quarantined": [], "last_run": {}}
            SA.run_observe(dry_run=False, state=state)

        # Should be queued
        pending = list(env["data_dir"].joinpath("pending_fixes").glob("*.json"))
        assert len(pending) == 1
        rec = json.loads(pending[0].read_text())
        assert rec["fix_class"] == "safe_auto"

    def test_revalidation_at_consume_time_blocks_if_positions_appear(self, env):
        """A proposal queued earlier is RE-validated at consume time. If positions
        appeared between the propose and apply phases, consume_pending must reject."""
        env["state_dir"].joinpath("positions.json").write_text(json.dumps({
            "last_updated": "x", "status": "ok", "data": {"count": 0, "positions": []},
        }))
        # Plant an approved-safe-auto proposal (use far-future expires_at so
        # the test isn't time-bombed; safety gate must fire BEFORE expiry check)
        from datetime import datetime, timezone, timedelta
        far_future = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
        rec = {
            "fix_id": "abc12345", "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": far_future, "severity": "critical",
            "fix_class": "safe_auto", "auto_apply": True,
            "description": "restart ibgateway",
            "target_files": [], "shell_commands": ["systemctl restart ibgateway"],
            "diff": "", "discord_message_id": None, "channel_id": None,
            "status": "approved_safe_auto",
        }
        env["data_dir"].joinpath("pending_fixes/abc12345.json").write_text(json.dumps(rec))

        # Now positions exist (race condition: opened a trade between cycles)
        state = {"attempts": {}, "quarantined": [], "last_run": {}}
        with patch.object(SA, "post", return_value="msg-id"):
            counts = SA.consume_pending(state, dry_run=False, open_positions=2,
                                        market_open=False)

        assert counts["blocked_by_safety"] == 1
        assert counts["applied"] == 0
