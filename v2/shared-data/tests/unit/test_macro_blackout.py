"""Unit tests for _macro_blackout.py — ±15 min blackout around macro events.

Gate rule: block ALL entries (except explicitly long-vol strategies) within
±15 minutes of a major macro event release (CPI, NFP, FOMC, GDP, PPI, PCE).

Key differences from Gate 3 (_event_calendar.py):
  - Gate 3: blocks only is_event_trade=True trades AFTER release
  - Gate 6: blocks all non-exempt trades within ±15 min AROUND release

Boundary: ±900 seconds inclusive, second-level precision.
  CPI at 08:30 → window 08:15:00–08:45:00
  08:15:00 = blocked, 08:14:59 = allowed, 08:45:00 = blocked, 08:45:01 = allowed
"""
from __future__ import annotations

from datetime import time

import pytest

import _macro_blackout as mb
from _macro_blackout import (
    BLACKOUT_MINUTES,
    LONG_VOL_STRATEGIES,
    BlackoutResult,
    check_macro_blackout,
)


def _pin_time(monkeypatch, t: time) -> None:
    monkeypatch.setattr(mb, "_now_et", lambda: t)


def _intel(is_event_day: bool, event_type: str | None = "CPI") -> dict:
    return {"macro": {"is_event_day": is_event_day, "event_type": event_type}}


# ── Constants ────────────────────────────────────────────────────────────────


class TestConstants:
    def test_blackout_minutes_is_15(self):
        assert BLACKOUT_MINUTES == 15

    def test_long_vol_strategies_contains_event_strangle(self):
        assert "event_strangle" in LONG_VOL_STRATEGIES

    def test_long_vol_strategies_contains_vix_calls(self):
        assert "vix_calls" in LONG_VOL_STRATEGIES

    def test_rsi_pullback_not_exempt(self):
        """Gamma's mean-reversion strategy is NOT exempt from blackout."""
        assert "rsi_pullback_debit_spread" not in LONG_VOL_STRATEGIES


# ── Long-vol exempt ─────────────────────────────────────────────────────────


class TestLongVolExempt:
    """Long-vol strategies pass through the blackout window."""

    def test_event_strangle_allowed_during_blackout(self, monkeypatch):
        _pin_time(monkeypatch, time(8, 30))
        result = check_macro_blackout(_intel(True, "CPI"), "event_strangle")
        assert result.allowed is True

    def test_vix_calls_allowed_during_blackout(self, monkeypatch):
        _pin_time(monkeypatch, time(8, 30))
        result = check_macro_blackout(_intel(True, "CPI"), "vix_calls")
        assert result.allowed is True

    def test_debit_spread_allowed_during_blackout(self, monkeypatch):
        _pin_time(monkeypatch, time(8, 30))
        result = check_macro_blackout(_intel(True, "CPI"), "debit_spread")
        assert result.allowed is True

    def test_call_ratio_backspread_allowed(self, monkeypatch):
        _pin_time(monkeypatch, time(8, 30))
        result = check_macro_blackout(_intel(True, "CPI"), "call_ratio_backspread")
        assert result.allowed is True

    def test_put_ratio_backspread_allowed(self, monkeypatch):
        _pin_time(monkeypatch, time(8, 30))
        result = check_macro_blackout(_intel(True, "CPI"), "put_ratio_backspread")
        assert result.allowed is True


# ── No event day (pass-through) ─────────────────────────────────────────────


class TestNoEventDay:
    def test_non_event_day_allowed(self, monkeypatch):
        _pin_time(monkeypatch, time(8, 30))
        result = check_macro_blackout(_intel(False), "iron_condor")
        assert result.allowed is True

    def test_missing_macro_allowed(self, monkeypatch):
        """Fail-open: no macro data → no evidence of event → allow."""
        _pin_time(monkeypatch, time(8, 30))
        result = check_macro_blackout({}, "iron_condor")
        assert result.allowed is True

    def test_missing_is_event_day_allowed(self, monkeypatch):
        _pin_time(monkeypatch, time(8, 30))
        result = check_macro_blackout({"macro": {}}, "iron_condor")
        assert result.allowed is True


# ── CPI blackout window (08:30 release) ─────────────────────────────────────


