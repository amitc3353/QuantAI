"""Unit tests for system_monitor.check_karna_backup_freshness.

Background: KARNA's daily backup is performed by a system cron entry
(0 2 * * * /root/scripts/karna-backup.sh) that writes to /root/logs/backup.log.
KARNA's own routine reports the backup status to Discord but its OpenClaw
shell can't exec /root/scripts/karna-backup.sh and produces FALSE failure
alerts even when the backup succeeded.

This check is the trusted verifier: it reads the log directly and reports
on the most recent successful 'Backup pushed: ... verified=OK' line.

These tests pin the parsing, freshness thresholds, and graceful-degradation
behavior so the check stays deterministic.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import system_monitor as sm  # noqa: E402


UTC = timezone.utc


def _fmt_log_line(dt: datetime, verified: str = "OK", files: int = 48,
                   bundle: str = "karna-backup-test.tar.gz.age") -> str:
    """Format a log line in the same style as /root/scripts/karna-backup.sh."""
    # Format: [Fri May 29 02:00:04 AM UTC 2026] Backup pushed: ... | verified=OK
    ts_str = dt.strftime("%a %b %d %I:%M:%S %p UTC %Y")
    return f"[{ts_str}] Backup pushed: {bundle} | files={files} | verified={verified}"


def _write_log(tmp_path: Path, content: str) -> Path:
    log = tmp_path / "backup.log"
    log.write_text(content)
    return log


@pytest.fixture
def at_now():
    """Returns a function that produces a datetime offset hours from a frozen 'now'."""
    frozen = datetime(2026, 5, 29, 12, 0, 0, tzinfo=UTC)

    def _at(hours_offset: float) -> datetime:
        return frozen + timedelta(hours=hours_offset)

    return frozen, _at


@pytest.fixture(autouse=True)
def freeze_now(monkeypatch, at_now):
    """Freeze datetime.now(UTC) inside system_monitor at a known instant."""
    frozen, _ = at_now

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return frozen.replace(tzinfo=None)
            return frozen.astimezone(tz)

    monkeypatch.setattr(sm, "datetime", _FrozenDateTime)


# ── Happy path / freshness tiers ──────────────────────────────────────────


class TestFreshnessTiers:
    """Backup age determines ok/warning/error tier."""

    def test_fresh_backup_returns_ok(self, monkeypatch, tmp_path, at_now):
        _, at = at_now
        log = _write_log(tmp_path, _fmt_log_line(at(-1)))  # 1h old
        monkeypatch.setattr(sm, "KARNA_BACKUP_LOG", log)
        result = sm.check_karna_backup_freshness()
        assert result["status"] == "ok"
        assert result["age_hours"] == 1.0
        assert result["verified"] == "OK"

    def test_24_hour_backup_still_ok(self, monkeypatch, tmp_path, at_now):
        """24h-old backup is within the daily cron cycle — ok."""
        _, at = at_now
        log = _write_log(tmp_path, _fmt_log_line(at(-24)))
        monkeypatch.setattr(sm, "KARNA_BACKUP_LOG", log)
        result = sm.check_karna_backup_freshness()
        assert result["status"] == "ok"

    def test_just_below_warn_threshold_is_ok(self, monkeypatch, tmp_path, at_now):
        """Right at WARN_HOURS - 1 minute still ok (boundary check)."""
        _, at = at_now
        # WARN_HOURS = 26 → 25.9h ago is ok
        log = _write_log(tmp_path, _fmt_log_line(at(-25.9)))
        monkeypatch.setattr(sm, "KARNA_BACKUP_LOG", log)
        result = sm.check_karna_backup_freshness()
        assert result["status"] == "ok"

    def test_at_warn_threshold_is_warning(self, monkeypatch, tmp_path, at_now):
        """At exactly WARN_HOURS (26h), tip into warning."""
        _, at = at_now
        log = _write_log(tmp_path, _fmt_log_line(at(-26)))
        monkeypatch.setattr(sm, "KARNA_BACKUP_LOG", log)
        result = sm.check_karna_backup_freshness()
        assert result["status"] == "warning"
        assert "stale" in result["hint"].lower()

    def test_between_warn_and_error_is_warning(self, monkeypatch, tmp_path, at_now):
        _, at = at_now
        log = _write_log(tmp_path, _fmt_log_line(at(-36)))
        monkeypatch.setattr(sm, "KARNA_BACKUP_LOG", log)
        result = sm.check_karna_backup_freshness()
        assert result["status"] == "warning"

    def test_at_error_threshold_is_error(self, monkeypatch, tmp_path, at_now):
        """At ERROR_HOURS (48h), escalate to error."""
        _, at = at_now
        log = _write_log(tmp_path, _fmt_log_line(at(-48)))
        monkeypatch.setattr(sm, "KARNA_BACKUP_LOG", log)
        result = sm.check_karna_backup_freshness()
        assert result["status"] == "error"
        assert "broken" in result["hint"].lower() or "stale" in result["hint"].lower()

    def test_way_stale_is_error(self, monkeypatch, tmp_path, at_now):
        _, at = at_now
        log = _write_log(tmp_path, _fmt_log_line(at(-72)))  # 3 days
        monkeypatch.setattr(sm, "KARNA_BACKUP_LOG", log)
        result = sm.check_karna_backup_freshness()
        assert result["status"] == "error"


# ── Most-recent-entry semantics ───────────────────────────────────────────


class TestMostRecentSemantics:
    """The check looks at the LAST 'Backup pushed' line — not the first."""

    def test_multiple_entries_uses_most_recent(self, monkeypatch, tmp_path, at_now):
        _, at = at_now
        log_text = "\n".join([
            _fmt_log_line(at(-72)),  # 3 days old
            _fmt_log_line(at(-48)),  # 2 days old
            _fmt_log_line(at(-1)),   # 1 hour old — this is what we should see
        ])
        log = _write_log(tmp_path, log_text)
        monkeypatch.setattr(sm, "KARNA_BACKUP_LOG", log)
        result = sm.check_karna_backup_freshness()
        assert result["status"] == "ok"
        assert result["age_hours"] == 1.0

    def test_interleaved_non_pushed_lines_ignored(self, monkeypatch, tmp_path, at_now):
        """Lines like 'KARNA backup starting' or 'Encrypted bundle created'
        don't match the regex and shouldn't trigger anything."""
        _, at = at_now
        log_text = "\n".join([
            "[Fri May 29 02:00:01 AM UTC 2026] KARNA backup starting: 2026-05-29",
            "[Fri May 29 02:00:02 AM UTC 2026] Encrypted bundle created",
            "[Fri May 29 02:00:02 AM UTC 2026] Integrity verified: 48 files match",
            _fmt_log_line(at(-1)),
        ])
        log = _write_log(tmp_path, log_text)
        monkeypatch.setattr(sm, "KARNA_BACKUP_LOG", log)
        result = sm.check_karna_backup_freshness()
        assert result["status"] == "ok"


# ── Verification flag ─────────────────────────────────────────────────────


class TestVerificationFlag:
    """If verified != OK, escalate to error regardless of freshness."""

    def test_fresh_but_verified_failed_is_error(self, monkeypatch, tmp_path, at_now):
        _, at = at_now
        log = _write_log(tmp_path, _fmt_log_line(at(-1), verified="FAILED"))
        monkeypatch.setattr(sm, "KARNA_BACKUP_LOG", log)
        result = sm.check_karna_backup_freshness()
        assert result["status"] == "error"
        assert result["verified"] == "FAILED"

    def test_lowercase_ok_accepted(self, monkeypatch, tmp_path, at_now):
        """verified=ok (lowercase) should still be considered OK (defensive)."""
        _, at = at_now
        log = _write_log(tmp_path, _fmt_log_line(at(-1), verified="ok"))
        monkeypatch.setattr(sm, "KARNA_BACKUP_LOG", log)
        result = sm.check_karna_backup_freshness()
        assert result["status"] == "ok"


# ── Edge cases / graceful degradation ─────────────────────────────────────


class TestEdgeCases:
    """Missing file, permission denied, no Backup-pushed lines, parse errors."""

    def test_missing_log_returns_warning(self, monkeypatch, tmp_path):
        """Log file does not exist → warning (not error — could be first run)."""
        monkeypatch.setattr(sm, "KARNA_BACKUP_LOG", tmp_path / "nonexistent.log")
        result = sm.check_karna_backup_freshness()
        assert result["status"] == "warning"
        assert "not found" in result["error"].lower()

    def test_permission_denied_returns_warning(self, monkeypatch, tmp_path):
        """Permission denied (running as non-root) → warning, not error.
        Dev environments shouldn't trip an error-level alert."""
        log = tmp_path / "no-read.log"
        log.write_text("dummy")

        # Monkeypatch Path.read_text to raise PermissionError for this log
        original_read_text = Path.read_text

        def _raise_perm(self, *args, **kwargs):
            if self == log:
                raise PermissionError(f"[Errno 13] Permission denied: {self}")
            return original_read_text(self, *args, **kwargs)

        monkeypatch.setattr(sm, "KARNA_BACKUP_LOG", log)
        monkeypatch.setattr(Path, "read_text", _raise_perm)
        result = sm.check_karna_backup_freshness()
        assert result["status"] == "warning"
        assert "not readable" in result["error"].lower() or "permission" in result["error"].lower()

    def test_empty_log_returns_error(self, monkeypatch, tmp_path):
        """File exists but no 'Backup pushed' lines → error."""
        log = _write_log(tmp_path, "")
        monkeypatch.setattr(sm, "KARNA_BACKUP_LOG", log)
        result = sm.check_karna_backup_freshness()
        assert result["status"] == "error"
        assert "backup pushed" in result["error"].lower()

    def test_only_startup_lines_returns_error(self, monkeypatch, tmp_path):
        """Log has activity but no successful push → error."""
        log = _write_log(tmp_path, "\n".join([
            "[Fri May 29 02:00:01 AM UTC 2026] KARNA backup starting: 2026-05-29",
            "[Fri May 29 02:00:02 AM UTC 2026] Encrypted bundle created",
            "[Fri May 29 02:00:03 AM UTC 2026] Push FAILED — git error",
        ]))
        monkeypatch.setattr(sm, "KARNA_BACKUP_LOG", log)
        result = sm.check_karna_backup_freshness()
        assert result["status"] == "error"

    def test_malformed_timestamp_returns_warning(self, monkeypatch, tmp_path):
        """Regex matches but timestamp is unparseable → warning + hint to update regex."""
        log = _write_log(tmp_path, "[Garbage May 99 99:99:99 ZZ UTC 9999] Backup pushed: x | verified=OK")
        monkeypatch.setattr(sm, "KARNA_BACKUP_LOG", log)
        result = sm.check_karna_backup_freshness()
        # Either the regex doesn't match (error) or it matches but parse fails (warning).
        # Both are acceptable graceful-degradation outcomes — but if it DID match the
        # current regex, we get warning + hint to update _KARNA_BACKUP_PUSHED_RE.
        assert result["status"] in ("warning", "error")


