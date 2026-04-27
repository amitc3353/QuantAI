#!/usr/bin/env python3
"""Event move database seeder for Agent Beta.

Builds a rolling 8-event window of absolute SPX % moves on FOMC/CPI/NFP/GDP
event days. Beta's Event Strangle strategy compares current implied move %
against this historical average to detect mispriced volatility.

Sources:
  - Finnhub economic calendar (FROM-only — last 2 years of past events)
  - yfinance ^GSPC daily history for the actual move

Output: /root/quantai-v2/shared-data/cache/event_moves.json (spec § 3D shape).
Cron: weekly Sunday 06:00 UTC refresh.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, date
from pathlib import Path

sys.path.insert(0, "/home/trader/QuantAI/v2/shared-data/scripts")
from _logger import setup as _logger_setup

_logger_setup("event_moves_seeder")

CACHE = Path("/root/quantai-v2/shared-data/cache")
OUT = CACHE / "event_moves.json"

# Auto-load .env
import pathlib as _pl
for _ef in [_pl.Path("/home/trader/QuantAI/.env"), _pl.Path("/root/quantai-v2/.env")]:
    if _ef.exists():
        for _line in _ef.read_text().splitlines():
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.partition("=")
                if not os.environ.get(_k.strip()):
                    os.environ[_k.strip()] = _v.strip()
        break

FINNHUB_KEY = os.environ.get("FINNHUB_API_KEY", "")

# Event-name patterns mapping to spec event types.
PATTERNS = {
    "FOMC": ["fomc", "fed funds", "interest rate decision", "fed rate"],
    "CPI": ["cpi", "consumer price", "inflation rate"],
    "NFP": ["nonfarm", "non-farm", "payroll", "employment"],
    "GDP": ["gdp"],
}


def _classify(event_name: str) -> str | None:
    n = (event_name or "").lower()
    for tag, kws in PATTERNS.items():
        if any(k in n for k in kws):
            return tag
    return None


def _fetch_finnhub_events(start: date, end: date) -> list[dict]:
    """Query Finnhub economic calendar for [start, end]. US-only events."""
    if not FINNHUB_KEY:
        logging.error("FINNHUB_API_KEY not set — cannot fetch events")
        return []
    qs = urllib.parse.urlencode({
        "from": start.isoformat(),
        "to": end.isoformat(),
        "token": FINNHUB_KEY,
    })
    url = f"https://finnhub.io/api/v1/calendar/economic?{qs}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "quantai-beta/1.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        events = data.get("economicCalendar") or []
        # Finnhub may use 'country' field; filter US.
        return [e for e in events if (e.get("country") or "").upper() in ("US", "USA", "")]
    except Exception as e:
        logging.error("Finnhub fetch failed: %s", e)
        return []


def _spx_abs_move(target: date) -> float | None:
    """Absolute % SPX move on `target` vs prior trading day. None if missing."""
    try:
        import yfinance as yf
        # Pull a small window around the target so we have at least one prior close.
        start = (target - timedelta(days=10)).isoformat()
        end = (target + timedelta(days=2)).isoformat()
        hist = yf.Ticker("^GSPC").history(start=start, end=end)
        if hist.empty:
            return None
        # Match by date. yfinance index is tz-aware; normalize.
        dates = [d.date() for d in hist.index]
        if target not in dates:
            return None
        idx = dates.index(target)
        if idx == 0:
            return None
        close_today = float(hist["Close"].iloc[idx])
        close_prev = float(hist["Close"].iloc[idx - 1])
        if close_prev <= 0:
            return None
        return abs((close_today - close_prev) / close_prev * 100)
    except Exception as e:
        logging.warning("yfinance ^GSPC fetch failed for %s: %s", target, e)
        return None


def build_event_moves(window: int = 8, lookback_days: int = 730) -> dict:
    """Walk back `lookback_days`, classify events, fetch SPX moves, keep last `window` per type."""
    today = date.today()
    start = today - timedelta(days=lookback_days)
    events = _fetch_finnhub_events(start, today)
    logging.info("Fetched %d economic events from Finnhub", len(events))

    # Classify + dedupe by (date, type) — multiple FOMC entries on same day collapse.
    by_type: dict[str, list[date]] = {k: [] for k in PATTERNS}
    seen: set[tuple[str, date]] = set()
    for ev in events:
        name = ev.get("event") or ev.get("name") or ""
        tag = _classify(name)
        if not tag:
            continue
        ts = ev.get("time") or ev.get("date") or ""
        try:
            d = datetime.strptime(ts[:10], "%Y-%m-%d").date()
        except Exception:
            continue
        if d > today:
            continue
        if (tag, d) in seen:
            continue
        seen.add((tag, d))
        by_type[tag].append(d)

    output: dict[str, dict] = {}
    for tag, dates in by_type.items():
        dates.sort(reverse=True)
        moves: list[float] = []
        used_dates: list[str] = []
        for d in dates:
            if len(moves) >= window:
                break
            m = _spx_abs_move(d)
            if m is None:
                continue
            moves.append(round(m, 3))
            used_dates.append(d.isoformat())
        moves_chrono = list(reversed(moves))
        used_dates_chrono = list(reversed(used_dates))
        avg = round(sum(moves_chrono) / len(moves_chrono), 3) if moves_chrono else None
        output[tag] = {
            "moves": moves_chrono,
            "dates": used_dates_chrono,
            "avg_8": avg,
            "last_updated": today.isoformat(),
        }
        logging.info("%s: %d moves, avg=%s", tag, len(moves_chrono), avg)
    return output


def main() -> int:
    CACHE.mkdir(parents=True, exist_ok=True)
    data = build_event_moves()
    if not any(d.get("moves") for d in data.values()):
        logging.error("No event moves built — aborting write")
        return 1
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, OUT)
    print(f"[event_moves_seeder] wrote {OUT} — types: {[(k, len(v['moves'])) for k, v in data.items()]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
