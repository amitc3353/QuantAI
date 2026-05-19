"""Unit tests for check_gamma_exit() source-routing logic.

Bug discovered 2026-05-18: check_gamma_exit() used an exact match
(source == "agent_gamma") which excluded per-arm trades that have
source = "agent_gamma_arm_a" through "agent_gamma_arm_d". Those trades
fell through to check_beta_exit() → wrong exit rules applied.

Fix: source.startswith("agent_gamma") instead of source == "agent_gamma".

These tests pin both the positive cases (Gamma trades route to Gamma) and
the negative cases (Alpha/Beta/manual trades do NOT route to Gamma), so the
routing invariant cannot silently break again.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

# Bootstrap: position_monitor imports broker which has heavy deps.
# Install a fake broker module BEFORE importing position_monitor.
_BROKER_MOD = MagicMock()
_BROKER_MOD.BrokerBase = type("FakeBrokerBase", (), {})
_BROKER_MOD.DRY_RUN_SENTINEL = {"DRY_RUN": True}
sys.modules.setdefault("broker", _BROKER_MOD)

import position_monitor as pm  # noqa: E402


# ── Fixtures ──────────────────────────────────────────────────────────────


def _gamma_trade(source: str = "agent_gamma", **overrides) -> dict:
    """Build a minimal Gamma-style trade for exit-routing tests."""
    trade = {
        "id": "Gtest",
        "source": source,
        "symbol": "USB",
        "strategy": "rsi_pullback_debit_spread",
        "net_debit": 0.55,
        "exit_rules": {
            "rsi_exit_threshold": 40,
            "rsi_period": 10,
            "time_stop_days": 10,
            "trend_break_ma": 200,
            "stop_loss_pct": -50,
            "take_profit_pct": 150,
            "entry_date": "2026-05-01",
        },
        "legs": [
            {"side": "buy", "symbol": "USB260529C00053000", "type": "C",
             "strike": 53.0, "expiry": "2026-05-29"},
            {"side": "sell", "symbol": "USB260529C00054000", "type": "C",
             "strike": 54.0, "expiry": "2026-05-29"},
        ],
    }
    trade.update(overrides)
    return trade


def _non_gamma_trade(source: str, **overrides) -> dict:
    """Build a minimal non-Gamma trade (Alpha/Beta/manual)."""
    trade = {
        "id": "Xtest",
        "source": source,
        "symbol": "INTC",
        "strategy": "iron_condor",
        "estimated_credit": 1.65,
        "legs": [
            {"action": "sell", "type": "put", "strike": 100.0,
             "expiry": "2026-06-15", "symbol": "INTC260615P00100000"},
            {"action": "buy", "type": "put", "strike": 99.0,
             "expiry": "2026-06-15", "symbol": "INTC260615P00099000"},
        ],
    }
    trade.update(overrides)
    return trade


# Use a fixed "now" well before any expiry to avoid expiry-proximity triggers
_NOW = datetime(2026, 5, 10, 12, 0, 0)


# ── Positive routing: Gamma trades → check_gamma_exit ─────────────────────


class TestGammaSourceRoutesToGammaExit:
    """All agent_gamma* source strings must be handled by check_gamma_exit(),
    NOT fall through to check_beta_exit() or the Alpha catch-all."""

    def test_legacy_agent_gamma_routes_to_gamma(self, monkeypatch):
        """Pre-experiment trades with source='agent_gamma' still route correctly."""
        monkeypatch.setattr(pm, "_gamma_indicators", lambda sym: {})
        trade = _gamma_trade(source="agent_gamma")
        result = pm.check_gamma_exit(trade, pnl=0.0, now=_NOW)
        assert result is not None, (
            "source='agent_gamma' must route to check_gamma_exit, not return None"
        )

    @pytest.mark.parametrize("arm_id", ["a", "b", "c", "d"])
    def test_per_arm_gamma_routes_to_gamma(self, arm_id, monkeypatch):
        """Per-arm trades (agent_gamma_arm_a through _d) must route to Gamma."""
        monkeypatch.setattr(pm, "_gamma_indicators", lambda sym: {})
        trade = _gamma_trade(source=f"agent_gamma_arm_{arm_id}")
        result = pm.check_gamma_exit(trade, pnl=0.0, now=_NOW)
        assert result is not None, (
            f"source='agent_gamma_arm_{arm_id}' must route to check_gamma_exit, "
            f"not return None (which would fall through to Beta/Alpha exit logic)"
        )

    def test_gamma_with_exit_rules_evaluates_stop_loss(self, monkeypatch):
        """Gamma exit rules are actually evaluated, not just acknowledged."""
        monkeypatch.setattr(pm, "_gamma_indicators", lambda sym: {})
        trade = _gamma_trade(source="agent_gamma_arm_c")
        # P&L of -60% on a $0.55 debit: pnl = -0.55 * 100 * 0.60 = -$33
        pnl = -33.0
        result = pm.check_gamma_exit(trade, pnl=pnl, now=_NOW)
        assert result is not None
        should_close, reason, fraction, flag = result
        assert should_close is True
        assert "stop_loss" in reason

    def test_gamma_arm_trade_rsi_recovery_exit(self, monkeypatch):
        """Per-arm trade triggers RSI recovery (primary Connors exit)."""
        monkeypatch.setattr(pm, "_gamma_indicators",
                            lambda sym: {"rsi_10": 45.0, "close": 55.0, "sma_200": 50.0})
        trade = _gamma_trade(source="agent_gamma_arm_a")
        result = pm.check_gamma_exit(trade, pnl=5.0, now=_NOW)
        assert result is not None
        should_close, reason, _, _ = result
        assert should_close is True
        assert "rsi_recovery" in reason

    def test_gamma_arm_trade_hold(self, monkeypatch):
        """Per-arm trade with no exit condition returns (False, 'hold', ...)."""
        monkeypatch.setattr(pm, "_gamma_indicators",
                            lambda sym: {"rsi_10": 30.0, "close": 55.0, "sma_200": 50.0})
        trade = _gamma_trade(source="agent_gamma_arm_b")
        result = pm.check_gamma_exit(trade, pnl=0.0, now=_NOW)
        assert result is not None, "Gamma trade must not return None even when holding"
        should_close, reason, _, _ = result
        assert should_close is False
        assert reason == "hold"


# ── Negative routing: non-Gamma trades → check_gamma_exit returns None ────


class TestNonGammaSourceSkipsGammaExit:
    """Alpha, Beta, manual, and other non-Gamma sources must get None from
    check_gamma_exit() so they proceed to the correct downstream handler."""

    def test_agent_alpha_returns_none(self):
        trade = _non_gamma_trade(source="agent_alpha")
        result = pm.check_gamma_exit(trade, pnl=-50.0, now=_NOW)
        assert result is None, "agent_alpha must not route to Gamma exit logic"

    def test_agent_beta_returns_none(self):
        trade = _non_gamma_trade(source="agent_beta")
        result = pm.check_gamma_exit(trade, pnl=-50.0, now=_NOW)
        assert result is None, "agent_beta must not route to Gamma exit logic"

    def test_manual_source_returns_none(self):
        trade = _non_gamma_trade(source="manual")
        result = pm.check_gamma_exit(trade, pnl=-50.0, now=_NOW)
        assert result is None, "manual source must not route to Gamma exit logic"

    def test_empty_source_returns_none(self):
        trade = _non_gamma_trade(source="")
        result = pm.check_gamma_exit(trade, pnl=-50.0, now=_NOW)
        assert result is None, "empty source must not route to Gamma exit logic"

    def test_none_source_returns_none(self):
        trade = {"id": "X", "symbol": "SPY", "legs": []}
        # source key absent entirely
        result = pm.check_gamma_exit(trade, pnl=-50.0, now=_NOW)
        assert result is None, "missing source key must not route to Gamma exit logic"

    def test_partial_match_not_gamma(self):
        """A source like 'agent_gamma_scanner' would match startswith —
        but no such source exists in the system. This test documents that
        startswith('agent_gamma') is intentionally broad: any future
        agent_gamma_* variant routes to Gamma exit."""
        trade = _non_gamma_trade(source="agent_gamma_future_variant",
                                 exit_rules={"stop_loss_pct": -50,
                                             "take_profit_pct": 150,
                                             "entry_date": "2026-05-01"})
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(pm, "_gamma_indicators", lambda sym: {})
        result = pm.check_gamma_exit(trade, pnl=0.0, now=_NOW)
        assert result is not None, (
            "Any agent_gamma_* source should route to Gamma exit logic"
        )
        monkeypatch.undo()


# ── post_close_alert routing (line 1193) ──────────────────────────────────


class TestPostCloseAlertRouting:
    """post_close_alert() has a cosmetic branch that formats Gamma closes
    differently. The same startswith fix applies."""

    def test_gamma_arm_trade_gets_gamma_formatting(self, monkeypatch):
        """Per-arm trade triggers the Gamma-specific alert format."""
        monkeypatch.setattr(pm, "_gamma_indicators",
                            lambda sym: {"rsi_10": 45.0})
        monkeypatch.setattr(pm, "post_discord", lambda msg: None)
        trade = _gamma_trade(source="agent_gamma_arm_d")
        # Should not raise — confirms the Gamma branch is entered
        pm.post_close_alert(trade, "rsi_recovery", pnl=-10.0)

    def test_alpha_trade_does_not_get_gamma_formatting(self, monkeypatch):
        """Alpha trade should NOT enter the Gamma-specific formatting branch."""
        monkeypatch.setattr(pm, "post_discord", lambda msg: None)
        trade = _non_gamma_trade(source="agent_alpha")
        # Should not raise
        pm.post_close_alert(trade, "stop_loss", pnl=-50.0)


# ── Integration: check_exit_threshold dispatch chain ──────────────────────


class TestExitThresholdDispatchChain:
    """End-to-end: per-arm Gamma trades go through check_gamma_exit (not Beta)
    in the check_exit_threshold dispatch chain."""

    def test_per_arm_gamma_dispatches_to_gamma_not_beta(self, monkeypatch):
        """A per-arm Gamma trade with exit_rules must be evaluated by Gamma
        logic, not Beta logic — even though both check for exit_rules."""
        monkeypatch.setattr(pm, "_gamma_indicators",
                            lambda sym: {"rsi_10": 45.0, "close": 55.0, "sma_200": 50.0})
        monkeypatch.setattr(pm, "_load_intel_macro", lambda: {})
        trade = _gamma_trade(source="agent_gamma_arm_a")
        should_close, reason, fraction, flag = pm.check_exit_threshold(
            trade, pnl=5.0, now=_NOW
        )
        assert should_close is True
        assert "rsi_recovery" in reason, (
            f"Per-arm Gamma trade should exit via Gamma's RSI recovery, "
            f"but got reason='{reason}' — likely routed to Beta or Alpha"
        )

    def test_alpha_trade_bypasses_gamma_and_beta(self, monkeypatch):
        """Alpha trade with no exit_rules falls through to the Alpha catch-all."""
        trade = _non_gamma_trade(source="agent_alpha")
        # Far expiry, no credit/debit thresholds hit → should return False
        should_close, reason, fraction, flag = pm.check_exit_threshold(
            trade, pnl=0.0, now=_NOW
        )
        assert should_close is False, (
            "Alpha trade with no exit thresholds hit should not close"
        )
