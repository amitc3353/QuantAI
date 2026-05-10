"""Tests for the 4 ranker implementations + reward:risk estimator parity.

Per the 4-arm A/B/C/D test plan
(``docs/gamma-four-arm-ab-test-plan.md``), this file covers:

* Ranker correctness — each of A/B/C/D produces expected ordering on
  hand-computable fixtures.
* Normalization edge cases — single symbol, all-equal values.
* Output schema — ``_rank`` and ``_score`` attached; input list NOT mutated.
* Registry & arm-letter dispatch.
* **Parity check (pre-flight item 8b)** — ``compute_reward_risk_estimate()``
  vs ``build_spread()`` on a 20-symbol fixture set, asserts median absolute
  delta in r:r < 15%.
"""
from __future__ import annotations

import statistics
import sys
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from gamma.rankers import (  # noqa: E402
    ARM_TO_RANKER,
    RANKER_DEFAULT,
    RANKERS,
    CompositeRanker,
    RewardRiskFirstRanker,
    RsiOnlyRanker,
    WeightedBlendRanker,
    get_ranker,
)
from gamma.reward_risk_estimator import (  # noqa: E402
    compute_reward_risk_estimates,
    estimate_one,
)


# ─────────────────────────────────────────────────────────
# Setup fixture builder
# ─────────────────────────────────────────────────────────


def _setup(symbol: str, rsi: float, price: float, sma200: float,
            sma50: float, rr: float | None = 1.5) -> dict:
    """Build one qualifying-setup dict with all factor fields populated."""
    return {
        "symbol": symbol,
        "rsi_10": rsi,
        "close": price,
        "sma_200": sma200,
        "distance_above_200ma_pct": (price - sma200) / sma200 * 100,
        "distance_above_50ma_pct": (price - sma50) / sma50 * 100,
        "reward_risk_estimate": rr,
    }


def _five_setups() -> list[dict]:
    """5-setup synthetic scan used by multiple tests."""
    return [
        _setup("AAPL", 28.5, 200, 180, 195, rr=1.85),
        _setup("TMO", 26.0, 500, 470, 490, rr=1.20),
        _setup("JNJ", 29.8, 150, 145, 148, rr=2.10),
        _setup("GILD", 27.5, 90, 85, 88, rr=1.50),
        _setup("PEP", 28.0, 170, 160, 165, rr=1.65),
    ]


# ─────────────────────────────────────────────────────────
# RsiOnlyRanker (Arm A)
# ─────────────────────────────────────────────────────────


class TestRsiOnlyRanker:
    def test_ranks_by_rsi_ascending(self):
        ranked = RsiOnlyRanker().rank(_five_setups(), {})
        assert [s["symbol"] for s in ranked] == ["TMO", "GILD", "PEP", "AAPL", "JNJ"]

    def test_tiebreak_alphabetical(self):
        setups = [
            _setup("BBB", 28.0, 100, 90, 95),
            _setup("AAA", 28.0, 100, 90, 95),
            _setup("CCC", 28.0, 100, 90, 95),
        ]
        ranked = RsiOnlyRanker().rank(setups, {})
        assert [s["symbol"] for s in ranked] == ["AAA", "BBB", "CCC"]

    def test_attaches_rank_and_score(self):
        ranked = RsiOnlyRanker().rank(_five_setups(), {})
        for i, s in enumerate(ranked):
            assert s["_rank"] == i + 1
            assert s["_score"] == -s["rsi_10"]

    def test_does_not_mutate_input(self):
        setups = _five_setups()
        RsiOnlyRanker().rank(setups, {})
        for s in setups:
            assert "_rank" not in s
            assert "_score" not in s

    def test_empty_input(self):
        assert RsiOnlyRanker().rank([], {}) == []


# ─────────────────────────────────────────────────────────
# CompositeRanker (Arm B) — pure 4-factor, no VIX dampener
# ─────────────────────────────────────────────────────────


