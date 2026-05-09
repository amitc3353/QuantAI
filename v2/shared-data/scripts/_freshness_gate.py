"""Data freshness gate.

Blocks entry when the market intelligence packet is too old to be trusted.

Thresholds:
  - Event-regime trades (Beta PRE_EVENT / Alpha is_event_day): 300s (5 min).
    Stale event intel means the event may have already released; the regime
    label would be wrong and any event-dependent strategy invalid.
  - All other trades: 1200s (20 min). Alpha's pipeline takes 12-17 min by
    design; this catches genuinely broken or wrong-day packets without
    false-positives in normal operation.

vix_timestamp is checked separately when present (written by
market_intelligence.py at VIX fetch time). Falls back to the top-level
intel timestamp if absent.

Missing or unparseable timestamps → fail-closed (block).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

MAX_INTEL_AGE_EVENT_SECONDS = 300    # 5 min — event-regime trades
MAX_INTEL_AGE_GENERAL_SECONDS = 1200  # 20 min — all other trades

logger = logging.getLogger(__name__)

UTC = timezone.utc


@dataclass
class FreshnessResult:
    allowed: bool
    reason: str
    age_seconds: int
    field: str  # which timestamp triggered the block, or "passed"


def _age_of(timestamp_iso: str | None) -> int | None:
    """Return age in seconds, or None if timestamp is missing/unparseable."""
    if not timestamp_iso:
        return None
    try:
        ts = datetime.fromisoformat(str(timestamp_iso).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        return max(0, int((datetime.now(UTC) - ts).total_seconds()))
    except Exception:
        return None


def check_freshness(intel: dict, is_event_trade: bool = False) -> FreshnessResult:
    """Return FreshnessResult for the current intel packet.

    Checks the top-level intel timestamp and, if present, the VIX timestamp.
    Returns allowed=False when any checked field exceeds its threshold or
    when a timestamp is missing / unparseable.
    """
    threshold = MAX_INTEL_AGE_EVENT_SECONDS if is_event_trade else MAX_INTEL_AGE_GENERAL_SECONDS
    event_label = " (event-regime)" if is_event_trade else ""

    # ── Intel packet timestamp ────────────────────────────────────────────────
    intel_ts = intel.get("timestamp")
    intel_age = _age_of(intel_ts)

    if intel_age is None:
        reason = "freshness_gate: intel timestamp missing or unparseable — failing closed"
        logger.warning(reason)
        return FreshnessResult(allowed=False, reason=reason, age_seconds=0, field="intel_timestamp")

    if intel_age > threshold:
        reason = (
            f"freshness_gate: intel packet stale{event_label} "
            f"({intel_age}s > {threshold}s threshold)"
        )
        logger.warning(reason)
        return FreshnessResult(allowed=False, reason=reason, age_seconds=intel_age, field="intel_timestamp")

    # ── VIX timestamp (optional; only checked when explicitly present) ────────
    vix_ts = (intel.get("macro") or {}).get("vix_timestamp")
    if vix_ts is not None:
        vix_age = _age_of(vix_ts)
        if vix_age is None:
            reason = "freshness_gate: vix_timestamp unparseable — failing closed"
            logger.warning(reason)
            return FreshnessResult(allowed=False, reason=reason, age_seconds=intel_age, field="vix_timestamp")
        if vix_age > threshold:
            reason = (
                f"freshness_gate: vix data stale{event_label} "
                f"({vix_age}s > {threshold}s threshold)"
            )
            logger.warning(reason)
            return FreshnessResult(allowed=False, reason=reason, age_seconds=vix_age, field="vix_timestamp")

    return FreshnessResult(allowed=True, reason="passed", age_seconds=intel_age, field="passed")
