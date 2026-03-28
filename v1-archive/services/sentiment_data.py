"""
sentiment_data.py — Market Sentiment Indicators
=================================================
Sources:
  CBOE Daily Market Statistics (free scrape, updated ~4:30 PM ET daily)
    - Total put/call ratio
    - Equity put/call ratio
    - Index put/call ratio (most useful for SPY condors)

  CNN Fear & Greed Index (free scrape)
    - Composite score 0-100 (0=extreme fear, 100=extreme greed)
    - 7 sub-indicators including put/call, momentum, safe haven demand

  VIX Term Structure (yfinance — free)
    - VIX9D: 9-day expected volatility (near-term fear)
    - VIX:   30-day expected volatility (current baseline — already have)
    - VIX3M: 3-month expected volatility (longer-term fear)
    - Ratios reveal whether fear is short-term spike or sustained

All cached for 2 hours during market hours.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional
import asyncio
import aiohttp
import yfinance as yf

log = logging.getLogger("sentiment-data")

CACHE_DIR = Path("/app/data/cache")
CACHE_TTL_HOURS = 2


def _cache_path(key: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"sentiment_{key}.json"


def _read_cache(key: str) -> Optional[dict]:
    p = _cache_path(key)
    if not p.exists():
        return None
    try:
        with open(p) as f:
            data = json.load(f)
        cached_at = datetime.fromisoformat(data.get("_cached_at", "2000-01-01"))
        if (datetime.now() - cached_at).total_seconds() / 3600 < CACHE_TTL_HOURS:
            return data
    except Exception:
        pass
    return None


def _write_cache(key: str, data: dict):
    data["_cached_at"] = datetime.now().isoformat()
    with open(_cache_path(key), "w") as f:
        json.dump(data, f)


# ─────────────────────────────────────────────────────────────────────────────
# PUT/CALL RATIO — CBOE Daily Stats
# ─────────────────────────────────────────────────────────────────────────────

async def get_put_call_ratio() -> dict:
    """
    Scrape CBOE daily market statistics for put/call ratios.
    CBOE publishes this daily at ~4:30 PM ET.

    Interpretation:
      < 0.70 → complacency/greed — market buying calls aggressively
      0.70–1.00 → neutral range
      1.00–1.20 → mild fear — some hedging
      > 1.20 → elevated fear — heavy put buying, potential contrarian buy signal
               BUT for condors: high put/call = tail risk is real, widen wings

    Falls back to yfinance-derived estimate if CBOE scrape fails.
    """
    cached = _read_cache("put_call")
    if cached:
        return cached

    result = {
        "total_pcr": None,
        "equity_pcr": None,
        "index_pcr": None,
        "pcr_regime": "unknown",
        "source": None,
    }

    # Method 1: CBOE daily stats CSV (most reliable)
    try:
        url = "https://www.cboe.com/us/options/market_statistics/daily/"
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                headers={"User-Agent": "Mozilla/5.0 (compatible; QuantAI/1.0)"},
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    # Parse the put/call table from CBOE HTML
                    total_pcr = _parse_cboe_pcr(html, "total")
                    equity_pcr = _parse_cboe_pcr(html, "equity")
                    index_pcr = _parse_cboe_pcr(html, "index")

                    if total_pcr:
                        result.update({
                            "total_pcr": total_pcr,
                            "equity_pcr": equity_pcr,
                            "index_pcr": index_pcr,
                            "source": "cboe_scrape",
                        })
    except Exception as e:
        log.warning(f"CBOE scrape failed: {e}")

    # Method 2: Fallback — compute PCR from yfinance SPY options chain
    if not result["total_pcr"]:
        try:
            spy = yf.Ticker("SPY")
            # Use nearest expiry for current sentiment
            if spy.options:
                chain = spy.option_chain(spy.options[0])
                put_vol = chain.puts["volume"].sum()
                call_vol = chain.calls["volume"].sum()
                if call_vol > 0:
                    pcr = round(put_vol / call_vol, 2)
                    result.update({
                        "total_pcr": pcr,
                        "equity_pcr": pcr,
                        "index_pcr": pcr,
                        "source": "yfinance_spy_proxy",
                    })
                    log.info(f"PCR from yfinance SPY proxy: {pcr}")
        except Exception as e:
            log.warning(f"PCR yfinance fallback failed: {e}")

    # Classify regime
    pcr = result.get("total_pcr") or result.get("equity_pcr")
    if pcr:
        if pcr < 0.60:
            result["pcr_regime"] = "extreme_greed"
            result["pcr_signal"] = "Complacency — market crowded into calls"
        elif pcr < 0.75:
            result["pcr_regime"] = "greed"
            result["pcr_signal"] = "Bullish sentiment — low hedging"
        elif pcr < 1.00:
            result["pcr_regime"] = "neutral"
            result["pcr_signal"] = "Balanced sentiment — normal conditions"
        elif pcr < 1.20:
            result["pcr_regime"] = "fear"
            result["pcr_signal"] = "Elevated hedging — some tail risk concern"
        else:
            result["pcr_regime"] = "extreme_fear"
            result["pcr_signal"] = "Heavy put buying — significant fear, widen condor wings"

    result["fetched_at"] = datetime.now().isoformat()
    _write_cache("put_call", result)
    log.info(f"PCR: total={result.get('total_pcr')} regime={result.get('pcr_regime')} source={result.get('source')}")
    return result


def _parse_cboe_pcr(html: str, ratio_type: str) -> Optional[float]:
    """Extract put/call ratio from CBOE HTML. Fragile but cached."""
    import re
    patterns = {
        "total":  r"Total\s+Put/Call[^<]*<[^>]+>[^<]*<[^>]+>\s*([\d.]+)",
        "equity": r"Equity\s+Put/Call[^<]*<[^>]+>[^<]*<[^>]+>\s*([\d.]+)",
        "index":  r"Index\s+Put/Call[^<]*<[^>]+>[^<]*<[^>]+>\s*([\d.]+)",
    }
    pattern = patterns.get(ratio_type, "")
    try:
        match = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
        if match:
            return float(match.group(1))
    except Exception:
        pass
    # Try simpler number extraction near the keyword
    try:
        keyword = {"total": "Total Put", "equity": "Equity Put", "index": "Index Put"}.get(ratio_type, "")
        idx = html.lower().find(keyword.lower())
        if idx > 0:
            snippet = html[idx:idx+200]
            numbers = re.findall(r"\b(0\.\d{2,3}|1\.\d{2,3})\b", snippet)
            if numbers:
                return float(numbers[0])
    except Exception:
        pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
# FEAR & GREED INDEX — CNN
# ─────────────────────────────────────────────────────────────────────────────

async def get_fear_greed() -> dict:
    """
    Fetch CNN Fear & Greed index.
    Score: 0 = extreme fear, 100 = extreme greed.

    Interpretation for options:
      0–25:  extreme fear — VIX is high, condors risky (wide wings needed)
      25–45: fear — elevated premium, decent for selling
      45–55: neutral — ideal conditions
      55–75: greed — lower premium, smaller edge
      75–100: extreme greed — complacency, market vulnerable to correction
    """
    cached = _read_cache("fear_greed")
    if cached:
        return cached

    score = None
    rating = "unknown"

    # CNN Fear & Greed API (unofficial but stable endpoint)
    try:
        url = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                headers={"User-Agent": "Mozilla/5.0 (compatible; QuantAI/1.0)"},
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    fg = data.get("fear_and_greed", {})
                    score = fg.get("score")
                    rating = fg.get("rating", "unknown")
                    if score:
                        score = round(float(score), 1)
                        log.info(f"Fear & Greed: {score} ({rating})")
    except Exception as e:
        log.warning(f"CNN Fear & Greed failed: {e}")

    # Fallback: derive from VIX level as proxy
    if score is None:
        try:
            vix_ticker = yf.Ticker("^VIX")
            hist = vix_ticker.history(period="2d")
            if not hist.empty:
                vix = float(hist["Close"].iloc[-1])
                # Invert VIX to Fear & Greed scale (rough proxy)
                # VIX 12 ≈ greed 75, VIX 20 ≈ neutral 50, VIX 35 ≈ fear 20
                score = round(max(0, min(100, 100 - (vix - 10) * 3.5)), 1)
                rating = "vix_proxy"
                log.info(f"Fear & Greed from VIX proxy: {score} (VIX={vix:.1f})")
        except Exception as e:
            log.warning(f"Fear & Greed VIX fallback failed: {e}")
            score = 50  # Default neutral

    # Classify
    if score is not None:
        if score <= 25:
            regime = "extreme_fear"
            signal = "Extreme fear — elevated risk, widen wings significantly"
        elif score <= 45:
            regime = "fear"
            signal = "Fear regime — good premium selling environment"
        elif score <= 55:
            regime = "neutral"
            signal = "Neutral — ideal conditions for condors"
        elif score <= 75:
            regime = "greed"
            signal = "Greed — lower premium, normal conditions"
        else:
            regime = "extreme_greed"
            signal = "Extreme greed — complacency, market vulnerable, reduce size"
    else:
        regime = "unknown"
        signal = "Could not determine Fear & Greed"

    result = {
        "score": score,
        "rating": rating,
        "regime": regime,
        "signal": signal,
        "fetched_at": datetime.now().isoformat(),
    }
    _write_cache("fear_greed", result)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# VIX TERM STRUCTURE
# ─────────────────────────────────────────────────────────────────────────────

def get_vix_term_structure() -> dict:
    """
    Fetch VIX9D, VIX, and VIX3M from yfinance.
    Compute term structure shape: contango (normal) vs backwardation (stressed).

    Key ratios:
      VIX9D/VIX > 1.0 → near-term fear spike (event risk this week)
      VIX3M/VIX > 1.0 → long-term fear elevated (sustained stress)
      VIX/VIX3M > 1.0 → backwardation (acute near-term fear > long-term)

    For 0DTE condors:
      Contango (VIX9D < VIX < VIX3M) → normal, proceed
      Flat (all roughly equal) → neutral, proceed
      VIX9D spike (> VIX * 1.10) → skip entry 1, wait
      Full backwardation (VIX > VIX3M) → high alert, widen wings significantly
    """
    cached = _read_cache("vix_term")
    if cached:
        return cached

    tickers = {"VIX9D": "^VIX9D", "VIX": "^VIX", "VIX3M": "^VIX3M"}
    values = {}

    for name, symbol in tickers.items():
        try:
            hist = yf.Ticker(symbol).history(period="3d")
            if not hist.empty:
                values[name] = round(float(hist["Close"].iloc[-1]), 2)
        except Exception as e:
            log.debug(f"VIX term {name} failed: {e}")

    vix9d = values.get("VIX9D")
    vix = values.get("VIX")
    vix3m = values.get("VIX3M")

    # Term structure shape
    term_shape = "unknown"
    term_signal = "Could not determine VIX term structure"
    term_stress = 0  # 0-3 scale

    if vix9d and vix and vix3m:
        vix9d_vix_ratio = round(vix9d / vix, 3)
        vix_vix3m_ratio = round(vix / vix3m, 3)

        if vix9d < vix < vix3m:
            term_shape = "contango"
            term_signal = "Normal contango — near-term calmer than long-term, ideal for condors"
            term_stress = 0
        elif abs(vix9d - vix) < 0.5 and abs(vix - vix3m) < 0.5:
            term_shape = "flat"
            term_signal = "Flat term structure — neutral, proceed normally"
            term_stress = 1
        elif vix9d > vix * 1.10:
            term_shape = "near_term_spike"
            term_signal = f"VIX9D spike ({vix9d} vs VIX {vix}) — near-term event risk, skip entry or widen wings"
            term_stress = 2
        elif vix > vix3m:
            term_shape = "backwardation"
            term_signal = f"Full backwardation — acute fear spike, high alert, widen wings significantly"
            term_stress = 3
        else:
            term_shape = "mixed"
            term_signal = "Mixed term structure — monitor closely"
            term_stress = 1

        result = {
            "vix9d": vix9d,
            "vix": vix,
            "vix3m": vix3m,
            "vix9d_vix_ratio": vix9d_vix_ratio,
            "vix_vix3m_ratio": vix_vix3m_ratio,
            "term_shape": term_shape,
            "term_signal": term_signal,
            "term_stress": term_stress,
            "fetched_at": datetime.now().isoformat(),
        }
    else:
        result = {
            "vix9d": vix9d,
            "vix": vix,
            "vix3m": vix3m,
            "term_shape": "unknown",
            "term_signal": "Partial VIX term data",
            "term_stress": 1,
            "fetched_at": datetime.now().isoformat(),
        }

    log.info(f"VIX term: {vix9d}/{vix}/{vix3m} → {term_shape}")
    _write_cache("vix_term", result)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# FULL SENTIMENT CONTEXT
# ─────────────────────────────────────────────────────────────────────────────

async def get_sentiment_context() -> dict:
    """
    Assemble complete sentiment context.
    Called by context_builder.py.
    """
    pcr_task = asyncio.create_task(get_put_call_ratio())
    fg_task = asyncio.create_task(get_fear_greed())

    # VIX term structure is synchronous (yfinance)
    import concurrent.futures
    loop = asyncio.get_event_loop()
    with concurrent.futures.ThreadPoolExecutor() as pool:
        vix_term = await loop.run_in_executor(pool, get_vix_term_structure)

    pcr = await pcr_task
    fg = await fg_task

    return {
        "put_call_ratio": pcr,
        "fear_greed": fg,
        "vix_term_structure": vix_term,
        "fetched_at": datetime.now().isoformat(),
    }


# CLI test
if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO)

    async def main():
        print("\n=== Put/Call Ratio ===")
        pcr = await get_put_call_ratio()
        print(json.dumps(pcr, indent=2))

        print("\n=== Fear & Greed ===")
        fg = await get_fear_greed()
        print(json.dumps(fg, indent=2))

        print("\n=== VIX Term Structure ===")
        vts = get_vix_term_structure()
        print(json.dumps(vts, indent=2))

    asyncio.run(main())
