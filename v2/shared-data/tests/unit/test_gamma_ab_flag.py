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


# Path of the production .env auto-load happens during gamma_agent import; we
# block it in tests by short-circuiting the .env file existence check.
# Without this, any .env line for GAMMA_AB_TEST_ENABLED bleeds into the test
# environment after the .env flip activates the experiment in production.
_DOTENV_PATHS = (
    "/home/trader/QuantAI/.env",
    "/root/quantai-v2/.env",
)


def _reload_gamma_agent_with_env(env_overrides: dict[str, str],
                                  block_dotenv: bool = True):
    """Reload gamma_agent with a fresh environment so the module-level
    GAMMA_AB_TEST_ENABLED flag is re-evaluated.

    When ``block_dotenv=True`` (default), the .env auto-loader inside
    gamma_agent.py is neutered for the duration of the import so tests can
    deterministically control the flag value regardless of what's on disk.
    """
    if "gamma_agent" in sys.modules:
        del sys.modules["gamma_agent"]

    # Wrap Path.exists so the two known .env file lookups return False during
    # import. Other Path.exists calls are unaffected.
    original_exists = Path.exists

    def _filtered_exists(self):
        if block_dotenv and str(self) in _DOTENV_PATHS:
            return False
        return original_exists(self)

    with patch.dict(os.environ, env_overrides, clear=False):
        with patch.object(sys, "argv", ["gamma_agent.py"]):
            with patch.object(Path, "exists", _filtered_exists):
                module = importlib.import_module("gamma_agent")
    return module


class TestFeatureFlag:
    def test_flag_off_by_default(self):
        """With no env override AND no .env source providing the flag,
        the code default is OFF."""
        # Strip the var and block .env auto-load so neither source can set it.
        env = {k: v for k, v in os.environ.items()
                if k != "GAMMA_AB_TEST_ENABLED"}
        if "gamma_agent" in sys.modules:
            del sys.modules["gamma_agent"]

        original_exists = Path.exists

        def _filtered_exists(self):
            if str(self) in _DOTENV_PATHS:
                return False
            return original_exists(self)

        with patch.dict(os.environ, env, clear=True):
            with patch.object(sys, "argv", ["gamma_agent.py"]):
                with patch.object(Path, "exists", _filtered_exists):
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
        Strict equality with '1' avoids accidental enablement.
        .env auto-load is blocked so empty-string and other non-1 values
        don't get silently overridden by the production .env."""
        for val in ("0", "true", "True", "yes", "on", "", "x"):
            mod = _reload_gamma_agent_with_env(
                {"GAMMA_AB_TEST_ENABLED": val}, block_dotenv=True,
            )
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
