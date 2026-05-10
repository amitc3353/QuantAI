"""Gamma A/B/C/D ranking strategies (added 2026-05-10).

The Gamma agent's existing ``filter_setups()`` ranks qualifying setups by
RSI ascending and takes the top ``MAX_DAILY_ENTRIES``. The 4-arm A/B/C/D
test evaluates four ranking philosophies in parallel for ≥60 days; the
winner becomes the production ranker. See
``docs/gamma-four-arm-ab-test-plan.md`` for the full design.

Each ranker takes the qualifying-setup list (output of
``scan_with_indicators()``) plus a context dict and returns the same setups
ordered best-first with two attached fields:

* ``_rank`` (1-indexed position in the order; 1 = best)
* ``_score`` (numeric, higher = better; for debugging/logging)

Rankers do NOT mutate input — each makes a shallow copy of each setup before
adding fields. Callers can safely share a single setup list across all four
rankers.

Registry::

    from gamma.rankers import RANKERS, ARM_TO_RANKER, get_ranker

    ranker = get_ranker("a")          # → RsiOnlyRanker (Arm A)
    ranker = get_ranker("composite")  # → CompositeRanker
    ranked = ranker.rank(setups, {"vix": 17.4, "today": today})
"""
from __future__ import annotations

from typing import Protocol

from .composite import CompositeRanker
from .reward_risk_first import RewardRiskFirstRanker
from .rsi_only import RsiOnlyRanker
from .weighted_blend import WeightedBlendRanker


class Ranker(Protocol):
    name: str

    def rank(
        self, qualifying_setups: list[dict], context: dict
    ) -> list[dict]: ...


RANKERS: dict[str, Ranker] = {
    "rsi_only": RsiOnlyRanker(),
    "composite": CompositeRanker(),
    "weighted_blend": WeightedBlendRanker(),
    "reward_risk_first": RewardRiskFirstRanker(),
}

# Arm letter → registry key. Used by the 4-arm dispatch in gamma_agent.py.
ARM_TO_RANKER: dict[str, str] = {
    "a": "rsi_only",
    "b": "composite",
    "c": "weighted_blend",
    "d": "reward_risk_first",
}

# Default ranker used when GAMMA_AB_TEST_ENABLED=0 (single-arm fallback).
# This keeps current production behavior — Connors' "lowest RSI first."
RANKER_DEFAULT = "rsi_only"


def get_ranker(arm_id_or_name: str) -> Ranker:
    """Resolve either an arm letter (a/b/c/d) or a ranker name to a Ranker.

    Raises KeyError if the input matches neither. The 4-arm dispatch path
    uses arm letters; the fallback (single-ranker) path uses
    ``RANKER_DEFAULT`` directly.
    """
    if arm_id_or_name in ARM_TO_RANKER:
        return RANKERS[ARM_TO_RANKER[arm_id_or_name]]
    if arm_id_or_name in RANKERS:
        return RANKERS[arm_id_or_name]
    raise KeyError(
        f"Unknown ranker or arm: {arm_id_or_name!r}. "
        f"Valid arms: {sorted(ARM_TO_RANKER)}. "
        f"Valid ranker names: {sorted(RANKERS)}"
    )


__all__ = [
    "Ranker",
    "RANKERS",
    "ARM_TO_RANKER",
    "RANKER_DEFAULT",
    "get_ranker",
    "RsiOnlyRanker",
    "CompositeRanker",
    "WeightedBlendRanker",
    "RewardRiskFirstRanker",
]
