"""
macro_data.py — Macro & Event Calendar Data
============================================
Sources:
  FRED (St. Louis Fed) — free, no API key needed for most series
    - Fed funds rate (DFF)
    - 2yr Treasury yield (DGS2)
    - 10yr Treasury yield (DGS10)
    - CPI YoY (CPIAUCSL)
    - Unemployment rate (UNRATE)

  Finnhub — free tier, API key required (60 calls/min)
    - Economic calendar (FOMC, CPI, NFP dates)
    - Earnings calendar for watchlist tickers
    - News sentiment scores

Outputs:
  get_macro_context() → dict used by context_builder.py
  get_event_calendar() → list of upcoming events with days-away
  is_event_day() → bool — should agents skip today?

All data cached for 4 hours (macro moves slowly).
"""

import os
import json
import logging
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional
import aiohttp
import asyncio

log = logging.getLogger("macro-data")

FINNHUB_KEY = os.getenv("FINNHUB_API_KEY", "")
CACHE_DIR = Path("/app/data/cache")
CACHE_TTL_HOURS = 4

# FRED series we care about
FRED_SERIES = {
    "fed_funds_rate": "DFF",       # Fed funds effective rate
    "treasury_2yr":   "DGS2",      # 2-year Treasury yield
    "treasury_10yr":  "DGS10",     # 10-year Treasury yield
    "cpi_yoy":        "CPIAUCSL",  # CPI (we compute YoY ourselves)
    "unemployment":   "UNRATE",    # Unemployment rate
}

# High-impact economic events to watch
HIGH_IMPACT_EVENTS = [
    "FOMC", "Federal Reserve", "Fed Rate", "Interest Rate Decision",
    "CPI", "Consumer Price Index", "Inflation",
    "NFP", "Nonfarm Payroll", "Jobs Report", "Employment",
    "GDP", "Gross Domestic Product",
    "PCE", "Personal Consumption",
]


# ─────────────────────────────────────────────────────────────────────────────
# CACHE
# ─────────────────────────────────────────────────────────────────────────────

