"""Regression test: weekly_synthesis.py must hold an exclusive run-lock.

Before 2026-05-04: concurrent invocations stacked → 6+ Discord posts where
3 should have appeared. After: a non-blocking flock keeps the second
invocation from doing any work — it exits silently with code 0.

This test imports the script's RunLock class and verifies the contract:
1. First acquire succeeds.
2. Second acquire (before first releases) raises BlockingIOError.
3. After release, a fresh acquire succeeds again.
"""
from __future__ import annotations

import fcntl
import os
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))


class TestRunLock:
    @pytest.fixture
    def lock_path(self, tmp_path):
        return tmp_path / "weekly_synthesis_test.lock"

    def test_lock_class_exists(self):
        import weekly_synthesis
        assert hasattr(weekly_synthesis, "RunLock"), (
            "weekly_synthesis must define RunLock — concurrent invocations "
            "would otherwise double-post to Discord"
        )

    def test_first_acquire_succeeds(self, lock_path):
        import weekly_synthesis
        with weekly_synthesis.RunLock(lock_path) as lock:
            assert lock.fd is not None
            assert lock_path.exists()

    def test_second_acquire_raises_blocking(self, lock_path):
        """A second concurrent attempt must NOT block — non-blocking flock."""
        import weekly_synthesis

        # First holder
        first = weekly_synthesis.RunLock(lock_path)
        first.__enter__()
        try:
            # Manually simulate what the lock contender does — try to grab the lock
            # without going through __enter__ (which calls sys.exit(0))
            with open(lock_path, "w") as fd:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            first.__exit__()

    def test_release_then_reacquire(self, lock_path):
        import weekly_synthesis

        with weekly_synthesis.RunLock(lock_path):
            pass  # release on context exit

        # Fresh acquire should work
        with weekly_synthesis.RunLock(lock_path) as lock:
            assert lock.fd is not None

    def test_lock_path_is_in_tmp(self):
        """Lock should live in /tmp so it doesn't bloat the repo or leak across reboots."""
        import weekly_synthesis
        assert str(weekly_synthesis.LOCK_FILE).startswith("/tmp/"), (
            f"LOCK_FILE should be in /tmp/, got {weekly_synthesis.LOCK_FILE}"
        )

    def test_main_skips_lock_when_dry_run(self, monkeypatch, lock_path, capsys):
        """Dry-run path bypasses the lock so devs can iterate freely."""
        import weekly_synthesis
        monkeypatch.setattr(weekly_synthesis, "LOCK_FILE", lock_path)

        # Hold the lock externally
        with open(lock_path, "w") as fd:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

            # Run main in dry-run — should bypass lock entirely and run synthesize
            monkeypatch.setattr(sys, "argv", [
                "weekly_synthesis.py", "--dry-run", "--week-start", "2026-04-27",
            ])
            # synthesize() reads the journal; mock it to avoid hitting production data
            calls = []
            def fake_synth(week_start, dry_run=False):
                calls.append((week_start, dry_run))
                return 0
            monkeypatch.setattr(weekly_synthesis, "synthesize", fake_synth)

            rc = weekly_synthesis.main()
            assert rc == 0
            assert len(calls) == 1, "dry-run main should still call synthesize once"
            assert calls[0][1] is True, "dry-run flag should propagate"

            fcntl.flock(fd, fcntl.LOCK_UN)
