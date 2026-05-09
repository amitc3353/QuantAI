"""Unit tests for _cooldown_gate.py — same-symbol cooldown after stop-loss.

Gate rule: after a stop-loss exit on a symbol, block re-entry for
COOLDOWN_DAYS (3) calendar days. Calendar days, not trading days.

Key calendar-day cases the user called out:
  - Stop Friday, attempt Monday = 3 calendar days = allowed
  - Stop Friday, attempt Sunday = 2 calendar days = blocked (hypothetical;
    no real trading on Sunday, but the math must be correct)

Fail-closed on missing journal (same as Gate 1): can't know if a recent
stop occurred without the journal, so the safe choice is to block.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

import _cooldown_gate as cg
from _cooldown_gate import (
    COOLDOWN_DAYS,
    CooldownResult,
    check_cooldown,
    is_in_cooldown,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

UTC = timezone.utc

# Fixed "today" for deterministic tests: Thursday 2026-05-07
_TODAY = date(2026, 5, 7)


def _pin_today(monkeypatch, d: date) -> None:
    monkeypatch.setattr(cg, "_today", lambda: d)


def _trade(
    trade_id: str = "A001",
    symbol: str = "GOOGL",
    status: str = "CLOSED",
    close_reason: str = "stop_loss (-45%<=-40%)",
    close_date: date | None = None,  # defaults to _TODAY
    source: str = "agent_alpha",
) -> dict:
    d = close_date or _TODAY
    ts = datetime(d.year, d.month, d.day, 10, 0, 0, tzinfo=UTC).isoformat()
    return {
        "id": trade_id,
        "symbol": symbol,
        "status": status,
        "source": source,
        "close_reason": close_reason,
        "close_timestamp": ts,
        "timestamp": ts,
    }


def _write_journal(path: Path, trades: list[dict]) -> None:
    with open(path, "w") as f:
        for t in trades:
            f.write(json.dumps(t) + "\n")


# ── Threshold constant ────────────────────────────────────────────────────────


class TestConstants:
    def test_cooldown_days_is_three(self):
        assert COOLDOWN_DAYS == 3


# ── No cooldown cases ─────────────────────────────────────────────────────────


class TestNoCooldown:
    def test_empty_journal_allowed(self, monkeypatch):
        _pin_today(monkeypatch, _TODAY)
        result = is_in_cooldown("GOOGL", [])
        assert result.allowed is True
        assert result.days_since_stop == -1

    def test_take_profit_not_counted(self, monkeypatch):
        _pin_today(monkeypatch, _TODAY)
        journal = [_trade(close_reason="TAKE_PROFIT", close_date=_TODAY)]
        result = is_in_cooldown("GOOGL", journal)
        assert result.allowed is True

    def test_rsi_recovery_not_counted(self, monkeypatch):
        _pin_today(monkeypatch, _TODAY)
        journal = [_trade(close_reason="RSI_RECOVERY", close_date=_TODAY)]
        result = is_in_cooldown("GOOGL", journal)
        assert result.allowed is True

    def test_open_position_not_counted(self, monkeypatch):
        """OPEN entries (even with a stop_loss reason field) don't trigger cooldown."""
        _pin_today(monkeypatch, _TODAY)
        journal = [_trade(status="OPEN", close_reason="stop_loss", close_date=_TODAY)]
        result = is_in_cooldown("GOOGL", journal)
        assert result.allowed is True

    def test_different_symbol_not_counted(self, monkeypatch):
        _pin_today(monkeypatch, _TODAY)
        journal = [_trade(symbol="AAPL", close_reason="stop_loss", close_date=_TODAY)]
        result = is_in_cooldown("GOOGL", journal)
        assert result.allowed is True


# ── Within-cooldown cases (blocked) ──────────────────────────────────────────


class TestWithinCooldown:
    def test_stop_today_blocked(self, monkeypatch):
        _pin_today(monkeypatch, _TODAY)
        journal = [_trade(close_date=_TODAY)]
        result = is_in_cooldown("GOOGL", journal)
        assert result.allowed is False
        assert "cooldown" in result.reason.lower()

    def test_stop_1_day_ago_blocked(self, monkeypatch):
        _pin_today(monkeypatch, _TODAY)
        journal = [_trade(close_date=_TODAY - timedelta(days=1))]
        result = is_in_cooldown("GOOGL", journal)
        assert result.allowed is False

    def test_stop_2_days_ago_blocked(self, monkeypatch):
        _pin_today(monkeypatch, _TODAY)
        journal = [_trade(close_date=_TODAY - timedelta(days=2))]
        result = is_in_cooldown("GOOGL", journal)
        assert result.allowed is False


# ── Boundary: exactly 3 calendar days ────────────────────────────────────────


