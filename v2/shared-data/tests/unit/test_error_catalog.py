"""Unit tests: error-catalog.json and referenced runbook files.

The catalog uses an envelope format:
  {
    "schema_version": 1,
    "last_updated": "...",
    "description": "...",
    "errors": [ { ...entry... }, ... ]
  }

lib_errors.load_catalog() calls doc.get("errors", []) so that's the authoritative
contract we validate here.

Every referenced runbook file must also exist.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent.parent.parent  # /home/trader/QuantAI
CATALOG_PATH = REPO_ROOT / "docs" / "error-catalog.json"
RUNBOOKS_DIR = REPO_ROOT / "docs" / "runbooks"

# Required fields on every catalog entry
REQUIRED_ENTRY_FIELDS = (
    "id", "pattern", "is_regex", "category",
    "severity", "auto_action", "description",
)

# Severities accepted by lib_errors.normalize_severity without mapping to "info"
VALID_SEVERITIES = {
    "critical", "high", "error",
    "medium", "warning",
    "low", "info",
    "unknown", "transient",
}

VALID_AUTO_ACTIONS = {"none", "restart_service", "retry", "skip", "notify"}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_catalog_doc() -> dict:
    """Load the full envelope dict."""
    return json.loads(CATALOG_PATH.read_text())


def _load_entries() -> list:
    """Return the errors list (what lib_errors.load_catalog returns)."""
    doc = _load_catalog_doc()
    return doc.get("errors", [])


def _entry_ids() -> list[str]:
    if not CATALOG_PATH.exists():
        return []
    return [e["id"] for e in _load_entries()]


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

    def test_catalog_has_envelope_format(self):
        """The catalog uses {schema_version, errors: [...]} envelope — not a flat dict."""
        doc = _load_catalog_doc()
        assert "errors" in doc, (
            "error-catalog.json must have an 'errors' key at the root. "
            "lib_errors.load_catalog() calls doc.get('errors', []) — "
            "a flat dict returns an empty list and 'Catalog: 0' on the dashboard."
        )
        assert isinstance(doc["errors"], list), "'errors' must be a list"

    def test_catalog_is_not_empty(self):
        entries = _load_entries()
        assert len(entries) > 0, "catalog must have at least one entry in errors[]"

    def test_catalog_has_schema_version(self):
        doc = _load_catalog_doc()
        assert "schema_version" in doc, "envelope should have schema_version"

    @pytest.mark.parametrize("entry_id", _entry_ids())
    def test_every_entry_has_required_fields(self, entry_id):
        entries = {e["id"]: e for e in _load_entries()}
        entry = entries[entry_id]
        for field in REQUIRED_ENTRY_FIELDS:
            assert field in entry, f"entry '{entry_id}' missing required field '{field}'"

    @pytest.mark.parametrize("entry_id", _entry_ids())
    def test_every_entry_has_valid_severity(self, entry_id):
        entries = {e["id"]: e for e in _load_entries()}
        sev = entries[entry_id].get("severity", "")
        assert sev in VALID_SEVERITIES, (
            f"entry '{entry_id}' has invalid severity '{sev}'; must be one of {VALID_SEVERITIES}"
        )

    @pytest.mark.parametrize("entry_id", _entry_ids())
    def test_every_entry_has_valid_auto_action(self, entry_id):
        entries = {e["id"]: e for e in _load_entries()}
        action = entries[entry_id].get("auto_action", "")
        assert action in VALID_AUTO_ACTIONS, (
            f"entry '{entry_id}' has invalid auto_action '{action}'; "
            f"must be one of {VALID_AUTO_ACTIONS}"
        )

    @pytest.mark.parametrize("entry_id", [
        e["id"] for e in _load_entries() if e.get("runbook")
    ] if CATALOG_PATH.exists() else [])
    def test_referenced_runbooks_exist(self, entry_id):
        entries = {e["id"]: e for e in _load_entries()}
        runbook_rel = entries[entry_id]["runbook"]
        runbook_path = REPO_ROOT / runbook_rel
        assert runbook_path.exists(), (
            f"entry '{entry_id}' references runbook '{runbook_rel}' "
            f"which does not exist at {runbook_path}"
        )

    @pytest.mark.parametrize("entry_id", [
        e["id"] for e in _load_entries() if e.get("runbook")
    ] if CATALOG_PATH.exists() else [])
    def test_referenced_runbooks_not_empty(self, entry_id):
        entries = {e["id"]: e for e in _load_entries()}
        runbook_rel = entries[entry_id]["runbook"]
        runbook_path = REPO_ROOT / runbook_rel
        if runbook_path.exists():
            assert runbook_path.stat().st_size > 0, (
                f"runbook '{runbook_rel}' referenced by '{entry_id}' is empty"
            )

    def test_no_duplicate_ids(self):
        ids = [e["id"] for e in _load_entries()]
        seen = set()
        dupes = [i for i in ids if i in seen or seen.add(i)]
        assert not dupes, f"duplicate entry IDs: {dupes}"

    def test_no_empty_patterns(self):
        bad = [e["id"] for e in _load_entries() if not e.get("pattern", "").strip()]
        assert not bad, f"entries with empty pattern: {bad}"


# ── Severity correctness: known noisy patterns must NOT be critical/error ─────

class TestSeverityCorrectness:
    """Guard against high-severity labels on known-noise patterns.

    These are patterns that generate thousands of events on a VPS exposed to
    the internet. If they were ever re-raised to 'error' or 'critical', they'd
    spam Discord.
    """

    def _by_id(self) -> dict:
        return {e["id"]: e for e in _load_entries()}

    def test_ufw_block_is_info(self):
        entries = self._by_id()
        if "ufw-block" in entries:
            assert entries["ufw-block"]["severity"] == "info", \
                "ufw-block must be 'info' — it generates thousands of events per day"

    def test_sshd_invalid_user_is_info(self):
        entries = self._by_id()
        for eid in ("sshd-invalid-user", "sshd-brute-force-attempt", "sshd-invalid-user-disconnect"):
            if eid in entries:
                sev = entries[eid]["severity"]
                assert sev == "info", f"{eid} must be 'info', got '{sev}'"

    def test_fail2ban_sshd_is_info(self):
        entries = self._by_id()
        if "fail2ban-sshd-found" in entries:
            sev = entries["fail2ban-sshd-found"]["severity"]
            assert sev == "info", f"fail2ban-sshd-found must be 'info', got '{sev}'"

    def test_health_monitor_stale_socket_is_info(self):
        entries = self._by_id()
        if "health-monitor-stale-socket" in entries:
            sev = entries["health-monitor-stale-socket"]["severity"]
            assert sev == "info", \
                f"health-monitor-stale-socket must be 'info', got '{sev}'"

    def test_pipeline_stale_off_hours_is_info(self):
        entries = self._by_id()
        for eid in ("pipeline-stale-off-hours", "pipeline-beat-stale-off-hours-log"):
            if eid in entries:
                sev = entries[eid]["severity"]
                assert sev == "info", f"{eid} must be 'info', got '{sev}'"


# ── ibkr-port-refused entry (specific assertions) ────────────────────────────

class TestIbkrPortRefusedEntry:
    def _catalog(self):
        return {e["id"]: e for e in _load_entries()}

    def test_ibkr_entry_exists(self):
        assert "ibkr-port-refused" in self._catalog(), (
            "ibkr-port-refused entry missing. "
            "Required so error_detector can escalate IBKR connection failures to Discord."
        )

    def test_ibkr_entry_severity_is_critical(self):
        entry = self._catalog().get("ibkr-port-refused", {})
        assert entry.get("severity") == "critical", (
            f"ibkr-port-refused severity must be 'critical'. Got: {entry.get('severity')!r}"
        )

    def test_ibkr_entry_pattern_matches_log_message(self):
        """Pattern must match the exact string logged by _broker_ibkr.py."""
        entry = self._catalog().get("ibkr-port-refused", {})
        pattern = entry.get("pattern", "")
        log_line = (
            "IBKRBroker: gave up after 3 connect attempts to 127.0.0.1:4002 "
            "(last err: ConnectionRefusedError)"
        )
        if entry.get("is_regex"):
            assert re.search(pattern, log_line)
        else:
            assert pattern in log_line, (
                f"Pattern {pattern!r} not found in log line {log_line!r}"
            )

    def test_ibkr_entry_has_runbook(self):
        entry = self._catalog().get("ibkr-port-refused", {})
        assert entry.get("runbook"), "ibkr-port-refused entry must reference a runbook"

    def test_ibkr_runbook_file_exists(self):
        entry = self._catalog().get("ibkr-port-refused", {})
        runbook_rel = entry.get("runbook", "")
        if runbook_rel:
            assert (REPO_ROOT / runbook_rel).exists(), \
                f"ibkr-port-refused runbook '{runbook_rel}' does not exist"

    def test_ibkr_runbook_contains_restart_command(self):
        entry = self._catalog().get("ibkr-port-refused", {})
        runbook_rel = entry.get("runbook", "")
        if runbook_rel:
            runbook_path = REPO_ROOT / runbook_rel
            if runbook_path.exists():
                text = runbook_path.read_text()
                assert "restart ibgateway" in text

    def test_ibkr_entry_auto_action_is_none(self):
        entry = self._catalog().get("ibkr-port-refused", {})
        assert entry.get("auto_action") == "none"

    def test_ibkr_entry_category_is_broker_connectivity(self):
        entry = self._catalog().get("ibkr-port-refused", {})
        assert entry.get("category") == "broker_connectivity"


# ── Runbook: runbook-ibkr-connection.md ──────────────────────────────────────

class TestIbkrRunbook:
    RUNBOOK = RUNBOOKS_DIR / "runbook-ibkr-connection.md"

    def test_runbook_exists(self):
        assert self.RUNBOOK.exists()

    def test_runbook_not_empty(self):
        assert self.RUNBOOK.stat().st_size > 0

    def test_runbook_has_symptoms_section(self):
        text = self.RUNBOOK.read_text()
        assert "Symptoms" in text

    def test_runbook_has_diagnosis_section(self):
        text = self.RUNBOOK.read_text()
        assert "Diagnosis" in text

    def test_runbook_has_fix_section(self):
        text = self.RUNBOOK.read_text()
        assert "Fix" in text

    def test_runbook_has_prevention_section(self):
        text = self.RUNBOOK.read_text()
        assert "Prevention" in text

    def test_runbook_mentions_reconnect_window_seconds(self):
        text = self.RUNBOOK.read_text()
        assert "ReconnectWindowSeconds" in text

    def test_runbook_mentions_verify_command(self):
        text = self.RUNBOOK.read_text()
        assert "isConnected" in text or "DUP851506" in text


# ── lib_errors integration: catalog actually matches DB patterns ──────────────

class TestLibErrorsIntegration:
    """Verify match_catalog() returns non-None for the heaviest DB patterns."""

    @pytest.fixture(autouse=True)
    def catalog(self):
        sys.path.insert(0, "/var/dashboard")
        import lib_errors as L
        self._L = L
        self._cat = L.load_catalog()

    def _match(self, line: str, sig: str):
        return self._L.match_catalog(line, sig, self._cat)

    def test_ibkr_port_refused_matches(self):
        line = ("IBKRBroker: gave up after 3 connect attempts to 127.0.0.1:4002 "
                "(last err: ConnectionRefusedError)")
        result = self._match(line, line[:120])
        assert result is not None
        cat_id, sev, _ = result
        assert cat_id == "ibkr-port-refused"
        assert sev == "critical"

    def test_ufw_block_matches_as_info(self):
        line = "QuantAI kernel: [UFW BLOCK] IN=eth0 OUT= MAC=92:00 SRC=1.2.3.4 DST=87.99.141.55"
        result = self._match(line, "QuantAI kernel: [UFW BLOCK] IN=eth0")
        assert result is not None
        _, sev, _ = result
        assert sev == "info"

    def test_fail2ban_matches_as_info(self):
        line = "2026-05-03 17:14:26,724 fail2ban.filter [948304]: INFO [sshd] Found 14.103.112.56"
        result = self._match(line, "fail2ban.filter [N]: INFO [sshd] Found N.N")
        assert result is not None
        _, sev, _ = result
        assert sev == "info"

    def test_ibkr_restart_window_matches(self):
        line = "IBKRBroker: in IB Gateway restart window (23:30-00:15 ET) — refusing to connect"
        result = self._match(line, line[:120])
        assert result is not None
        _, sev, _ = result
        # transient normalizes to info
        assert sev == "info"

    def test_unmatched_line_returns_none(self):
        line = "something completely novel that is not in any catalog entry xyz123"
        result = self._match(line, line[:120])
        assert result is None
