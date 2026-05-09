"""Conviction-score entry and sizing gate.

Every agent computes a 1-10 conviction score before trade execution:
  Alpha: judge debate score (0-100) ÷ 10
  Beta:  reward-to-risk ratio mapping
  Gamma: RSI(10) depth below 30

This gate enforces three rules (applied in order):
  1. conviction < REJECT (3)   → block the trade entirely
  2. conviction < CONDOR (6)
     AND strategy is a condor
     AND >1 active condors open → block (condor concentration penalty)
  3. conviction < HALFSIZE (5) → allow at 0.5× position size
  4. else                      → allow at full size

Alpha always trades qty=1, so the half-size rule is structurally a no-op
for Alpha. Beta and Gamma benefit from both reject and half-size.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

CONVICTION_REJECT_THRESHOLD = 3
CONVICTION_HALFSIZE_THRESHOLD = 5
CONVICTION_CONDOR_THRESHOLD = 6


@dataclass
class ConvictionResult:
    allowed: bool
    reason: str
    size_multiplier: float  # 1.0 = full, 0.5 = half, 0.0 = rejected
    conviction_score: int


def check_conviction(
    conviction_score: int,
    strategy: str = "",
    active_condor_count: int = 0,
) -> ConvictionResult:
    """Check conviction thresholds and return allow/reject + sizing."""
    cs = conviction_score

    if cs < CONVICTION_REJECT_THRESHOLD:
        reason = (
            f"conviction too low: {cs}/10 < {CONVICTION_REJECT_THRESHOLD} — rejecting"
        )
        logger.warning(reason)
        return ConvictionResult(
            allowed=False, reason=reason, size_multiplier=0.0, conviction_score=cs,
        )

    if (
        cs < CONVICTION_CONDOR_THRESHOLD
        and "condor" in strategy.lower()
        and active_condor_count > 1
    ):
        reason = (
            f"condor concentration: conviction {cs}/10 < {CONVICTION_CONDOR_THRESHOLD} "
            f"with {active_condor_count} active condors — rejecting"
        )
        logger.warning(reason)
        return ConvictionResult(
            allowed=False, reason=reason, size_multiplier=0.0, conviction_score=cs,
        )

    if cs < CONVICTION_HALFSIZE_THRESHOLD:
        reason = (
            f"low conviction: {cs}/10 < {CONVICTION_HALFSIZE_THRESHOLD} — "
            f"allowing at 0.5× size"
        )
        logger.info(reason)
        return ConvictionResult(
            allowed=True, reason=reason, size_multiplier=0.5, conviction_score=cs,
        )

    return ConvictionResult(
        allowed=True,
        reason=f"conviction {cs}/10 — full size",
        size_multiplier=1.0,
        conviction_score=cs,
    )
