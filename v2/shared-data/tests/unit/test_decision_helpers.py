"""Unit tests for _decision_helpers.py — including new week boundary helpers."""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

import _decision_helpers as dh

ET = ZoneInfo("America/New_York")


# ── effective_equity ──────────────────────────────────────────────────────────
class TestEffectiveEquity:
    def test_caps_at_50k(self):
        assert dh.effective_equity(1_000_000) == 50_000

    def test_passes_through_small_values(self):
        assert dh.effective_equity(30_000) == 30_000

    def test_exactly_at_cap(self):
        assert dh.effective_equity(50_000) == 50_000

    def test_none_returns_zero(self):
        assert dh.effective_equity(None) == 0.0

    def test_zero_returns_zero(self):
        assert dh.effective_equity(0) == 0.0

    def test_negative_returns_zero(self):
        assert dh.effective_equity(-100) == 0.0

    def test_non_numeric_returns_zero(self):
        assert dh.effective_equity("not_a_number") == 0.0

    def test_string_numeric_works(self):
        # Broker APIs sometimes return strings
        assert dh.effective_equity("80000") == 50_000

    def test_cap_constant_is_50k(self):
        assert dh.AGENT_ACCOUNT_CAP == 50_000


# ── age_of ────────────────────────────────────────────────────────────────────
class TestAgeOf:
    def test_recent_timestamp_small_age(self):
        now = datetime.now(timezone.utc).isoformat()
        age = dh.age_of(now)
        assert 0 <= age <= 5  # within 5 seconds

    def test_old_timestamp_large_age(self):
        old = "2020-01-01T00:00:00+00:00"
        age = dh.age_of(old)
        assert age > 86400  # at least 1 day in seconds

    def test_none_returns_zero(self):
        assert dh.age_of(None) == 0

    def test_empty_string_returns_zero(self):
        assert dh.age_of("") == 0

    def test_unparseable_returns_zero(self):
        assert dh.age_of("not-a-date") == 0

    def test_z_suffix_handled(self):
        ts = "2025-01-01T12:00:00Z"
        age = dh.age_of(ts)
        assert age > 0


# ── rsi_depth_score ───────────────────────────────────────────────────────────
class TestRsiDepthScore:
    def test_above_30_returns_4(self):
        assert dh.rsi_depth_score(35) == 4
        assert dh.rsi_depth_score(30) == 4

    def test_25_to_30(self):
        assert dh.rsi_depth_score(27) == 6

    def test_20_to_25(self):
        assert dh.rsi_depth_score(22) == 7

    def test_15_to_20(self):
        assert dh.rsi_depth_score(17) == 8

    def test_below_15_returns_9(self):
        assert dh.rsi_depth_score(5) == 9

    def test_none_returns_5(self):
        assert dh.rsi_depth_score(None) == 5

    def test_non_numeric_returns_5(self):
        assert dh.rsi_depth_score("bad") == 5


# ── week_start_for ────────────────────────────────────────────────────────────
class TestWeekStartFor:
    def test_monday_returns_itself(self):
        result = dh.week_start_for("2026-04-27")  # Monday
        assert result == "2026-04-27"

    def test_wednesday_returns_monday(self):
        result = dh.week_start_for("2026-04-29")  # Wednesday
        assert result == "2026-04-27"

    def test_sunday_returns_previous_monday(self):
        result = dh.week_start_for("2026-05-03")  # Sunday
        assert result == "2026-04-27"

    def test_none_returns_current_week(self):
        result = dh.week_start_for(None)
        # Just check it's a valid YYYY-MM-DD Monday
        d = datetime.fromisoformat(result)
        assert d.weekday() == 0

    def test_garbage_returns_current_week(self):
        result = dh.week_start_for("not-a-date")
        d = datetime.fromisoformat(result)
        assert d.weekday() == 0

    def test_iso_with_time_component(self):
        result = dh.week_start_for("2026-04-29T15:30:00")
        assert result == "2026-04-27"

    def test_result_is_always_monday(self):
        dates = [
            "2026-04-27", "2026-04-28", "2026-04-29",
            "2026-04-30", "2026-05-01", "2026-05-02", "2026-05-03",
        ]
        for ds in dates:
            result = dh.week_start_for(ds)
            d = datetime.fromisoformat(result)
            assert d.weekday() == 0, f"{ds} → {result} is not a Monday"


# ── this_week_monday ──────────────────────────────────────────────────────────
class TestThisWeekMonday:
    def test_returns_datetime(self):
        result = dh.this_week_monday()
        assert isinstance(result, datetime)

    def test_result_is_monday(self):
        result = dh.this_week_monday()
        assert result.weekday() == 0

    def test_result_is_midnight_et(self):
        result = dh.this_week_monday()
        assert result.hour == 0
        assert result.minute == 0
        assert result.second == 0

    def test_passing_wednesday_returns_monday(self):
        wednesday = datetime(2026, 4, 29, 14, 0, 0, tzinfo=timezone.utc)
        result = dh.this_week_monday(wednesday)
        assert result.weekday() == 0
        assert result.year == 2026
        assert result.month == 4
        assert result.day == 27

    def test_passing_monday_returns_itself(self):
        monday = datetime(2026, 4, 27, 9, 30, 0, tzinfo=timezone.utc)
        result = dh.this_week_monday(monday)
        assert result.day == 27

    def test_consistent_with_week_start_for(self):
        wed = datetime(2026, 4, 29, 12, 0, 0, tzinfo=timezone.utc)
        monday = dh.this_week_monday(wed)
        week_str = dh.week_start_for("2026-04-29")
        assert monday.date().isoformat() == week_str
