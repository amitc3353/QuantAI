"""Unit tests for _freshness_gate.py — data freshness gates.

Gate rule: block entry when the market intelligence packet is stale.
  - Event-regime trades (PRE_EVENT / is_event_day): threshold = 300s (5 min)
  - All other trades: threshold = 1200s (20 min)
  - Missing or unparseable timestamp → fail-closed (block)
  - vix_timestamp checked separately if present; falls back to intel timestamp.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from _freshness_gate import (
    MAX_INTEL_AGE_EVENT_SECONDS,
    MAX_INTEL_AGE_GENERAL_SECONDS,
    FreshnessResult,
    check_freshness,
)

UTC = timezone.utc


def _ts(seconds_ago: float) -> str:
    """Return an ISO8601 timestamp for N seconds ago."""
    return (datetime.now(UTC) - timedelta(seconds=seconds_ago)).isoformat()


def _intel(seconds_ago: float, vix_seconds_ago: float | None = None) -> dict:
    """Build a minimal intel dict with a timestamp N seconds old."""
    macro: dict = {}
    if vix_seconds_ago is not None:
        macro["vix_timestamp"] = _ts(vix_seconds_ago)
    return {"timestamp": _ts(seconds_ago), "macro": macro}


# ── Threshold sanity ──────────────────────────────────────────────────────────


class TestThresholdConstants:
    def test_event_threshold_is_300s(self):
        assert MAX_INTEL_AGE_EVENT_SECONDS == 300

    def test_general_threshold_is_1200s(self):
        assert MAX_INTEL_AGE_GENERAL_SECONDS == 1200

    def test_event_threshold_stricter_than_general(self):
        assert MAX_INTEL_AGE_EVENT_SECONDS < MAX_INTEL_AGE_GENERAL_SECONDS


# ── Non-event trades ──────────────────────────────────────────────────────────


class TestNonEventFreshness:
    def test_fresh_intel_10s_allows_non_event(self):
        result = check_freshness(_intel(10), is_event_trade=False)
        assert result.allowed is True
        assert result.reason == "passed"

    def test_intel_just_under_general_threshold_allows(self):
        result = check_freshness(_intel(MAX_INTEL_AGE_GENERAL_SECONDS - 10), is_event_trade=False)
        assert result.allowed is True

    def test_intel_just_over_general_threshold_blocks(self):
        result = check_freshness(_intel(MAX_INTEL_AGE_GENERAL_SECONDS + 10), is_event_trade=False)
        assert result.allowed is False
        assert "stale" in result.reason.lower()

    def test_event_threshold_not_applied_to_non_event(self):
        """310s is over event threshold but under general — non-event must pass."""
        assert MAX_INTEL_AGE_EVENT_SECONDS < 310 < MAX_INTEL_AGE_GENERAL_SECONDS
        result = check_freshness(_intel(310), is_event_trade=False)
        assert result.allowed is True


# ── Event-regime trades ───────────────────────────────────────────────────────


class TestEventFreshness:
    def test_fresh_intel_10s_allows_event_trade(self):
        result = check_freshness(_intel(10), is_event_trade=True)
        assert result.allowed is True

    def test_intel_just_under_event_threshold_allows(self):
        result = check_freshness(_intel(MAX_INTEL_AGE_EVENT_SECONDS - 10), is_event_trade=True)
        assert result.allowed is True

    def test_intel_just_over_event_threshold_blocks(self):
        result = check_freshness(_intel(MAX_INTEL_AGE_EVENT_SECONDS + 10), is_event_trade=True)
        assert result.allowed is False
        assert "stale" in result.reason.lower()

    def test_stale_event_intel_reason_mentions_event(self):
        result = check_freshness(_intel(MAX_INTEL_AGE_EVENT_SECONDS + 10), is_event_trade=True)
        assert "event" in result.reason.lower()


# ── Fail-safe on missing / bad timestamps ─────────────────────────────────────


class TestFailClosed:
    def test_missing_timestamp_key_fails_closed(self):
        result = check_freshness({"macro": {}}, is_event_trade=False)
        assert result.allowed is False
        assert "missing" in result.reason.lower()

    def test_none_timestamp_fails_closed(self):
        result = check_freshness({"timestamp": None, "macro": {}}, is_event_trade=False)
        assert result.allowed is False

    def test_unparseable_timestamp_fails_closed(self):
        result = check_freshness({"timestamp": "not-a-date", "macro": {}}, is_event_trade=False)
        assert result.allowed is False

    def test_empty_string_timestamp_fails_closed(self):
        result = check_freshness({"timestamp": "", "macro": {}}, is_event_trade=False)
        assert result.allowed is False


# ── vix_timestamp field ───────────────────────────────────────────────────────


class TestVixTimestamp:
    def test_vix_timestamp_absent_falls_back_to_intel_ts(self):
        """No vix_timestamp → gate uses intel timestamp only; fresh intel passes."""
        intel = {"timestamp": _ts(10), "macro": {}}
        result = check_freshness(intel, is_event_trade=False)
        assert result.allowed is True

    def test_vix_timestamp_fresh_passes(self):
        result = check_freshness(_intel(10, vix_seconds_ago=5), is_event_trade=False)
        assert result.allowed is True

    def test_vix_timestamp_stale_blocks_even_if_intel_fresh(self):
        """Stale VIX (>general threshold) with a fresh intel packet → block."""
        result = check_freshness(
            _intel(10, vix_seconds_ago=MAX_INTEL_AGE_GENERAL_SECONDS + 10),
            is_event_trade=False,
        )
        assert result.allowed is False
        assert "vix" in result.reason.lower()

    def test_vix_timestamp_unparseable_fails_closed(self):
        intel = {"timestamp": _ts(10), "macro": {"vix_timestamp": "bad-ts"}}
        result = check_freshness(intel, is_event_trade=False)
        assert result.allowed is False


# ── Result fields ─────────────────────────────────────────────────────────────


class TestResultFields:
    def test_age_seconds_populated_on_block(self):
        result = check_freshness(_intel(MAX_INTEL_AGE_GENERAL_SECONDS + 60), is_event_trade=False)
        assert result.allowed is False
        assert result.age_seconds >= MAX_INTEL_AGE_GENERAL_SECONDS

    def test_age_seconds_populated_on_pass(self):
        result = check_freshness(_intel(30), is_event_trade=False)
        assert result.allowed is True
        assert 25 <= result.age_seconds <= 35

    def test_field_name_set_on_intel_block(self):
        result = check_freshness(_intel(MAX_INTEL_AGE_GENERAL_SECONDS + 10), is_event_trade=False)
        assert result.field == "intel_timestamp"

    def test_field_name_set_on_vix_block(self):
        result = check_freshness(
            _intel(10, vix_seconds_ago=MAX_INTEL_AGE_GENERAL_SECONDS + 10),
            is_event_trade=False,
        )
        assert result.field == "vix_timestamp"

    def test_field_is_passed_on_success(self):
        result = check_freshness(_intel(10), is_event_trade=False)
        assert result.field == "passed"
