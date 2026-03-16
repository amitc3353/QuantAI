"""
Guard Engine Tests
===================
Tests every guard from Section IV of the blueprint.
Run: python -m pytest tests/ -v
"""

import sys
import os
from unittest.mock import patch
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from guards import (
    TradeProposal,
    PortfolioState,
    run_guard_pipeline,
    check_halted,
    check_whitelist,
    check_position_size,
    check_max_loss,
    check_max_contracts,
    check_min_dte,
    check_liquidity,
    check_earnings_blackout,
    check_vix,
    check_portfolio_delta,
    check_portfolio_theta,
    check_max_positions,
    check_daily_loss,
    check_pmcc,
    check_bull_put,
    check_iron_condor,
    check_covered_call,
    CONFIG,
)


# ---------------------------------------------------------------------------
# Position-level guards
# ---------------------------------------------------------------------------

class TestPositionGuards:
    def test_position_size_pass(self):
        ok, _ = check_position_size(3.0)
        assert ok

    def test_position_size_fail(self):
        ok, msg = check_position_size(6.0)
        assert not ok
        assert "EXCEEDS" in msg

    def test_position_size_at_limit(self):
        ok, _ = check_position_size(5.0)
        assert ok

    def test_max_loss_pass(self):
        ok, _ = check_max_loss(1.5)
        assert ok

    def test_max_loss_fail(self):
        ok, _ = check_max_loss(3.0)
        assert not ok

    def test_max_contracts_pass(self):
        ok, _ = check_max_contracts(5)
        assert ok

    def test_max_contracts_fail(self):
        ok, _ = check_max_contracts(15)
        assert not ok

    def test_min_dte_pass(self):
        ok, _ = check_min_dte(30)
        assert ok

    def test_min_dte_fail(self):
        ok, _ = check_min_dte(7)
        assert not ok

    def test_min_dte_covered_call_exempt(self):
        ok, _ = check_min_dte(5, strategy="covered_call")
        assert ok

    def test_liquidity_pass(self):
        ok, _ = check_liquidity(500, 0.05)
        assert ok

    def test_liquidity_low_oi(self):
        ok, _ = check_liquidity(50, 0.05)
        assert not ok

    def test_liquidity_wide_spread(self):
        ok, _ = check_liquidity(500, 0.25)
        assert not ok


# ---------------------------------------------------------------------------
# Portfolio-level guards
# ---------------------------------------------------------------------------

class TestPortfolioGuards:
    def test_delta_pass(self):
        ok, _ = check_portfolio_delta(0.15)
        assert ok

    def test_delta_fail(self):
        ok, _ = check_portfolio_delta(0.45)
        assert not ok

    def test_delta_negative_pass(self):
        ok, _ = check_portfolio_delta(-0.20)
        assert ok

    def test_delta_negative_fail(self):
        ok, _ = check_portfolio_delta(-0.40)
        assert not ok

    def test_theta_pass(self):
        ok, _ = check_portfolio_theta(-30.0)
        assert ok

    def test_theta_fail(self):
        ok, _ = check_portfolio_theta(-60.0)
        assert not ok

    def test_max_positions_pass(self):
        ok, _ = check_max_positions(5)
        assert ok

    def test_max_positions_fail(self):
        ok, _ = check_max_positions(8)
        assert not ok

    def test_daily_loss_pass(self):
        ok, _ = check_daily_loss(-1.5)
        assert ok

    def test_daily_loss_fail(self):
        ok, _ = check_daily_loss(-3.5)
        assert not ok


# ---------------------------------------------------------------------------
# Timing guards
# ---------------------------------------------------------------------------

class TestTimingGuards:
    def test_earnings_blackout(self):
        ok, _ = check_earnings_blackout(True)
        assert not ok

    def test_no_earnings(self):
        ok, _ = check_earnings_blackout(False)
        assert ok

    def test_vix_pass(self):
        ok, _ = check_vix(22.0)
        assert ok

    def test_vix_fail(self):
        ok, _ = check_vix(40.0)
        assert not ok

    def test_vix_none_skips(self):
        ok, _ = check_vix(None)
        assert ok


# ---------------------------------------------------------------------------
# Whitelist
# ---------------------------------------------------------------------------

class TestWhitelist:
    def test_whitelisted_symbol(self):
        ok, _ = check_whitelist("NVDA")
        assert ok

    def test_non_whitelisted_symbol(self):
        ok, _ = check_whitelist("PLTR")
        assert not ok

    def test_case_insensitive(self):
        ok, _ = check_whitelist("spy")
        assert ok


# ---------------------------------------------------------------------------
# Strategy-specific guards
# ---------------------------------------------------------------------------

