"""Integration tests: conviction gate with agent conviction-score functions.

Verifies end-to-end flow: agent-specific conviction computation → gate check
→ sizing decision, using the real helper functions from _decision_helpers.py.
"""
from __future__ import annotations

import pytest

from _conviction_gate import check_conviction
from _decision_helpers import (
    alpha_conviction_from_judge,
    rsi_depth_score,
    signal_strength_score,
)


class TestAlphaConvictionPath:
    """Simulates Alpha: judge_score → conviction → gate."""

    def test_low_judge_score_rejected(self):
        """judge_score=15 → conviction=2 → rejected."""
        conv = alpha_conviction_from_judge(15)
        assert conv == 2
        result = check_conviction(conv, strategy="bull_put_spread")
        assert result.allowed is False

    def test_medium_judge_score_half_size(self):
        """judge_score=42 → conviction=4 → half-size."""
        conv = alpha_conviction_from_judge(42)
        assert conv == 4
        result = check_conviction(conv, strategy="bull_put_spread")
        assert result.allowed is True
        assert result.size_multiplier == 0.5

    def test_high_judge_score_full_size(self):
        """judge_score=75 → conviction=8 → full size."""
        conv = alpha_conviction_from_judge(75)
        assert conv == 8
        result = check_conviction(conv, strategy="iron_condor")
        assert result.allowed is True
        assert result.size_multiplier == 1.0

    def test_condor_concentration_with_medium_conviction(self):
        """judge_score=48 → conviction=5, condor, 2 active → rejected by condor rule."""
        conv = alpha_conviction_from_judge(48)
        assert conv == 5
        result = check_conviction(conv, strategy="iron_condor", active_condor_count=2)
        assert result.allowed is False


class TestBetaConvictionPath:
    """Simulates Beta: R:R → conviction → gate."""

    def test_low_rr_half_size(self):
        """R:R = 1.2 → conviction=3 → half-size."""
        conv = signal_strength_score({"reward_to_risk": 1.2})
        assert conv == 3
        result = check_conviction(conv, strategy="event_strangle")
        assert result.allowed is True
        assert result.size_multiplier == 0.5

    def test_good_rr_full_size(self):
        """R:R = 3.5 → conviction=7 → full size."""
        conv = signal_strength_score({"reward_to_risk": 3.5})
        assert conv == 7
        result = check_conviction(conv, strategy="broken_wing_butterfly")
        assert result.allowed is True
        assert result.size_multiplier == 1.0


class TestGammaConvictionPath:
    """Simulates Gamma: RSI(10) → conviction → gate."""

    def test_rsi_above_threshold_half_size(self):
        """RSI(10)=30.5 → conviction=4 → half-size."""
        conv = rsi_depth_score(30.5)
        assert conv == 4
        result = check_conviction(conv, strategy="rsi_pullback_debit_spread")
        assert result.allowed is True
        assert result.size_multiplier == 0.5

    def test_deep_oversold_full_size(self):
        """RSI(10)=12.0 → conviction=9 → full size."""
        conv = rsi_depth_score(12.0)
        assert conv == 9
        result = check_conviction(conv, strategy="rsi_pullback_debit_spread")
        assert result.allowed is True
        assert result.size_multiplier == 1.0
