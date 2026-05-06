"""Unit tests for sentinel_agent.reclassify_catalog_noise.

Built-in safe_auto action: any unresolved event matching a known-noise pattern
at warning/error/critical severity gets flipped to info + resolved.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import sentinel_agent as SA


def _create_db(path: Path) -> None:
    """Create the events table schema matching production errors.db."""
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            source TEXT NOT NULL,
            severity TEXT NOT NULL,
            message TEXT NOT NULL,
            signature TEXT NOT NULL,
            signature_hash TEXT NOT NULL,
            count INTEGER NOT NULL DEFAULT 1,
            catalog_id TEXT,
            runbook TEXT,
            resolved_at TEXT,
            resolved_by TEXT
        );
    """)
    con.commit()
    con.close()


def _insert(path: Path, *, severity: str, signature: str, message: str = "",
            count: int = 1, resolved_at: str | None = None) -> int:
    con = sqlite3.connect(path)
    cur = con.cursor()
    cur.execute(
        "INSERT INTO events (first_seen, last_seen, source, severity, "
        "message, signature, signature_hash, count, resolved_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("2026-05-01T00:00:00Z", "2026-05-03T00:00:00Z", "test",
         severity, message or signature, signature, "h" + signature[:8],
         count, resolved_at),
    )
    con.commit()
    eid = cur.lastrowid
    con.close()
    return eid


