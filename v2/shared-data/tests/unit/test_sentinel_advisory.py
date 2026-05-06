"""Regression tests for Sentinel advisory acknowledgment path (added 2026-05-04).

Before this change: a propose_wait card with no diff and no shell_commands,
when ✅'d on Discord, was marked "applied" with an empty receipt — making
day-one apply look like 4 fixes succeeded when in reality 0 actual work
got done.

After: such cards are detected as 'advisory' by `_is_advisory()`, returned
as outcome='acknowledged' from `execute_proposal`, counted as `acknowledged`
(not `applied`), produce no receipt, and post a `📋 acknowledged advisory`
log message instead of `✅ applied`.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import sentinel_agent as SA


class TestIsAdvisory:
    def test_no_diff_no_cmds_is_advisory(self):
        rec = {"diff": "", "shell_commands": []}
        assert SA._is_advisory(rec) is True

    def test_with_diff_is_not_advisory(self):
        rec = {"diff": "--- a/f\n+++ b/f\n", "shell_commands": []}
        assert SA._is_advisory(rec) is False

    def test_with_shell_commands_is_not_advisory(self):
        rec = {"diff": "", "shell_commands": ["echo hi"]}
        assert SA._is_advisory(rec) is False

    def test_whitespace_only_diff_is_advisory(self):
        rec = {"diff": "   \n  \n", "shell_commands": []}
        assert SA._is_advisory(rec) is True

    def test_missing_keys_is_advisory(self):
        rec = {}  # nothing to do at all
        assert SA._is_advisory(rec) is True


class TestExecuteProposalAdvisory:
    """execute_proposal must short-circuit advisories with outcome='acknowledged'."""

    def test_advisory_returns_acknowledged_outcome(self):
        rec = {
            "fix_id": "abc12345",
            "fix_class": "propose_wait",
            "description": "Operator should manually verify thing X",
            "diff": "",
            "shell_commands": [],
        }
        outcome, receipt = SA.execute_proposal(rec, dry_run=True)
        assert outcome == "acknowledged"
        assert receipt is None, "no receipt for advisory"

    def test_real_proposal_returns_applied(self):
        # diff/shell case still works — outcome is "applied" or "failed"
        rec = {
            "fix_id": "def67890",
            "fix_class": "safe_auto",
            "description": "real fix",
            "diff": "",
            "shell_commands": ["echo hello"],
        }
        outcome, receipt = SA.execute_proposal(rec, dry_run=True)
        assert outcome == "applied"
        assert receipt is not None
        assert receipt["fix_id"] == "def67890"
