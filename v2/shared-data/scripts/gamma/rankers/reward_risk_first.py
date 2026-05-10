"""Arm D — REWARD_RISK_FIRST. Sort by reward:risk descending; lower-RSI tiebreak.

Added per user review (2026-05-10) to force divergence from the
RSI-correlated A/B/C trio. Tests whether "best spread economics" beats
"deepest oversold" as the primary ranking philosophy.

Score formula: ``_score = reward_risk_estimate`` (higher = better).
Setups with missing r:r (estimator failure) get score = 0.0 → ranked last.
Tiebreak: lower RSI(10) wins.
"""
from __future__ import annotations


class RewardRiskFirstRanker:
    """Sort by reward:risk descending, lower-RSI tiebreak."""

    name = "reward_risk_first"

    def rank(self, qualifying_setups: list[dict], context: dict) -> list[dict]:
        # Shallow copy
        setups = [dict(s) for s in qualifying_setups]
        # Use 0.0 for missing reward_risk_estimate (estimator failed for this
        # symbol). The symbol still ranks below any successful estimate.
        ranked = sorted(
            setups,
            key=lambda s: (
                -(s.get("reward_risk_estimate") or 0.0),
                float(s["rsi_10"]),
            ),
        )
        for i, s in enumerate(ranked):
            s["_rank"] = i + 1
            s["_score"] = float(s.get("reward_risk_estimate") or 0.0)
        return ranked
