"""Put Ratio Backspread 1x2 — XSP. Sell 1 ATM put, buy 2 OTM puts 5-8% below.

Regime: TREND_DOWN, CRISIS. Spec § 6.3.
"""
from __future__ import annotations
from typing import Optional

from .._chain_helpers import find_nearest_expiry, nearest_strike, mid, leg


NAME = "put_ratio_backspread"
INSTRUMENT = "XSP"


def can_enter(intel: dict, regime: str, journal: list) -> tuple[bool, str]:
    if regime not in ("TREND_DOWN", "CRISIS"):
        return False, f"regime {regime} not in TREND_DOWN/CRISIS"
    macro = intel.get("macro", {})
    skew = macro.get("spx_put_call_skew")
    if skew is not None and skew >= 1.25:
        return False, f"skew {skew} >=1.25, OTM puts too rich; use BWB"
    # BB width expanding: percentile must be >= 30 (not in lower tercile = contracting)
    bb_pct = macro.get("spx_bb_width_percentile_126d")
    if bb_pct is not None and bb_pct < 30:
        return False, f"BB width percentile {bb_pct:.0f} <30 (bands contracting)"
    if any(t.get("strategy") in (NAME, "call_ratio_backspread") and t.get("status") == "OPEN"
           for t in journal):
        return False, "ratio position already open"
    return True, "passed"


def select_strikes(intel: dict, broker, account_equity: float) -> Optional[dict]:
    xsp_q = broker.get_quote("XSP")
    xsp_price = (xsp_q or {}).get("mid") if xsp_q else None
    if not xsp_price:
        spx = (intel.get("macro") or {}).get("spx_price") or 0
        xsp_price = spx / 10 if spx else 0
    if xsp_price <= 0:
        return None
    chain = broker.fetch_option_chain(
        INSTRUMENT, dte_range=(21, 30),
        strike_range=(xsp_price * 0.90, xsp_price * 1.02),
        include_quotes=True,
    )
    expiry = find_nearest_expiry(chain, target_dte=21, min_dte=21, max_dte=30)
    if not expiry:
        return None
    short_p = nearest_strike(chain, xsp_price, "P", expiry)
    long_p = nearest_strike(chain, xsp_price * 0.94, "P", expiry)
    sm, lm = mid(short_p), mid(long_p)
    if not (short_p and long_p and sm and lm):
        return None
    if short_p["strike"] <= long_p["strike"]:
        return None
    net_debit = round(2 * lm - sm, 2)
    if net_debit > 1.00:
        return None
    valley_max_loss = round((short_p["strike"] - long_p["strike"] + max(net_debit, 0)) * 100, 2)
    return {
        "legs": [leg("sell", short_p, ratio=1), leg("buy", long_p, ratio=2)],
        "underlying": INSTRUMENT,
        "expiry": expiry,
        "net_debit": net_debit,
        "max_risk": valley_max_loss,
        "valley_strike": long_p["strike"],
        "net_delta": round((-1 * (short_p.get("delta") or -0.5)) + (2 * (long_p.get("delta") or -0.20)), 3),
        "net_vega": round((-1 * (short_p.get("vega") or 0.10)) + (2 * (long_p.get("vega") or 0.05)), 3),
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
