"""Arm B — COMPOSITE. Pure 4-factor weighted score, no regime adjustment.

Score per setup is a weighted sum of within-day min-max-normalized factors:

* **45% × (1 - normalized RSI)** — lower RSI scores higher
* **30% × normalized reward:risk** — higher r:r scores higher
* **15% × normalized dist_above_SMA200** — more cushion above 200-MA scores higher
* **10% × normalized dist_above_SMA50** — recent trend strength scores higher

Normalization is **within-day** (min-max across the qualifying set on that
scan). This means a symbol's score is relative to the day's peer set, not an
absolute scale.

**No VIX dampener.** The original spec multiplied the final score by 0.7 if
VIX > 25, but per user review (2026-05-10) we removed it: the 25 threshold
and 0.7 multiplier are arbitrary and would muddy the test of the composite
philosophy itself. If composite (Arm B) wins this test, a regime adjustment
can be added later with evidence.

Failed reward:risk estimates (None) are treated as 0.0 → those symbols rank
last on the r:r factor only; other factors still contribute. This is by
design: a symbol whose r:r couldn't be computed gets penalized but isn't
hard-blocked from ranking (the volume / earnings filters already gate
qualifying setups; ranking is downstream).
"""
from __future__ import annotations


def _norm(value: float, vmin: float, vmax: float) -> float:
    """Min-max normalize to [0, 1]. Returns 0.5 if vmax == vmin (neutral),
    matching the user spec for single-symbol or all-equal cases."""
    if vmax == vmin:
        return 0.5
    return (value - vmin) / (vmax - vmin)


class CompositeRanker:
    """4-factor weighted composite. No VIX dampener."""

    name = "composite"

    WEIGHTS = {
        "rsi": 0.45,
        "reward_risk": 0.30,
        "dist_sma200": 0.15,
        "dist_sma50": 0.10,
    }

    def _factor_values(self, setups: list[dict]) -> dict[str, list[float]]:
        """Extract raw factor values across the qualifying set. None values
        for missing reward:risk and dist_sma50 are coerced to 0.0 so the
        normalizer has a numeric input."""
        return {
            "rsi": [float(s["rsi_10"]) for s in setups],
            "reward_risk": [float(s.get("reward_risk_estimate") or 0.0) for s in setups],
            "dist_sma200": [float(s["distance_above_200ma_pct"]) for s in setups],
            "dist_sma50": [float(s.get("distance_above_50ma_pct") or 0.0) for s in setups],
        }

    def rank(self, qualifying_setups: list[dict], context: dict) -> list[dict]:
        if not qualifying_setups:
            return []
        # Shallow copy to avoid mutating caller
        setups = [dict(s) for s in qualifying_setups]

        values = self._factor_values(setups)
        ranges = {k: (min(v), max(v)) for k, v in values.items()}

        for s in setups:
            # RSI: lower is better → invert normalization
            rsi_norm = _norm(float(s["rsi_10"]), *ranges["rsi"])
            rsi_score = 1.0 - rsi_norm
            rr_norm = _norm(
                float(s.get("reward_risk_estimate") or 0.0),
                *ranges["reward_risk"],
            )
            sma200_norm = _norm(
                float(s["distance_above_200ma_pct"]),
                *ranges["dist_sma200"],
            )
            sma50_norm = _norm(
                float(s.get("distance_above_50ma_pct") or 0.0),
                *ranges["dist_sma50"],
            )

            score = (
                self.WEIGHTS["rsi"] * rsi_score
                + self.WEIGHTS["reward_risk"] * rr_norm
                + self.WEIGHTS["dist_sma200"] * sma200_norm
                + self.WEIGHTS["dist_sma50"] * sma50_norm
            )
            s["_score"] = score
            s["_factor_breakdown"] = {
                "rsi_score": round(rsi_score, 3),
                "rr_norm": round(rr_norm, 3),
                "sma200_norm": round(sma200_norm, 3),
                "sma50_norm": round(sma50_norm, 3),
            }

        # Sort by score descending (higher = better)
        ranked = sorted(setups, key=lambda s: -s["_score"])
        for i, s in enumerate(ranked):
            s["_rank"] = i + 1
        return ranked
