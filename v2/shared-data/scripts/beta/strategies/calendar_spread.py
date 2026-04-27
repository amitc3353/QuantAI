"""Calendar Spread — SPX. Sell 14 DTE ATM, buy 45-60 DTE ATM at same strike.

Regimes: RANGE, NORMAL. Spec § 6.7.
"""
from __future__ import annotations
from typing import Optional

from .._chain_helpers import find_nearest_expiry, nearest_strike, mid, leg


NAME = "calendar_spread"
INSTRUMENT = "SPX"


def can_enter(intel: dict, regime: str, journal: list) -> tuple[bool, str]:
    if regime not in ("RANGE", "NORMAL"):
        return False, f"regime {regime} not RANGE/NORMAL"
    macro = intel.get("macro", {})
    if (macro.get("spx_adx_14") or 50) >= 25:
        return False, f"ADX {macro.get('spx_adx_14')} >=25"
    return True, "passed"


def select_strikes(intel: dict, broker, account_equity: float) -> Optional[dict]:
    macro = intel.get("macro", {})
    spx = macro.get("spx_price") or 0
    if spx <= 0:
        return None
    chain = broker.fetch_option_chain(
        INSTRUMENT, dte_range=(7, 65),
        strike_range=(spx * 0.99, spx * 1.01),
        include_quotes=True,
    )
    short_expiry = find_nearest_expiry(chain, target_dte=14, min_dte=10, max_dte=21)
    long_expiry = find_nearest_expiry(chain, target_dte=49, min_dte=45, max_dte=60)
    if not (short_expiry and long_expiry) or short_expiry == long_expiry:
        return None
    short_c = nearest_strike(chain, spx, "C", short_expiry)
    long_c = nearest_strike(chain, spx, "C", long_expiry)
    sm, lm = mid(short_c), mid(long_c)
    if not (short_c and long_c and sm and lm):
        return None
    if short_c["strike"] != long_c["strike"]:
        # try put side instead
        short_p = nearest_strike(chain, spx, "P", short_expiry)
        long_p = nearest_strike(chain, spx, "P", long_expiry)
        if short_p and long_p and short_p["strike"] == long_p["strike"]:
            short_c, long_c = short_p, long_p
            sm, lm = mid(short_c), mid(long_c)
        else:
            return None
    debit = round(lm - sm, 2)
    if debit <= 0:
        return None
    return {
        "legs": [leg("buy", long_c, 1), leg("sell", short_c, 1)],
        "underlying": INSTRUMENT,
        "expiry_short": short_expiry,
        "expiry_long": long_expiry,
        "expiry": long_expiry,
        "net_debit": debit,
        "max_risk": round(debit * 100, 2),
        "strike": short_c["strike"],
        "net_delta": round((long_c.get("delta") or 0) - (short_c.get("delta") or 0), 3),
        "net_vega": round((long_c.get("vega") or 0.15) - (short_c.get("vega") or 0.08), 3),
    }


def build_exit_rules(strikes: dict, intel: dict) -> dict:
    return {
        "take_profit_pct": 50,
        "stop_loss_pct": -30,
        "short_leg_min_dte": 3,
        "underlying_breach_pct": 3.0,
        "underlying_breach_strike": strikes.get("strike"),
    }


def position_size(account_equity: float, max_risk: float, risk_pct: float = 0.0075) -> int:
    if max_risk <= 0:
        return 0
    budget = account_equity * risk_pct
    return max(1, int(budget // max_risk))
