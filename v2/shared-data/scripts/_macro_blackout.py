"""Macro event blackout gate — ±15 min around major releases.

Blocks all entries (except explicitly long-vol strategies) within a symmetric
±15 minute window around scheduled macro event releases. This protects
short-vol positions from event-driven volatility spikes.

Coexistence with Gate 3 (_event_calendar.py):
  Gate 3: blocks only is_event_trade=True trades AFTER the event releases.
  Gate 6: blocks all non-exempt trades ±15 min AROUND the release.
  No logic overlap — Gate 3 guards event-regime trades, Gate 6 guards
  everything else that sells volatility.

Fail-open on missing macro data (same reasoning as Gate 3): if we have no
evidence that an event is happening today, blocking would freeze all trades
whenever Finnhub is down.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo

from _event_calendar import DEFAULT_RELEASE_TIME_ET, EVENT_RELEASE_TIMES_ET

ET = ZoneInfo("America/New_York")
logger = logging.getLogger(__name__)

BLACKOUT_MINUTES = 15

LONG_VOL_STRATEGIES: frozenset[str] = frozenset({
    "event_strangle",
    "vix_calls",
    "debit_spread",
    "call_ratio_backspread",
    "put_ratio_backspread",
})


def _now_et() -> time:
    """Return current wall-clock time in ET. Extracted for test monkeypatching."""
    return datetime.now(ET).time()


def _total_seconds(t: time) -> int:
    return t.hour * 3600 + t.minute * 60 + t.second


def _add_minutes(t: time, minutes: int) -> time:
    total = t.hour * 60 + t.minute + minutes
    total = max(0, min(total, 23 * 60 + 59))
    return time(total // 60, total % 60)


@dataclass
class BlackoutResult:
    allowed: bool
    reason: str
    event_type: str | None = None
    window_start_et: str = ""  # "HH:MM"
    window_end_et: str = ""    # "HH:MM"


def check_macro_blackout(intel: dict, strategy: str) -> BlackoutResult:
    """Check if current time is within ±BLACKOUT_MINUTES of a macro event.

    Long-vol strategies (event_strangle, vix_calls, etc.) are exempt — they
    benefit from volatility spikes.
    """
    if strategy.lower() in LONG_VOL_STRATEGIES:
        return BlackoutResult(allowed=True, reason="long-vol strategy — blackout exempt")

    macro = intel.get("macro") or {}

    if not macro.get("is_event_day"):
        return BlackoutResult(allowed=True, reason="no macro event today")

    event_type = macro.get("event_type")
    release = EVENT_RELEASE_TIMES_ET.get(event_type, DEFAULT_RELEASE_TIME_ET)
    now = _now_et()

    window_start = _add_minutes(release, -BLACKOUT_MINUTES)
    window_end = _add_minutes(release, BLACKOUT_MINUTES)
    start_str = window_start.strftime("%H:%M")
    end_str = window_end.strftime("%H:%M")

    diff = abs(_total_seconds(now) - _total_seconds(release))

    if diff <= BLACKOUT_MINUTES * 60:
        reason = (
            f"macro blackout: {event_type or 'event'} releases at "
            f"{release.strftime('%H:%M')} ET — current time "
            f"{now.strftime('%H:%M')} ET is within ±{BLACKOUT_MINUTES} min window "
            f"({start_str}–{end_str} ET)"
        )
        logger.warning(reason)
        return BlackoutResult(
            allowed=False,
            reason=reason,
            event_type=event_type,
            window_start_et=start_str,
            window_end_et=end_str,
        )

    return BlackoutResult(
        allowed=True,
        reason=(
            f"outside blackout window — {event_type or 'event'} window is "
            f"{start_str}–{end_str} ET, current time {now.strftime('%H:%M')} ET"
        ),
        event_type=event_type,
        window_start_et=start_str,
        window_end_et=end_str,
    )
