"""Daily scanner for Agent Gamma — Connors RSI(10) pullback in 200-SMA uptrend.

Runs once daily after market close (cron: 30 20 * * 1-5 = 4:30 PM ET).
For each symbol in UNIVERSE:
  1. Fetch ~252 daily bars via yfinance
  2. Compute SMA(200) and Wilder's RSI(10)
  3. Apply six filters (trend, RSI, earnings, post-earnings, liquidity, no-double-entry)
  4. Sort qualifying setups by RSI ascending (most oversold first)
"""
from __future__ import annotations

import concurrent.futures
import logging
import random
import time
from datetime import date
from typing import Iterable, Optional

YF_FETCH_TIMEOUT_SECONDS = 20

# Outer-loop parallelism for the universe scan (added 2026-05-09 with
# universe expansion 27 → 155). 12 workers gives ~5x speedup with
# ample headroom under yfinance's documented rate limit (~2K req/h).
SCAN_WORKERS_DEFAULT = 12

from . import (
    EARNINGS_BLACKOUT_DAYS,
    EARNINGS_POST_DAYS,
    INSTRUMENT_CONFIG,
    LIQUIDITY_MIN_VOLUME,
    RSI_ENTRY_THRESHOLD,
    UNIVERSE,
    yf_symbol,
)
from ._indicators import avg_volume, distance_above_pct, sma, wilders_rsi
from .earnings import days_since_earnings, days_to_earnings


def _do_yf_fetch(yf_sym: str) -> tuple[list[float], list[float]] | None:
    """Inner fetch — runs inside a worker thread so we can enforce a timeout."""
    try:
        import yfinance as yf
    except ImportError:
        logging.error("yfinance not installed; cannot scan")
        return None
    hist = yf.Ticker(yf_sym).history(period="1y", auto_adjust=False)
    if hist is None or hist.empty or "Close" not in hist:
        return None
    closes = [float(c) for c in hist["Close"].dropna().tolist()]
    volumes = [float(v) for v in hist.get("Volume", []).fillna(0).tolist()] if "Volume" in hist else []
    return closes, volumes


def _fetch_history(symbol: str) -> tuple[list[float], list[float]] | None:
    """Return (closes, volumes) for the symbol over the last ~1y, or None on
    failure / timeout. Each fetch is bounded by YF_FETCH_TIMEOUT_SECONDS so
    a single hung Yahoo backend cannot stall the scan.
    """
    yf_sym = yf_symbol(symbol)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        future = ex.submit(_do_yf_fetch, yf_sym)
        try:
            result = future.result(timeout=YF_FETCH_TIMEOUT_SECONDS)
        except concurrent.futures.TimeoutError:
            logging.warning("yfinance fetch timed out (>%ds) for %s (%s) — skipping",
                            YF_FETCH_TIMEOUT_SECONDS, symbol, yf_sym)
            future.cancel()
            return None
        except Exception as e:
            logging.warning("yfinance history failed for %s (%s): %s", symbol, yf_sym, e)
            return None
    if result is None:
        logging.warning("yfinance returned empty history for %s (%s)", symbol, yf_sym)
        return None
    return result


def _compute_indicators(symbol: str) -> dict | None:
    """Pure indicator pass — fetch, compute, no filters. Returns
    {close, sma_200, rsi_10, distance_above_200ma_pct, avg_volume_20d} or None."""
    cfg = INSTRUMENT_CONFIG[symbol]
    fetched = _fetch_history(symbol)
    if fetched is None:
        return None
    closes, volumes = fetched
    if len(closes) < 220:
        logging.warning("%s: only %d closes, need ≥220", symbol, len(closes))
        return None
    close = closes[-1]
    sma_200 = sma(closes, period=200)
    rsi_10 = wilders_rsi(closes, period=10)
    if sma_200 is None or rsi_10 is None:
        return None
    return {
        "symbol": symbol,
        "close": round(close, 2),
        "sma_200": round(sma_200, 2),
        "rsi_10": round(rsi_10, 2),
        "distance_above_200ma_pct": round(distance_above_pct(close, sma_200), 2),
        "avg_volume_20d": (round(avg_volume(volumes, period=20), 0)
                            if volumes else None),
        "type": cfg["type"],
        "tax": cfg["tax"],
        "sector": cfg.get("sector", "unknown"),
    }


