"""
market_data.py — Live Market Data Service for QuantAI
======================================================
Provides:
  - VIX level + regime classification (yfinance — free, no API key)
  - SPY/QQQ options chain with strikes, IVs, Greeks (Alpaca)
  - IV Rank calculation (52-week high/low of IV)
  - Market context snapshot (used by both Agent 1 and Agent 2)

Used by:
  - orchestrator/scheduler.py (morning brief enrichment)
  - orchestrator/agent1_iron_condor.py
  - orchestrator/agent2_covered_call.py

Install: pip install yfinance alpaca-py
"""

import os
import json
import logging
from datetime import datetime, date, timedelta
from typing import Optional
from pathlib import Path

import yfinance as yf

log = logging.getLogger("market-data")

ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")
CACHE_DIR = Path("/app/data/cache")
CACHE_TTL_MINUTES = int(os.getenv("CACHE_TTL_MINUTES", "15"))


# ─────────────────────────────────────────────────────────────────────────────
# CACHE HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _cache_path(key: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{key}.json"


def _read_cache(key: str) -> Optional[dict]:
    """Return cached data if fresh, else None."""
    p = _cache_path(key)
    if not p.exists():
        return None
    try:
        with open(p) as f:
            data = json.load(f)
        cached_at = datetime.fromisoformat(data.get("_cached_at", "2000-01-01"))
        age_minutes = (datetime.now() - cached_at).total_seconds() / 60
        if age_minutes < CACHE_TTL_MINUTES:
            return data
    except Exception:
        pass
    return None


def _write_cache(key: str, data: dict):
    data["_cached_at"] = datetime.now().isoformat()
    with open(_cache_path(key), "w") as f:
        json.dump(data, f)


# ─────────────────────────────────────────────────────────────────────────────
# VIX — yfinance (free, no API key required)
# ─────────────────────────────────────────────────────────────────────────────

def get_vix() -> dict:
    """
    Fetch current VIX level from yfinance.
    Returns regime classification used by guard engine and agents.

    Regimes:
      < 13  → extremely low vol — skip condors (too cheap)
      13-18 → normal — ideal for condors
      18-25 → elevated — rich premiums, manageable
      25-30 → high — tighten wings, reduce size
      > 30  → danger zone — advisory only (guard rule)
      > 35  → auto-halt (existing guard rule)
    """
    cached = _read_cache("vix")
    if cached:
        log.debug(f"VIX from cache: {cached['vix']}")
        return cached

    try:
        vix_ticker = yf.Ticker("^VIX")
        hist = vix_ticker.history(period="5d")
        if hist.empty:
            raise ValueError("No VIX data returned")

        current_vix = float(hist["Close"].iloc[-1])
        prev_vix = float(hist["Close"].iloc[-2]) if len(hist) > 1 else current_vix
        vix_change = round(current_vix - prev_vix, 2)

        # 52-week range for context
        hist_52w = vix_ticker.history(period="1y")
        vix_52w_low = float(hist_52w["Close"].min()) if not hist_52w.empty else current_vix
        vix_52w_high = float(hist_52w["Close"].max()) if not hist_52w.empty else current_vix
        vix_pct_rank = round(
            (current_vix - vix_52w_low) / (vix_52w_high - vix_52w_low) * 100, 1
        ) if vix_52w_high != vix_52w_low else 50.0

        # Regime classification
        if current_vix < 13:
            regime = "extremely_low"
            tradeable = False
            regime_note = "VIX < 13: premiums too cheap for condors, skip"
        elif current_vix < 18:
            regime = "normal"
            tradeable = True
            regime_note = "Normal vol — ideal conditions for iron condors"
        elif current_vix < 25:
            regime = "elevated"
            tradeable = True
            regime_note = "Elevated vol — rich premiums, good risk/reward"
        elif current_vix < 30:
            regime = "high"
            tradeable = True
            regime_note = "High vol — widen strikes, reduce position size"
        elif current_vix < 35:
            regime = "danger"
            tradeable = False
            regime_note = "VIX > 30: advisory only, no auto-execution"
        else:
            regime = "halt"
            tradeable = False
            regime_note = "VIX > 35: GUARD HALT — all auto-execution suspended"

        result = {
            "vix": round(current_vix, 2),
            "vix_change": vix_change,
            "vix_52w_low": round(vix_52w_low, 2),
            "vix_52w_high": round(vix_52w_high, 2),
            "vix_pct_rank": vix_pct_rank,
            "regime": regime,
            "tradeable": tradeable,
            "regime_note": regime_note,
            "source": "yfinance",
            "fetched_at": datetime.now().isoformat(),
        }

        _write_cache("vix", result)
        log.info(f"VIX: {current_vix:.2f} | Regime: {regime} | Tradeable: {tradeable}")
        return result

    except Exception as e:
        log.error(f"VIX fetch failed: {e}")
        # Return last cached value even if stale, or fallback
        p = _cache_path("vix")
        if p.exists():
            try:
                with open(p) as f:
                    stale = json.load(f)
                stale["stale"] = True
                log.warning(f"Returning stale VIX: {stale.get('vix')}")
                return stale
            except Exception:
                pass
        return {
            "vix": 20.0,
            "regime": "unknown",
            "tradeable": False,
            "regime_note": "VIX fetch failed — defaulting to non-tradeable for safety",
            "error": str(e),
        }