class TestCompositeRanker:
    def test_no_vix_dampener_per_2026_05_10_review(self):
        """Per user review 2026-05-10: VIX dampener REMOVED. Score must be
        identical regardless of context['vix']."""
        setups = _five_setups()
        ranker = CompositeRanker()
        scores_low = [s["_score"] for s in ranker.rank(setups, {"vix": 15.0})]
        scores_high = [s["_score"] for s in ranker.rank(setups, {"vix": 35.0})]
        assert scores_low == scores_high

    def test_lower_rsi_higher_rr_wins(self):
        """In a 2-symbol set where LO has lower RSI AND higher r:r AND more
        SMA200 cushion, LO must rank first — every factor pushes it ahead."""
        setups = [
            _setup("LO", 25.0, 110, 90, 95, rr=2.0),
            _setup("HI", 30.0, 200, 195, 196, rr=1.0),
        ]
        ranked = CompositeRanker().rank(setups, {})
        assert ranked[0]["symbol"] == "LO"
        assert ranked[0]["_score"] > ranked[1]["_score"]

    def test_normalization_single_symbol_is_neutral(self):
        """Single symbol → all factors collapse to 0.5 default → score = 0.5
        (sum of weights = 1.0, each factor at 0.5)."""
        setups = [_setup("ONLY", 28.0, 100, 90, 95, rr=1.5)]
        ranked = CompositeRanker().rank(setups, {})
        assert abs(ranked[0]["_score"] - 0.5) < 1e-9

    def test_normalization_all_equal_values(self):
        """All symbols share identical factors → all scores = 0.5."""
        setups = [
            _setup("A", 28.0, 100, 90, 95, rr=1.5),
            _setup("B", 28.0, 100, 90, 95, rr=1.5),
            _setup("C", 28.0, 100, 90, 95, rr=1.5),
        ]
        ranked = CompositeRanker().rank(setups, {})
        assert all(abs(s["_score"] - 0.5) < 1e-9 for s in ranked)

    def test_handles_missing_reward_risk(self):
        """When estimator fails (rr is None), composite uses 0.0 — symbol
        still ranks but loses on the r:r factor."""
        setups = _five_setups()
        setups[0]["reward_risk_estimate"] = None
        # Should not raise
        ranked = CompositeRanker().rank(setups, {})
        assert all("_score" in s for s in ranked)
        assert len(ranked) == 5

    def test_attaches_factor_breakdown(self):
        ranked = CompositeRanker().rank(_five_setups(), {})
        for s in ranked:
            assert "_factor_breakdown" in s
            fb = s["_factor_breakdown"]
            assert "rsi_score" in fb and "rr_norm" in fb
            assert "sma200_norm" in fb and "sma50_norm" in fb

    def test_does_not_mutate_input(self):
        setups = _five_setups()
        CompositeRanker().rank(setups, {})
        for s in setups:
            assert "_score" not in s
            assert "_factor_breakdown" not in s

    def test_weights_sum_to_one(self):
        """Weights must sum to 1.0 so score is a proper weighted average."""
        assert abs(sum(CompositeRanker.WEIGHTS.values()) - 1.0) < 1e-9


# ─────────────────────────────────────────────────────────
# WeightedBlendRanker (Arm C)
# ─────────────────────────────────────────────────────────


