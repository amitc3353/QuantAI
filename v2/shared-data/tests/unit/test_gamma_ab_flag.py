"""Feature flag tests for the Gamma 4-arm A/B/C/D test (commit 3).

Verify that ``GAMMA_AB_TEST_ENABLED`` cleanly gates per-arm dispatch:

* Flag OFF → run_scan/run_execute fire (existing single-arm production behavior)
* Flag ON  → run_scan_4arm/run_execute_4arm fire
* Flag value is read from environment at module-import time

These are integration-shape tests: they verify the dispatch wiring, not the
internals of run_scan_4arm (those are covered in test_gamma_arm_orchestration.py).
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))


def _reload_gamma_agent_with_env(env_overrides: dict[str, str]):
    """Reload gamma_agent with a fresh environment so the module-level
    GAMMA_AB_TEST_ENABLED flag is re-evaluated."""
    # Clear any cached gamma_agent
    if "gamma_agent" in sys.modules:
        del sys.modules["gamma_agent"]
    with patch.dict(os.environ, env_overrides, clear=False):
        # Patch sys.argv too so we don't accidentally trip --scan / --execute
        # on the test-runner's own argv
        with patch.object(sys, "argv", ["gamma_agent.py"]):
            module = importlib.import_module("gamma_agent")
    return module


class TestFeatureFlag:
    def test_flag_off_by_default(self):
        """If env doesn't set GAMMA_AB_TEST_ENABLED, default is OFF."""
        # Remove the var if present
        env = {k: v for k, v in os.environ.items() if k != "GAMMA_AB_TEST_ENABLED"}
        with patch.dict(os.environ, env, clear=True):
            with patch.object(sys, "argv", ["gamma_agent.py"]):
                if "gamma_agent" in sys.modules:
                    del sys.modules["gamma_agent"]
                import gamma_agent
                assert gamma_agent.GAMMA_AB_TEST_ENABLED is False

    def test_flag_on_via_env(self):
        mod = _reload_gamma_agent_with_env({"GAMMA_AB_TEST_ENABLED": "1"})
        assert mod.GAMMA_AB_TEST_ENABLED is True

    def test_flag_off_via_env(self):
        mod = _reload_gamma_agent_with_env({"GAMMA_AB_TEST_ENABLED": "0"})
        assert mod.GAMMA_AB_TEST_ENABLED is False

    def test_flag_only_truthy_for_exactly_1(self):
        """``GAMMA_AB_TEST_ENABLED=true`` (non-1) → flag stays OFF.
        Strict equality with '1' avoids accidental enablement."""
        for val in ("0", "true", "True", "yes", "on", "", "x"):
            mod = _reload_gamma_agent_with_env({"GAMMA_AB_TEST_ENABLED": val})
            assert mod.GAMMA_AB_TEST_ENABLED is False, (
                f"Unexpected truthy: {val!r}"
            )