class TestCPIBlackout:
    def test_exactly_15_min_before_blocked(self, monkeypatch):
        """08:15:00 = exactly 900s before 08:30 = blocked (inclusive)."""
        _pin_time(monkeypatch, time(8, 15, 0))
        result = check_macro_blackout(_intel(True, "CPI"), "bull_put_spread")
        assert result.allowed is False
        assert "blackout" in result.reason.lower()

    def test_16_min_before_allowed(self, monkeypatch):
        """08:14:00 = 960s before 08:30 = outside window."""
        _pin_time(monkeypatch, time(8, 14, 0))
        result = check_macro_blackout(_intel(True, "CPI"), "bull_put_spread")
        assert result.allowed is True

    def test_1_second_outside_before_allowed(self, monkeypatch):
        """08:14:59 = 901s before 08:30 = just outside window."""
        _pin_time(monkeypatch, time(8, 14, 59))
        result = check_macro_blackout(_intel(True, "CPI"), "bull_put_spread")
        assert result.allowed is True

    def test_at_release_blocked(self, monkeypatch):
        _pin_time(monkeypatch, time(8, 30, 0))
        result = check_macro_blackout(_intel(True, "CPI"), "iron_condor")
        assert result.allowed is False
        assert result.event_type == "CPI"

    def test_5_min_after_blocked(self, monkeypatch):
        _pin_time(monkeypatch, time(8, 35))
        result = check_macro_blackout(_intel(True, "CPI"), "bear_call_spread")
        assert result.allowed is False

    def test_exactly_15_min_after_blocked(self, monkeypatch):
        """08:45:00 = exactly 900s after 08:30 = blocked (inclusive)."""
        _pin_time(monkeypatch, time(8, 45, 0))
        result = check_macro_blackout(_intel(True, "CPI"), "iron_condor")
        assert result.allowed is False

    def test_1_second_outside_after_allowed(self, monkeypatch):
        """08:45:01 = 901s after 08:30 = just outside window."""
        _pin_time(monkeypatch, time(8, 45, 1))
        result = check_macro_blackout(_intel(True, "CPI"), "iron_condor")
        assert result.allowed is True

    def test_well_after_allowed(self, monkeypatch):
        _pin_time(monkeypatch, time(10, 0))
        result = check_macro_blackout(_intel(True, "CPI"), "iron_condor")
        assert result.allowed is True


# ── FOMC blackout window (14:00 release) ────────────────────────────────────


class TestFOMCBlackout:
    def test_fomc_15_min_before_blocked(self, monkeypatch):
        _pin_time(monkeypatch, time(13, 45))
        result = check_macro_blackout(_intel(True, "FOMC"), "iron_condor")
        assert result.allowed is False

    def test_fomc_at_release_blocked(self, monkeypatch):
        _pin_time(monkeypatch, time(14, 0))
        result = check_macro_blackout(_intel(True, "FOMC"), "iron_condor")
        assert result.allowed is False
        assert result.event_type == "FOMC"

    def test_fomc_15_min_after_blocked(self, monkeypatch):
        _pin_time(monkeypatch, time(14, 15))
        result = check_macro_blackout(_intel(True, "FOMC"), "iron_condor")
        assert result.allowed is False

    def test_fomc_16_min_after_allowed(self, monkeypatch):
        _pin_time(monkeypatch, time(14, 16))
        result = check_macro_blackout(_intel(True, "FOMC"), "iron_condor")
        assert result.allowed is True


# ── Gamma blocked (not exempt) ──────────────────────────────────────────────


class TestGammaBlocked:
    def test_rsi_pullback_blocked_during_blackout(self, monkeypatch):
        """Gamma's mean-reversion strategy is NOT long-vol — gets blocked."""
        _pin_time(monkeypatch, time(8, 25))
        result = check_macro_blackout(_intel(True, "NFP"), "rsi_pullback_debit_spread")
        assert result.allowed is False


# ── Unknown event type ──────────────────────────────────────────────────────


class TestUnknownEvent:
    def test_unknown_event_uses_default_release_time(self, monkeypatch):
        """Unknown event type → defaults to 08:30 release."""
        _pin_time(monkeypatch, time(8, 30))
        result = check_macro_blackout(_intel(True, "RETAIL_SALES"), "iron_condor")
        assert result.allowed is False


# ── Result fields ────────────────────────────────────────────────────────────


class TestResultFields:
    def test_event_type_populated_on_block(self, monkeypatch):
        _pin_time(monkeypatch, time(8, 30))
        result = check_macro_blackout(_intel(True, "CPI"), "iron_condor")
        assert result.event_type == "CPI"

    def test_window_times_populated_on_block(self, monkeypatch):
        _pin_time(monkeypatch, time(8, 30))
        result = check_macro_blackout(_intel(True, "CPI"), "iron_condor")
        assert result.window_start_et == "08:15"
        assert result.window_end_et == "08:45"

    def test_fomc_window_times(self, monkeypatch):
        _pin_time(monkeypatch, time(14, 0))
        result = check_macro_blackout(_intel(True, "FOMC"), "iron_condor")
        assert result.window_start_et == "13:45"
        assert result.window_end_et == "14:15"
