"""Smoke test: run system_monitor.py against the live VPS.

Verifies the report file exists with all 13 checks and required envelope keys.
No LLM call. Read-only.

Skipped if /var/dashboard/state is not writable (CI / local dev).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

REPORT_PATH = Path("/var/dashboard/state/system-health-report.json")
EXPECTED_CHECKS = {
    "ibkr_port", "litellm_4000", "clawroute_18790", "cron_freshness",
    "disk", "memory", "self_learning_sla", "weekly_synthesis",
    "collector_staleness", "journal_schema", "test_results", "graphify",
    "open_positions",
}


@pytest.mark.skipif(
    not REPORT_PATH.parent.exists(),
    reason="dashboard state dir not present (not running on VPS)"
)
class TestLiveHealthReport:
    def test_report_exists(self):
        assert REPORT_PATH.exists(), "Run system_monitor.py first"

    def test_envelope_shape(self):
        doc = json.loads(REPORT_PATH.read_text())
        assert "last_updated" in doc
        assert "status" in doc
        assert doc["status"] in ("ok", "info", "warning", "error")
        assert "data" in doc
        assert "checks" in doc["data"]

    def test_all_13_checks_present(self):
        doc = json.loads(REPORT_PATH.read_text())
        actual = set(doc["data"]["checks"].keys())
        missing = EXPECTED_CHECKS - actual
        assert not missing, f"missing checks: {missing}"

    def test_each_check_has_status(self):
        doc = json.loads(REPORT_PATH.read_text())
        for name, v in doc["data"]["checks"].items():
            assert "status" in v, f"check {name} missing status"
            assert v["status"] in ("ok", "info", "warning", "error"), \
                f"check {name} has invalid status: {v['status']}"
