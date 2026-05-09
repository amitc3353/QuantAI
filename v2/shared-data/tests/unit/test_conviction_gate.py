"""Unit tests for _conviction_gate.py — conviction-score entry and sizing gate.

Gate rules (applied in order):
  1. conviction < 3           → REJECT (too weak to trade at any size)
  2. conviction < 6
     AND strategy is condor
     AND active_condor_count > 1  → REJECT (condor concentration penalty)
  3. conviction < 5           → ALLOW at 0.5× size
  4. else                     → ALLOW at 1.0× size

All three agents compute conviction on a 1-10 integer scale:
  Alpha: judge_score (0-100) ÷ 10
  Beta:  reward-to-risk ratio mapping
  Gamma: RSI(10) depth mapping
"""
from __future__ import annotations

import pytest

from _conviction_gate import (
    CONVICTION_CONDOR_THRESHOLD,
    CONVICTION_HALFSIZE_THRESHOLD,
    CONVICTION_REJECT_THRESHOLD,
    ConvictionResult,
    check_conviction,
)


# ── Constants ────────────────────────────────────────────────────────────────


class TestConstants:
    def test_reject_threshold_is_3(self):
        assert CONVICTION_REJECT_THRESHOLD == 3

    def test_halfsize_threshold_is_5(self):
        assert CONVICTION_HALFSIZE_THRESHOLD == 5

    def test_condor_threshold_is_6(self):
        assert CONVICTION_CONDOR_THRESHOLD == 6


# ── Reject (conviction < 3) ─────────────────────────────────────────────────


class TestReject:
    def test_conviction_0_rejected(self):
        result = check_conviction(0)
        assert result.allowed is False
        assert "conviction" in result.reason.lower()

    def test_conviction_1_rejected(self):
        result = check_conviction(1)
        assert result.allowed is False

    def test_conviction_2_rejected(self):
        result = check_conviction(2)
        assert result.allowed is False

    def test_conviction_3_not_rejected(self):
        """Exactly at threshold — NOT below it."""
        result = check_conviction(3)
        assert result.allowed is True


# ── Half-size (3 <= conviction < 5) ─────────────────────────────────────────


class TestHalfSize:
    def test_conviction_3_half_size(self):
        result = check_conviction(3)
        assert result.allowed is True
        assert result.size_multiplier == 0.5

    def test_conviction_4_half_size(self):
        result = check_conviction(4)
        assert result.allowed is True
        assert result.size_multiplier == 0.5

    def test_conviction_5_full_size(self):
        """Exactly at threshold — full size."""
        result = check_conviction(5)
        assert result.allowed is True
        assert result.size_multiplier == 1.0


# ── Full size (conviction >= 5) ──────────────────────────────────────────────


class TestFullSize:
    def test_conviction_5(self):
        result = check_conviction(5)
        assert result.allowed is True
        assert result.size_multiplier == 1.0

    def test_conviction_7(self):
        result = check_conviction(7)
        assert result.allowed is True
        assert result.size_multiplier == 1.0

    def test_conviction_10(self):
        result = check_conviction(10)
        assert result.allowed is True
        assert result.size_multiplier == 1.0


# ── Condor concentration (conviction < 6 + condor + count > 1) ──────────────


class TestCondorConcentration:
    def test_low_conviction_condor_with_existing_rejected(self):
        """conviction=5, iron_condor, 2 active condors → rejected."""
        result = check_conviction(5, strategy="iron_condor", active_condor_count=2)
        assert result.allowed is False
        assert "condor" in result.reason.lower()

    def test_low_conviction_condor_single_existing_allowed(self):
        """conviction=5, iron_condor, only 1 active condor → allowed (count not > 1)."""
        result = check_conviction(5, strategy="iron_condor", active_condor_count=1)
        assert result.allowed is True
        assert result.size_multiplier == 1.0

    def test_very_low_conviction_condor_rejected_by_rule1(self):
        """conviction=2, iron_condor, 2 active → rejected by rule 1 (< 3), not rule 2."""
        result = check_conviction(2, strategy="iron_condor", active_condor_count=2)
        assert result.allowed is False

    def test_half_size_conviction_condor_with_existing_rejected(self):
        """conviction=4, iron_condor, 2 active condors → rejected by rule 2."""
        result = check_conviction(4, strategy="iron_condor", active_condor_count=2)
        assert result.allowed is False

    def test_half_size_conviction_condor_no_existing_allowed(self):
        """conviction=4, iron_condor, 0 active condors → allowed at 0.5×."""
        result = check_conviction(4, strategy="iron_condor", active_condor_count=0)
        assert result.allowed is True
        assert result.size_multiplier == 0.5

    def test_at_condor_threshold_allowed(self):
        """conviction=6, iron_condor, 3 active condors → allowed (6 not < 6)."""
        result = check_conviction(6, strategy="iron_condor", active_condor_count=3)
        assert result.allowed is True
        assert result.size_multiplier == 1.0

    def test_non_condor_strategy_skips_condor_rule(self):
        """conviction=3, bull_put_spread, 2 active condors → allowed at 0.5× (not condor)."""
        result = check_conviction(3, strategy="bull_put_spread", active_condor_count=2)
        assert result.allowed is True
        assert result.size_multiplier == 0.5

    def test_condor_count_zero_skips_condor_rule(self):
        """conviction=3, iron_condor, 0 active → allowed at 0.5× (count not > 1)."""
        result = check_conviction(3, strategy="iron_condor", active_condor_count=0)
        assert result.allowed is True
        assert result.size_multiplier == 0.5

    def test_condor_in_strategy_name_case_insensitive(self):
        """Strategy name matching is case-insensitive."""
        result = check_conviction(5, strategy="Iron_Condor", active_condor_count=2)
        assert result.allowed is False


# ── Result fields ────────────────────────────────────────────────────────────


class TestResultFields:
    def test_conviction_score_populated_on_reject(self):
        result = check_conviction(1)
        assert result.conviction_score == 1

    def test_conviction_score_populated_on_allow(self):
        result = check_conviction(8)
        assert result.conviction_score == 8

    def test_size_multiplier_on_reject(self):
        """Rejected trades still carry a size_multiplier (0.0 by convention)."""
        result = check_conviction(1)
        assert result.size_multiplier == 0.0
