"""Credit Spread (theta offset) — SPX, HIGH_VOL ONLY. Spec § 6.8.

Negative-EV standalone (1:3 R:R against). Exists to offset theta drag from
long-vol strategies. Strict guards: max 1 open at a time, no weekends, no
events, half size (0.5%). Track P&L separately to validate the offset.
"""
from __future__ import annotations
from datetime import date
from typing import Optional

from .._chain_helpers import find_nearest_expiry, find_by_delta, nearest_strike, mid, leg, fill_quote


NAME = "credit_spread_offset"
INSTRUMENT = "SPX"


def _direction(intel: dict) -> str:
    macro = intel.get("macro", {})
    return "bull" if macro.get("spx_ema_20_slope") == "positive" else "bear"


def can_enter(intel: dict, regime: str, journal: list) -> tuple[bool, str]:
    if regime != "HIGH_VOL":
        return False, f"regime {regime} != HIGH_VOL (offset only fires here)"
    if any(t.get("strategy") == NAME and t.get("status") == "OPEN"
           for t in journal):
        return False, "credit-offset already open (max 1)"
    macro = intel.get("macro", {})
    for d in ("fomc_days_away", "cpi_days_away", "jobs_days_away"):
        if (macro.get(d) or 999) <= 1:
            return False, f"{d} <=1 — never hold credit through events"
    if date.today().weekday() == 4:  # Friday — close existing, don't open new
        return False, "Friday — never open credit spread before weekend"
    return True, "passed"


def select_strikes(intel: dict, broker, account_equity: float) -> Optional[dict]:
    macro = intel.get("macro", {})
    spx = macro.get("spx_price") or 0
    if spx <= 0:
        return None
    direction = _direction(intel)
    right = "P" if direction == "bull" else "C"
    chain = broker.fetch_option_chain(
        INSTRUMENT, dte_range=(21, 30),
        strike_range=(spx * 0.85, spx * 1.15),
        include_quotes=True,
    )
    expiry = find_nearest_expiry(chain, target_dte=25, min_dte=21, max_dte=30)
    if not expiry:
        return None
    target_short_delta = -0.12 if right == "P" else 0.12
    short = find_by_delta(chain, target_short_delta, right, expiry)
    if not short:
        return None
    # Long ~5 strikes further OTM
    width = max(round(spx * 0.005), 5)
    if right == "P":
        long_target = short["strike"] - width
    else:
        long_target = short["strike"] + width
    long_ = nearest_strike(chain, long_target, right, expiry)
    if not long_ or long_["strike"] == short["strike"]:
        return None
    fill_quote(short, broker)
    fill_quote(long_, broker)
    sm, lm = mid(short), mid(long_)
    if None in (sm, lm):
        return None
    credit = round(sm - lm, 2)
    if credit <= 0.20:
        return None
    actual_width = abs(short["strike"] - long_["strike"])
    max_loss = round((actual_width - credit) * 100, 2)
    return {
        "legs": [leg("sell", short, 1), leg("buy", long_, 1)],
        "underlying": INSTRUMENT,
        "expiry": expiry,
        "net_credit": credit,
        "max_risk": max_loss,
        "direction": direction,
        "spread_width": actual_width,
        "net_delta": round((-1 * (short.get("delta") or 0)) + (long_.get("delta") or 0), 3),
        "net_vega": round((-1 * (short.get("vega") or 0.08)) + (long_.get("vega") or 0.06), 3),
    }


def build_exit_rules(strikes: dict, intel: dict) -> dict:
    credit = strikes.get("net_credit") or 0
    return {
        "take_profit_pct": 50,
        "stop_loss_2x_credit": True,
        "credit_at_entry": credit,
        "time_exit_dte": 7,
        "regime_exit_on_change": ["HIGH_VOL"],  # exit if regime leaves
        "weekend_close": True,
        "event_close_buffer_days": 1,
        "gap_open_close": True,
    }


def position_size(account_equity: float, max_risk: float, risk_pct: float = 0.005) -> int:
    if max_risk <= 0:
        return 0
    budget = account_equity * risk_pct
    return max(1, int(budget // max_risk))