def _qualifies(ind: dict, today: date, open_symbols: set[str],
               spread_status: Optional[dict] = None) -> bool:
    """Apply the 7 entry filters to a precomputed indicator row.

    Filters in evaluation order:
      F0  spread_status blocklist (added 2026-05-09 with universe expansion).
          Skips symbols flagged spread_too_wide or permanent_block_3_strikes.
          fetch_failed entries fall through (fail-open semantics).
      F1  open_symbols  — no duplicate position
      F2  trend         — close > SMA(200)
      F3  oversold      — RSI(10) < threshold
      F4  liquidity     — avg vol(20) >= 1M for stocks
      F5  pre-earnings  — days_to_earnings > 7 (or unknown)
      F6  post-earnings — days_since_earnings > 2 (or unknown)
    """
    sym = ind["symbol"]

    # F0: spread blocklist (added 2026-05-09). Runs FIRST so that blocked
    # symbols short-circuit without needing an INSTRUMENT_CONFIG entry — keeps
    # tests isolated and lets the verifier act on transient new symbols.
    # blocked_symbols() helper excludes fetch_failed entries → fail-open.
    if spread_status is not None:
        try:
            from .spread_verifier import blocked_symbols
            if sym in blocked_symbols(spread_status):
                return False
        except ImportError:
            # Verifier module not present (very early bootstrap or test) — fail open.
            pass

    cfg = INSTRUMENT_CONFIG[sym]

    if sym in open_symbols:
        return False
    if ind["close"] <= ind["sma_200"]:
        return False
    if ind["rsi_10"] >= RSI_ENTRY_THRESHOLD:
        return False
    if cfg["type"] == "stock":
        if (ind.get("avg_volume_20d") or 0) < LIQUIDITY_MIN_VOLUME and ind.get("avg_volume_20d") is not None:
            return False
    if cfg["type"] == "stock":
        d_to = days_to_earnings(sym, today)
        if d_to is not None and d_to <= EARNINGS_BLACKOUT_DAYS:
            ind["days_to_earnings"] = d_to
            return False
        ind["days_to_earnings"] = d_to
        d_since = days_since_earnings(sym, today)
        if d_since is not None and d_since <= EARNINGS_POST_DAYS:
            ind["days_since_earnings"] = d_since
            return False
        ind["days_since_earnings"] = d_since
    return True


def scan_with_indicators(universe: Iterable[str] | None = None,
                         open_symbols: set[str] | None = None,
                         today: date | None = None,
                         spread_status: Optional[dict] = None,
                         n_workers: int = SCAN_WORKERS_DEFAULT,
                         ) -> tuple[list[dict], dict[str, dict]]:
    """Parallel single-pass scan returning qualifying setups + indicator cache.

    Outer loop parallelized 2026-05-09 with universe expansion (27 → 155):
    sequential 27 × ~3s = ~90s; parallel 155 / 12 workers × ~3s = ~40s.

    Args:
      universe: symbols to scan (default: full UNIVERSE)
      open_symbols: skip these (already-held positions)
      today: scan reference date
      spread_status: payload from gamma_spread_status.json — F0 blocklist
      n_workers: ThreadPoolExecutor size (default 12)

    Returns (setups, indicator_cache):
      setups: list of qualifying entries sorted by RSI asc
      indicator_cache: {symbol: {close, rsi_10, sma_200, ...}} for every
        symbol whose indicators successfully computed
    """
    universe = list(universe) if universe is not None else UNIVERSE
    open_symbols = open_symbols or set()
    today = today or date.today()

    def _process(sym: str):
        # Small jitter so workers don't all hit yfinance at the exact same moment
        time.sleep(random.uniform(0, 0.2))
        try:
            return sym, _compute_indicators(sym)
        except Exception as e:
            logging.warning("gamma scan: %s evaluation failed: %s", sym, e)
            return sym, None

    cache: dict[str, dict] = {}
    setups: list[dict] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=n_workers) as ex:
        futures = [ex.submit(_process, s) for s in universe]
        for fut in concurrent.futures.as_completed(futures):
            sym, ind = fut.result()
            if ind is None:
                continue
            cache[sym] = ind
            # Note: _qualifies has internal stateful side-effect (writes
            # days_to_earnings into ind dict), so call it serially after
            # the parallel fetch completes — keeps Finnhub serialized.
            if _qualifies(ind, today, open_symbols, spread_status):
                setups.append(dict(ind))

    setups.sort(key=lambda x: x["rsi_10"])
    return setups, cache


def scan(universe: Iterable[str] | None = None,
         open_symbols: set[str] | None = None,
         today: date | None = None) -> list[dict]:
    """Compatibility wrapper — returns only qualifying setups."""
    setups, _ = scan_with_indicators(universe, open_symbols, today)
    return setups


def scan_summary(universe: Iterable[str] | None = None,
                 today: date | None = None) -> dict:
    """Lightweight scan that produces dashboard-friendly tallies WITHOUT the
    earnings calls (cheap version for the dashboard collector)."""
    universe = list(universe) if universe is not None else UNIVERSE
    today = today or date.today()

    total = 0
    above_ma = 0
    rsi_below = 0
    for sym in universe:
        fetched = _fetch_history(sym)
        if fetched is None:
            continue
        closes, _ = fetched
        if len(closes) < 220:
            continue
        total += 1
        s = sma(closes, period=200)
        r = wilders_rsi(closes, period=10)
        if s is None or r is None:
            continue
        if closes[-1] > s:
            above_ma += 1
        if r < RSI_ENTRY_THRESHOLD:
            rsi_below += 1
    return {"total_scanned": total, "above_200ma": above_ma, "rsi_below_30": rsi_below}
