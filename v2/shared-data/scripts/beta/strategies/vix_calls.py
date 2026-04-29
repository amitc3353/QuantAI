"""VIX Calls — Tail Hedge. Spec § 6.5.

Monthly purchase when conditions met:
  VIX < 20, contango < 10%, first trading day of month, no unexpired VIX calls.
Emergency: VIX 1d Δ ≥ +5 and not holding VIX calls → buy regardless.
Strike 80-100% above current VIX, 25-35 DTE, ~0.1% of account.
"""
from __future__ import annotations
from datetime import date
from typing import Optional

from .._chain_helpers import find_nearest_expiry, nearest_strike, mid, leg, fill_quote


NAME = "vix_calls"
INSTRUMENT = "VIX"


def _is_first_trading_day_of_month(today: date) -> bool:
    if today.day > 5:  # cheap pre-filter
        return False
    return today.weekday() < 5 and (today - date(today.year, today.month, 1)).days <= 4


def can_enter(intel: dict, regime: str, journal: list) -> tuple[bool, str]:
    macro = intel.get("macro", {})
    vix = macro.get("vix") or 0
    one_d = macro.get("vix_1d_change") or 0
    contango = macro.get("vix_contango_pct") or 999
    has_open_vix = any(t.get("strategy") == NAME and t.get("status") == "OPEN"
                       for t in journal)
    if one_d >= 5 and not has_open_vix:
        return True, f"emergency: VIX 1d Δ {one_d:+.1f}, no open VIX calls"
    if has_open_vix:
        return False, "already holding VIX calls"
    if not _is_first_trading_day_of_month(date.today()):
        return False, "not first trading day of month"
    if vix >= 20:
        return False, f"VIX {vix:.1f} >=20"
    if contango < 10:
        return False, f"contango {contango:.1f}% <10%"
    return True, "monthly schedule + conditions met"


def select_strikes(intel: dict, broker, account_equity: float) -> Optional[dict]:
    macro = intel.get("macro", {})
    vix = macro.get("vix") or 0
    if vix <= 0:
        return None
    chain = broker.fetch_option_chain(
        INSTRUMENT, dte_range=(25, 35),
        strike_range=(vix * 1.5, vix * 2.5),
        include_quotes=True,
    )
    expiry = find_nearest_expiry(chain, target_dte=30, min_dte=25, max_dte=35)
    if not expiry:
        return None
    target_strike = vix * 1.9  # midpoint of 80-100% above
    call = nearest_strike(chain, target_strike, "C", expiry)
    fill_quote(call, broker)
    cm = mid(call)
    if not (call and cm):
        return None
    return {
        "legs": [leg("buy", call, 1)],
        "underlying": INSTRUMENT,
        "expiry": expiry,
        "net_debit": round(cm, 2),
        "max_risk": round(cm * 100, 2),
        "strike": call["strike"],
        "net_delta": round((call.get("delta") or 0.10), 3),
        "net_vega": round((call.get("vega") or 0.05), 3),
    }


def build_exit_rules(strikes: dict, intel: dict) -> dict:
    strike = strikes.get("strike", 0)
    return {
        "vix_strike": strike,
        "vix_cross_strike_sell_fraction": 0.5,
        "vix_2x_strike_sell_fraction": 0.75,
        "let_expire_if_no_spike": True,
        "stop_loss_pct": -100,  # hedge — let it expire worthless
        "time_exit_dte": 0,
    }


def position_size(account_equity: float, max_risk: float, risk_pct: float = 0.001) -> int:
    if max_risk <= 0:
        return 0
    budget = account_equity * risk_pct
    return max(1, int(budget // max_risk))
