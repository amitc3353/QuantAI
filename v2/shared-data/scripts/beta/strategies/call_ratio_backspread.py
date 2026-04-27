"""Call Ratio Backspread 1x2 — XSP. Sell 1 ATM call, buy 2 OTM calls 5-8% above.

Regime: TREND_UP. Spec § 6.2.
"""
from __future__ import annotations
from typing import Optional

from .._chain_helpers import (
    expiries_in_range, find_nearest_expiry, nearest_strike, mid, leg,
)


NAME = "call_ratio_backspread"
INSTRUMENT = "XSP"


def can_enter(intel: dict, regime: str, journal: list) -> tuple[bool, str]:
    if regime != "TREND_UP":
        return False, f"regime {regime} != TREND_UP"
    macro = intel.get("macro", {})
    if (macro.get("spx_iv_rank") or 100) >= 30:
        return False, f"IVR {macro.get('spx_iv_rank')} >=30, ratios need cheap vol"
    rsi = macro.get("spx_rsi_14") or 50
    if not (55 <= rsi <= 75):
        return False, f"RSI {rsi:.0f} outside 55-75"
    if macro.get("spx_macd_signal") != "bullish":
        return False, "MACD not bullish"
    # BB width expanding: percentile must be >= 30 (not in lower tercile = contracting)
    bb_pct = macro.get("spx_bb_width_percentile_126d")
    if bb_pct is not None and bb_pct < 30:
        return False, f"BB width percentile {bb_pct:.0f} <30 (bands contracting)"
    if any(t.get("strategy") in (NAME, "put_ratio_backspread") and t.get("status") == "OPEN"
           for t in journal):
        return False, "ratio position already open"
    return True, "passed"


def select_strikes(intel: dict, broker, account_equity: float) -> Optional[dict]:
    # XSP price ≈ SPX/10. Use SPX*0.1 if XSP quote unavailable.
    xsp_q = broker.get_quote("XSP")
    xsp_price = (xsp_q or {}).get("mid") if xsp_q else None
    if not xsp_price:
        spx = (intel.get("macro") or {}).get("spx_price") or 0
        xsp_price = spx / 10 if spx else 0
    if xsp_price <= 0:
        return None
    chain = broker.fetch_option_chain(
        INSTRUMENT, dte_range=(21, 30),
        strike_range=(xsp_price * 0.98, xsp_price * 1.10),
        include_quotes=True,
    )
    expiry = find_nearest_expiry(chain, target_dte=21, min_dte=21, max_dte=30)
    if not expiry:
        return None
    short_c = nearest_strike(chain, xsp_price, "C", expiry)
    long_c = nearest_strike(chain, xsp_price * 1.06, "C", expiry)
    sm, lm = mid(short_c), mid(long_c)
    if not (short_c and long_c and sm and lm):
        return None
    if short_c["strike"] >= long_c["strike"]:
        return None
    net_debit = round(2 * lm - sm, 2)
    if net_debit > 1.00:
        return None
    valley_max_loss = round((long_c["strike"] - short_c["strike"] + max(net_debit, 0)) * 100, 2)
    return {
        "legs": [leg("sell", short_c, ratio=1), leg("buy", long_c, ratio=2)],
        "underlying": INSTRUMENT,
        "expiry": expiry,
        "net_debit": net_debit,
        "max_risk": valley_max_loss,
        "valley_strike": long_c["strike"],
        "net_delta": round((-1 * (short_c.get("delta") or 0.5)) + (2 * (long_c.get("delta") or 0.20)), 3),
        "net_vega": round((-1 * (short_c.get("vega") or 0.10)) + (2 * (long_c.get("vega") or 0.05)), 3),
    }


def build_exit_rules(strikes: dict, intel: dict) -> dict:
    return {
        "gamma_scalp_pct": 3.0,
        "gamma_scalp_sell_fraction": 0.5,
        "delta_exit_threshold": 0.65,
        "valley_strike": strikes.get("valley_strike"),
        "valley_proximity_pct": 5.0,
        "valley_exit_dte": 14,
        "hard_time_exit_dte": 14,
        "trend_reversal_ema": 20,
        "trend_reversal_adx_min": 15,
    }


def position_size(account_equity: float, max_risk: float, risk_pct: float = 0.01) -> int:
    if max_risk <= 0:
        return 0
    budget = account_equity * risk_pct
    return max(1, int(budget // max_risk))
