"""Unit tests: Sentinel proposal dedup via semantic fingerprinting + suppression.

Tests cover:
  1. pattern_fingerprint groups equivalent proposals
  2. is_suppressed/record_suppression enforce the suppression window
  3. clean_stale_suppressions removes old entries
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPTS = str(Path(__file__).resolve().parents[2] / "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import sentinel_agent as SA


class TestPatternFingerprint:
    """Verify equivalent proposals map to the same fingerprint."""

    def test_equivalent_critical_proposals_same_fingerprint(self):
        """Different wordings for 'hidden criticals' should produce the same fingerprint."""
        p1 = {"id": "hidden-criticals-triage",
              "description": "402 critical errors are unresolved in errors.db"}
        p2 = {"id": "errors-db-investigation",
              "description": "11 unresolved critical entries in errors.db not represented"}
        p3 = {"id": "critical-severity-review",
              "description": "Critical-severity errors hidden from top_unresolved summary"}
        fp1 = SA.pattern_fingerprint(p1)
        fp2 = SA.pattern_fingerprint(p2)
        fp3 = SA.pattern_fingerprint(p3)
        assert fp1 == fp2 == fp3

    def test_equivalent_pytest_proposals_same_fingerprint(self):
        """'Run pytest' and 'stale test suite' should get same fingerprint."""
        p1 = {"id": "run-pytest", "description": "Trigger a full test suite run"}
        p2 = {"id": "stale-tests", "description": "Test suite is 520 hours old"}
        p3 = {"id": "test-run-needed", "description": "pytest has not been run in 22 days"}
        fp1 = SA.pattern_fingerprint(p1)
        fp2 = SA.pattern_fingerprint(p2)
        fp3 = SA.pattern_fingerprint(p3)
        assert fp1 == fp2 == fp3

    def test_equivalent_graphify_proposals_same_fingerprint(self):
        """Graphify-related proposals should get same fingerprint."""
        p1 = {"id": "graphify-stale", "description": "Graphify refresh is 7 days stale"}
        p2 = {"id": "refresh-graph", "description": "Knowledge graph is overdue for refresh"}
        assert SA.pattern_fingerprint(p1) == SA.pattern_fingerprint(p2)

    def test_equivalent_dedup_proposals_same_fingerprint(self):
        """Catalog dedup proposals should get same fingerprint."""
        p1 = {"id": "catalog-dedup", "description": "Entries have identical signature"}
        p2 = {"id": "remove-duplicates", "description": "Duplicate entries in catalog"}
        assert SA.pattern_fingerprint(p1) == SA.pattern_fingerprint(p2)

    def test_unrelated_proposals_different_fingerprint(self):
        """'criticals' vs 'pytest' should get different fingerprints."""
        p_crit = {"id": "criticals", "description": "402 critical errors are unresolved"}
        p_test = {"id": "pytest", "description": "Test suite is stale"}
        p_graph = {"id": "graphify", "description": "Graphify refresh overdue"}
        fp_crit = SA.pattern_fingerprint(p_crit)
        fp_test = SA.pattern_fingerprint(p_test)
        fp_graph = SA.pattern_fingerprint(p_graph)
        assert fp_crit != fp_test
        assert fp_test != fp_graph
        assert fp_crit != fp_graph

    def test_fallback_uses_proposal_id(self):
        """Unknown category should hash the proposal id."""
        p1 = {"id": "some-unique-issue", "description": "Something novel happened"}
        p2 = {"id": "some-unique-issue", "description": "Completely different wording"}
        p3 = {"id": "other-issue", "description": "Something else entirely"}
        assert SA.pattern_fingerprint(p1) == SA.pattern_fingerprint(p2)
        assert SA.pattern_fingerprint(p1) != SA.pattern_fingerprint(p3)


class TestSuppression:
    """Verify suppression window prevents re-proposals."""

    def test_proposal_suppressed_within_window(self, tmp_path):
        """Fingerprint exists + fresh -> suppressed."""
        sup_dir = tmp_path / "suppressed"
        sup_dir.mkdir()
        fp = "abc123def456"
        rec = {
            "fingerprint": fp,
            "proposed_at": SA.now_utc().isoformat(),
            "description": "test",
        }
        (sup_dir / f"{fp}.json").write_text(json.dumps(rec))

        with patch.object(SA, "SUPPRESSION_DIR", sup_dir):
            assert SA.is_suppressed(fp) is True

    def test_proposal_allowed_after_window_expires(self, tmp_path):
        """Fingerprint exists + 73h old (> 72h window) -> allowed."""
        sup_dir = tmp_path / "suppressed"
        sup_dir.mkdir()
        fp = "abc123def456"
        old_time = (SA.now_utc() - timedelta(hours=73)).isoformat()
        rec = {"fingerprint": fp, "proposed_at": old_time, "description": "test"}
        (sup_dir / f"{fp}.json").write_text(json.dumps(rec))

        with patch.object(SA, "SUPPRESSION_DIR", sup_dir):
            assert SA.is_suppressed(fp) is False

    def test_no_suppression_file_returns_false(self, tmp_path):
        """No file -> not suppressed."""
        sup_dir = tmp_path / "suppressed"
        sup_dir.mkdir()

        with patch.object(SA, "SUPPRESSION_DIR", sup_dir):
            assert SA.is_suppressed("nonexistent123") is False

    def test_record_creates_suppression_file(self, tmp_path):
        """record_suppression creates file with timestamp."""
        sup_dir = tmp_path / "suppressed"

        with patch.object(SA, "SUPPRESSION_DIR", sup_dir):
            SA.record_suppression("fp123", "fix_abc", "test description", dry_run=False)

        assert (sup_dir / "fp123.json").exists()
        data = json.loads((sup_dir / "fp123.json").read_text())
        assert data["fingerprint"] == "fp123"
        assert data["fix_id"] == "fix_abc"
        assert "proposed_at" in data

    def test_record_dry_run_no_file(self, tmp_path):
        """record_suppression with dry_run=True should not create file."""
        sup_dir = tmp_path / "suppressed"
        sup_dir.mkdir()

        with patch.object(SA, "SUPPRESSION_DIR", sup_dir):
            SA.record_suppression("fp123", "fix_abc", "test", dry_run=True)

        assert not (sup_dir / "fp123.json").exists()


class TestCleanSuppression:
    """Verify clean_stale_suppressions removes old entries."""

    def test_old_suppression_files_cleaned(self, tmp_path):
        """Files older than SUPPRESSION_WINDOW_HOURS should be deleted."""
        sup_dir = tmp_path / "suppressed"
        sup_dir.mkdir()
        old_time = (SA.now_utc() - timedelta(hours=80)).isoformat()
        rec = {"fingerprint": "old123", "proposed_at": old_time, "description": "old"}
        (sup_dir / "old123.json").write_text(json.dumps(rec))

        with patch.object(SA, "SUPPRESSION_DIR", sup_dir):
            removed = SA.clean_stale_suppressions()

        assert removed == 1
        assert not (sup_dir / "old123.json").exists()

    def test_fresh_suppression_files_kept(self, tmp_path):
        """Files within SUPPRESSION_WINDOW_HOURS should be kept."""
        sup_dir = tmp_path / "suppressed"
        sup_dir.mkdir()
        fresh_time = (SA.now_utc() - timedelta(hours=10)).isoformat()
        rec = {"fingerprint": "fresh123", "proposed_at": fresh_time, "description": "fresh"}
        (sup_dir / "fresh123.json").write_text(json.dumps(rec))

        with patch.object(SA, "SUPPRESSION_DIR", sup_dir):
            removed = SA.clean_stale_suppressions()

        assert removed == 0
        assert (sup_dir / "fresh123.json").exists()

    def test_nonexistent_dir_returns_zero(self, tmp_path):
        """Non-existent suppression dir should return 0."""
        with patch.object(SA, "SUPPRESSION_DIR", tmp_path / "nope"):
            assert SA.clean_stale_suppressions() == 0