# ─────────────────────────────────────────────────────────────────────────────
# OPTIONS CHAIN — Alpaca
# ─────────────────────────────────────────────────────────────────────────────

def get_options_chain(symbol: str, dte_min: int = 0, dte_max: int = 45) -> dict:
    """
    Fetch live options chain for a symbol from Alpaca.
    Filters to strikes within ±10% of current price.
    Returns calls and puts with IV, bid/ask, OI.

    Requires: alpaca-py, ALPACA_API_KEY, ALPACA_SECRET_KEY
    """
    cache_key = f"options_{symbol}_{dte_min}_{dte_max}"
    cached = _read_cache(cache_key)
    if cached:
        log.debug(f"Options chain from cache: {symbol}")
        return cached

    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        return {"error": "Alpaca keys not configured", "symbol": symbol}

    try:
        from alpaca.data.historical.option import OptionHistoricalDataClient
        from alpaca.data.requests import OptionChainRequest

        client = OptionHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)

        # Get current price first
        stock_price = _get_stock_price(symbol)
        if not stock_price:
            return {"error": f"Could not get {symbol} price", "symbol": symbol}

        # Date range for expiries
        exp_start = date.today() + timedelta(days=dte_min)
        exp_end = date.today() + timedelta(days=dte_max)

        # Strike range: ±10% of current price
        strike_low = round(stock_price * 0.90, 0)
        strike_high = round(stock_price * 1.10, 0)

        request = OptionChainRequest(
            underlying_symbol=symbol.upper(),
            expiration_date_gte=exp_start,
            expiration_date_lte=exp_end,
            strike_price_gte=strike_low,
            strike_price_lte=strike_high,
        )

        chain = client.get_option_chain(request)

        calls = []
        puts = []
        for contract_symbol, snapshot in chain.items():
            try:
                greeks = snapshot.greeks
                quote = snapshot.latest_quote
                trade = snapshot.latest_trade

                # Parse option symbol: e.g. SPY250321C00565000
                parts = _parse_option_symbol(contract_symbol)

                entry = {
                    "symbol": contract_symbol,
                    "underlying": symbol.upper(),
                    "expiry": parts.get("expiry"),
                    "dte": parts.get("dte"),
                    "strike": parts.get("strike"),
                    "option_type": parts.get("option_type"),
                    "bid": float(quote.bid_price) if quote and quote.bid_price else 0,
                    "ask": float(quote.ask_price) if quote and quote.ask_price else 0,
                    "mid": round(
                        (float(quote.bid_price or 0) + float(quote.ask_price or 0)) / 2, 2
                    ) if quote else 0,
                    "last": float(trade.price) if trade and trade.price else 0,
                    "iv": round(float(snapshot.implied_volatility) * 100, 1) if snapshot.implied_volatility else None,
                    "delta": round(float(greeks.delta), 3) if greeks and greeks.delta else None,
                    "gamma": round(float(greeks.gamma), 4) if greeks and greeks.gamma else None,
                    "theta": round(float(greeks.theta), 3) if greeks and greeks.theta else None,
                    "vega": round(float(greeks.vega), 3) if greeks and greeks.vega else None,
                    "open_interest": int(snapshot.open_interest) if snapshot.open_interest else 0,
                    "volume": int(snapshot.day.volume) if snapshot.day and snapshot.day.volume else 0,
                    "spread": round(
                        float(quote.ask_price or 0) - float(quote.bid_price or 0), 2
                    ) if quote else 0,
                }

                if parts.get("option_type") == "call":
                    calls.append(entry)
                else:
                    puts.append(entry)
            except Exception as e:
                log.debug(f"Skipped {contract_symbol}: {e}")
                continue

        # Sort by strike
        calls.sort(key=lambda x: x.get("strike", 0))
        puts.sort(key=lambda x: x.get("strike", 0))

        result = {
            "symbol": symbol.upper(),
            "underlying_price": stock_price,
            "calls": calls,
            "puts": puts,
            "total_contracts": len(calls) + len(puts),
            "fetched_at": datetime.now().isoformat(),
        }

        _write_cache(cache_key, result)
        log.info(f"Options chain {symbol}: {len(calls)} calls, {len(puts)} puts")
        return result

    except ImportError:
        return {"error": "alpaca-py not installed — run: pip install alpaca-py", "symbol": symbol}
    except Exception as e:
        log.error(f"Options chain fetch failed for {symbol}: {e}")
        return {"error": str(e), "symbol": symbol}


