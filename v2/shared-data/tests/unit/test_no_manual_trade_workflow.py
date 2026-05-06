"""Regression tests: ensure the manual trading workflow stays REMOVED.

The manual SOFI collar / "Amit (SOFI collar + manual trades)" stream was
retired on 2026-05-03 — QuantAI is fully autonomous (Alpha / Beta / Gamma
trading agents only). These assertions guard against accidental
re-introduction of:

  - Amit / SOFI / manual_today / manual_open / manual_closed in eod_summary
  - "Manual Trades" Google Sheet tab in sheets_sync
  - scan_collar_candidates() function or "collars" branch in scan_options
  - MANUAL_ONLY (renamed to REQUIRES_SHARES) in autonomous_execution
  - self_evolution.py or fetch_sofi.py as live scripts (retired only)

Each test reads the file once and asserts the absence of specific patterns.
"""
from __future__ import annotations

from pathlib import Path

import pytest

SCRIPTS = Path("/home/trader/QuantAI/v2/shared-data/scripts")


# ── eod_summary.py ───────────────────────────────────────────────────

class TestEodSummaryClean:
    """eod_summary.py must show only Alpha + Beta + Gamma. No Amit, no SOFI, no manual_*."""

    @pytest.fixture(scope="class")
    def src(self) -> str:
        return (SCRIPTS / "eod_summary.py").read_text()

    @pytest.mark.parametrize("forbidden", [
        "Amit",
        "SOFI",
        "sofi",
        "manual_today",
        "manual_open",
        "manual_closed",
        '"manual"',          # source string match
        "(SOFI collar",
        "No manual trades",
    ])
    def test_no_manual_workflow_strings(self, src, forbidden):
        assert forbidden not in src, (
            f"eod_summary.py contains forbidden token {forbidden!r} — "
            f"the manual trade workflow was retired 2026-05-03. "
            f"Sources are agent_alpha / agent_beta / agent_gamma only."
        )

    def test_three_agents_present(self, src):
        for agent in ("agent_alpha", "agent_beta", "agent_gamma"):
            assert agent in src, f"eod_summary missing {agent} tally"


# ── sheets_sync.py ───────────────────────────────────────────────────

class TestSheetsSyncClean:
    """No Manual Trades tab; agent filter widened to all agent_* prefixes."""

    @pytest.fixture(scope="class")
    def src(self) -> str:
        return (SCRIPTS / "sheets_sync.py").read_text()

    def test_no_manual_trades_tab(self, src):
        assert '"Manual Trades"' not in src, \
            "sheets_sync.py still writes a 'Manual Trades' Google Sheet tab"

    def test_no_manual_filter(self, src):
        assert "manual_trades" not in src, \
            "sheets_sync.py still filters manual_trades from the journal"

    def test_no_amit_in_docstring(self, src):
        first_500 = src[:500].lower()
        assert "amit" not in first_500, \
            "sheets_sync.py docstring still references 'Amit'"

    def test_agent_filter_uses_startswith(self, src):
        # We widened the filter so legacy "agent" + current "agent_alpha/_beta/_gamma" all match
        assert 'startswith("agent")' in src, (
            "sheets_sync.py agent_trades filter must match all agent sources "
            "(.startswith(\"agent\")). Older filter source==\"agent\" missed alpha/beta/gamma."
        )


# ── scan_options.py ──────────────────────────────────────────────────

class TestScanOptionsClean:
    """No collar scan function; no 'collars' mode branch."""

    @pytest.fixture(scope="class")
    def src(self) -> str:
        return (SCRIPTS / "scan_options.py").read_text()

    def test_no_collar_scanner_function(self, src):
        assert "def scan_collar_candidates" not in src, \
            "scan_collar_candidates() was retired — manual collar scanning is dead code"

    def test_no_collars_mode_branch(self, src):
        assert 'mode in ("all", "collars")' not in src, \
            "scan_options.py still has the 'collars' mode branch"

    def test_no_amit_manual_only_note(self, src):
        assert "Amit manual only" not in src, \
            "scan_options.py contains stale 'Amit manual only' annotation"


# ── autonomous_execution.py ──────────────────────────────────────────

class TestAutonomousExecutionGuard:
    """Defensive guard preserved as REQUIRES_SHARES (renamed from MANUAL_ONLY)."""

    @pytest.fixture(scope="class")
    def src(self) -> str:
        return (SCRIPTS / "autonomous_execution.py").read_text()

    def test_uses_new_name(self, src):
        assert "REQUIRES_SHARES" in src, \
            "Defensive guard set must be named REQUIRES_SHARES"

    def test_no_old_name(self, src):
        assert "MANUAL_ONLY" not in src, \
            "Stale MANUAL_ONLY symbol still in autonomous_execution.py"

    def test_no_amit_in_message(self, src):
        assert "Amit executes manually" not in src, \
            "Old 'Amit executes manually' rejection message still present"

    def test_new_message_references_share_ownership(self, src):
        assert "requires share ownership" in src, (
            "REQUIRES_SHARES rejection message should explain WHY the strategy "
            "is rejected (requires share ownership; agents trade defined-risk only)"
        )

    def test_guard_still_blocks_four_strategies(self, src):
        # Behavior must be identical: same 4 strategies still rejected
        for strategy in ("covered_call", "collar", "cash_secured_put", "covered_strangle"):
            assert strategy in src, \
                f"REQUIRES_SHARES set must still include {strategy!r}"


# ── Retired SOFI-only scripts ────────────────────────────────────────

class TestRetiredScripts:
    """self_evolution.py and fetch_sofi.py must be retired (renamed), not present as live .py."""

    def test_self_evolution_retired(self):
        live = SCRIPTS / "self_evolution.py"
        assert not live.exists(), \
            "self_evolution.py is still live — should be self_evolution.py.retired.YYYY-MM-DD"

    def test_fetch_sofi_retired(self):
        live = SCRIPTS / "fetch_sofi.py"
        assert not live.exists(), \
            "fetch_sofi.py is still live — should be fetch_sofi.py.retired.YYYY-MM-DD"

    def test_retired_artifacts_preserved(self):
        # We retire (rename), don't delete. At least one of each .retired.* file should exist.
        evolution_retired = list(SCRIPTS.glob("self_evolution.py.retired.*"))
        sofi_retired = list(SCRIPTS.glob("fetch_sofi.py.retired.*"))
        assert evolution_retired, "no self_evolution.py.retired.* archive present"
        assert sofi_retired, "no fetch_sofi.py.retired.* archive present"

    def test_no_live_importers_of_retired_scripts(self):
        # Scan all live scripts for "import self_evolution" or "from self_evolution"
        # or shell invocations of fetch_sofi.py.
        import re
        bad_patterns = [
            re.compile(r"^\s*import\s+self_evolution\b", re.MULTILINE),
            re.compile(r"^\s*from\s+self_evolution\b", re.MULTILINE),
            re.compile(r"^\s*import\s+fetch_sofi\b", re.MULTILINE),
            re.compile(r"^\s*from\s+fetch_sofi\b", re.MULTILINE),
        ]
        offenders = []
        for p in SCRIPTS.glob("*.py"):
            if p.name.endswith(".bak") or ".retired." in p.name:
                continue
            text = p.read_text()
            for pat in bad_patterns:
                if pat.search(text):
                    offenders.append(f"{p.name} matches {pat.pattern}")
        assert not offenders, (
            f"Live scripts still import retired modules:\n  "
            + "\n  ".join(offenders)
        )