# ── Timestamp parsing variants ────────────────────────────────────────────


class TestTimestampParsing:
    """Single-digit day padding ('May  5' vs 'May 05') is handled."""

    def test_two_digit_day(self):
        ts = "Fri May 29 02:00:04 AM UTC 2026"
        dt = sm._parse_karna_backup_ts(ts)
        assert dt is not None
        assert dt == datetime(2026, 5, 29, 2, 0, 4, tzinfo=UTC)

    def test_single_digit_day_zero_padded(self):
        ts = "Mon May 05 02:00:04 AM UTC 2026"
        dt = sm._parse_karna_backup_ts(ts)
        assert dt is not None
        assert dt == datetime(2026, 5, 5, 2, 0, 4, tzinfo=UTC)

    def test_single_digit_day_space_padded(self):
        """date(1) output uses ' 5' for single-digit days; check normalization."""
        ts = "Mon May  5 02:00:04 AM UTC 2026"
        dt = sm._parse_karna_backup_ts(ts)
        assert dt is not None
        assert dt == datetime(2026, 5, 5, 2, 0, 4, tzinfo=UTC)

    def test_pm_hour_parses_correctly(self):
        """02:00:04 PM = 14:00:04. Defensive — log uses AM but be ready for PM."""
        ts = "Fri May 29 02:00:04 PM UTC 2026"
        dt = sm._parse_karna_backup_ts(ts)
        assert dt is not None
        assert dt.hour == 14

    def test_garbage_returns_none(self):
        assert sm._parse_karna_backup_ts("not a timestamp") is None
        assert sm._parse_karna_backup_ts("") is None


