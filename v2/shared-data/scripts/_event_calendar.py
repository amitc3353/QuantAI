"""Pre-entry event-time verification gate.

Blocks PRE_EVENT-regime trades when the macro event scheduled for today has
already released. The Finnhub economic calendar (used by market_intelligence.py)
provides dates but not intra-day times, so release times are hardcoded from
the government/Fed fixed-schedule table.

Fail-open vs fail-closed:
  This gate is FAIL-OPEN on missing macro data (opposite of Gate 1, which is
  fail-closed on a missing journal). The reasoning differs by gate:
    - Gate 1 (concentration): if we can't read the journal we don't know how
      many positions are open — trading blindly risks unlimited concentration,
      so fail-closed is the safe choice.
    - Gate 3 (event timing): if we can't detect an event in the intel packet,
      we have no evidence that any event is happening today. Blocking on
      absence of evidence would prevent all trades whenever Finnhub is down or
      the macro block is missing — that's too conservative. We only block when
      we have positive evidence (is_event_day=True) that an event released.

Wired into beta_agent.py (regime == "PRE_EVENT") and autonomous_execution.py
(macro["is_event_day"] == True). Skipped for Gamma (no event dependency).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
logger = logging.getLogger(__name__)

# Fixed government / Fed release schedule — these times are institutionally stable.
EVENT_RELEASE_TIMES_ET: dict[str, time] = {
    "CPI":  time(8, 30),   # BLS Consumer Price Index
    "NFP":  time(8, 30),   # BLS Non-Farm Payrolls / Unemployment Rate
    "FOMC": time(14, 0),   # Federal Reserve rate decision
    "GDP":  time(8, 30),   # BEA Gross Domestic Product
    "PPI":  time(8, 30),   # BLS Producer Price Index
    "PCE":  time(8, 30),   # BEA Personal Consumption Expenditures
}
DEFAULT_RELEASE_TIME_ET = time(8, 30)  # conservative default for unknown types


def _now_et() -> time:
    """Return current wall-clock time in ET. Extracted for test monkeypatching."""
    return datetime.now(ET).time()


@dataclass
class EventTimingResult:
    allowed: bool
    reason: str
    event_type: str | None = None
    release_time_et: str = ""  # "HH:MM" string, empty when not applicable


def check_event_timing(intel: dict, is_event_trade: bool = False) -> EventTimingResult:
    """Return EventTimingResult for the proposed trade.

    For non-event trades: always allowed (gate is a no-op).
    For event trades:
      - If is_event_day is False/missing: allowed (event is days away, not released).
      - If is_event_day is True and current ET >= release time: blocked.
      - If macro is missing entirely: fail-open (see module docstring).
    """
    if not is_event_trade:
        return EventTimingResult(allowed=True, reason="not an event trade")

    macro = intel.get("macro") or {}

    if not macro.get("is_event_day"):
        return EventTimingResult(allowed=True, reason="event not today — PRE_EVENT trade allowed")

    event_type = macro.get("event_type")
    release_time = EVENT_RELEASE_TIMES_ET.get(event_type, DEFAULT_RELEASE_TIME_ET)
    release_str = release_time.strftime("%H:%M")
    now = _now_et()

    if now >= release_time:
        reason = (
            f"event_timing: {event_type or 'event'} releases at {release_str} ET, "
            f"current time {now.strftime('%H:%M')} ET — event already released, "
            f"this is POST-event territory"
        )
        logger.warning(reason)
        return EventTimingResult(
            allowed=False,
            reason=reason,
            event_type=event_type,
            release_time_et=release_str,
        )

    return EventTimingResult(
        allowed=True,
        reason=(
            f"event not yet released — {event_type or 'event'} releases at "
            f"{release_str} ET, current time {now.strftime('%H:%M')} ET"
        ),
        event_type=event_type,
        release_time_et=release_str,
    )
