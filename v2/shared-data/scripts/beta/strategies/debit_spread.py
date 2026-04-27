"""Debit Spread — SPX. Bull call or bear put, 14-21 DTE.

Regimes: TREND_UP, TREND_DOWN, NORMAL. Spec § 6.6.
"""
from __future__ import annotations
from typing import Optional

from .._chain_helpers import find_nearest_expiry, find_by_delta, nearest_strike, mid, leg


NAME = "debit_spread"
INSTRUMENT = "SPX"


def _direction(intel: dict) -> str:
    macro = intel.get("macro", {})
    if macro.get("spx_macd_signal") == "bullish" and macro.get("spx_ema_20_slope") == "positive":
        return "bull"
    if macro.get("spx_macd_signal") == "bearish" and macro.get("spx_ema_20_slope") == "negative":
        return "bear"
    return "bull" if macro.get("spx_ema_20_slope") == "positive" else "bear"


def can_enter(intel: dict, regime: str, journal: list) -> tuple[bool, str]:
    if regime not in ("TREND_UP", "TREND_DOWN", "NORMAL"):
        return False, f"regime {regime} not in debit-spread list"
    macro = intel.get("macro", {})
    if (macro.get("spx_iv_rank") or 100) >= 30:
        return False, f"IVR {macro.get('spx_iv_rank')} >=30"
    if (macro.get("spx_adx_14") or 0) <= 20:
        return False, f"ADX {macro.get('spx_adx_14')} <=20"
    rsi = macro.get("spx_rsi_14") or 50
    direction = _direction(intel)
    if direction == "bull" and not (40 <= rsi <= 70):
        return False, f"bull but RSI {rsi:.0f} outside 40-70"
    if direction == "bear" and not (30 <= rsi <= 60):
        return False, f"bear but RSI {rsi:.0f} outside 30-60"
    for d in ("fomc_days_away", "cpi_days_away", "jobs_days_away"):
        if (macro.get(d) or 999) <= 2:
            return False, f"{d} <=2"
    # Volume confirmation: SPX volume >= 20d average (low-volume days lack directional follow-through)
    vol_ratio = macro.get("spx_volume_ratio")
    if vol_ratio is not None and vol_ratio < 1.0:
        return False, f"spx_volume_ratio {vol_ratio:.2f} <1.0 (below avg volume)"
    return True, f"passed ({direction})"


def select_strikes(intel: dict, broker, account_equity: float) -> Optional[dict]:
    macro = intel.get("macro", {})
    spx = macro.get("spx_price") or 0
    if spx <= 0:
        return None
    direction = _direction(intel)
    right = "C" if direction == "bull" else "P"
    target_far = spx * (1.02 if direction == "bull" else 0.98)
    chain = broker.fetch_option_chain(
        INSTRUMENT, dte_range=(14, 21),
        strike_range=(spx * 0.95, spx * 1.05),
        include_quotes=True,
    )
    expiry = find_nearest_expiry(chain, target_dte=17, min_dte=14, max_dte=21)
    if not expiry:
        return None
    long_atm = find_by_delta(chain, 0.50 if right == "C" else -0.50, right, expiry) \
        or nearest_strike(chain, spx, right, expiry)
    short_otm = find_by_delta(chain, 0.27 if right == "C" else -0.27, right, expiry) \
        or nearest_strike(chain, target_far, right, expiry)
    if not (long_atm and short_otm) or long_atm["strike"] == short_otm["strike"]:
        return None
    lm, sm = mid(long_atm), mid(short_otm)
    if None in (lm, sm):
        return None
    debit = round(lm - sm, 2)
    width = abs(long_atm["strike"] - short_otm["strike"])
    if width <= 0 or debit <= 0:
        return None
    if debit > 0.35 * width:
        return None
    return {
        "legs": [leg("buy", long_atm, 1), leg("sell", short_otm, 1)],
        "underlying": INSTRUMENT,
        "expiry": expiry,
        "net_debit": debit,
        "max_risk": round(debit * 100, 2),
        "direction": direction,
        "spread_width": width,
        "net_delta": round((long_atm.get("delta") or 0.5) - (short_otm.get("delta") or 0.27), 3),
        "net_vega": round((long_atm.get("vega") or 0.10) - (short_otm.get("vega") or 0.07), 3),
    }


def build_exit_rules(strikes: dict, intel: dict) -> dict:
    return {
        "take_profit_pct": 100,
        "stop_loss_pct": -50,
        "time_exit_dte": 5,
        "trend_reversal_ema": 20,
        "event_buffer_days": 1,
    }


def position_size(account_equity: float, max_risk: float, risk_pct: float = 0.01) -> int:
    if max_risk <= 0:
        return 0
    budget = account_equity * risk_pct
    return max(1, int(budget // max_risk))
