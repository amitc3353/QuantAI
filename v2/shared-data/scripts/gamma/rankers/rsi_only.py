"""Arm A — RSI_ONLY (control). Sort by RSI(10) ascending; alphabetical tiebreak.

This is the existing Gamma logic, lifted into the ranker abstraction.
With ``GAMMA_AB_TEST_ENABLED=0`` (default) this is the single-arm production
ranker and behavior is unchanged from pre-experiment Gamma.

Score formula: ``_score = -rsi_10`` (higher score = lower RSI = better).
"""
from __future__ import annotations


class RsiOnlyRanker:
    """Sort by RSI(10) ascending, alphabetical tiebreak."""

    name = "rsi_only"

    def rank(self, qualifying_setups: list[dict], context: dict) -> list[dict]:
        # Shallow-copy each setup so we don't mutate caller's list. The
        # 4-arm dispatch passes the same setup list to all 4 rankers, so
        # mutation would cross-contaminate.
        ranked = sorted(
            (dict(s) for s in qualifying_setups),
            key=lambda s: (s["rsi_10"], s["symbol"]),
        )
        for i, s in enumerate(ranked):
            s["_rank"] = i + 1
            s["_score"] = -s["rsi_10"]  # higher score = better (lower RSI)
        return ranked
