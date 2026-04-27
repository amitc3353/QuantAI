"""SPX-derived intelligence helpers for Agent Beta.

market_intelligence.py imports from here. All functions degrade to None on
failure so Beta downstream can detect missing fields and skip gracefully.
"""
from __future__ import annotations

import logging
import math
from typing import Optional


def compute_adx_14(high, low, close) -> Optional[float]:
    """ADX(14) per spec § 3B. Pandas Series in. Float out, or None on failure."""
    try:
        import pandas as pd
        period = 14
        plus_dm = high.diff()
        minus_dm = low.diff().abs()
        plus_dm[plus_dm < 0] = 0
        minus_dm[minus_dm < 0] = 0
        tr = pd.DataFrame({
            'hl': high - low,
            'hc': (high - close.shift()).abs(),
            'lc': (low - close.shift()).abs(),
        }).max(axis=1)
        atr = tr.rolling(period).mean()
        plus_di = 100 * (plus_dm.rolling(period).mean() / atr.replace(0, 1e-10))
        minus_di = 100 * (minus_dm.rolling(period).mean() / atr.replace(0, 1e-10))
        denom = (plus_di + minus_di).replace(0, 1e-10)
        dx = 100 * ((plus_di - minus_di).abs() / denom)
        adx = dx.rolling(period).mean()
        v = float(adx.iloc[-1])
        if math.isnan(v) or math.isinf(v):
            return None
        return round(v, 2)
    except Exception as e:
        logging.warning("compute_adx_14 failed: %s", e)
        return None


def compute_bb_width_percentile(close, period: int = 20, lookback: int = 126) -> Optional[float]:
    """Bollinger Band width percentile (current vs trailing `lookback` days)."""
    try:
        sma = close.rolling(period).mean()
        std = close.rolling(period).std()
        upper = sma + 2 * std
        lower = sma - 2 * std
        width = (upper - lower) / sma.replace(0, 1e-10)
        if len(width) < lookback:
            return None
        current = float(width.iloc[-1])
        historical = width.iloc[-lookback:]
        pct = (historical < current).sum() / len(historical) * 100
        v = float(pct)
        if math.isnan(v):
            return None
        return round(v, 1)
    except Exception as e:
        logging.warning("compute_bb_width_percentile failed: %s", e)
        return None


def compute_iv_rank_252d(close) -> Optional[float]:
    """21-day realized-vol percentile over 252 days. Mirrors scan_options.get_iv_rank."""
    try:
        if len(close) < 30:
            return None
        returns = close.pct_change().dropna()
        rolling_std = returns.rolling(21).std()
        hv_current = float(returns.tail(21).std() * (252 ** 0.5) * 100)
        hv_52w_high = float(rolling_std.max() * (252 ** 0.5) * 100)
        hv_52w_low = float(rolling_std.min() * (252 ** 0.5) * 100)
        if hv_52w_high == hv_52w_low:
            return 50.0
        v = (hv_current - hv_52w_low) / (hv_52w_high - hv_52w_low) * 100
        if math.isnan(v):
            return None
        return round(v, 1)
    except Exception as e:
        logging.warning("compute_iv_rank_252d failed: %s", e)
        return None


def compute_ema_slope(close, period: int = 20, lookback: int = 5) -> tuple:
    """Returns (ema_value, slope_label). slope_label is 'positive' / 'negative' / 'flat'."""
    try:
        ema = close.ewm(span=period, adjust=False).mean()
        if len(ema) < lookback + 1:
            return (None, "flat")
        cur = float(ema.iloc[-1])
        ago = float(ema.iloc[-(lookback + 1)])
        slope_per_day = (cur - ago) / lookback
        threshold = abs(cur) * 0.0005  # 0.05% per day
        if slope_per_day > threshold:
            label = "positive"
        elif slope_per_day < -threshold:
            label = "negative"
        else:
            label = "flat"
        return (round(cur, 2), label)
    except Exception as e:
        logging.warning("compute_ema_slope failed: %s", e)
        return (None, "flat")


def compute_spx_chain_metrics(broker, spx_price: float, dte_target: int = 7) -> dict:
    """ATM straddle, implied move %, ATM bid-ask spread, 25-delta put/call skew.
    Falls back to None per field on any failure.
    """
    out = {
        "spx_atm_straddle_price": None,
        "spx_implied_move_pct": None,
        "spx_atm_bid_ask_spread": None,
        "spx_put_call_skew": None,
    }
    if broker is None or spx_price <= 0:
        return out
    try:
        strike_low = spx_price * 0.98
        strike_high = spx_price * 1.02
        chain = broker.fetch_option_chain(
            "SPX",
            dte_range=(0, dte_target),
            strike_range=(strike_low, strike_high),
            include_quotes=True,
        )
        if not chain:
            logging.warning("compute_spx_chain_metrics: empty SPX chain")
            return out
        expiries = sorted({e["expiry"] for e in chain if e.get("expiry")})
        if not expiries:
            return out
        target_expiry = expiries[0]
        contracts = [e for e in chain if e["expiry"] == target_expiry]
        atm_strike = min({c["strike"] for c in contracts}, key=lambda k: abs(k - spx_price))
        atm_call = next((c for c in contracts if c["strike"] == atm_strike and c["right"] == "C"), None)
        atm_put = next((c for c in contracts if c["strike"] == atm_strike and c["right"] == "P"), None)
        if atm_call and atm_put:
            cm, pm = atm_call.get("mid"), atm_put.get("mid")
            if cm is not None and pm is not None:
                straddle = round(cm + pm, 2)
                out["spx_atm_straddle_price"] = straddle
                out["spx_implied_move_pct"] = round(straddle / spx_price * 100, 3)
            cb, ca = atm_call.get("bid"), atm_call.get("ask")
            if cb is not None and ca is not None and ca > cb:
                out["spx_atm_bid_ask_spread"] = round(ca - cb, 2)
        puts = [c for c in contracts if c["right"] == "P" and c.get("delta") is not None]
        calls = [c for c in contracts if c["right"] == "C" and c.get("delta") is not None]
        skew = None
        if puts and calls:
            p25 = min(puts, key=lambda c: abs(abs(c["delta"]) - 0.25))
            c25 = min(calls, key=lambda c: abs(c["delta"] - 0.25))
            pm, cm = p25.get("mid"), c25.get("mid")
            if pm and cm and cm > 0:
                skew = round(pm / cm, 3)
        out["spx_put_call_skew"] = skew
        return out
    except Exception as e:
        logging.warning("compute_spx_chain_metrics failed: %s", e)
        return out
