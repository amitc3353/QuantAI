"""Integration tests: cooldown gate with real JSONL file I/O.

Verifies the end-to-end check_cooldown path (disk read → parse → decision)
for scenarios that simulate Alpha, Beta, and Gamma agent contexts.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest

import _cooldown_gate as cg
from _cooldown_gate import check_cooldown, is_in_cooldown

UTC = timezone.utc
_TODAY = date(2026, 5, 7)


def _pin_today(monkeypatch, d: date) -> None:
    monkeypatch.setattr(cg, "_today", lambda: d)


def _trade(
    trade_id: str = "A001",
    symbol: str = "GOOGL",
    status: str = "CLOSED",
    close_reason: str = "stop_loss (-45%<=-40%)",
    close_date: date | None = None,
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


def _write_journal(path, trades: list[dict]) -> None:
    with open(path, "w") as f:
        for t in trades:
            f.write(json.dumps(t) + "\n")


# ── Alpha / Beta path: check_cooldown with file I/O ─────────────────────────


class TestAlphaBetaCooldownPath:
    """Simulates Alpha / Beta: check_cooldown called with journal path."""

    def test_no_stop_in_journal_allowed(self, tmp_path, monkeypatch):
        _pin_today(monkeypatch, _TODAY)
        journal_path = tmp_path / "trades.jsonl"
        _write_journal(journal_path, [_trade(close_reason="TAKE_PROFIT")])
        result = check_cooldown("GOOGL", journal_path)
        assert result.allowed is True

    def test_recent_stop_blocks_re_entry(self, tmp_path, monkeypatch):
        _pin_today(monkeypatch, _TODAY)
        journal_path = tmp_path / "trades.jsonl"
        _write_journal(journal_path, [_trade(close_date=_TODAY - timedelta(days=1))])
        result = check_cooldown("GOOGL", journal_path)
        assert result.allowed is False
        assert result.days_since_stop == 1

    def test_cooldown_expired_allows_re_entry(self, tmp_path, monkeypatch):
        _pin_today(monkeypatch, _TODAY)
        journal_path = tmp_path / "trades.jsonl"
        _write_journal(journal_path, [_trade(close_date=_TODAY - timedelta(days=3))])
        result = check_cooldown("GOOGL", journal_path)
        assert result.allowed is True

    def test_cross_agent_stop_counts(self, tmp_path, monkeypatch):
        """Beta stop on GOOGL should block Alpha re-entry on GOOGL (shared journal)."""
        _pin_today(monkeypatch, _TODAY)
        journal_path = tmp_path / "trades.jsonl"
        beta_stop = _trade(
            trade_id="B001",
            close_date=_TODAY - timedelta(days=1),
            source="agent_beta",
        )
        _write_journal(journal_path, [beta_stop])
        result = check_cooldown("GOOGL", journal_path)
        assert result.allowed is False

    def test_missing_journal_fails_closed(self, tmp_path, monkeypatch):
        _pin_today(monkeypatch, _TODAY)
        result = check_cooldown("GOOGL", tmp_path / "nonexistent.jsonl")
        assert result.allowed is False
        assert "journal_unavailable" in result.reason

    def test_empty_journal_file_allowed(self, tmp_path, monkeypatch):
        _pin_today(monkeypatch, _TODAY)
        journal_path = tmp_path / "trades.jsonl"
        journal_path.write_text("")
        result = check_cooldown("GOOGL", journal_path)
        assert result.allowed is True


# ── Gamma path: is_in_cooldown with pre-loaded list ─────────────────────────


class TestGammaCooldownPath:
    """Simulates Gamma: is_in_cooldown called with pre-loaded journal list."""

    def test_no_stop_allowed(self, monkeypatch):
        _pin_today(monkeypatch, _TODAY)
        result = is_in_cooldown("AAPL", [])
        assert result.allowed is True

    def test_recent_stop_blocked(self, monkeypatch):
        _pin_today(monkeypatch, _TODAY)
        journal = [_trade(symbol="AAPL", close_date=_TODAY - timedelta(days=2))]
        result = is_in_cooldown("AAPL", journal)
        assert result.allowed is False
        assert result.days_since_stop == 2
