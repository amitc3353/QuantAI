"""Same-symbol cooldown gate after a stop-loss exit.

After a stop-loss on a symbol, block re-entry for COOLDOWN_DAYS (3) calendar
days.  Calendar days, not trading days — weekend spans count exactly the same
as weekday spans (Friday stop → Monday attempt = 3 days = allowed; Friday stop
→ Sunday = 2 days = blocked).

Fail-closed on missing journal (same design as Gate 1 / concentration_gate):
without the journal we cannot determine whether a recent stop occurred, so the
safe choice is to block.  This is the opposite of Gate 3 (event_calendar) which
is fail-open on missing macro data — the reasoning differs by gate:

  Gate 1/4 fail-closed: missing journal → unknown open/stop state → unlimited
    concentration or re-entry risk → too conservative to allow.
  Gate 3 fail-open: missing macro → no evidence of event → blocking on absence
    of evidence would freeze all trades whenever Finnhub is down.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

COOLDOWN_DAYS = 3

JOURNAL = Path("/root/quantai-v2/shared-data/journal/paper/trades.jsonl")


def _today() -> date:
    """Return today's date in UTC. Extracted for test monkeypatching."""
    return datetime.now(timezone.utc).date()


@dataclass
class CooldownResult:
    allowed: bool
    reason: str
    last_stop_date: str = ""  # ISO date string of most-recent stop; empty when n/a
    days_since_stop: int = -1  # -1 when no qualifying stop found


def is_in_cooldown(symbol: str, journal: list[dict]) -> CooldownResult:
    """Check same-symbol stop-loss cooldown against a pre-loaded journal list.

    Intended for Gamma (which already has the journal loaded) and for unit tests.
    Alpha / Beta should call check_cooldown() which loads from disk.

    Args:
        symbol: ticker to check (case-insensitive).
        journal: list of trade dicts, already parsed from JSONL.

    Returns:
        CooldownResult with allowed=True when outside the cooldown window.
    """
    today = _today()
    sym_upper = symbol.upper()
    latest_stop: date | None = None

    for trade in journal:
        if trade.get("status", "").upper() != "CLOSED":
            continue
        if (trade.get("symbol") or "").upper() != sym_upper:
            continue
        close_reason = (trade.get("close_reason") or "").lower()
        if not close_reason.startswith("stop_loss"):
            continue

        ts_str = trade.get("close_timestamp") or trade.get("timestamp") or ""
        if not ts_str:
            continue
        try:
            stop_date = datetime.fromisoformat(ts_str).date()
        except ValueError:
            continue

        if latest_stop is None or stop_date > latest_stop:
            latest_stop = stop_date

    if latest_stop is None:
        return CooldownResult(
            allowed=True,
            reason="no stop-loss found for symbol",
            days_since_stop=-1,
        )

    days_since = (today - latest_stop).days

    if days_since < COOLDOWN_DAYS:
        reason = (
            f"cooldown: stop-loss on {latest_stop.isoformat()} was {days_since} "
            f"calendar day(s) ago — cooldown window is {COOLDOWN_DAYS} days"
        )
        logger.warning(reason)
        return CooldownResult(
            allowed=False,
            reason=reason,
            last_stop_date=latest_stop.isoformat(),
            days_since_stop=days_since,
        )

    return CooldownResult(
        allowed=True,
        reason=f"cooldown clear — last stop {days_since} calendar days ago",
        last_stop_date=latest_stop.isoformat(),
        days_since_stop=days_since,
    )


def check_cooldown(
    symbol: str,
    journal_path: Path = JOURNAL,
) -> CooldownResult:
    """Load journal from disk and check same-symbol stop-loss cooldown.

    Fail-closed: if the journal file cannot be read for any reason, return
    blocked with a reason containing 'journal_unavailable'.
    """
    if not journal_path.exists():
        reason = f"journal_unavailable: {journal_path} not found — failing closed"
        logger.error(reason)
        return CooldownResult(allowed=False, reason=reason, days_since_stop=-1)

    journal: list[dict] = []
    try:
        with open(journal_path) as f:
            for line in f:
                line = line.strip()
                if line:
                    journal.append(json.loads(line))
    except (OSError, json.JSONDecodeError) as exc:
        reason = f"journal_unavailable: failed to read {journal_path} — {exc}"
        logger.error(reason)
        return CooldownResult(allowed=False, reason=reason, days_since_stop=-1)

    return is_in_cooldown(symbol, journal)
