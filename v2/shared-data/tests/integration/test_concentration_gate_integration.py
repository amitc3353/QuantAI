"""Integration test: concentration gate wired into agent paths.

Verifies that gamma/risk_check.filter_setups respects cross-agent concentration —
i.e. two open Alpha/Beta positions on AAPL block a Gamma entry on AAPL.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from gamma.risk_check import filter_setups


# ── Helpers ───────────────────────────────────────────────────────────────────


def _open_trade(trade_id: str, symbol: str, source: str = "agent_alpha") -> dict:
    return {"id": trade_id, "symbol": symbol, "source": source, "status": "OPEN"}


def _setup(symbol: str = "AAPL") -> dict:
    return {"symbol": symbol, "sector": "Technology", "rsi": 28.5}


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestGammaFilterSetupsCrossAgentConcentration:
    def test_no_cross_agent_positions_allows_setup(self):
        journal = []
        result = filter_setups([_setup("AAPL")], journal)
        assert len(result) == 1

    def test_one_cross_agent_position_allows_setup(self):
        journal = [_open_trade("A001", "AAPL", source="agent_alpha")]
        result = filter_setups([_setup("AAPL")], journal)
        assert len(result) == 1

    def test_two_cross_agent_positions_blocks_setup(self):
        """Alpha + Beta both open on AAPL → Gamma blocked from adding a 3rd."""
        journal = [
            _open_trade("A001", "AAPL", source="agent_alpha"),
            _open_trade("B001", "AAPL", source="agent_beta"),
        ]
        result = filter_setups([_setup("AAPL")], journal)
        assert len(result) == 0

    def test_concentration_block_on_target_symbol_does_not_block_others(self):
        """AAPL blocked but MSFT (no open positions) still passes through."""
        journal = [
            _open_trade("A001", "AAPL", source="agent_alpha"),
            _open_trade("B001", "AAPL", source="agent_beta"),
        ]
        setups = [_setup("AAPL"), _setup("MSFT")]
        result = filter_setups(setups, journal)
        assert len(result) == 1
        assert result[0]["symbol"] == "MSFT"

    def test_closed_cross_agent_positions_not_counted(self):
        """CLOSED Alpha+Beta on AAPL should not block Gamma from entering."""
        journal = [
            {"id": "A001", "symbol": "AAPL", "source": "agent_alpha", "status": "CLOSED"},
            {"id": "B001", "symbol": "AAPL", "source": "agent_beta", "status": "CLOSED"},
        ]
        result = filter_setups([_setup("AAPL")], journal)
        assert len(result) == 1