class TestWeightedBlendRanker:
    def test_blend_rank_is_average_of_a_and_b(self):
        ranked = WeightedBlendRanker().rank(_five_setups(), {})
        for s in ranked:
            assert s["_blend_rank_avg"] == (s["_rank_a"] + s["_rank_b"]) / 2.0

    def test_sorted_by_blend_ascending(self):
        ranked = WeightedBlendRanker().rank(_five_setups(), {})
        for i in range(len(ranked) - 1):
            # Sort key: (blend_avg, rsi_10) — primary is blend asc
            cur, nxt = ranked[i], ranked[i + 1]
            assert (cur["_blend_rank_avg"], cur["rsi_10"]) <= (
                nxt["_blend_rank_avg"], nxt["rsi_10"],
            )

    def test_tiebreak_lower_rsi_wins(self):
        """When blend_rank ties, lower RSI wins."""
        # Symbols where A and B disagree such that two have tied blend
        # X: rank_a=3, rank_b=1 → blend 2.0, RSI 27
        # Y: rank_a=1, rank_b=3 → blend 2.0, RSI 25 ← lower RSI, should win
        # Z: rank_a=2, rank_b=2 → blend 2.0, RSI 26
        setups = [
            # Set up RSI ascending order → rank_a: Y(1), Z(2), X(3)
            _setup("X", 27.0, 100, 90, 95, rr=2.5),
            _setup("Y", 25.0, 100, 90, 95, rr=0.5),
            _setup("Z", 26.0, 100, 90, 95, rr=1.5),
        ]
        ranked = WeightedBlendRanker().rank(setups, {})
        # All three may have similar but not identical blends; verify
        # tiebreak applies when blends tie.
        # Find two with same blend
        blends = [(s["symbol"], s["_blend_rank_avg"], s["rsi_10"]) for s in ranked]
        # Sort by blend then RSI ascending — that's exactly what the ranker does
        sorted_expected = sorted(blends, key=lambda b: (b[1], b[2]))
        actual_order = [(s["symbol"], s["_blend_rank_avg"], s["rsi_10"]) for s in ranked]
        assert actual_order == sorted_expected

    def test_attaches_rank_a_and_rank_b(self):
        ranked = WeightedBlendRanker().rank(_five_setups(), {})
        for s in ranked:
            assert "_rank_a" in s and "_rank_b" in s
            assert isinstance(s["_rank_a"], int) and isinstance(s["_rank_b"], int)

    def test_does_not_mutate_input(self):
        setups = _five_setups()
        WeightedBlendRanker().rank(setups, {})
        for s in setups:
            assert "_blend_rank_avg" not in s
            assert "_rank" not in s

    def test_empty_input(self):
        assert WeightedBlendRanker().rank([], {}) == []


# ─────────────────────────────────────────────────────────
# RewardRiskFirstRanker (Arm D)
# ─────────────────────────────────────────────────────────


class TestRewardRiskFirstRanker:
    def test_ranks_by_reward_risk_descending(self):
        """Per fixture: JNJ 2.10 > AAPL 1.85 > PEP 1.65 > GILD 1.50 > TMO 1.20."""
        ranked = RewardRiskFirstRanker().rank(_five_setups(), {})
        assert [s["symbol"] for s in ranked] == [
            "JNJ", "AAPL", "PEP", "GILD", "TMO",
        ]

    def test_tiebreak_lower_rsi_wins(self):
        """When r:r is equal, lower RSI(10) wins."""
        setups = [
            _setup("HI_RSI", 29.0, 100, 90, 95, rr=2.0),
            _setup("LO_RSI", 25.0, 100, 90, 95, rr=2.0),
            _setup("MID_RSI", 27.0, 100, 90, 95, rr=2.0),
        ]
        ranked = RewardRiskFirstRanker().rank(setups, {})
        assert [s["symbol"] for s in ranked] == ["LO_RSI", "MID_RSI", "HI_RSI"]

    def test_handles_missing_reward_risk(self):
        """Setups with reward_risk_estimate=None get 0.0 → ranked last.
        Among Nones, lower RSI tiebreaks normally."""
        setups = [
            _setup("FAIL_HI", 29.0, 100, 90, 95, rr=None),
            _setup("OK", 28.0, 100, 90, 95, rr=1.5),
            _setup("FAIL_LO", 25.0, 100, 90, 95, rr=None),
        ]
        # Explicit None
        setups[0]["reward_risk_estimate"] = None
        setups[2]["reward_risk_estimate"] = None
        ranked = RewardRiskFirstRanker().rank(setups, {})
        assert ranked[0]["symbol"] == "OK"
        # Both fails ranked after OK; among them, lower RSI wins
        assert ranked[1]["symbol"] == "FAIL_LO"
        assert ranked[2]["symbol"] == "FAIL_HI"

    def test_attaches_rank_and_score(self):
        ranked = RewardRiskFirstRanker().rank(_five_setups(), {})
        for i, s in enumerate(ranked):
            assert s["_rank"] == i + 1
            assert s["_score"] == s["reward_risk_estimate"]

    def test_does_not_mutate_input(self):
        setups = _five_setups()
        RewardRiskFirstRanker().rank(setups, {})
        for s in setups:
            assert "_rank" not in s

    def test_empty_input(self):
        assert RewardRiskFirstRanker().rank([], {}) == []


