"""Pure indicator helpers for Agent Gamma.

Wilder's RSI is mathematically distinct from the simple-MA RSI in
scan_options.py. Connors' published 88.89% backtest assumes Wilder's
smoothing at period=10, so we implement it here rather than reuse.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable, Sequence


def wilders_rsi(closes: Sequence[float], period: int = 10) -> float | None:
    """RSI(period) using Wilder's smoothing on a daily-close series.

    Returns the most recent RSI value, or None if there are too few
    observations. Requires at least period+1 closes; period+20 is preferred
    for the smoothing to settle.
    """
    if closes is None or len(closes) < period + 1:
        return None

    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0.0 for d in deltas]
    losses = [-d if d < 0 else 0.0 for d in deltas]

    # Seed with the simple average of the first `period` deltas
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    # Wilder smoothing for the remainder
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def sma(closes: Sequence[float], period: int = 200) -> float | None:
    """Simple moving average of the last `period` closes."""
    if closes is None or len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def count_trading_days(start_iso: str, end_iso: str) -> int:
    """Count weekdays between start (inclusive) and end (exclusive).

    Cheap approximation — does not exclude US market holidays. For the
    10-day max-hold check this is close enough; off-by-one near holidays
    is acceptable behavior (the time stop is a soft bound, not a hard one).
    """
    try:
        s = date.fromisoformat(start_iso[:10])
        e = date.fromisoformat(end_iso[:10])
    except Exception:
        return 0
    if e <= s:
        return 0
    days = 0
    cur = s
    while cur < e:
        if cur.weekday() < 5:  # Mon-Fri
            days += 1
        cur += timedelta(days=1)
    return days


def avg_volume(volumes: Sequence[float], period: int = 20) -> float | None:
    """20-day average daily volume."""
    if volumes is None or len(volumes) < period:
        return None
    return sum(volumes[-period:]) / period


def distance_above_pct(price: float, baseline: float) -> float:
    """% distance above baseline (typically the 200 SMA)."""
    if baseline is None or baseline <= 0:
        return 0.0
    return (price - baseline) / baseline * 100.0