class TestCalendarDayBoundary:
    def test_midweek_stop_exactly_3_days_ago_allowed(self, monkeypatch):
        """Mid-week case: stop Monday 2026-05-04, attempt Thursday 2026-05-07 = 3 days = allowed."""
        stop_date = date(2026, 5, 4)   # Monday
        today = date(2026, 5, 7)       # Thursday — 3 calendar days later
        assert (today - stop_date).days == 3
        _pin_today(monkeypatch, today)
        journal = [_trade(close_date=stop_date)]
        result = is_in_cooldown("GOOGL", journal)
        assert result.allowed is True
        assert result.days_since_stop == 3

    def test_weekend_spanning_stop_friday_attempt_monday_allowed(self, monkeypatch):
        """Weekend case: stop Friday 2026-05-01, attempt Monday 2026-05-04 = 3 calendar days = allowed."""
        stop_date = date(2026, 5, 1)   # Friday
        today = date(2026, 5, 4)       # Monday — exactly 3 calendar days later
        assert stop_date.weekday() == 4   # Friday
        assert today.weekday() == 0       # Monday
        assert (today - stop_date).days == 3
        _pin_today(monkeypatch, today)
        journal = [_trade(close_date=stop_date)]
        result = is_in_cooldown("GOOGL", journal)
        assert result.allowed is True

    def test_weekend_spanning_stop_friday_attempt_sunday_blocked(self, monkeypatch):
        """Weekend case: stop Friday 2026-05-01, attempt Sunday 2026-05-03 = 2 calendar days = blocked.
        No real trading on Sunday, but the calendar math must be correct."""
        stop_date = date(2026, 5, 1)   # Friday
        today = date(2026, 5, 3)       # Sunday — 2 calendar days later
        assert (today - stop_date).days == 2
        _pin_today(monkeypatch, today)
        journal = [_trade(close_date=stop_date)]
        result = is_in_cooldown("GOOGL", journal)
        assert result.allowed is False
        assert result.days_since_stop == 2

    def test_stop_4_days_ago_allowed(self, monkeypatch):
        _pin_today(monkeypatch, _TODAY)
        journal = [_trade(close_date=_TODAY - timedelta(days=4))]
        result = is_in_cooldown("GOOGL", journal)
        assert result.allowed is True


# ── Stop-reason matching ──────────────────────────────────────────────────────


class TestStopReasonMatching:
    def test_parameterized_stop_reason_counted(self, monkeypatch):
        """Most common format: 'stop_loss (-45%<=-40%)'."""
        _pin_today(monkeypatch, _TODAY)
        journal = [_trade(close_reason="stop_loss (-45%<=-40%)", close_date=_TODAY)]
        result = is_in_cooldown("GOOGL", journal)
        assert result.allowed is False

    def test_stop_loss_2x_credit_variant_counted(self, monkeypatch):
        _pin_today(monkeypatch, _TODAY)
        journal = [_trade(close_reason="stop_loss_2x_credit (-210%<=-200%)", close_date=_TODAY)]
        result = is_in_cooldown("GOOGL", journal)
        assert result.allowed is False

    def test_uppercase_stop_loss_counted(self, monkeypatch):
        """Older / test-data entries use 'STOP_LOSS' uppercase."""
        _pin_today(monkeypatch, _TODAY)
        journal = [_trade(close_reason="STOP_LOSS", close_date=_TODAY)]
        result = is_in_cooldown("GOOGL", journal)
        assert result.allowed is False

    def test_case_insensitive_symbol_match(self, monkeypatch):
        _pin_today(monkeypatch, _TODAY)
        journal = [_trade(symbol="googl", close_reason="stop_loss", close_date=_TODAY)]
        result = is_in_cooldown("GOOGL", journal)
        assert result.allowed is False


# ── Result fields ─────────────────────────────────────────────────────────────


class TestResultFields:
    def test_last_stop_date_populated_on_block(self, monkeypatch):
        stop_date = _TODAY - timedelta(days=1)
        _pin_today(monkeypatch, _TODAY)
        journal = [_trade(close_date=stop_date)]
        result = is_in_cooldown("GOOGL", journal)
        assert result.allowed is False
        assert result.last_stop_date == stop_date.isoformat()

    def test_days_since_stop_correct_on_block(self, monkeypatch):
        _pin_today(monkeypatch, _TODAY)
        journal = [_trade(close_date=_TODAY - timedelta(days=2))]
        result = is_in_cooldown("GOOGL", journal)
        assert result.days_since_stop == 2

    def test_most_recent_stop_used_when_multiple(self, monkeypatch):
        """Multiple stops on same symbol: only the most recent matters."""
        _pin_today(monkeypatch, _TODAY)
        journal = [
            _trade("A001", close_date=_TODAY - timedelta(days=5)),  # old — allowed
            _trade("A002", close_date=_TODAY - timedelta(days=1)),  # recent — blocked
        ]
        result = is_in_cooldown("GOOGL", journal)
        assert result.allowed is False
        assert result.days_since_stop == 1


# ── Fail-closed on missing journal ───────────────────────────────────────────


class TestFailClosed:
    def test_missing_journal_file_fails_closed(self, tmp_path, monkeypatch):
        _pin_today(monkeypatch, _TODAY)
        result = check_cooldown("GOOGL", tmp_path / "nonexistent.jsonl")
        assert result.allowed is False
        assert "journal_unavailable" in result.reason
