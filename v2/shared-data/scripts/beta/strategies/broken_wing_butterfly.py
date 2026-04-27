"""Broken Wing Butterfly — XSP. Asymmetric profit if market stays in range.

Regimes: HIGH_VOL, RANGE, MEAN_REVERSION_*. Spec § 6.4.

Bullish BWB (puts): buy 1 lower wing (-3-4% wide), sell 2 body (ATM),
                    buy 1 upper wing (+1% narrow). Bias = bullish/range-up.
Bearish BWB (calls): mirror — buy 1 upper wide, sell 2 body, buy 1 lower narrow.
"""
from __future__ import annotations
from typing import Optional

from .._chain_helpers import find_nearest_expiry, nearest_strike, mid, leg


NAME = "broken_wing_butterfly"
INSTRUMENT = "XSP"


def _direction(intel: dict, regime: str) -> str:
    macro = intel.get("macro", {})
    if regime == "MEAN_REVERSION_OVERBOUGHT":
        return "bearish"
    if regime == "MEAN_REVERSION_OVERSOLD":
        return "bullish"
    if regime in ("HIGH_VOL", "RANGE"):
        slope = macro.get("spx_ema_20_slope")
        if slope == "negative":
            return "bearish"
        return "bullish"
    return "bullish"


def can_enter(intel: dict, regime: str, journal: list) -> tuple[bool, str]:
    if regime not in ("HIGH_VOL", "RANGE", "MEAN_REVERSION_OVERBOUGHT", "MEAN_REVERSION_OVERSOLD"):
        return False, f"regime {regime} not in BWB list"
    macro = intel.get("macro", {})
    if (macro.get("spx_iv_rank") or 0) <= 40:
        return False, f"IVR {macro.get('spx_iv_rank')} <=40, premium too low"
    return True, "passed"


def select_strikes(intel: dict, broker, account_equity: float) -> Optional[dict]:
    direction = _direction(intel, "RANGE")  # placeholder; caller passes regime via intel below
    macro = intel.get("macro", {})
    direction = _direction(intel, macro.get("_regime_override", "RANGE"))
    xsp_q = broker.get_quote("XSP")
    xsp_price = (xsp_q or {}).get("mid") if xsp_q else None
    if not xsp_price:
        spx = macro.get("spx_price") or 0
        xsp_price = spx / 10 if spx else 0
    if xsp_price <= 0:
        return None
    if direction == "bullish":
        right = "P"
        wide_strike_target = xsp_price * 0.965
        body_strike_target = xsp_price
        narrow_strike_target = xsp_price * 1.01
    else:
        right = "C"
        wide_strike_target = xsp_price * 1.035
        body_strike_target = xsp_price
        narrow_strike_target = xsp_price * 0.99
    chain = broker.fetch_option_chain(
        INSTRUMENT, dte_range=(14, 35),
        strike_range=(xsp_price * 0.94, xsp_price * 1.06),
        include_quotes=True,
    )
    expiry = find_nearest_expiry(chain, target_dte=21, min_dte=14, max_dte=35)
    if not expiry:
        return None
    wide = nearest_strike(chain, wide_strike_target, right, expiry)
    body = nearest_strike(chain, body_strike_target, right, expiry)
    narrow = nearest_strike(chain, narrow_strike_target, right, expiry)
    if not (wide and body and narrow) or len({wide["strike"], body["strike"], narrow["strike"]}) != 3:
        return None
    wm, bm, nm = mid(wide), mid(body), mid(narrow)
    if None in (wm, bm, nm):
        return None
    net = round(wm - 2 * bm + nm, 2)
    if direction == "bullish":
        max_loss = round((body["strike"] - wide["strike"]) * 100 + max(net, 0) * 100, 2)
    else:
        max_loss = round((wide["strike"] - body["strike"]) * 100 + max(net, 0) * 100, 2)
    return {
        "legs": [leg("buy", wide, 1), leg("sell", body, 2), leg("buy", narrow, 1)],
        "underlying": INSTRUMENT,
        "expiry": expiry,
        "net_debit": net,
        "max_risk": max_loss,
        "direction": direction,
        "net_delta": round(
            (wide.get("delta") or 0) + (-2 * (body.get("delta") or 0)) + (narrow.get("delta") or 0), 3
        ),
        "net_vega": round(
            (wide.get("vega") or 0) + (-2 * (body.get("vega") or 0)) + (narrow.get("vega") or 0), 3
        ),
    }


def build_exit_rules(strikes: dict, intel: dict) -> dict:
    return {
        "take_profit_pct": 300,
        "sweet_spot_dte": 7,
        "stop_loss_pct": -100,
        "time_exit_dte": 5,
        "breakout_pct": 2.0,
    }


def position_size(account_equity: float, max_risk: float, risk_pct: float = 0.0075) -> int:
    if max_risk <= 0:
        return 0
    budget = account_equity * risk_pct
    return max(1, int(budget // max_risk))
