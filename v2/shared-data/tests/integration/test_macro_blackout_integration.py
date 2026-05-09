"""Integration tests: macro blackout gate across agent paths.

Verifies that Gate 6 correctly blocks short-vol entries and exempts long-vol
strategies during macro event windows, using representative intel dicts and
monkeypatched wall-clock times.
"""
from __future__ import annotations

from datetime import time

import pytest

import _macro_blackout as mb
from _macro_blackout import check_macro_blackout


def _pin_time(monkeypatch, t: time) -> None:
    monkeypatch.setattr(mb, "_now_et", lambda: t)


class TestAlphaShortVolBlocked:
    """Alpha's credit spreads are short-vol — blocked during blackout."""

    def test_bull_put_spread_blocked_during_cpi(self, monkeypatch):
        _pin_time(monkeypatch, time(8, 25))
        intel = {"macro": {"is_event_day": True, "event_type": "CPI"}}
        result = check_macro_blackout(intel, "bull_put_spread")
        assert result.allowed is False
        assert result.event_type == "CPI"

    def test_iron_condor_blocked_during_nfp(self, monkeypatch):
        _pin_time(monkeypatch, time(8, 40))
        intel = {"macro": {"is_event_day": True, "event_type": "NFP"}}
        result = check_macro_blackout(intel, "iron_condor")
        assert result.allowed is False

    def test_diagonal_spread_allowed_outside_window(self, monkeypatch):
        _pin_time(monkeypatch, time(10, 30))
        intel = {"macro": {"is_event_day": True, "event_type": "CPI"}}
        result = check_macro_blackout(intel, "diagonal_spread")
        assert result.allowed is True


class TestBetaLongVolExempt:
    """Beta's event_strangle and vix_calls are long-vol — exempt."""

    def test_event_strangle_allowed_during_cpi(self, monkeypatch):
        _pin_time(monkeypatch, time(8, 30))
        intel = {"macro": {"is_event_day": True, "event_type": "CPI"}}
        result = check_macro_blackout(intel, "event_strangle")
        assert result.allowed is True

    def test_beta_credit_spread_blocked_during_fomc(self, monkeypatch):
        _pin_time(monkeypatch, time(13, 50))
        intel = {"macro": {"is_event_day": True, "event_type": "FOMC"}}
        result = check_macro_blackout(intel, "credit_spread_offset")
        assert result.allowed is False


class TestGammaBlocked:
    """Gamma's mean-reversion debit spread is NOT long-vol — blocked."""

    def test_gamma_blocked_during_nfp(self, monkeypatch):
        _pin_time(monkeypatch, time(8, 20))
        intel = {"macro": {"is_event_day": True, "event_type": "NFP"}}
        result = check_macro_blackout(intel, "rsi_pullback_debit_spread")
        assert result.allowed is False

    def test_gamma_allowed_outside_window(self, monkeypatch):
        _pin_time(monkeypatch, time(10, 0))
        intel = {"macro": {"is_event_day": True, "event_type": "NFP"}}
        result = check_macro_blackout(intel, "rsi_pullback_debit_spread")
        assert result.allowed is True
