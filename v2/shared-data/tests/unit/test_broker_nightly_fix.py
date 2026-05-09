"""Tests for the 2026-05-09 broker nightly-restart fix.

Background: IB Gateway has a nightly restart cycle. The post-restart
reconnection lag extends from ~00:15 ET to ~01:15-01:30 ET, well past the
old 23:30-00:15 ET refuse-window. Every collector that fired during that
hour got 3-retry timeout cascades. Compounding this, ib_insync's per-attempt
clientId=1 collision (Error 326) caused the second/third retry to fail
even when the gateway had recovered.

Two fix layers:
  A. Widen `_is_in_restart_window()` from 23:30-00:15 ET to 23:30-01:30 ET.
  B. Stagger clientId across the 3 retries (1 / 1+1 / 1+2) so a stale
     clientId from a half-open socket doesn't kill the next retry.

Plus a small severity downgrade: the in-window refuse log was at ERROR
level even though refusing-during-window is the correct, expected behavior.
Downgrade to INFO so it stops escalating in the dashboard error catalog.

These tests are written BEFORE the fix and should FAIL initially, PASS
after the fix is applied.
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))


# ── Bootstrap pattern (matches test_trading_surgery_bugs.py) ────────────

class _FakeBrokerBase:
    pass


def _make_broker_module():
    mod = MagicMock()
    mod.BrokerBase = _FakeBrokerBase
    mod.DRY_RUN_SENTINEL = {"DRY_RUN": True}
    return mod


_BROKER_MOD = _make_broker_module()
sys.modules.setdefault("broker", _BROKER_MOD)

import _broker_ibkr as _bib  # noqa: E402

ET = ZoneInfo("America/New_York")


def _et(hour: int, minute: int) -> datetime:
    """Build a deterministic America/New_York datetime for window tests.
    Uses 2026-05-09 (a normal DST date) so the offset is stable.
    """
    return datetime(2026, 5, 9, hour, minute, 0, tzinfo=ET)


# ─────────────────────────────────────────────────────────────────────────
# Layer A — _is_in_restart_window covers 23:30-01:30 ET (was 23:30-00:15)
# ─────────────────────────────────────────────────────────────────────────

class TestRestartWindowWidened:
    """The widened window must catch the post-restart reconnection lag.

    Boundary points (all ET):
      - 23:29 → OUT (just before window starts)
      - 23:30 → IN  (window start, unchanged)
      - 00:00 → IN  (midnight, unchanged)
      - 00:15 → IN  (was OLD boundary; now IN since window extends past)
      - 00:45 → IN  (NEW — was OUT pre-fix; this is when Error 1100 fires)
      - 01:00 → IN  (NEW — was OUT pre-fix; this is when port-refused fires)
      - 01:29 → IN  (NEW — last minute of window)
      - 01:30 → OUT (window end)
      - 02:00 → OUT (well past window)
      - 12:00 → OUT (midday — sanity)
    """

    def test_just_before_window_is_out(self):
        assert _bib._is_in_restart_window(_et(23, 29)) is False

    def test_window_start_2330_is_in(self):
        assert _bib._is_in_restart_window(_et(23, 30)) is True

    def test_midnight_is_in(self):
        assert _bib._is_in_restart_window(_et(0, 0)) is True

    def test_old_boundary_0015_is_in(self):
        # Was the END of the old window (OUT pre-fix becomes IN post-fix is
        # not the test — 0:15 was always IN in old code: `n.minute < 15`
        # means 0:00-0:14 was IN, 0:15 was OUT). Post-fix: 0:15 IS IN.
        assert _bib._is_in_restart_window(_et(0, 15)) is True

    def test_0045_is_in_post_fix(self):
        """KEY: at 00:45 ET, Error 1100 connectivity-lost fired historically.
        Pre-fix this was OUT of window (collectors retried, got timeouts).
        Post-fix this MUST be IN."""
        assert _bib._is_in_restart_window(_et(0, 45)) is True

    def test_0100_is_in_post_fix(self):
        """KEY: at 01:00 ET the port-refused critical fired.
        Post-fix this MUST be IN."""
        assert _bib._is_in_restart_window(_et(1, 0)) is True

    def test_0129_is_in_post_fix(self):
        """Last minute of widened window."""
        assert _bib._is_in_restart_window(_et(1, 29)) is True

    def test_0130_is_out(self):
        """Window ends at 01:30 ET sharp."""
        assert _bib._is_in_restart_window(_et(1, 30)) is False

    def test_0200_is_out(self):
        assert _bib._is_in_restart_window(_et(2, 0)) is False

    def test_midday_is_out(self):
        """12:00 ET sanity — not in window."""
        assert _bib._is_in_restart_window(_et(12, 0)) is False

    def test_uses_now_when_arg_omitted(self):
        """No-arg call must default to current ET time and not raise."""
        result = _bib._is_in_restart_window()
        assert isinstance(result, bool)


# ─────────────────────────────────────────────────────────────────────────
# Layer A.1 — log severity downgrade ERROR → INFO for in-window refuse
# ─────────────────────────────────────────────────────────────────────────

class TestRestartWindowLogIsInfo:
    """The in-window refuse log was at ERROR level pre-fix, causing it to
    show up as warnings/errors in the dashboard catalog despite being
    expected behavior. Post-fix it should log at INFO."""

    @pytest.fixture(autouse=True)
    def _disable_dry_run(self, monkeypatch):
        monkeypatch.setattr(_bib, "BROKER_DRY_RUN", False)

    def test_in_window_refuse_logs_at_info_not_error(self, monkeypatch, caplog):
        # Force in-window
        monkeypatch.setattr(_bib, "_is_in_restart_window", lambda *a, **k: True)
        broker = object.__new__(_bib.IBKRBroker)
        broker.host = "127.0.0.1"
        broker.port = 4002
        broker.client_id = 1
        broker.account = ""
        broker._ib = None
        broker._md_type_warned = False
        broker._last_order_error = None
        with caplog.at_level(logging.INFO, logger=""):
            ok = broker.connect()
        assert ok is False, "must return False when in restart window"
        # Look for the 'restart window' log line
        window_lines = [r for r in caplog.records
                        if "restart window" in r.getMessage().lower()]
        assert len(window_lines) >= 1, "expected one 'restart window' log line"
        for r in window_lines:
            assert r.levelno < logging.WARNING, (
                f"in-window refuse must log at INFO/DEBUG, got "
                f"{logging.getLevelName(r.levelno)}: {r.getMessage()}"
            )


# ─────────────────────────────────────────────────────────────────────────
# Layer B — clientId staggered across the 3 retries
# ─────────────────────────────────────────────────────────────────────────

class TestClientIdStaggering:
    """When the first connect attempt fails mid-handshake, ib_insync may
    still hold clientId=N at the gateway. The second/third retry then gets
    Error 326 ("clientId already in use"). Post-fix: the broker uses
    clientId+attempt so each retry uses a distinct id.
    """

    @pytest.fixture(autouse=True)
    def _disable_dry_run(self, monkeypatch):
        monkeypatch.setattr(_bib, "BROKER_DRY_RUN", False)
        # Force OUT of window so connect() proceeds to the retry loop
        monkeypatch.setattr(_bib, "_is_in_restart_window", lambda *a, **k: False)

    def _broker(self, base_client_id: int = 1):
        broker = object.__new__(_bib.IBKRBroker)
        broker.host = "127.0.0.1"
        broker.port = 4002
        broker.client_id = base_client_id
        broker.account = ""
        broker._ib = None
        broker._md_type_warned = False
        broker._last_order_error = None
        return broker

    def test_three_retries_use_three_distinct_client_ids(self, monkeypatch):
        """Capture the clientId passed to every IB().connect() call.
        After fix B, the 3 attempts must use 3 distinct clientIds, not all 1.
        """
        ids_seen = []

        def fake_ib_factory():
            mock_ib = MagicMock()
            mock_ib.isConnected.return_value = False  # all attempts fail

            def fake_connect(host, port, clientId, timeout, readonly):
                ids_seen.append(clientId)
                raise ConnectionRefusedError("simulated")
            mock_ib.connect.side_effect = fake_connect
            return mock_ib

        monkeypatch.setattr(_bib, "IB", fake_ib_factory)
        # Speed up: zero retry sleep
        monkeypatch.setattr(_bib.time, "sleep", lambda *a, **k: None)

        broker = self._broker(base_client_id=1)
        ok = broker.connect()
        assert ok is False
        assert len(ids_seen) == 3, f"expected 3 attempts, got {len(ids_seen)}: {ids_seen}"
        assert len(set(ids_seen)) == 3, (
            f"all 3 attempts should use DISTINCT clientIds (post-fix); got {ids_seen}"
        )
        # First attempt must use the configured base id (1)
        assert ids_seen[0] == 1
        # Subsequent attempts must increment
        assert ids_seen[1] == 2
        assert ids_seen[2] == 3

    def test_stagger_starts_from_configured_base(self, monkeypatch):
        """If IBKR_CLIENT_ID=7, the 3 attempts should be 7, 8, 9 (not 1, 2, 3).
        Defends against accidental hardcoding of base=1."""
        ids_seen = []

        def fake_ib_factory():
            mock_ib = MagicMock()
            mock_ib.isConnected.return_value = False

            def fake_connect(host, port, clientId, timeout, readonly):
                ids_seen.append(clientId)
                raise ConnectionRefusedError("simulated")
            mock_ib.connect.side_effect = fake_connect
            return mock_ib

        monkeypatch.setattr(_bib, "IB", fake_ib_factory)
        monkeypatch.setattr(_bib.time, "sleep", lambda *a, **k: None)

        broker = self._broker(base_client_id=7)
        broker.connect()
        assert ids_seen == [7, 8, 9]

    def test_first_attempt_success_uses_base_client_id(self, monkeypatch):
        """If attempt 1 succeeds, only base clientId is consumed — no stagger
        needed. Defends against accidentally always burning 3 clientIds."""
        ids_seen = []

        def fake_ib_factory():
            mock_ib = MagicMock()
            mock_ib.isConnected.return_value = True
            mock_ib.managedAccounts.return_value = ["DUP_TEST"]

            def fake_connect(host, port, clientId, timeout, readonly):
                ids_seen.append(clientId)
            mock_ib.connect.side_effect = fake_connect
            return mock_ib

        monkeypatch.setattr(_bib, "IB", fake_ib_factory)
        monkeypatch.setattr(_bib.time, "sleep", lambda *a, **k: None)

        broker = self._broker(base_client_id=1)
        ok = broker.connect()
        assert ok is True
        assert ids_seen == [1], (
            f"successful first attempt should use only base clientId; got {ids_seen}"
        )