def _get_stock_price(symbol: str) -> Optional[float]:
    """Get current stock price via yfinance (fast, no API key)."""
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="1d", interval="1m")
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
        # Fallback to info
        info = ticker.info
        return float(info.get("regularMarketPrice") or info.get("currentPrice") or 0) or None
    except Exception as e:
        log.warning(f"Could not get {symbol} price: {e}")
        return None


def _parse_option_symbol(symbol: str) -> dict:
    """
    Parse OCC option symbol format: SPY250321C00565000
    Returns: {underlying, expiry, dte, strike, option_type}
    """
    try:
        # Find where the date starts (first digit after letters)
        i = 0
        while i < len(symbol) and not symbol[i].isdigit():
            i += 1
        underlying = symbol[:i]
        rest = symbol[i:]  # e.g. 250321C00565000

        date_str = rest[:6]   # 250321
        opt_type = rest[6].upper()  # C or P
        strike_raw = rest[7:]  # 00565000 → $565.00

        expiry = datetime.strptime(date_str, "%y%m%d").date()
        dte = (expiry - date.today()).days
        strike = int(strike_raw) / 1000

        return {
            "underlying": underlying,
            "expiry": expiry.isoformat(),
            "dte": dte,
            "strike": strike,
            "option_type": "call" if opt_type == "C" else "put",
        }
    except Exception:
        return {"option_type": "unknown", "strike": 0, "dte": 0, "expiry": None}


# ─────────────────────────────────────────────────────────────────────────────
# IV RANK — 52-week implied volatility percentile
# ─────────────────────────────────────────────────────────────────────────────

