"""Finnhub earnings-calendar helper for Agent Gamma.

Indices (XSP/SPX/NDX/RUT) and ETFs (SPY/QQQ/IWM) have no earnings — the
caller skips this lookup based on INSTRUMENT_CONFIG[sym]["type"].

Distances are reported in *trading* days, not calendar days, to match the
spec's "7 trading days" earnings blackout.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
from datetime import date, datetime, timedelta

from ._indicators import count_trading_days

FINNHUB_KEY = os.environ.get("FINNHUB_API_KEY", "")


def _fetch_window(symbol: str, start: date, end: date) -> list[dict]:
    if not FINNHUB_KEY:
        return []
    url = (
        f"https://finnhub.io/api/v1/calendar/earnings?"
        f"from={start.isoformat()}&to={end.isoformat()}&symbol={symbol}"
        f"&token={FINNHUB_KEY}"
    )
    try:
        with urllib.request.urlopen(urllib.request.Request(url), timeout=6) as r:
            data = json.loads(r.read())
        return data.get("earningsCalendar", []) or []
    except Exception as e:
        logging.warning("finnhub earnings fetch failed for %s: %s", symbol, e)
        return []


def days_to_earnings(symbol: str, today: date | None = None) -> int | None:
    """Trading days until the next earnings announcement, or None if unknown."""
    today = today or date.today()
    end = today + timedelta(days=60)
    events = _fetch_window(symbol, today, end)
    time.sleep(0.3)  # respect Finnhub free-tier rate limit
    nearest = None
    for ev in events:
        try:
            ed = datetime.strptime(ev["date"], "%Y-%m-%d").date()
            if ed >= today and (nearest is None or ed < nearest):
                nearest = ed
        except Exception:
            continue
    if nearest is None:
        return None
    # count_trading_days(start, end) counts weekdays in [start, end). Add one
    # to the end so the earnings day itself is included in the count.
    return count_trading_days(today.isoformat(),
                              (nearest + timedelta(days=1)).isoformat())


def days_since_earnings(symbol: str, today: date | None = None) -> int | None:
    """Trading days since the last earnings announcement, or None if unknown."""
    today = today or date.today()
    start = today - timedelta(days=21)  # widened slightly for trading-day reach
    events = _fetch_window(symbol, start, today)
    time.sleep(0.3)
    most_recent = None
    for ev in events:
        try:
            ed = datetime.strptime(ev["date"], "%Y-%m-%d").date()
            if ed <= today and (most_recent is None or ed > most_recent):
                most_recent = ed
        except Exception:
            continue
    if most_recent is None:
        return None
    return count_trading_days(most_recent.isoformat(),
                              (today + timedelta(days=1)).isoformat())