class TestMainDispatch:
    """Verify main() routes to the correct function based on flag state."""

    def test_flag_off_scan_uses_single_ranker(self):
        """Flag OFF + --scan → run_scan() called, run_scan_4arm NOT called."""
        mod = _reload_gamma_agent_with_env({"GAMMA_AB_TEST_ENABLED": "0"})
        with patch.object(mod, "SCAN", True), \
             patch.object(mod, "run_scan", return_value=0) as legacy, \
             patch.object(mod, "run_scan_4arm", return_value=0) as new:
            rc = mod.main()
        legacy.assert_called_once()
        new.assert_not_called()
        assert rc == 0

    def test_flag_on_scan_routes_to_4arm(self):
        mod = _reload_gamma_agent_with_env({"GAMMA_AB_TEST_ENABLED": "1"})
        with patch.object(mod, "SCAN", True), \
             patch.object(mod, "run_scan", return_value=0) as legacy, \
             patch.object(mod, "run_scan_4arm", return_value=0) as new:
            rc = mod.main()
        legacy.assert_not_called()
        new.assert_called_once()
        assert rc == 0

    def test_flag_off_execute_uses_single_ranker(self):
        mod = _reload_gamma_agent_with_env({"GAMMA_AB_TEST_ENABLED": "0"})
        with patch.object(mod, "EXECUTE", True), \
             patch.object(mod, "run_execute", return_value=0) as legacy, \
             patch.object(mod, "run_execute_4arm", return_value=0) as new:
            rc = mod.main()
        legacy.assert_called_once()
        new.assert_not_called()
        assert rc == 0

    def test_flag_on_execute_routes_to_4arm(self):
        mod = _reload_gamma_agent_with_env({"GAMMA_AB_TEST_ENABLED": "1"})
        with patch.object(mod, "EXECUTE", True), \
             patch.object(mod, "run_execute", return_value=0) as legacy, \
             patch.object(mod, "run_execute_4arm", return_value=0) as new:
            rc = mod.main()
        legacy.assert_not_called()
        new.assert_called_once()
        assert rc == 0

    def test_verify_spreads_unaffected_by_flag(self):
        """--verify-spreads dispatches to run_verify_spreads regardless of flag.
        The spread verifier is shared across flag states — it scopes to the
        full universe, not per-arm."""
        for flag_value in ("0", "1"):
            mod = _reload_gamma_agent_with_env(
                {"GAMMA_AB_TEST_ENABLED": flag_value}
            )
            with patch.object(mod, "VERIFY_SPREADS", True), \
                 patch.object(mod, "run_verify_spreads", return_value=0) as f:
                rc = mod.main()
            f.assert_called_once()
            assert rc == 0

    def test_reset_experiment_takes_priority_over_scan(self):
        """--reset-experiment + --scan → reset wins. Operator subcommands
        always take priority over normal scheduled flows."""
        mod = _reload_gamma_agent_with_env({"GAMMA_AB_TEST_ENABLED": "1"})
        with patch.object(mod, "RESET_EXPERIMENT", True), \
             patch.object(mod, "SCAN", True), \
             patch.object(mod, "run_reset_experiment", return_value=0) as r, \
             patch.object(mod, "run_scan_4arm", return_value=99) as s, \
             patch.object(mod, "run_scan", return_value=99) as legacy:
            rc = mod.main()
        r.assert_called_once()
        s.assert_not_called()
        legacy.assert_not_called()
        assert rc == 0

    def test_promote_arm_takes_priority_over_scan(self):
        mod = _reload_gamma_agent_with_env({"GAMMA_AB_TEST_ENABLED": "1"})
        with patch.object(mod, "PROMOTE_ARM", True), \
             patch.object(mod, "SCAN", True), \
             patch.object(mod, "run_promote_arm", return_value=0) as p, \
             patch.object(mod, "run_scan_4arm", return_value=99) as s:
            rc = mod.main()
        p.assert_called_once()
        s.assert_not_called()
        assert rc == 0


class TestFlagOffBytewiseEquivalence:
    """When the flag is OFF, run_scan() and run_execute() are NOT modified.
    They are the same functions that ran in pre-experiment Gamma. This is
    enforced by NOT touching their definitions in commit 3."""

    def test_run_scan_not_modified(self):
        """Source-level check: run_scan() is the original single-arm function."""
        mod = _reload_gamma_agent_with_env({"GAMMA_AB_TEST_ENABLED": "0"})
        # The function is defined and callable
        assert callable(mod.run_scan)
        # It does not reference any arm_state symbols (single-arm path)
        import inspect
        src = inspect.getsource(mod.run_scan)
        # The 4-arm-only modules must not appear in the legacy path
        assert "arm_state" not in src
        assert "rankers" not in src
        assert "filter_setups_for_arm" not in src

    def test_run_execute_not_modified(self):
        mod = _reload_gamma_agent_with_env({"GAMMA_AB_TEST_ENABLED": "0"})
        assert callable(mod.run_execute)
        import inspect
        src = inspect.getsource(mod.run_execute)
        assert "arm_state" not in src
        assert "rankers" not in src
        assert "filter_setups_for_arm" not in src
        # Existing helper used in legacy path is still referenced
        assert "filter_setups" in src
