"""Unit tests for Sentinel's wrapper schedule (resolve_mode_from_clock + next_scheduled_et)."""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import sentinel_agent as SA

ET = ZoneInfo("America/New_York")


def _at(year, month, day, hour, minute):
    return datetime(year, month, day, hour, minute, tzinfo=ET)


class TestResolveModeFromClock:
    """A Monday in EDT for predictable weekday/weekend behavior."""

    def _patch_now(self, dt):
        return patch("sentinel_agent.datetime", wraps=datetime,
                     **{"now": lambda tz=None: dt if tz else dt.replace(tzinfo=None)})

    @pytest.mark.parametrize("dt,expected", [
        # Mon = weekday 0
        (_at(2026, 5, 4, 8, 30), "apply"),     # 8:30 AM ET Monday
        (_at(2026, 5, 4, 10, 0), "observe"),
        (_at(2026, 5, 4, 11, 0), "observe"),
        (_at(2026, 5, 4, 12, 0), "observe"),
        (_at(2026, 5, 4, 13, 0), "observe"),
        (_at(2026, 5, 4, 14, 0), "observe"),
        (_at(2026, 5, 4, 15, 0), "observe"),
        (_at(2026, 5, 4, 16, 15), "apply"),
        # 2026-05-18: (21,0) weekday observe + Sat/Sun (10,0) observes were
        # removed from SCHEDULE_ET to cut Sentinel LLM spend while Alpha+Beta
        # are paused. These three former slots are asserted as non-slots in
        # test_non_slot_returns_none below.
    ])
    def test_scheduled_slots_resolve(self, dt, expected, monkeypatch):
        monkeypatch.setattr(SA, "datetime", _FakeDatetime(dt))
        assert SA.resolve_mode_from_clock() == expected

    @pytest.mark.parametrize("dt", [
        _at(2026, 5, 4, 8, 0),    # 8:00 AM Mon — not a slot
        _at(2026, 5, 4, 8, 31),   # one minute past 8:30 — not aligned
        _at(2026, 5, 4, 16, 0),   # 16:00 not a slot (16:15 is)
        _at(2026, 5, 4, 22, 0),   # 22:00 not a slot
        _at(2026, 5, 4, 21, 0),   # 2026-05-18: 21:00 ET weekday observe REMOVED
        _at(2026, 5, 9, 11, 0),   # Sat 11 AM — not a slot
        _at(2026, 5, 9, 8, 30),   # Sat 8:30 — weekday slot but it's a weekend
        _at(2026, 5, 9, 10, 0),   # 2026-05-18: Sat 10:00 weekend observe REMOVED
        _at(2026, 5, 10, 10, 0),  # 2026-05-18: Sun 10:00 weekend observe REMOVED
    ])
    def test_non_slot_returns_none(self, dt, monkeypatch):
        monkeypatch.setattr(SA, "datetime", _FakeDatetime(dt))
        assert SA.resolve_mode_from_clock() is None


class TestNextScheduledET:
    def test_after_last_weekday_slot_returns_next_weekend_or_monday(self, monkeypatch):
        # Mon 22:00 — past the last weekday slot (16:15 apply). With the
        # 2026-05-18 SCHEDULE_ET prune (no more 21:00 weekday + no weekend),
        # the next slot is Tuesday 08:30.
        monkeypatch.setattr(SA, "datetime", _FakeDatetime(_at(2026, 5, 4, 22, 0)))
        nxt = SA.next_scheduled_et()
        assert "Tue 08:30 ET (apply)" in nxt

    def test_sunday_after_slot_returns_monday(self, monkeypatch):
        # Sunday 14:00 — with the 2026-05-18 prune the weekend table is empty,
        # so the next slot is Monday 08:30 regardless of where in the weekend
        # we are.
        monkeypatch.setattr(SA, "datetime", _FakeDatetime(_at(2026, 5, 10, 14, 0)))
        nxt = SA.next_scheduled_et()
        assert "Mon 08:30 ET (apply)" in nxt


class _FakeDatetime:
    """Helper that makes datetime.now(tz) return a fixed datetime in tests."""
    def __init__(self, fixed):
        self._fixed = fixed

    def now(self, tz=None):
        if tz is None:
            return self._fixed.replace(tzinfo=None)
        return self._fixed.astimezone(tz)

    def __getattr__(self, name):
        return getattr(datetime, name)