# ─────────────────────────────────────────────────────────
# Registry + ARM_TO_RANKER mapping
# ─────────────────────────────────────────────────────────


class TestRegistry:
    def test_all_four_rankers_registered(self):
        assert set(RANKERS.keys()) == {
            "rsi_only", "composite", "weighted_blend", "reward_risk_first",
        }

    def test_arm_letter_mapping(self):
        assert ARM_TO_RANKER == {
            "a": "rsi_only",
            "b": "composite",
            "c": "weighted_blend",
            "d": "reward_risk_first",
        }

    def test_get_ranker_by_arm_letter(self):
        assert get_ranker("a").name == "rsi_only"
        assert get_ranker("b").name == "composite"
        assert get_ranker("c").name == "weighted_blend"
        assert get_ranker("d").name == "reward_risk_first"

    def test_get_ranker_by_name(self):
        assert get_ranker("rsi_only").name == "rsi_only"
        assert get_ranker("composite").name == "composite"

    def test_get_ranker_invalid_raises(self):
        with pytest.raises(KeyError):
            get_ranker("nonexistent_ranker")

    def test_ranker_default_is_rsi_only(self):
        """RANKER_DEFAULT must be rsi_only — preserves pre-experiment Gamma
        behavior when GAMMA_AB_TEST_ENABLED=0."""
        assert RANKER_DEFAULT == "rsi_only"


# ─────────────────────────────────────────────────────────
# Reward:risk estimator
# ─────────────────────────────────────────────────────────


def _build_chain(price: float, expiry_iso: str | None = None) -> list[dict]:
    """Build a synthetic option chain around `price` with deltas + populated
    mids. Used by both estimator tests and the parity test.

    Strikes at $5 increments for prices ≥ $100, $1 increments for lower.

    **Pricing model** (calibrated for ~18 DTE, moderate-IV equity):

    * ATM extrinsic: ``price × 0.025`` (e.g., $5 for $200 stock)
    * Decay: ``extrinsic = atm_extrinsic × max(0, 1 − distance_pct × 14)``
      → at 2.5% OTM ≈ 65% of ATM extrinsic; at 5% OTM ≈ 30%; at ~7% OTM the
      extrinsic floors at $0.10. This produces r:r in the 1.0–3.0 range for
      a 5-wide spread, matching real-world 18-DTE bull-call-debit norms.

    **Delta** (linear approximation):
    ``delta = 0.50 − (k − price) / (price × 0.15)`` clamped to [0.05, 0.95].
    ATM = 0.50; delta-0.27 lands at ~3.45% OTM (matches build_spread's
    delta-0.27 short).

    ``fill_quote()`` no-ops when both ``mid`` AND ``delta`` are populated, so
    the test mocks don't have to stub the broker's ``get_option_quote``.
    """
    if expiry_iso is None:
        expiry_iso = (date.today() + timedelta(days=18)).isoformat()

    increment = 5 if price >= 100 else 1
    strike_min = (int(price * 0.85 / increment)) * increment
    strike_max = (int(price * 1.15 / increment)) * increment
    strikes = list(range(strike_min, strike_max + increment, increment))

    atm_extrinsic = price * 0.025
    chain: list[dict] = []
    for k in strikes:
        intrinsic_call = max(price - k, 0.0)
        # Extrinsic: ATM_extrinsic × (1 − 14 × distance_pct), floored
        distance_pct = abs(k - price) / price
        extrinsic = max(0.10, atm_extrinsic * max(0.0, 1.0 - 14.0 * distance_pct))
        mid_call = round(intrinsic_call + extrinsic, 2)
        spread_dollars = max(0.05, round(mid_call * 0.04, 2))
        bid = round(max(0.05, mid_call - spread_dollars / 2), 2)
        ask = round(mid_call + spread_dollars / 2, 2)
        # Delta: linear in k; clamped to [0.05, 0.95]
        raw_delta = 0.50 - (k - price) / (price * 0.15)
        delta = max(0.05, min(0.95, raw_delta))
        chain.append({
            "right": "C",
            "expiry": expiry_iso,
            "strike": float(k),
            "bid": bid,
            "ask": ask,
            "mid": mid_call,  # fill_quote no-ops when mid+delta populated
            "delta": round(delta, 3),
            "vega": 0.10,
            "symbol": f"FAKE{int(k)}",
        })
    return chain


