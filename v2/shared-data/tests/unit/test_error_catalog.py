"""Unit tests: error-catalog.json and referenced runbook files.

Validates the catalog contract that error_detector.py relies on, and
confirms every referenced runbook actually exists.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent.parent.parent  # /home/trader/QuantAI
CATALOG_PATH = REPO_ROOT / "docs" / "error-catalog.json"
RUNBOOKS_DIR = REPO_ROOT / "docs" / "runbooks"

# Required fields on every catalog entry
REQUIRED_ENTRY_FIELDS = (
    "id", "pattern", "is_regex", "category",
    "severity", "auto_action", "description", "runbook",
)

VALID_SEVERITIES = {"info", "warning", "error", "critical", "unknown"}
VALID_AUTO_ACTIONS = {"none", "restart_service", "retry", "skip", "notify"}


# ── Catalog file ─────────────────────────────────────────────────────────────

class TestCatalogFile:
    def test_catalog_exists(self):
        assert CATALOG_PATH.exists(), f"error-catalog.json not found at {CATALOG_PATH}"

    def test_catalog_is_valid_json(self):
        try:
            data = json.loads(CATALOG_PATH.read_text())
        except json.JSONDecodeError as e:
            pytest.fail(f"error-catalog.json is not valid JSON: {e}")
        assert isinstance(data, dict), "catalog root must be a dict"

    def test_catalog_is_not_empty(self):
        data = json.loads(CATALOG_PATH.read_text())
        assert len(data) > 0, "error-catalog.json must have at least one entry"

    @pytest.mark.parametrize("entry_id", list(json.loads(CATALOG_PATH.read_text()).keys())
                             if CATALOG_PATH.exists() else [])
    def test_every_entry_has_required_fields(self, entry_id):
        data = json.loads(CATALOG_PATH.read_text())
        entry = data[entry_id]
        for field in REQUIRED_ENTRY_FIELDS:
            assert field in entry, f"entry '{entry_id}' missing required field '{field}'"

    @pytest.mark.parametrize("entry_id", list(json.loads(CATALOG_PATH.read_text()).keys())
                             if CATALOG_PATH.exists() else [])
    def test_every_entry_has_valid_severity(self, entry_id):
        data = json.loads(CATALOG_PATH.read_text())
        sev = data[entry_id].get("severity", "")
        assert sev in VALID_SEVERITIES, (
            f"entry '{entry_id}' has invalid severity '{sev}'; must be one of {VALID_SEVERITIES}"
        )

    @pytest.mark.parametrize("entry_id", list(json.loads(CATALOG_PATH.read_text()).keys())
                             if CATALOG_PATH.exists() else [])
    def test_every_entry_has_valid_auto_action(self, entry_id):
        data = json.loads(CATALOG_PATH.read_text())
        action = data[entry_id].get("auto_action", "")
        assert action in VALID_AUTO_ACTIONS, (
            f"entry '{entry_id}' has invalid auto_action '{action}'; "
            f"must be one of {VALID_AUTO_ACTIONS}"
        )

    @pytest.mark.parametrize("entry_id", [
        eid for eid, entry in json.loads(CATALOG_PATH.read_text()).items()
        if entry.get("runbook")
    ] if CATALOG_PATH.exists() else [])
    def test_referenced_runbooks_exist(self, entry_id):
        data = json.loads(CATALOG_PATH.read_text())
        runbook_rel = data[entry_id]["runbook"]
        runbook_path = REPO_ROOT / runbook_rel
        assert runbook_path.exists(), (
            f"entry '{entry_id}' references runbook '{runbook_rel}' "
            f"which does not exist at {runbook_path}"
        )

    @pytest.mark.parametrize("entry_id", [
        eid for eid, entry in json.loads(CATALOG_PATH.read_text()).items()
        if entry.get("runbook")
    ] if CATALOG_PATH.exists() else [])
    def test_referenced_runbooks_not_empty(self, entry_id):
        data = json.loads(CATALOG_PATH.read_text())
        runbook_rel = data[entry_id]["runbook"]
        runbook_path = REPO_ROOT / runbook_rel
        if runbook_path.exists():
            assert runbook_path.stat().st_size > 0, (
                f"runbook '{runbook_rel}' referenced by '{entry_id}' is empty"
            )


# ── ibkr-port-refused entry (specific assertions) ───────────────────────────

class TestIbkrPortRefusedEntry:
    @pytest.fixture(autouse=True)
    def catalog(self):
        return json.loads(CATALOG_PATH.read_text())

    def test_ibkr_entry_exists(self, catalog):
        assert "ibkr-port-refused" in catalog, (
            "ibkr-port-refused entry missing from error-catalog.json. "
            "This entry is required so error_detector.py can escalate IBKR "
            "connection failures to Discord."
        )

    def test_ibkr_entry_severity_is_critical(self, catalog):
        entry = catalog.get("ibkr-port-refused", {})
        assert entry.get("severity") == "critical", (
            f"ibkr-port-refused severity must be 'critical' so error_detector.py "
            f"posts to Discord (discord_eligible = severity in ('warning', 'critical')). "
            f"Got: {entry.get('severity')!r}"
        )

    def test_ibkr_entry_pattern_matches_log_message(self, catalog):
        """Pattern must match the exact string logged by _broker_ibkr.py."""
        import re
        entry = catalog.get("ibkr-port-refused", {})
        pattern = entry.get("pattern", "")
        log_line = (
            "IBKRBroker: gave up after 3 connect attempts to 127.0.0.1:4002 "
            "(last err: ConnectionRefusedError)"
        )
        if entry.get("is_regex"):
            assert re.search(pattern, log_line), (
                f"Pattern {pattern!r} does not match the expected log line"
            )
        else:
            assert pattern in log_line, (
                f"Pattern {pattern!r} not found in log line {log_line!r}"
            )

    def test_ibkr_entry_has_runbook(self, catalog):
        entry = catalog.get("ibkr-port-refused", {})
        assert entry.get("runbook"), "ibkr-port-refused entry must reference a runbook"

    def test_ibkr_runbook_file_exists(self, catalog):
        entry = catalog.get("ibkr-port-refused", {})
        runbook_rel = entry.get("runbook", "")
        if runbook_rel:
            runbook_path = REPO_ROOT / runbook_rel
            assert runbook_path.exists(), (
                f"ibkr-port-refused runbook '{runbook_rel}' does not exist at {runbook_path}"
            )

    def test_ibkr_runbook_contains_restart_command(self, catalog):
        """Runbook must include the fix command so it's actionable from Discord."""
        entry = catalog.get("ibkr-port-refused", {})
        runbook_rel = entry.get("runbook", "")
        if runbook_rel:
            runbook_path = REPO_ROOT / runbook_rel
            if runbook_path.exists():
                text = runbook_path.read_text()
                assert "restart ibgateway" in text, (
                    "Runbook must contain 'restart ibgateway' so the fix is "
                    "immediately actionable from a Discord alert"
                )

    def test_ibkr_entry_auto_action_is_none(self, catalog):
        """Auto-action must be 'none' — gateway restart requires human confirmation."""
        entry = catalog.get("ibkr-port-refused", {})
        assert entry.get("auto_action") == "none", (
            "ibkr-port-refused auto_action must be 'none' — restarting the "
            "gateway during live trading could cause order loss."
        )

    def test_ibkr_entry_category_is_broker_connectivity(self, catalog):
        entry = catalog.get("ibkr-port-refused", {})
        assert entry.get("category") == "broker_connectivity"