class TestReclassifyCatalogNoise:
    @pytest.fixture(autouse=True)
    def _db(self, tmp_path, monkeypatch):
        self.db = tmp_path / "errors.db"
        _create_db(self.db)
        monkeypatch.setattr(SA, "ERRORS_DB", self.db)

    def test_no_db_returns_skipped(self, tmp_path, monkeypatch):
        monkeypatch.setattr(SA, "ERRORS_DB", tmp_path / "missing.db")
        result = SA.reclassify_catalog_noise(dry_run=False)
        assert result["reclassified"] == 0
        assert result.get("skipped_db_missing") is True

    def test_fail2ban_warning_reclassified(self):
        eid = _insert(
            self.db,
            severity="warning",
            signature="fail2ban.filter [N]: INFO [sshd] Found N.N",
            count=1000,
        )
        result = SA.reclassify_catalog_noise(dry_run=False)
        assert result["reclassified"] >= 1

        # Verify DB state
        con = sqlite3.connect(self.db)
        cur = con.cursor()
        cur.execute("SELECT severity, resolved_at, resolved_by FROM events WHERE id=?", (eid,))
        sev, resolved_at, resolved_by = cur.fetchone()
        con.close()
        assert sev == "info"
        assert resolved_at is not None
        assert "fail2ban" in resolved_by.lower()

    def test_ufw_block_reclassified(self):
        _insert(self.db, severity="warning", signature="QuantAI kernel: [UFW BLOCK] IN=eth0")
        result = SA.reclassify_catalog_noise(dry_run=False)
        assert result["reclassified"] >= 1

    def test_ssh_invalid_user_reclassified(self):
        _insert(self.db, severity="warning",
                signature="QuantAI sshd[N]: Invalid user admin from N.N port N")
        result = SA.reclassify_catalog_noise(dry_run=False)
        assert result["reclassified"] >= 1

    # ─── New patterns added 2026-05-04 ───

    def test_received_disconnect_reclassified(self):
        """sshd 'Received disconnect ... [preauth]' is internet brute-force noise."""
        _insert(self.db, severity="warning",
                signature="QuantAI sshd[N]: Received disconnect from N.N port N:N: Bye [preauth]")
        result = SA.reclassify_catalog_noise(dry_run=False)
        assert result["reclassified"] >= 1

    def test_fail2ban_actions_reclassified(self):
        """fail2ban.actions NOTICE Ban/Unban — different subsystem from .filter."""
        _insert(self.db, severity="warning",
                signature=",N fail2ban.actions [N]: NOTICE [sshd] Ban N.N")
        result = SA.reclassify_catalog_noise(dry_run=False)
        assert result["reclassified"] >= 1

    def test_review_empty_haiku_reclassified(self):
        """trade_reviewer transient Haiku response failure."""
        _insert(self.db, severity="warning",
                signature="review: empty Haiku response for A019")
        result = SA.reclassify_catalog_noise(dry_run=False)
        assert result["reclassified"] >= 1

    def test_haiku_timeout_reclassified(self):
        _insert(self.db, severity="warning", signature="Haiku call failed: timed out")
        result = SA.reclassify_catalog_noise(dry_run=False)
        assert result["reclassified"] >= 1

    def test_ibkr_1100_connectivity_reclassified(self):
        """IBKR Error 1100 — expected during nightly restart window."""
        _insert(self.db, severity="error",
                signature="Error N, reqId -N: Connectivity between IBKR and Trader Workstation has been lost")
        result = SA.reclassify_catalog_noise(dry_run=False)
        assert result["reclassified"] >= 1

    def test_ibkr_no_security_definition_reclassified(self):
        """Routine — agent tried option that doesn't exist on IBKR."""
        _insert(self.db, severity="error",
                signature="Error N, reqId N: No security definition has been found for the request")
        result = SA.reclassify_catalog_noise(dry_run=False)
        assert result["reclassified"] >= 1

    def test_ibkr_failed_to_qualify_leg_reclassified(self):
        _insert(self.db, severity="error",
                signature="IBKRBroker: failed to qualify leg XOP260515P00167500")
        result = SA.reclassify_catalog_noise(dry_run=False)
        assert result["reclassified"] >= 1

    def test_health_monitor_stale_socket_reclassified(self):
        _insert(self.db, severity="warning",
                signature="health-monitor: restarting (reason: stale-socket)")
        result = SA.reclassify_catalog_noise(dry_run=False)
        assert result["reclassified"] >= 1

    def test_already_resolved_NOT_touched(self):
        eid = _insert(
            self.db, severity="warning",
            signature="fail2ban.filter old event",
            resolved_at="2026-05-01T00:00:00Z",
        )
        result = SA.reclassify_catalog_noise(dry_run=False)
        # Already resolved — should not be re-reclassified
        con = sqlite3.connect(self.db)
        cur = con.cursor()
        cur.execute("SELECT severity, resolved_at FROM events WHERE id=?", (eid,))
        sev, resolved_at = cur.fetchone()
        con.close()
        assert sev == "warning"  # severity unchanged
        assert resolved_at == "2026-05-01T00:00:00Z"  # original timestamp preserved

    def test_info_severity_NOT_touched(self):
        # info-severity events are not in the reclassification scope (only
        # warning/error/critical get flipped)
        eid = _insert(self.db, severity="info", signature="fail2ban harmless")
        result = SA.reclassify_catalog_noise(dry_run=False)
        con = sqlite3.connect(self.db)
        cur = con.cursor()
        cur.execute("SELECT resolved_at FROM events WHERE id=?", (eid,))
        resolved_at = cur.fetchone()[0]
        con.close()
        # Should remain unresolved (we only resolve warning/error/critical)
        assert resolved_at is None

    def test_unrelated_warning_NOT_touched(self):
        eid = _insert(self.db, severity="warning",
                      signature="some completely novel pattern xyz123")
        result = SA.reclassify_catalog_noise(dry_run=False)
        con = sqlite3.connect(self.db)
        cur = con.cursor()
        cur.execute("SELECT severity, resolved_at FROM events WHERE id=?", (eid,))
        sev, resolved_at = cur.fetchone()
        con.close()
        assert sev == "warning"  # severity unchanged
        assert resolved_at is None  # still unresolved

    def test_dry_run_does_not_mutate(self):
        eid = _insert(self.db, severity="warning",
                      signature="fail2ban.filter dry-run test", count=5)
        result = SA.reclassify_catalog_noise(dry_run=True)
        assert result.get("dry_run") is True
        assert result["reclassified"] == 0
        assert result.get("would_reclassify", 0) >= 1

        # DB unchanged
        con = sqlite3.connect(self.db)
        cur = con.cursor()
        cur.execute("SELECT severity, resolved_at FROM events WHERE id=?", (eid,))
        sev, resolved_at = cur.fetchone()
        con.close()
        assert sev == "warning"
        assert resolved_at is None