def _cache_path(key: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"macro_{key}.json"


def _read_cache(key: str, ttl_hours: int = CACHE_TTL_HOURS) -> Optional[dict]:
    p = _cache_path(key)
    if not p.exists():
        return None
    try:
        with open(p) as f:
            data = json.load(f)
        cached_at = datetime.fromisoformat(data.get("_cached_at", "2000-01-01"))
        if (datetime.now() - cached_at).total_seconds() / 3600 < ttl_hours:
            return data
    except Exception:
        pass
    return None


def _write_cache(key: str, data: dict):
    data["_cached_at"] = datetime.now().isoformat()
    with open(_cache_path(key), "w") as f:
        json.dump(data, f)


# ─────────────────────────────────────────────────────────────────────────────
# FRED — Federal Reserve Economic Data
# ─────────────────────────────────────────────────────────────────────────────

async def fetch_fred_series(series_id: str, limit: int = 5) -> Optional[list]:
    """Fetch latest N observations from FRED. No API key needed for public series."""
    cached = _read_cache(f"fred_{series_id}")
    if cached:
        return cached.get("observations")

    url = (
        f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    )
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    log.warning(f"FRED {series_id}: HTTP {resp.status}")
                    return None
                text = await resp.text()
                lines = [l for l in text.strip().split("\n") if l and not l.startswith("DATE")]
                observations = []
                for line in lines[-limit:]:
                    parts = line.split(",")
                    if len(parts) == 2 and parts[1].strip() not in (".", ""):
                        try:
                            observations.append({
                                "date": parts[0].strip(),
                                "value": float(parts[1].strip()),
                            })
                        except ValueError:
                            continue

                if observations:
                    _write_cache(f"fred_{series_id}", {"observations": observations})
                    log.debug(f"FRED {series_id}: latest = {observations[-1]['value']}")
                return observations
    except Exception as e:
        log.error(f"FRED fetch failed for {series_id}: {e}")
        return None


async def get_fred_macro() -> dict:
    """
    Fetch all FRED series and compute macro context.
    Returns structured dict with yield curve regime, rate environment, etc.
    """
    cached = _read_cache("fred_macro_composite", ttl_hours=6)
    if cached:
        return cached

    # Fetch all series concurrently
    results = await asyncio.gather(
        fetch_fred_series("DFF", limit=3),
        fetch_fred_series("DGS2", limit=3),
        fetch_fred_series("DGS10", limit=3),
        fetch_fred_series("CPIAUCSL", limit=14),  # Need 12mo for YoY
        fetch_fred_series("UNRATE", limit=3),
        return_exceptions=True,
    )

    def latest(obs):
        if obs and isinstance(obs, list) and obs:
            return obs[-1]["value"]
        return None

    fed_rate = latest(results[0])
    treasury_2yr = latest(results[1])
    treasury_10yr = latest(results[2])
    cpi_data = results[3] if isinstance(results[3], list) else []
    unemployment = latest(results[4])

    # Yield curve: 10yr minus 2yr (positive = normal, negative = inverted)
    yield_spread = None
    yield_curve_regime = "unknown"
    if treasury_2yr and treasury_10yr:
        yield_spread = round(treasury_10yr - treasury_2yr, 2)
        if yield_spread > 0.5:
            yield_curve_regime = "normal"
        elif yield_spread > 0:
            yield_curve_regime = "flat"
        elif yield_spread > -0.5:
            yield_curve_regime = "slightly_inverted"
        else:
            yield_curve_regime = "inverted"

    # CPI YoY: compare latest to 12 months ago
    cpi_yoy = None
    if len(cpi_data) >= 13:
        try:
            cpi_now = cpi_data[-1]["value"]
            cpi_year_ago = cpi_data[-13]["value"]
            cpi_yoy = round((cpi_now - cpi_year_ago) / cpi_year_ago * 100, 1)
        except Exception:
            pass

    # Rate environment classification
    rate_env = "unknown"
    if fed_rate:
        if fed_rate >= 5.0:
            rate_env = "restrictive"
        elif fed_rate >= 3.0:
            rate_env = "elevated"
        elif fed_rate >= 1.0:
            rate_env = "moderate"
        else:
            rate_env = "accommodative"

    # Macro stress score (0 = stressed, 100 = healthy)
    macro_stress = 100
    if yield_curve_regime == "inverted":
        macro_stress -= 30
    elif yield_curve_regime == "slightly_inverted":
        macro_stress -= 15
    elif yield_curve_regime == "flat":
        macro_stress -= 5

    if cpi_yoy and cpi_yoy > 4.0:
        macro_stress -= 20
    elif cpi_yoy and cpi_yoy > 3.0:
        macro_stress -= 10

    if unemployment and unemployment > 5.0:
        macro_stress -= 15
    elif unemployment and unemployment > 4.5:
        macro_stress -= 5

    macro_stress = max(0, macro_stress)

    result = {
        "fed_funds_rate": fed_rate,
        "treasury_2yr": treasury_2yr,
        "treasury_10yr": treasury_10yr,
        "yield_spread_10y2y": yield_spread,
        "yield_curve_regime": yield_curve_regime,
        "cpi_yoy": cpi_yoy,
        "unemployment": unemployment,
        "rate_environment": rate_env,
        "macro_stress_score": macro_stress,
        "macro_regime": (
            "stressed" if macro_stress < 40
            else "cautious" if macro_stress < 70
            else "healthy"
        ),
        "source": "FRED",
        "fetched_at": datetime.now().isoformat(),
    }

    _write_cache("fred_macro_composite", result)
    log.info(
        f"Macro: yield_curve={yield_curve_regime} fed_rate={fed_rate} "
        f"cpi_yoy={cpi_yoy} stress={macro_stress}"
    )
    return result


# ─────────────────────────────────────────────────────────────────────────────
# FINNHUB — Event Calendar & News Sentiment
# ─────────────────────────────────────────────────────────────────────────────

async def get_economic_calendar() -> list:
    """
    Fetch upcoming high-impact economic events from Finnhub.
    Returns list sorted by days_away, filtered to high-impact only.
    Falls back to hardcoded known dates if Finnhub unavailable.
    """
    cached = _read_cache("finnhub_calendar", ttl_hours=12)
    if cached:
        return cached.get("events", [])

    events = []

    if FINNHUB_KEY:
        today = date.today()
        end = today + timedelta(days=30)
        url = (
            f"https://finnhub.io/api/v1/calendar/economic"
            f"?from={today.isoformat()}&to={end.isoformat()}&token={FINNHUB_KEY}"
        )
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        for event in data.get("economicCalendar", []):
                            name = event.get("event", "")
                            impact = event.get("impact", "").lower()
                            event_date_str = event.get("time", "")[:10]

                            # Only high-impact events
                            if impact not in ("high", "medium"):
                                continue
                            is_high_impact = any(
                                kw.lower() in name.lower()
                                for kw in HIGH_IMPACT_EVENTS
                            )
                            if not is_high_impact:
                                continue

                            try:
                                event_date = date.fromisoformat(event_date_str)
                                days_away = (event_date - today).days
                                if days_away >= 0:
                                    events.append({
                                        "name": name,
                                        "date": event_date_str,
                                        "days_away": days_away,
                                        "impact": impact,
                                        "is_today": days_away == 0,
                                    })
                            except ValueError:
                                continue

                        events.sort(key=lambda x: x["days_away"])
                        log.info(f"Finnhub calendar: {len(events)} high-impact events in next 30 days")
        except Exception as e:
            log.warning(f"Finnhub calendar failed: {e}")

    # If no Finnhub key or call failed, use yfinance earnings calendar as fallback
    if not events:
        events = await _get_watchlist_earnings_calendar()

    _write_cache("finnhub_calendar", {"events": events})
    return events


async def _get_watchlist_earnings_calendar() -> list:
    """Fallback: get earnings dates for watchlist via yfinance."""
    import yfinance as yf
    watchlist = ["SPY", "QQQ", "PLTR", "TSM", "AMD", "NVDA", "AVGO", "ASML", "MU"]
    events = []
    today = date.today()

    for symbol in watchlist:
        try:
            ticker = yf.Ticker(symbol)
            cal = ticker.calendar
            if cal is None:
                continue
            # yfinance calendar: columns are datetime index
            for col in (cal.columns if hasattr(cal, 'columns') else []):
                try:
                    earnings_date = col.date() if hasattr(col, 'date') else date.fromisoformat(str(col)[:10])
                    days_away = (earnings_date - today).days
                    if 0 <= days_away <= 30:
                        events.append({
                            "name": f"{symbol} Earnings",
                            "date": earnings_date.isoformat(),
                            "days_away": days_away,
                            "impact": "high",
                            "is_today": days_away == 0,
                            "ticker": symbol,
                        })
                except Exception:
                    continue
        except Exception:
            continue

    events.sort(key=lambda x: x["days_away"])
    return events


async def get_news_sentiment(symbols: list = None) -> dict:
    """
    Get news sentiment scores for symbols via Finnhub.
    Returns per-symbol sentiment: -1.0 (very bearish) to +1.0 (very bullish)
    Falls back to 0.0 (neutral) if unavailable.
    """
    if not FINNHUB_KEY:
        return {s: 0.0 for s in (symbols or [])}

    if symbols is None:
        symbols = ["SPY", "QQQ", "NVDA", "PLTR"]

    cached = _read_cache("finnhub_sentiment", ttl_hours=2)
    if cached:
        return cached.get("sentiment", {})

    sentiment = {}
    today = date.today()
    week_ago = today - timedelta(days=7)

    for symbol in symbols[:6]:  # Limit to avoid rate limit
        try:
            url = (
                f"https://finnhub.io/api/v1/company-news"
                f"?symbol={symbol}&from={week_ago.isoformat()}"
                f"&to={today.isoformat()}&token={FINNHUB_KEY}"
            )
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                    if resp.status == 200:
                        articles = await resp.json()
                        if articles:
                            # Simple sentiment: count positive vs negative headlines
                            positive_words = ["beat", "surge", "rally", "gain", "record", "strong", "growth"]
                            negative_words = ["miss", "fall", "drop", "loss", "weak", "decline", "cut", "warn"]
                            score = 0
                            for article in articles[:20]:
                                headline = (article.get("headline", "") + " " + article.get("summary", "")).lower()
                                pos = sum(1 for w in positive_words if w in headline)
                                neg = sum(1 for w in negative_words if w in headline)
                                score += (pos - neg)
                            # Normalize to -1 to +1
                            sentiment[symbol] = max(-1.0, min(1.0, score / max(len(articles[:20]), 1) / 2))
                        else:
                            sentiment[symbol] = 0.0
            await asyncio.sleep(0.5)  # Rate limit courtesy
        except Exception as e:
            log.debug(f"News sentiment failed for {symbol}: {e}")
            sentiment[symbol] = 0.0

    _write_cache("finnhub_sentiment", {"sentiment": sentiment})
    return sentiment


# ─────────────────────────────────────────────────────────────────────────────
# CONVENIENCE FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────

async def get_macro_context() -> dict:
    """
    Full macro context: FRED indicators + event calendar + news sentiment.
    Called by context_builder.py before every agent entry.
    """
    fred, calendar, sentiment = await asyncio.gather(
        get_fred_macro(),
        get_economic_calendar(),
        get_news_sentiment(),
        return_exceptions=True,
    )

    if isinstance(fred, Exception):
        fred = {"macro_regime": "unknown", "macro_stress_score": 50}
    if isinstance(calendar, Exception):
        calendar = []
    if isinstance(sentiment, Exception):
        sentiment = {}

    # Find the next high-impact event
    next_event = calendar[0] if calendar else None
    days_to_next_event = next_event["days_away"] if next_event else 99
    is_event_today = any(e["is_today"] for e in calendar)
    is_event_imminent = days_to_next_event <= 1  # Today or tomorrow

    # Event danger level
    if is_event_today:
        event_danger = "critical"      # Skip all entries
    elif days_to_next_event <= 1:
        event_danger = "high"          # Skip Agent 1, caution on Agent 2
    elif days_to_next_event <= 3:
        event_danger = "medium"        # Widen wings
    elif days_to_next_event <= 7:
        event_danger = "low"           # Note it but proceed
    else:
        event_danger = "none"

    return {
        "fred": fred,
        "next_event": next_event,
        "days_to_next_event": days_to_next_event,
        "is_event_today": is_event_today,
        "is_event_imminent": is_event_imminent,
        "event_danger": event_danger,
        "upcoming_events": calendar[:5],
        "news_sentiment": sentiment,
        "macro_regime": fred.get("macro_regime", "unknown"),
        "yield_curve": fred.get("yield_curve_regime", "unknown"),
        "fetched_at": datetime.now().isoformat(),
    }


# CLI test
if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO)

    async def main():
        print("\n=== FRED Macro ===")
        macro = await get_fred_macro()
        print(json.dumps(macro, indent=2))

        print("\n=== Event Calendar ===")
        cal = await get_economic_calendar()
        print(json.dumps(cal[:5], indent=2))

        print("\n=== Full Macro Context ===")
        ctx = await get_macro_context()
        print(json.dumps(ctx, indent=2, default=str))

    asyncio.run(main())
