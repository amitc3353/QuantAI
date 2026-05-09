"""Unit tests for _concentration_gate.py — same-underlying concentration limit.

Gate rule: block entry if >= 2 open positions exist on the same symbol,
regardless of agent source or strategy.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from _concentration_gate import (
    MAX_OPEN_PER_SYMBOL,
    ConcentrationResult,
    check_concentration,
)


# ── Factories ─────────────────────────────────────────────────────────────────


def _trade(
    trade_id: str = "A001",
    symbol: str = "SPY",
    status: str = "OPEN",
    source: str = "agent_alpha",
    strategy: str = "iron_condor",
) -> dict:
    return {
        "id": trade_id,
        "symbol": symbol,
        "status": status,
        "source": source,
        "strategy": strategy,
    }


def _write_journal(path: Path, trades: list[dict]) -> None:
    with open(path, "w") as f:
        for t in trades:
            f.write(json.dumps(t) + "\n")


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestEmptyAndLowCount:
    def test_empty_journal_allows_entry(self, tmp_path):
        journal = tmp_path / "trades.jsonl"
        _write_journal(journal, [])
        result = check_concentration("SPY", journal)
        assert result.allowed is True
        assert result.reason == "passed"

    def test_one_open_allows_entry(self, tmp_path):
        journal = tmp_path / "trades.jsonl"
        _write_journal(journal, [_trade("A001", symbol="SPY", status="OPEN")])
        result = check_concentration("SPY", journal)
        assert result.allowed is True

    def test_max_open_is_two(self):
        assert MAX_OPEN_PER_SYMBOL == 2


class TestBlockingAtLimit:
    def test_two_open_blocks_entry(self, tmp_path):
        journal = tmp_path / "trades.jsonl"
        _write_journal(journal, [
            _trade("A001", symbol="SPY", status="OPEN"),
            _trade("A002", symbol="SPY", status="OPEN"),
        ])
        result = check_concentration("SPY", journal)
        assert result.allowed is False
        assert "concentration_limit" in result.reason

    def test_three_open_still_blocked(self, tmp_path):
        journal = tmp_path / "trades.jsonl"
        _write_journal(journal, [
            _trade("A001", symbol="SPY", status="OPEN"),
            _trade("A002", symbol="SPY", status="OPEN"),
            _trade("B001", symbol="SPY", status="OPEN", source="agent_beta"),
        ])
        result = check_concentration("SPY", journal)
        assert result.allowed is False

    def test_result_includes_open_ids(self, tmp_path):
        journal = tmp_path / "trades.jsonl"
        _write_journal(journal, [
            _trade("A001", symbol="SPY", status="OPEN"),
            _trade("A002", symbol="SPY", status="OPEN"),
        ])
        result = check_concentration("SPY", journal)
        assert set(result.open_ids) == {"A001", "A002"}


class TestClosedTradesNotCounted:
    def test_closed_trades_do_not_count(self, tmp_path):
        journal = tmp_path / "trades.jsonl"
        _write_journal(journal, [
            _trade("A001", symbol="SPY", status="CLOSED"),
            _trade("A002", symbol="SPY", status="CLOSED"),
            _trade("A003", symbol="SPY", status="CLOSED"),
        ])
        result = check_concentration("SPY", journal)
        assert result.allowed is True
        assert result.open_ids == []

    def test_one_closed_one_open_allows_entry(self, tmp_path):
        journal = tmp_path / "trades.jsonl"
        _write_journal(journal, [
            _trade("A001", symbol="SPY", status="CLOSED"),
            _trade("A002", symbol="SPY", status="OPEN"),
        ])
        result = check_concentration("SPY", journal)
        assert result.allowed is True


class TestSymbolIsolation:
    def test_different_symbol_not_counted(self, tmp_path):
        journal = tmp_path / "trades.jsonl"
        _write_journal(journal, [
            _trade("A001", symbol="INTC", status="OPEN"),
            _trade("A002", symbol="INTC", status="OPEN"),
        ])
        result = check_concentration("SPY", journal)
        assert result.allowed is True

    def test_symbol_match_is_case_insensitive(self, tmp_path):
        journal = tmp_path / "trades.jsonl"
        _write_journal(journal, [
            _trade("A001", symbol="spy", status="OPEN"),
            _trade("A002", symbol="SPY", status="OPEN"),
        ])
        result = check_concentration("Spy", journal)
        assert result.allowed is False
        assert len(result.open_ids) == 2


class TestSourceAndStrategyAgnostic:
    def test_cross_agent_counted(self, tmp_path):
        """Alpha + Beta on same symbol both count toward the limit."""
        journal = tmp_path / "trades.jsonl"
        _write_journal(journal, [
            _trade("A001", symbol="SPY", status="OPEN", source="agent_alpha"),
            _trade("B001", symbol="SPY", status="OPEN", source="agent_beta"),
        ])
        result = check_concentration("SPY", journal)
        assert result.allowed is False

    def test_manual_positions_counted(self, tmp_path):
        """Manual M### entries count toward the limit."""
        journal = tmp_path / "trades.jsonl"
        _write_journal(journal, [
            _trade("A001", symbol="SPY", status="OPEN", source="agent_alpha"),
            _trade("M001", symbol="SPY", status="OPEN", source="manual"),
        ])
        result = check_concentration("SPY", journal)
        assert result.allowed is False

    def test_strategy_agnostic(self, tmp_path):
        """Two different strategies on the same symbol both count."""
        journal = tmp_path / "trades.jsonl"
        _write_journal(journal, [
            _trade("A001", symbol="SPY", status="OPEN", strategy="iron_condor"),
            _trade("A002", symbol="SPY", status="OPEN", strategy="bull_put_spread"),
        ])
        result = check_concentration("SPY", journal)
        assert result.allowed is False


class TestResilienceAndFailSafe:
    def test_corrupt_line_skipped_valid_lines_counted(self, tmp_path):
        journal = tmp_path / "trades.jsonl"
        with open(journal, "w") as f:
            f.write(json.dumps(_trade("A001", symbol="SPY", status="OPEN")) + "\n")
            f.write("not valid json\n")
            f.write(json.dumps(_trade("A002", symbol="SPY", status="OPEN")) + "\n")
        result = check_concentration("SPY", journal)
        assert result.allowed is False
        assert len(result.open_ids) == 2

    def test_missing_journal_fails_closed(self, tmp_path):
        journal = tmp_path / "nonexistent.jsonl"
        result = check_concentration("SPY", journal)
        assert result.allowed is False
        assert "journal_unavailable" in result.reason

    def test_result_symbol_is_uppercased(self, tmp_path):
        journal = tmp_path / "trades.jsonl"
        _write_journal(journal, [])
        result = check_concentration("spy", journal)
        assert result.symbol == "SPY"
