"""Integration tests: freshness gate blocks at correct thresholds for each agent path.

Verifies that check_freshness returns the right decision for:
  - Fresh intel (always passes)
  - Stale intel in PRE_EVENT regime (Beta) → blocked at 300s threshold
  - Stale intel on is_event_day (Alpha) → blocked at 300s threshold
  - Stale intel in non-event regime → blocked at 1200s threshold
  - Missing intel timestamp → fail-closed regardless of regime
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from _freshness_gate import (
    MAX_INTEL_AGE_EVENT_SECONDS,
    MAX_INTEL_AGE_GENERAL_SECONDS,
    check_freshness,
)

UTC = timezone.utc


def _ts(seconds_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(seconds=seconds_ago)).isoformat()


class TestBetaPath:
    """Simulates beta_agent: is_event_trade = (regime == 'PRE_EVENT')."""

    def test_fresh_intel_pre_event_passes(self):
        intel = {"timestamp": _ts(30), "macro": {}}
        result = check_freshness(intel, is_event_trade=True)
        assert result.allowed is True

    def test_stale_pre_event_blocked_at_event_threshold(self):
        """Beta PRE_EVENT with 310s-old intel → blocked (event threshold = 300s)."""
        intel = {"timestamp": _ts(MAX_INTEL_AGE_EVENT_SECONDS + 10), "macro": {}}
        result = check_freshness(intel, is_event_trade=True)
        assert result.allowed is False
        assert result.age_seconds >= MAX_INTEL_AGE_EVENT_SECONDS

    def test_stale_non_event_passes_event_threshold(self):
        """Beta non-event with 310s-old intel → still passes (uses general threshold)."""
        intel = {"timestamp": _ts(MAX_INTEL_AGE_EVENT_SECONDS + 10), "macro": {}}
        result = check_freshness(intel, is_event_trade=False)
        assert result.allowed is True

    def test_very_stale_non_event_blocked(self):
        """Beta non-event with 1210s-old intel → blocked (general threshold = 1200s)."""
        intel = {"timestamp": _ts(MAX_INTEL_AGE_GENERAL_SECONDS + 10), "macro": {}}
        result = check_freshness(intel, is_event_trade=False)
        assert result.allowed is False


class TestAlphaPath:
    """Simulates autonomous_execution: is_event_trade = intel['macro']['is_event_day']."""

    def test_fresh_non_event_day_passes(self):
        intel = {"timestamp": _ts(60), "macro": {"is_event_day": False}}
        result = check_freshness(intel, is_event_trade=False)
        assert result.allowed is True

    def test_stale_event_day_blocked(self):
        """Alpha on is_event_day with 310s-old intel → blocked."""
        intel = {"timestamp": _ts(MAX_INTEL_AGE_EVENT_SECONDS + 10), "macro": {"is_event_day": True}}
        result = check_freshness(intel, is_event_trade=True)
        assert result.allowed is False

    def test_missing_timestamp_fails_closed_for_alpha(self):
        """Alpha with no intel timestamp → fail-closed regardless of event flag."""
        intel = {"macro": {"is_event_day": False}}
        result = check_freshness(intel, is_event_trade=False)
        assert result.allowed is False
        assert "missing" in result.reason.lower()