class TestRewardRiskEstimator:
    def _broker(self, chain: list[dict]) -> MagicMock:
        broker = MagicMock()
        broker.fetch_option_chain.return_value = chain
        return broker

    def test_estimate_one_returns_reasonable_value(self):
        broker = self._broker(_build_chain(price=200.0))
        setup = _setup("AAPL", 28.0, 200.0, 180.0, 195.0)
        rr = estimate_one(setup, broker)
        assert rr is not None
        # Bull-call debit spreads in the 14-21 DTE window with reasonable
        # strikes typically deliver r:r in the 0.5-3.0 range.
        assert 0.3 < rr < 5.0, f"r:r {rr} out of plausible range"

    def test_estimate_one_handles_empty_chain(self):
        broker = MagicMock()
        broker.fetch_option_chain.return_value = []
        setup = _setup("XYZ", 28.0, 100.0, 90.0, 95.0)
        assert estimate_one(setup, broker) is None

    def test_estimate_one_handles_broker_exception(self):
        broker = MagicMock()
        broker.fetch_option_chain.side_effect = ConnectionError("network down")
        setup = _setup("XYZ", 28.0, 100.0, 90.0, 95.0)
        assert estimate_one(setup, broker) is None

    def test_compute_reward_risk_estimates_attaches_field(self):
        broker = self._broker(_build_chain(price=200.0))
        setups = [_setup("AAPL", 28.0, 200.0, 180.0, 195.0)]
        results, duration = compute_reward_risk_estimates(setups, broker)
        assert len(results) == 1
        assert "reward_risk_estimate" in results[0]
        assert results[0]["reward_risk_estimate"] is not None
        assert duration >= 0.0

    def test_compute_reward_risk_estimates_empty(self):
        broker = MagicMock()
        results, duration = compute_reward_risk_estimates([], broker)
        assert results == []
        assert duration == 0.0

    def test_compute_reward_risk_estimates_runs_in_parallel(self):
        """Five setups should complete quickly under parallel execution.
        Lenient bound (5s for mocked work) — real broker calls are slower."""
        import time as _time
        broker = self._broker(_build_chain(price=200.0))
        setups = [
            _setup("S1", 28.0, 200.0, 180.0, 195.0),
            _setup("S2", 27.0, 200.0, 180.0, 195.0),
            _setup("S3", 26.0, 200.0, 180.0, 195.0),
            _setup("S4", 25.0, 200.0, 180.0, 195.0),
            _setup("S5", 24.0, 200.0, 180.0, 195.0),
        ]
        t0 = _time.time()
        results, duration = compute_reward_risk_estimates(setups, broker)
        wall = _time.time() - t0
        assert all(s["reward_risk_estimate"] is not None for s in results)
        assert wall < 5.0
        # duration_sec should approximately match wall time (within 1s)
        assert abs(duration - wall) < 1.0