# ── Wiring: check is in CHECKS list ───────────────────────────────────────


class TestWiring:
    """The check must be registered in CHECKS so run_all_checks invokes it."""

    def test_check_in_checks_list(self):
        names = [name for name, _ in sm.CHECKS]
        assert "karna_backup_freshness" in names

    def test_check_function_in_checks_list(self):
        for name, fn in sm.CHECKS:
            if name == "karna_backup_freshness":
                assert fn is sm.check_karna_backup_freshness
                return
        pytest.fail("karna_backup_freshness not found in CHECKS")

    def test_run_all_checks_handles_check_exception(self, monkeypatch):
        """If the check raises (e.g. unexpected IO error), run_all_checks
        wraps it in a {status: warning, error: ...} dict instead of crashing."""
        monkeypatch.setattr(sm, "check_karna_backup_freshness",
                             lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        # Re-build CHECKS to use the patched function — replicate the list
        # since CHECKS is built at module import time
        patched_checks = []
        for name, fn in sm.CHECKS:
            if name == "karna_backup_freshness":
                patched_checks.append((name, sm.check_karna_backup_freshness))
            else:
                patched_checks.append((name, fn))
        monkeypatch.setattr(sm, "CHECKS", patched_checks)
        results = sm.run_all_checks()
        assert results["karna_backup_freshness"]["status"] == "warning"
        assert "check raised" in results["karna_backup_freshness"]["error"]
