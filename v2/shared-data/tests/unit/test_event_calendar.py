"""Unit tests for _event_calendar.py — pre-entry event-time verification.

Gate rule: block PRE_EVENT trades when today's macro event has already released.
Uses a hardcoded release-time table (ET) because Finnhub provides dates only.

Fail-open on missing macro data: if we can't detect the event, we can't know
it's released, so we allow the trade (opposite of Gate 1's fail-closed on a
missing journal — see _event_calendar.py docstring for the reasoning).
"""
from __future__ import annotations

from datetime import time

import pytest

import _event_calendar as ec
from _event_calendar import (
    DEFAULT_RELEASE_TIME_ET,
    EVENT_RELEASE_TIMES_ET,
    EventTimingResult,
    check_event_timing,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _intel(is_event_day: bool = True, event_type: str | None = "CPI") -> dict:
    return {"macro": {"is_event_day": is_event_day, "event_type": event_type}}


def _pin_time(monkeypatch, t: time) -> None:
    """Pin _event_calendar._now_et() to return a fixed ET time."""
    monkeypatch.setattr(ec, "_now_et", lambda: t)


# ── Constants ─────────────────────────────────────────────────────────────────


class TestConstants:
    def test_cpi_releases_at_0830(self):
        assert EVENT_RELEASE_TIMES_ET["CPI"] == time(8, 30)

    def test_nfp_releases_at_0830(self):
        assert EVENT_RELEASE_TIMES_ET["NFP"] == time(8, 30)

    def test_fomc_releases_at_1400(self):
        assert EVENT_RELEASE_TIMES_ET["FOMC"] == time(14, 0)

    def test_default_release_time_is_0830(self):
        assert DEFAULT_RELEASE_TIME_ET == time(8, 30)


# ── Non-event trades always pass ──────────────────────────────────────────────


class TestNonEventTrades:
    def test_non_event_trade_always_allowed_even_on_event_day(self, monkeypatch):
        _pin_time(monkeypatch, time(10, 0))  # post-CPI release
        result = check_event_timing(_intel(is_event_day=True), is_event_trade=False)
        assert result.allowed is True

    def test_non_event_trade_never_checks_clock(self, monkeypatch):
        """Gate must not touch the clock for non-event trades."""
        _pin_time(monkeypatch, time(23, 59))
        result = check_event_timing(_intel(is_event_day=True), is_event_trade=False)
        assert result.allowed is True


# ── Event days away (not today) ───────────────────────────────────────────────


class TestEventNotToday:
    def test_event_trade_allowed_when_event_not_today(self, monkeypatch):
        _pin_time(monkeypatch, time(10, 0))
        result = check_event_timing(_intel(is_event_day=False), is_event_trade=True)
        assert result.allowed is True
        assert "not today" in result.reason

    def test_event_trade_allowed_when_macro_missing(self, monkeypatch):
        """Fail-open: missing macro = can't detect event = allow."""
        _pin_time(monkeypatch, time(10, 0))
        result = check_event_timing({"no_macro": True}, is_event_trade=True)
        assert result.allowed is True

    def test_event_trade_allowed_when_intel_empty(self, monkeypatch):
        _pin_time(monkeypatch, time(10, 0))
        result = check_event_timing({}, is_event_trade=True)
        assert result.allowed is True


# ── CPI at 08:30 ET ───────────────────────────────────────────────────────────


class TestCPITiming:
    def test_before_cpi_release_allowed(self, monkeypatch):
        _pin_time(monkeypatch, time(8, 0))  # 30 min before CPI
        result = check_event_timing(_intel(event_type="CPI"), is_event_trade=True)
        assert result.allowed is True

    def test_one_minute_before_cpi_release_allowed(self, monkeypatch):
        _pin_time(monkeypatch, time(8, 29))
        result = check_event_timing(_intel(event_type="CPI"), is_event_trade=True)
        assert result.allowed is True

    def test_exactly_at_cpi_release_blocked(self, monkeypatch):
        """Boundary: exactly 08:30:00 ET — CPI number is hitting the tape."""
        _pin_time(monkeypatch, time(8, 30, 0))  # exactly 08:30:00, not 08:30:01
        result = check_event_timing(_intel(event_type="CPI"), is_event_trade=True)
        assert result.allowed is False
        assert "CPI" in result.reason

    def test_after_cpi_release_blocked(self, monkeypatch):
        _pin_time(monkeypatch, time(9, 0))  # 30 min after CPI
        result = check_event_timing(_intel(event_type="CPI"), is_event_trade=True)
        assert result.allowed is False


# ── FOMC at 14:00 ET ─────────────────────────────────────────────────────────


class TestFOMCTiming:
    def test_before_fomc_release_allowed(self, monkeypatch):
        _pin_time(monkeypatch, time(13, 0))
        result = check_event_timing(_intel(event_type="FOMC"), is_event_trade=True)
        assert result.allowed is True

    def test_exactly_at_fomc_release_blocked(self, monkeypatch):
        """Boundary: exactly 14:00:00 ET."""
        _pin_time(monkeypatch, time(14, 0, 0))
        result = check_event_timing(_intel(event_type="FOMC"), is_event_trade=True)
        assert result.allowed is False
        assert "FOMC" in result.reason

    def test_after_fomc_release_blocked(self, monkeypatch):
        _pin_time(monkeypatch, time(14, 30))
        result = check_event_timing(_intel(event_type="FOMC"), is_event_trade=True)
        assert result.allowed is False


# ── Unknown event type uses default ──────────────────────────────────────────


class TestUnknownEventType:
    def test_none_event_type_uses_0830_default(self, monkeypatch):
        """event_type=None → default 08:30; 09:00 ET should block."""
        _pin_time(monkeypatch, time(9, 0))
        result = check_event_timing(_intel(event_type=None), is_event_trade=True)
        assert result.allowed is False

    def test_none_event_type_before_default_allowed(self, monkeypatch):
        _pin_time(monkeypatch, time(8, 0))
        result = check_event_timing(_intel(event_type=None), is_event_trade=True)
        assert result.allowed is True


# ── Result fields ─────────────────────────────────────────────────────────────


class TestResultFields:
    def test_event_type_in_result_on_block(self, monkeypatch):
        _pin_time(monkeypatch, time(9, 0))
        result = check_event_timing(_intel(event_type="NFP"), is_event_trade=True)
        assert result.allowed is False
        assert result.event_type == "NFP"

    def test_release_time_et_in_result_on_block(self, monkeypatch):
        _pin_time(monkeypatch, time(9, 0))
        result = check_event_timing(_intel(event_type="NFP"), is_event_trade=True)
        assert result.release_time_et == "08:30"

    def test_fomc_release_time_et_string(self, monkeypatch):
        _pin_time(monkeypatch, time(15, 0))
        result = check_event_timing(_intel(event_type="FOMC"), is_event_trade=True)
        assert result.release_time_et == "14:00"