def get_iv_rank(symbol: str) -> dict:
    """
    Calculate IV Rank using yfinance historical volatility as a proxy.
    IV Rank = (current IV - 52w low IV) / (52w high IV - 52w low IV) * 100

    Note: Uses historical volatility as IV proxy since true IV requires
    options data. Good enough for rank/regime classification.
    Higher = sell premium. Lower = buy premium.
    """
    cache_key = f"ivr_{symbol}"
    cached = _read_cache(cache_key)
    if cached:
        return cached

    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="1y")
        if hist.empty or len(hist) < 20:
            return {"symbol": symbol, "iv_rank": 50, "error": "Insufficient history"}

        # Rolling 30-day historical volatility
        returns = hist["Close"].pct_change().dropna()
        rolling_hv = returns.rolling(21).std() * (252 ** 0.5) * 100  # annualized %

        current_hv = float(rolling_hv.iloc[-1])
        hv_52w_low = float(rolling_hv.min())
        hv_52w_high = float(rolling_hv.max())

        if hv_52w_high == hv_52w_low:
            iv_rank = 50.0
        else:
            iv_rank = round(
                (current_hv - hv_52w_low) / (hv_52w_high - hv_52w_low) * 100, 1
            )

        # Classification
        if iv_rank >= 60:
            iv_regime = "high"
            sell_premium = True
        elif iv_rank >= 40:
            iv_regime = "medium"
            sell_premium = True
        else:
            iv_regime = "low"
            sell_premium = False

        result = {
            "symbol": symbol.upper(),
            "iv_rank": iv_rank,
            "current_hv": round(current_hv, 1),
            "hv_52w_low": round(hv_52w_low, 1),
            "hv_52w_high": round(hv_52w_high, 1),
            "iv_regime": iv_regime,
            "sell_premium": sell_premium,
            "source": "yfinance_hv_proxy",
            "fetched_at": datetime.now().isoformat(),
        }

        _write_cache(cache_key, result)
        log.info(f"IV Rank {symbol}: {iv_rank:.1f} ({iv_regime})")
        return result

    except Exception as e:
        log.error(f"IV rank failed for {symbol}: {e}")
        return {"symbol": symbol, "iv_rank": 50, "iv_regime": "unknown", "error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# MARKET CONTEXT — Full snapshot for agents
# ─────────────────────────────────────────────────────────────────────────────

def get_market_context(symbols: list[str] = None) -> dict:
    """
    Assemble a full market context snapshot.
    Called by agents before making any trading decision.
    Returns everything needed: VIX, IV ranks, prices, regimes.
    """
    if symbols is None:
        symbols = ["SPY", "QQQ", "NVDA", "PLTR", "TSM", "AMD", "AVGO", "ASML", "MU"]

    context = {
        "timestamp": datetime.now().isoformat(),
        "trading_day": date.today().isoformat(),
        "vix": get_vix(),
        "symbols": {},
    }

    for symbol in symbols:
        try:
            price = _get_stock_price(symbol)
            ivr = get_iv_rank(symbol)
            context["symbols"][symbol] = {
                "price": price,
                "iv_rank": ivr.get("iv_rank"),
                "iv_regime": ivr.get("iv_regime"),
                "sell_premium": ivr.get("sell_premium"),
            }
        except Exception as e:
            log.warning(f"Context failed for {symbol}: {e}")
            context["symbols"][symbol] = {"error": str(e)}

    log.info(
        f"Market context: VIX={context['vix'].get('vix', '?')} "
        f"regime={context['vix'].get('regime', '?')} "
        f"symbols={len(context['symbols'])}"
    )
    return context


# ─────────────────────────────────────────────────────────────────────────────
# STRIKE FINDER — Find optimal strikes for condor/spread
# ─────────────────────────────────────────────────────────────────────────────

def find_strikes_by_delta(
    chain: dict,
    option_type: str,
    target_delta: float,
    tolerance: float = 0.03,
) -> Optional[dict]:
    """
    Find the contract closest to target_delta in the options chain.
    option_type: 'call' or 'put'
    target_delta: e.g. 0.10 for OTM short strikes
    tolerance: acceptable delta range
    """
    contracts = chain.get("calls" if option_type == "call" else "puts", [])
    if not contracts:
        return None

    best = None
    best_diff = float("inf")

    for c in contracts:
        delta = c.get("delta")
        if delta is None:
            continue

        # For puts, delta is negative — compare absolute value
        abs_delta = abs(delta)
        diff = abs(abs_delta - abs(target_delta))

        if diff < best_diff and diff <= tolerance:
            # Liquidity check
            if c.get("open_interest", 0) >= 100 and c.get("spread", 999) <= 0.50:
                best_diff = diff
                best = c

    return best


def build_iron_condor_strikes(
    chain: dict,
    short_delta: float = 0.10,
    wing_width: float = 5.0,
) -> Optional[dict]:
    """
    Build a complete iron condor from the options chain.
    short_delta: target delta for short strikes (e.g. 0.10)
    wing_width: distance in dollars between short and long strikes

    Returns: {put_long, put_short, call_short, call_long, max_credit_estimate}
    """
    underlying_price = chain.get("underlying_price", 0)
    if not underlying_price:
        return None

    # Find short strikes
    short_put = find_strikes_by_delta(chain, "put", -short_delta)
    short_call = find_strikes_by_delta(chain, "call", short_delta)

    if not short_put or not short_call:
        log.warning("Could not find short strikes at target delta")
        return None

    # Build long strikes (protection wings)
    put_long_strike = short_put["strike"] - wing_width
    call_long_strike = short_call["strike"] + wing_width

    # Find the actual long contracts
    put_long = _find_contract_by_strike(chain["puts"], put_long_strike)
    call_long = _find_contract_by_strike(chain["calls"], call_long_strike)

    # Estimate credit
    put_credit = (short_put.get("mid", 0) - (put_long.get("mid", 0) if put_long else 0))
    call_credit = (short_call.get("mid", 0) - (call_long.get("mid", 0) if call_long else 0))
    total_credit = round(put_credit + call_credit, 2)
    max_risk = wing_width - total_credit

    return {
        "underlying": chain.get("symbol"),
        "underlying_price": underlying_price,
        "put_long": put_long,
        "put_short": short_put,
        "call_short": short_call,
        "call_long": call_long,
        "put_long_strike": put_long_strike,
        "put_short_strike": short_put.get("strike"),
        "call_short_strike": short_call.get("strike"),
        "call_long_strike": call_long_strike,
        "estimated_credit": total_credit,
        "max_risk": round(max_risk, 2),
        "risk_reward": round(max_risk / total_credit, 2) if total_credit > 0 else None,
        "short_put_delta": short_put.get("delta"),
        "short_call_delta": short_call.get("delta"),
        "expiry": short_put.get("expiry"),
        "dte": short_put.get("dte"),
    }


def _find_contract_by_strike(contracts: list, strike: float, tolerance: float = 1.0) -> Optional[dict]:
    best = None
    best_diff = float("inf")
    for c in contracts:
        diff = abs(c.get("strike", 0) - strike)
        if diff < best_diff and diff <= tolerance:
            best_diff = diff
            best = c
    return best


# ─────────────────────────────────────────────────────────────────────────────
# CLI TEST
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO)

    print("\n=== VIX ===")
    vix = get_vix()
    print(json.dumps(vix, indent=2))

    print("\n=== IV Rank: SPY ===")
    ivr = get_iv_rank("SPY")
    print(json.dumps(ivr, indent=2))

    print("\n=== Market Context ===")
    ctx = get_market_context(["SPY", "QQQ", "PLTR"])
    print(json.dumps(ctx, indent=2))
