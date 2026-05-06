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
        (_at(2026, 5, 4, 21, 0), "observe"),
        # Sat = 5
        (_at(2026, 5, 9, 10, 0), "observe"),
        # Sun = 6
        (_at(2026, 5, 10, 10, 0), "observe"),
    ])
    def test_scheduled_slots_resolve(self, dt, expected, monkeypatch):
        monkeypatch.setattr(SA, "datetime", _FakeDatetime(dt))
        assert SA.resolve_mode_from_clock() == expected

    @pytest.mark.parametrize("dt", [
        _at(2026, 5, 4, 8, 0),    # 8:00 AM Mon — not a slot
        _at(2026, 5, 4, 8, 31),   # one minute past 8:30 — not aligned
        _at(2026, 5, 4, 16, 0),   # 16:00 not a slot (16:15 is)
        _at(2026, 5, 4, 22, 0),   # 22:00 not a slot
        _at(2026, 5, 9, 11, 0),   # Sat 11 AM — not a slot
        _at(2026, 5, 9, 8, 30),   # Sat 8:30 — weekday slot but it's a weekend
    ])
    def test_non_slot_returns_none(self, dt, monkeypatch):
        monkeypatch.setattr(SA, "datetime", _FakeDatetime(dt))
        assert SA.resolve_mode_from_clock() is None


class TestNextScheduledET:
    def test_after_last_weekday_slot_returns_next_weekend_or_monday(self, monkeypatch):
        # Mon 22:00 — past 21:00 slot. Next is either Sat 10:00 or next Mon 8:30.
        # Schedule: weekday has up through 21:00, then weekend Sat 10:00, then Mon 8:30
        monkeypatch.setattr(SA, "datetime", _FakeDatetime(_at(2026, 5, 4, 22, 0)))
        nxt = SA.next_scheduled_et()
        # Tuesday 8:30 AM is the next slot
        assert "Tue 08:30 ET (apply)" in nxt

    def test_sunday_after_slot_returns_monday(self, monkeypatch):
        # Sunday 14:00 — past Sun 10:00. Next is Mon 8:30.
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
