"""Arm C — WEIGHTED_BLEND. Average rank from Arm A + Arm B; lower-RSI tiebreak.

For each qualifying setup, compute its rank in Arm A (RSI_ONLY) AND its rank
in Arm B (COMPOSITE). Average the two ranks. Sort ascending — lower average
rank = better (rank 1 is best).

Tiebreak: when two setups share the same averaged rank, the one with the
LOWER RSI(10) wins. This biases the tiebreak toward Connors' canonical
oversold signal.

Score formula: ``_score = -blend_rank_avg`` (higher score = lower blend rank
= better). Field-level metadata also includes ``_rank_a`` and ``_rank_b`` so
the audit log can reconstruct each arm's contribution.
"""
from __future__ import annotations

from .composite import CompositeRanker
from .rsi_only import RsiOnlyRanker


class WeightedBlendRanker:
    """Ensemble of A and B: average their ranks, lower wins."""

    name = "weighted_blend"

    def __init__(self) -> None:
        self._a = RsiOnlyRanker()
        self._b = CompositeRanker()

    def rank(self, qualifying_setups: list[dict], context: dict) -> list[dict]:
        if not qualifying_setups:
            return []

        # Run A and B independently. Each returns a ranked copy with _rank.
        a_ranked = self._a.rank(qualifying_setups, context)
        b_ranked = self._b.rank(qualifying_setups, context)
        a_rank_by_sym = {s["symbol"]: s["_rank"] for s in a_ranked}
        b_rank_by_sym = {s["symbol"]: s["_rank"] for s in b_ranked}

        # Build new copies with blend metadata
        setups = [dict(s) for s in qualifying_setups]
        for s in setups:
            sym = s["symbol"]
            blend = (a_rank_by_sym[sym] + b_rank_by_sym[sym]) / 2.0
            s["_blend_rank_avg"] = blend
            s["_rank_a"] = a_rank_by_sym[sym]
            s["_rank_b"] = b_rank_by_sym[sym]

        # Sort by blend ascending; tiebreak: lower RSI wins
        ranked = sorted(
            setups,
            key=lambda s: (s["_blend_rank_avg"], float(s["rsi_10"])),
        )
        for i, s in enumerate(ranked):
            s["_rank"] = i + 1
            # Negate blend so higher score = better (consistent with other rankers)
            s["_score"] = -s["_blend_rank_avg"]
        return ranked
