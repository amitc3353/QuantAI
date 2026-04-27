"""Shared chain query / strike-selection helpers for Beta strategy modules.

Operates on the option chain shape returned by broker.fetch_option_chain():
  {symbol, underlying, strike, expiry (YYYY-MM-DD), right (C|P),
   bid, ask, mid, last, delta, gamma, theta, vega, open_interest, volume,
   _exchange, _tradingClass}
"""
from __future__ import annotations

from datetime import datetime, date, timedelta
from typing import Optional


def dte(expiry_iso: str, today: Optional[date] = None) -> int:
    today = today or date.today()
    try:
        d = datetime.strptime(expiry_iso, "%Y-%m-%d").date()
    except Exception:
        return -1
    return (d - today).days


def expiries_in_range(chain: list, min_dte: int, max_dte: int,
                      today: Optional[date] = None) -> list[str]:
    """Sorted unique expiries in [min_dte, max_dte]."""
    today = today or date.today()
    return sorted({e["expiry"] for e in chain
                   if e.get("expiry") and min_dte <= dte(e["expiry"], today) <= max_dte})


def find_nearest_expiry(chain: list, target_dte: int, min_dte: int = 0,
                        max_dte: int = 60, today: Optional[date] = None) -> Optional[str]:
    """Pick the expiry whose DTE is closest to `target_dte`, within bounds."""
    today = today or date.today()
    cands = expiries_in_range(chain, min_dte, max_dte, today)
    if not cands:
        return None
    return min(cands, key=lambda e: abs(dte(e, today) - target_dte))


def find_expiry_after(chain: list, after: date, min_dte: int = 0,
                      max_dte: int = 14, today: Optional[date] = None) -> Optional[str]:
    """Pick the soonest expiry strictly after `after` within [min_dte, max_dte]."""
    today = today or date.today()
    cands = [e for e in expiries_in_range(chain, min_dte, max_dte, today)
             if datetime.strptime(e, "%Y-%m-%d").date() > after]
    return cands[0] if cands else None


def filter_chain(chain: list, expiry: str, right: Optional[str] = None) -> list:
    out = [c for c in chain if c.get("expiry") == expiry]
    if right:
        out = [c for c in out if c.get("right") == right]
    return out


def nearest_strike(chain: list, target: float, right: str, expiry: str) -> Optional[dict]:
    """Return the chain entry with strike closest to target for given right/expiry."""
    cands = filter_chain(chain, expiry, right)
    if not cands:
        return None
    return min(cands, key=lambda c: abs(c["strike"] - target))


def find_by_delta(chain: list, target_delta: float, right: str,
                  expiry: str) -> Optional[dict]:
    """Closest-delta contract. Falls back to None if no deltas populated."""
    cands = [c for c in filter_chain(chain, expiry, right) if c.get("delta") is not None]
    if not cands:
        return None
    if right == "P":
        return min(cands, key=lambda c: abs(abs(c["delta"]) - abs(target_delta)))
    return min(cands, key=lambda c: abs(c["delta"] - target_delta))


def mid(entry: dict) -> Optional[float]:
    if entry is None:
        return None
    m = entry.get("mid")
    if m is not None:
        return float(m)
    b, a = entry.get("bid"), entry.get("ask")
    if b is not None and a is not None and a > b > 0:
        return round((b + a) / 2, 2)
    return None


def occ_for(entry: dict) -> str:
    return entry.get("symbol", "")


def leg(side: str, entry: dict, ratio: int = 1) -> dict:
    """Build the leg dict in the broker.place_mleg_order shape."""
    return {
        "side": side,
        "ratio_qty": str(ratio),
        "symbol": occ_for(entry),
        "type": entry.get("right", ""),
        "strike": entry.get("strike"),
        "expiry": entry.get("expiry"),
    }