# ── Runbook: runbook-ibkr-connection.md ──────────────────────────────────────

class TestIbkrRunbook:
    RUNBOOK = RUNBOOKS_DIR / "runbook-ibkr-connection.md"

    def test_runbook_exists(self):
        assert self.RUNBOOK.exists(), f"runbook not found at {self.RUNBOOK}"

    def test_runbook_not_empty(self):
        assert self.RUNBOOK.stat().st_size > 0

    def test_runbook_has_symptoms_section(self):
        text = self.RUNBOOK.read_text()
        assert "## Symptoms" in text or "Symptoms" in text

    def test_runbook_has_diagnosis_section(self):
        text = self.RUNBOOK.read_text()
        assert "## Diagnosis" in text or "Diagnosis" in text

    def test_runbook_has_fix_section(self):
        text = self.RUNBOOK.read_text()
        assert "## Fix" in text or "Fix" in text

    def test_runbook_has_prevention_section(self):
        text = self.RUNBOOK.read_text()
        assert "## Prevention" in text or "Prevention" in text

    def test_runbook_mentions_reconnect_window_seconds(self):
        """Runbook should reference the IBC config fix so it's self-contained."""
        text = self.RUNBOOK.read_text()
        assert "ReconnectWindowSeconds" in text

    def test_runbook_mentions_verify_command(self):
        """Runbook must include a way to verify the fix worked."""
        text = self.RUNBOOK.read_text()
        assert "isConnected" in text or "DUP851506" in text