# ─────────────────────────────────────────────────────────
# Parity check: estimator vs build_spread (pre-flight item 8b)
# ─────────────────────────────────────────────────────────


class TestEstimatorVsBuildSpreadParity:
    """Per pre-flight item 8b: ``compute_reward_risk_estimate()`` vs
    ``build_spread()`` median absolute delta in r:r must be < 15% across a
    20-symbol fixture set with mocked chain data. Failure means the estimator
    is drifting from build_spread() and ranker decisions become unreliable.
    """

    FIXTURE_SYMBOLS: list[tuple[str, float, float]] = [
        ("AAPL", 200.0, 28.0),
        ("MSFT", 350.0, 27.5),
        ("NVDA", 450.0, 26.0),
        ("GOOGL", 140.0, 28.5),
        ("AMZN", 175.0, 29.0),
        ("TSLA", 250.0, 26.5),
        ("META", 380.0, 28.0),
        ("AVGO", 1100.0, 27.0),
        ("LLY", 580.0, 28.5),
        ("JNJ", 158.0, 29.5),
        ("UNH", 520.0, 28.0),
        ("XOM", 110.0, 27.5),
        ("CVX", 155.0, 28.0),
        ("PG", 165.0, 29.0),
        ("HD", 380.0, 27.0),
        ("V", 270.0, 28.5),
        ("MA", 470.0, 28.0),
        ("COST", 850.0, 28.5),
        ("JPM", 200.0, 27.5),
        ("PEP", 170.0, 28.0),
    ]

    def _setup_for(self, symbol: str, price: float, rsi: float) -> dict:
        return {
            "symbol": symbol,
            "close": price,
            "rsi_10": rsi,
            "sma_200": price * 0.92,
            "distance_above_200ma_pct": 8.7,
            "distance_above_50ma_pct": 4.5,
        }

    def test_estimator_vs_build_spread_median_delta(self, capsys):
        """20 fixture symbols. For each: build a synthetic chain, run
        ``estimate_one()`` and ``build_spread()`` with that chain, compute
        |estimate − actual| / |actual|. Median must be < 15%."""
        from gamma.strike_selector import build_spread

        deltas_pct: list[float] = []
        per_symbol: list[tuple[str, float | None, float | None, float | None]] = []

        for symbol, price, rsi in self.FIXTURE_SYMBOLS:
            chain = _build_chain(price=price)

            # Same broker mock for both calls so they see identical chain
            broker = MagicMock()
            broker.fetch_option_chain.return_value = chain

            setup = self._setup_for(symbol, price, rsi)

            estimate = estimate_one(setup, broker)
            today_iso = date.today().isoformat()
            proposal = build_spread(
                setup, broker, account_equity=10000.0, today_iso=today_iso
            )
            actual = proposal["reward_risk"] if proposal else None

            if estimate is None or actual is None or actual <= 0:
                per_symbol.append((symbol, estimate, actual, None))
                continue
            delta_pct = abs(estimate - actual) / actual
            per_symbol.append((symbol, estimate, actual, delta_pct))
            deltas_pct.append(delta_pct)

        # Visibility in test output (-s flag)
        print(f"\n  Parity check ({len(deltas_pct)}/{len(self.FIXTURE_SYMBOLS)} datapoints):")
        for sym, est, act, dp in per_symbol:
            if dp is None:
                print(f"    {sym:<6} estimate={est}, actual={act}  (skipped)")
            else:
                print(f"    {sym:<6} estimate={est:.2f}, actual={act:.2f}, "
                       f"delta={dp * 100:>5.2f}%")
        if deltas_pct:
            median_delta = statistics.median(deltas_pct)
            print(f"  Median r:r delta: {median_delta * 100:.2f}%")
        else:
            median_delta = None

        # Acceptance criteria
        assert len(deltas_pct) >= 10, (
            f"Parity test produced too few datapoints "
            f"({len(deltas_pct)}/{len(self.FIXTURE_SYMBOLS)}) — "
            "fixture chain or function logic broken"
        )
        assert median_delta < 0.15, (
            f"Median r:r delta {median_delta * 100:.2f}% exceeds 15% threshold. "
            "Estimator and build_spread() are drifting. Investigate before "
            "committing further to ranker dispatch."
        )