class TestStrategyGuards:
    def test_pmcc_pass(self):
        p = TradeProposal(symbol="AAPL", strategy="pmcc", leaps_delta=0.80, short_delta=0.20, leaps_dte=120)
        ok, _ = check_pmcc(p)
        assert ok

    def test_pmcc_bad_leaps_delta(self):
        p = TradeProposal(symbol="AAPL", strategy="pmcc", leaps_delta=0.60, short_delta=0.20, leaps_dte=120)
        ok, _ = check_pmcc(p)
        assert not ok

    def test_bull_put_pass(self):
        p = TradeProposal(symbol="SPY", strategy="bull_put", iv_rank=55, spread_width=4, credit_ratio=0.35)
        ok, _ = check_bull_put(p)
        assert ok

    def test_bull_put_low_iv(self):
        p = TradeProposal(symbol="SPY", strategy="bull_put", iv_rank=30)
        ok, _ = check_bull_put(p)
        assert not ok

    def test_iron_condor_pass(self):
        p = TradeProposal(symbol="SPY", strategy="iron_condor", iv_rank=65, sigma_distance=1.2, risk_credit_ratio=1.8)
        ok, _ = check_iron_condor(p)
        assert ok

    def test_iron_condor_low_iv(self):
        p = TradeProposal(symbol="SPY", strategy="iron_condor", iv_rank=40)
        ok, _ = check_iron_condor(p)
        assert not ok

    def test_covered_call_pass(self):
        p = TradeProposal(symbol="AAPL", strategy="covered_call", delta=0.25)
        ok, _ = check_covered_call(p)
        assert ok

    def test_covered_call_earnings_blocked(self):
        p = TradeProposal(symbol="AAPL", strategy="covered_call", delta=0.25, is_earnings_week=True)
        ok, _ = check_covered_call(p)
        assert not ok


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------

class TestFullPipeline:
    @patch("guards.datetime")
    def test_clean_trade_approves(self, mock_dt):
        # Mock to midday so timing guards pass
        mock_dt.now.return_value = datetime(2026, 3, 16, 11, 0, 0)
        mock_dt.utcnow.return_value = datetime(2026, 3, 16, 16, 0, 0)
        mock_dt.fromisoformat = datetime.fromisoformat
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        # Temporarily ensure not halted
        original = CONFIG.get("halted", False)
        CONFIG["halted"] = False

        proposal = TradeProposal(
            symbol="SPY",
            strategy="bull_put",
            position_pct=3.0,
            max_loss_pct=1.5,
            contracts=2,
            dte=30,
            open_interest=5000,
            bid_ask_spread=0.05,
            iv_rank=55,
            spread_width=4,
            credit_ratio=0.35,
        )
        result = run_guard_pipeline(proposal)
        CONFIG["halted"] = original
        assert result.result == "APPROVE"

    def test_halted_rejects(self):
        original = CONFIG.get("halted", False)
        CONFIG["halted"] = True

        proposal = TradeProposal(symbol="SPY", position_pct=1.0, max_loss_pct=0.5, dte=30)
        result = run_guard_pipeline(proposal)
        CONFIG["halted"] = original
        assert result.result == "REJECT"
        assert "HALTED" in result.reason

    def test_non_whitelisted_rejects(self):
        original = CONFIG.get("halted", False)
        CONFIG["halted"] = False

        proposal = TradeProposal(symbol="PLTR", position_pct=2.0, max_loss_pct=1.0, dte=30)
        result = run_guard_pipeline(proposal)
        CONFIG["halted"] = original
        assert result.result == "REJECT"
        assert "whitelist" in result.reason.lower()

    def test_oversized_position_rejects(self):
        original = CONFIG.get("halted", False)
        CONFIG["halted"] = False

        proposal = TradeProposal(symbol="SPY", position_pct=8.0, max_loss_pct=1.0, dte=30)
        result = run_guard_pipeline(proposal)
        CONFIG["halted"] = original
        assert result.result == "REJECT"

    @patch("guards.datetime")
    def test_portfolio_guards_checked(self, mock_dt):
        mock_dt.now.return_value = datetime(2026, 3, 16, 11, 0, 0)
        mock_dt.utcnow.return_value = datetime(2026, 3, 16, 16, 0, 0)
        mock_dt.fromisoformat = datetime.fromisoformat
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        original = CONFIG.get("halted", False)
        CONFIG["halted"] = False

        proposal = TradeProposal(
            symbol="SPY", position_pct=3.0, max_loss_pct=1.0, contracts=2,
            dte=30, open_interest=5000, bid_ask_spread=0.05,
        )
        portfolio = PortfolioState(
            net_delta=0.40,  # Over limit
            daily_theta=-30,
            open_positions=3,
        )
        result = run_guard_pipeline(proposal, portfolio)
        CONFIG["halted"] = original
        assert result.result == "REJECT"
        assert "delta" in result.reason.lower()
