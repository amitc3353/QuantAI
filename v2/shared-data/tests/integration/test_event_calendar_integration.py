"""Integration tests: event timing gate wired into agent paths.

Verifies end-to-end blocking for Beta PRE_EVENT and Alpha is_event_day paths
using representative intel dicts and monkeypatched wall-clock times.
"""
from __future__ import annotations

from datetime import time

import pytest

import _event_calendar as ec
from _event_calendar import check_event_timing


def _pin_time(monkeypatch, t: time) -> None:
    monkeypatch.setattr(ec, "_now_et", lambda: t)


class TestBetaPreEventPath:
    """Simulates beta_agent: is_event_trade = (regime == 'PRE_EVENT')."""

    def _cpi_today_intel(self) -> dict:
        return {"macro": {"is_event_day": True, "event_type": "CPI"}}

    def test_pre_event_before_release_allowed(self, monkeypatch):
        _pin_time(monkeypatch, time(8, 15))
        result = check_event_timing(self._cpi_today_intel(), is_event_trade=True)
        assert result.allowed is True

    def test_pre_event_exactly_at_release_blocked(self, monkeypatch):
        """B002 scenario: entering at exactly 08:30:00 ET when CPI drops."""
        _pin_time(monkeypatch, time(8, 30, 0))
        result = check_event_timing(self._cpi_today_intel(), is_event_trade=True)
        assert result.allowed is False
        assert result.event_type == "CPI"
        assert result.release_time_et == "08:30"

    def test_pre_event_after_release_blocked(self, monkeypatch):
        _pin_time(monkeypatch, time(9, 33))  # typical execution time
        result = check_event_timing(self._cpi_today_intel(), is_event_trade=True)
        assert result.allowed is False

    def test_pre_event_when_event_days_away_allowed(self, monkeypatch):
        """PRE_EVENT with CPI tomorrow — is_event_day=False — must pass."""
        _pin_time(monkeypatch, time(14, 0))
        intel = {"macro": {"is_event_day": False, "event_type": "CPI"}}
        result = check_event_timing(intel, is_event_trade=True)
        assert result.allowed is True


class TestAlphaEventDayPath:
    """Simulates autonomous_execution: is_event_trade = macro['is_event_day']."""

    def test_non_event_day_always_passes(self, monkeypatch):
        _pin_time(monkeypatch, time(15, 0))
        intel = {"macro": {"is_event_day": False, "event_type": None}}
        result = check_event_timing(intel, is_event_trade=False)
        assert result.allowed is True

    def test_fomc_day_before_2pm_passes(self, monkeypatch):
        _pin_time(monkeypatch, time(13, 45))
        intel = {"macro": {"is_event_day": True, "event_type": "FOMC"}}
        result = check_event_timing(intel, is_event_trade=True)
        assert result.allowed is True

    def test_fomc_day_after_2pm_blocked(self, monkeypatch):
        _pin_time(monkeypatch, time(14, 15))
        intel = {"macro": {"is_event_day": True, "event_type": "FOMC"}}
        result = check_event_timing(intel, is_event_trade=True)
        assert result.allowed is False