# ─────────────────────────────────────────────────────────
# Synthetic 5-setup scan reproducibility
# ─────────────────────────────────────────────────────────


class TestSynthetic5SetupScan:
    """Verify all 4 rankers produce expected outputs on a fixed 5-setup scan.
    The user's commit-1 acceptance criterion: all 4 rankers produce expected
    output on a synthetic 5-setup test scan."""

    def test_all_four_rankers_produce_complete_output(self):
        setups = _five_setups()
        for arm_id in ("a", "b", "c", "d"):
            ranked = get_ranker(arm_id).rank(setups, {})
            assert len(ranked) == 5, (
                f"Arm {arm_id} returned {len(ranked)} entries, expected 5"
            )
            assert all("_rank" in s for s in ranked), f"Arm {arm_id} missing _rank"
            assert all("_score" in s for s in ranked), f"Arm {arm_id} missing _score"
            ranks = sorted(s["_rank"] for s in ranked)
            assert ranks == [1, 2, 3, 4, 5], f"Arm {arm_id} _rank not 1..5: {ranks}"

    def test_arm_a_orders_by_rsi(self):
        """Arm A: lowest RSI first."""
        ranked = get_ranker("a").rank(_five_setups(), {})
        # Setups: AAPL 28.5, TMO 26.0, JNJ 29.8, GILD 27.5, PEP 28.0
        assert [s["symbol"] for s in ranked] == [
            "TMO", "GILD", "PEP", "AAPL", "JNJ",
        ]

    def test_arm_d_orders_by_reward_risk(self):
        """Arm D: highest r:r first."""
        ranked = get_ranker("d").rank(_five_setups(), {})
        # rr: JNJ 2.10, AAPL 1.85, PEP 1.65, GILD 1.50, TMO 1.20
        assert [s["symbol"] for s in ranked] == [
            "JNJ", "AAPL", "PEP", "GILD", "TMO",
        ]

    def test_arms_a_and_d_diverge_when_rsi_and_rr_decorrelated(self):
        """Construct a fixture where lowest RSI ≠ highest r:r — Arms A and D
        should produce different orderings."""
        setups = [
            _setup("LOW_RSI_LOW_RR", 25.0, 100, 90, 95, rr=0.9),
            _setup("HI_RSI_HI_RR", 29.0, 100, 90, 95, rr=2.5),
            _setup("MID", 27.0, 100, 90, 95, rr=1.5),
        ]
        a = get_ranker("a").rank(setups, {})
        d = get_ranker("d").rank(setups, {})
        assert a[0]["symbol"] == "LOW_RSI_LOW_RR"  # Arm A picks lowest RSI
        assert d[0]["symbol"] == "HI_RSI_HI_RR"     # Arm D picks highest r:r
        assert [s["symbol"] for s in a] != [s["symbol"] for s in d]

    def test_arm_c_blends_a_and_b(self):
        """Arm C's _rank_a and _rank_b must match what Arms A and B
        independently produced."""
        setups = _five_setups()
        a = get_ranker("a").rank(setups, {})
        b = get_ranker("b").rank(setups, {})
        c = get_ranker("c").rank(setups, {})
        a_ranks = {s["symbol"]: s["_rank"] for s in a}
        b_ranks = {s["symbol"]: s["_rank"] for s in b}
        for cs in c:
            assert cs["_rank_a"] == a_ranks[cs["symbol"]]
            assert cs["_rank_b"] == b_ranks[cs["symbol"]]
