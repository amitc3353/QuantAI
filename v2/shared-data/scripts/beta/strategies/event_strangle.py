"""Event Strangle — SPX. Long put + long call ~1.5% OTM, 3-7 DTE.

Regimes: PRE_EVENT (primary), SQUEEZE (pre-position), LOW_VOL (pre-position).
Thesis: buy vol before catalysts when implied move underprices history.
Spec § 6.1.
"""
from __future__ import annotations

from datetime import datetime, date
from typing import Optional

from .._chain_helpers import (
    expiries_in_range, filter_chain, nearest_strike, mid, leg, dte,
)


NAME = "event_strangle"
INSTRUMENT = "SPX"
PRIORITY_ORDER = ["CPI", "NFP", "GDP", "FOMC"]


def _next_event_info(intel: dict) -> tuple[Optional[str], int]:
    """Return (event_type, days_away) for the soonest upcoming event."""
    macro = intel.get("macro", {})
    items = [
        ("CPI", macro.get("cpi_days_away", 999)),
        ("NFP", macro.get("jobs_days_away", 999)),
        ("FOMC", macro.get("fomc_days_away", 999)),
    ]
    items = [(t, d) for t, d in items if isinstance(d, int) and d <= 3]
    if not items:
        return None, 999
    items.sort(key=lambda x: (x[1], PRIORITY_ORDER.index(x[0]) if x[0] in PRIORITY_ORDER else 99))
    return items[0]


def can_enter(intel: dict, regime: str, journal: list) -> tuple[bool, str]:
    if regime not in ("PRE_EVENT", "SQUEEZE", "LOW_VOL"):
        return False, f"regime {regime} not in event-strangle list"
    event_type, days = _next_event_info(intel)
    if regime == "PRE_EVENT" and event_type is None:
        return False, "PRE_EVENT but no priority event within 3d"
    macro = intel.get("macro", {})
    iv_rank = macro.get("spx_iv_rank") or 0
    if iv_rank >= 70:
        return False, f"IVR {iv_rank:.0f} >=70, vol already pricey"
    skew = macro.get("spx_put_call_skew")
    if skew is not None and skew >= 1.15:
        return False, f"put-call skew {skew} >=1.15, market hedging"
    # Implied move vs historical: only enforced when both fields present.
    move = macro.get("spx_implied_move_pct")
    if event_type and move is not None:
        try:
            import json
            from pathlib import Path
            em = json.loads(Path("/root/quantai-v2/shared-data/cache/event_moves.json").read_text())
            avg = (em.get(event_type) or {}).get("avg_8")
            if avg and move >= 0.65 * avg:
                return False, f"implied move {move:.2f}% >=65% of historical {avg:.2f}%"
        except Exception:
            pass
    if any(t.get("strategy") == NAME and t.get("status") == "OPEN"
           for t in journal):
        return False, "Event Strangle already open"
    return True, "all entry conditions passed"


def select_strikes(intel: dict, broker, account_equity: float) -> Optional[dict]:
    spx = (intel.get("macro") or {}).get("spx_price") or 0
    if spx <= 0:
        return None
    chain = broker.fetch_option_chain(
        INSTRUMENT, dte_range=(3, 7),
        strike_range=(spx * 0.97, spx * 1.03),
        include_quotes=True,
    )
    if not chain:
        return None
    expiries = expiries_in_range(chain, 3, 7)
    if not expiries:
        return None
    expiry = expiries[0]
    put = nearest_strike(chain, spx * 0.985, "P", expiry)
    call = nearest_strike(chain, spx * 1.015, "C", expiry)
    pm, cm = mid(put), mid(call)
    if not (put and call and pm and cm):
        return None
    cost = pm + cm
    if cost > spx * 0.015:
        return None
    return {
        "legs": [leg("buy", put), leg("buy", call)],
        "underlying": INSTRUMENT,
        "expiry": expiry,
        "net_debit": round(cost, 2),
        "max_risk": round(cost * 100, 2),
        "net_delta": round((call.get("delta") or 0.30) + (put.get("delta") or -0.30), 3),
        "net_vega": round((call.get("vega") or 0.10) + (put.get("vega") or 0.10), 3),
        "event_type": _next_event_info(intel)[0],
    }


def build_exit_rules(strikes: dict, intel: dict) -> dict:
    return {
        "scale_out_at": [{"gain_pct": 100, "sell_fraction": 0.5}],
        "trailing_stop_pct": 30,
        "stop_loss_pct": -50,
        "post_event_exit_hours": 1,
        "time_exit_dte": 1,
        "big_move_scale_out_pct": 3.0,
    }


def position_size(account_equity: float, max_risk: float, risk_pct: float = 0.005) -> int:
    if max_risk <= 0:
        return 0
    budget = account_equity * risk_pct
    return max(1, int(budget // max_risk))
