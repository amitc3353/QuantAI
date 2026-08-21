"""
gex_engine.py — Gamma Exposure (GEX) Approximation
====================================================
Computes net gamma exposure across all strikes from free Alpaca option chain data.

WHAT IS GEX:
  Market makers who sell options must hedge. When they're short calls,
  they buy stock as price rises (positive gamma). When they're short puts,
  they sell stock as price falls (positive gamma). This creates a "magnetic"
  effect around high-gamma strikes — price tends to pin there.

  When gamma flips NEGATIVE (dealers are long options, not short), they
  hedge in the SAME direction as price — buying into rallies, selling
  into drops. This amplifies moves and creates whipsaw.

WHY IT MATTERS FOR IRON CONDORS:
  - Positive GEX near current price = pinning = great for iron condors
  - Negative GEX or GEX flip near current price = wild swings = terrible
  - The "GEX flip point" tells you where the market transitions from
    calm (positive) to wild (negative)

FORMULA:
  Per-strike GEX = gamma × open_interest × 100 × spot_price²
  (100 because each contract = 100 shares)
  Call OI contributes positive gamma (dealers assumed short calls)
  Put OI contributes negative gamma (dealers assumed short puts)
  Net GEX = sum of call GEX - sum of |put GEX| per strike

DATA SOURCE: Alpaca option chain (free, already fetched by market_data.py)

APPROXIMATION NOTE:
  We don't know actual dealer positioning. The standard assumption is
  dealers are net short options (retail is net long). This holds for
  most liquid names (SPY, QQQ) but breaks during unusual institutional
  hedging. Our approximation uses OI as a proxy for dealer exposure.
  Real GEX (SpotGamma, $50/mo) uses actual flow data.
"""

import os
import json
import logging
from datetime import datetime, date
from pathlib import Path
from typing import Optional

log = logging.getLogger("gex_engine")

CACHE_DIR = Path(os.getenv("CACHE_DIR", "/app/data/cache"))
CACHE_TTL_MINUTES = 15

# ── Cache helpers (same pattern as other services) ────────────────────────

def _cache_path(key: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"gex_{key}.json"


def _read_cache(key: str) -> Optional[dict]:
    p = _cache_path(key)
    if not p.exists():
        return None
    try:
        with open(p) as f:
            data = json.load(f)
        cached_at = datetime.fromisoformat(data.get("_cached_at", "2000-01-01"))
        age = (datetime.now() - cached_at).total_seconds() / 60
        if age < CACHE_TTL_MINUTES:
            return data
    except Exception:
        pass
    return None


def _write_cache(key: str, data: dict):
    data["_cached_at"] = datetime.now().isoformat()
    try:
        with open(_cache_path(key), "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        log.warning(f"GEX cache write failed: {e}")


# ── GEX Computation ──────────────────────────────────────────────────────

def compute_gex(chain: dict) -> dict:
    """
    Compute Gamma Exposure (GEX) profile from an options chain.

    chain: output from market_data.get_options_chain()
           Must contain 'calls', 'puts', 'underlying_price'

    Returns:
        {
            "symbol": "SPY",
            "spot_price": 565.0,
            "net_gex": 1234567.0,           # Total net GEX (positive = pinning)
            "gex_regime": "positive",        # positive | negative | neutral
            "gex_flip_point": 558.0,         # Price where GEX flips sign
            "flip_distance": 7.0,            # Distance from spot to flip
            "flip_distance_pct": 1.24,       # Flip distance as % of spot
            "max_gamma_strike": 565.0,       # Strike with highest absolute GEX
            "top_positive_strikes": [...],   # Strikes with most positive GEX (pinning)
            "top_negative_strikes": [...],   # Strikes with most negative GEX (whipsaw)
            "per_strike_gex": {...},         # Full GEX by strike
            "condor_assessment": "favorable", # favorable | neutral | unfavorable
            "condor_note": "...",
        }
    """
    symbol = chain.get("symbol", "?")
    spot = chain.get("underlying_price", 0)

    if not spot or spot <= 0:
        return {
            "symbol": symbol, "error": "No underlying price",
            "gex_regime": "unknown", "condor_assessment": "unknown",
        }

    calls = chain.get("calls", [])
    puts = chain.get("puts", [])

    if not calls and not puts:
        return {
            "symbol": symbol, "error": "Empty option chain",
            "gex_regime": "unknown", "condor_assessment": "unknown",
        }

    # ── Compute per-strike GEX ──────────────────────────────────────────
    # Standard formula: GEX = gamma × OI × 100 × spot²
    # Convention: call OI → positive GEX, put OI → negative GEX
    # (assumes dealers are short options = standard retail flow assumption)

    strike_gex = {}  # strike → net GEX value

    for contract in calls:
        strike = contract.get("strike")
        gamma = contract.get("gamma") or 0
        oi = contract.get("open_interest") or 0
        if strike and gamma > 0 and oi > 0:
            gex_val = gamma * oi * 100 * (spot ** 2) / 1e6  # Scale to millions
            strike_gex[strike] = strike_gex.get(strike, 0) + gex_val

    for contract in puts:
        strike = contract.get("strike")
        gamma = contract.get("gamma") or 0
        oi = contract.get("open_interest") or 0
        if strike and gamma > 0 and oi > 0:
            # Put gamma contributes NEGATIVE GEX (dealers short puts hedge opposite)
            gex_val = gamma * oi * 100 * (spot ** 2) / 1e6
            strike_gex[strike] = strike_gex.get(strike, 0) - gex_val

    if not strike_gex:
        return {
            "symbol": symbol, "error": "No gamma data in chain",
            "gex_regime": "unknown", "condor_assessment": "unknown",
        }

    # ── Aggregate metrics ───────────────────────────────────────────────

    net_gex = sum(strike_gex.values())
    sorted_strikes = sorted(strike_gex.items(), key=lambda x: x[0])

    # Find GEX flip point: where cumulative GEX changes sign
    # Walk from lowest strike upward, tracking cumulative sum
    gex_flip_point = None
    cumulative = 0
    prev_sign = None
    for strike, gex_val in sorted_strikes:
        cumulative += gex_val
        current_sign = 1 if cumulative >= 0 else -1
        if prev_sign is not None and current_sign != prev_sign:
            gex_flip_point = strike
        prev_sign = current_sign

    # Max gamma strike (absolute value)
    max_strike = max(strike_gex.items(), key=lambda x: abs(x[1]))

    # Top positive and negative strikes
    positive_strikes = sorted(
        [(s, g) for s, g in strike_gex.items() if g > 0],
        key=lambda x: x[1], reverse=True
    )[:5]
    negative_strikes = sorted(
        [(s, g) for s, g in strike_gex.items() if g < 0],
        key=lambda x: x[1]
    )[:5]

    # GEX at and near spot price (within $2)
    near_spot_gex = sum(
        g for s, g in strike_gex.items()
        if abs(s - spot) <= 2.0
    )

    # ── Regime classification ───────────────────────────────────────────

    if near_spot_gex > 0 and net_gex > 0:
        gex_regime = "positive"
    elif near_spot_gex < 0 or net_gex < 0:
        gex_regime = "negative"
    else:
        gex_regime = "neutral"

    # ── Flip distance ───────────────────────────────────────────────────

    flip_distance = abs(spot - gex_flip_point) if gex_flip_point else None
    flip_distance_pct = round((flip_distance / spot) * 100, 2) if flip_distance else None

    # ── Iron condor assessment ──────────────────────────────────────────

    if gex_regime == "positive" and (flip_distance is None or flip_distance_pct > 1.5):
        condor_assessment = "favorable"
        condor_note = (
            f"Positive GEX near spot — price tends to pin. "
            f"Flip point {'at $' + f'{gex_flip_point:.0f} ({flip_distance_pct:.1f}% away)' if gex_flip_point else 'not detected'}. "
            f"Good conditions for iron condors."
        )
    elif gex_regime == "positive" and flip_distance_pct and flip_distance_pct <= 1.5:
        condor_assessment = "neutral"
        condor_note = (
            f"Positive GEX but flip point close at ${gex_flip_point:.0f} "
            f"({flip_distance_pct:.1f}% away). Consider widening wings."
        )
    elif gex_regime == "negative":
        condor_assessment = "unfavorable"
        condor_note = (
            f"Negative GEX near spot — whipsaw risk elevated. "
            f"Dealers hedge same direction as price = amplified moves. "
            f"Skip iron condors or go advisory-only."
        )
    else:
        condor_assessment = "neutral"
        condor_note = "GEX neutral — no strong pinning or whipsaw signal."

    result = {
        "symbol": symbol,
        "spot_price": spot,
        "net_gex": round(net_gex, 2),
        "near_spot_gex": round(near_spot_gex, 2),
        "gex_regime": gex_regime,
        "gex_flip_point": gex_flip_point,
        "flip_distance": round(flip_distance, 2) if flip_distance else None,
        "flip_distance_pct": flip_distance_pct,
        "max_gamma_strike": max_strike[0],
        "max_gamma_value": round(max_strike[1], 2),
        "top_positive_strikes": [
            {"strike": s, "gex": round(g, 2)} for s, g in positive_strikes
        ],
        "top_negative_strikes": [
            {"strike": s, "gex": round(g, 2)} for s, g in negative_strikes
        ],
        "per_strike_gex": {
            str(s): round(g, 2) for s, g in sorted_strikes
        },
        "condor_assessment": condor_assessment,
        "condor_note": condor_note,
        "computed_at": datetime.now().isoformat(),
    }

    _write_cache(f"{symbol}_{date.today().isoformat()}", result)

    log.info(
        f"GEX {symbol}: regime={gex_regime} net={net_gex:.0f}M "
        f"near_spot={near_spot_gex:.0f}M flip={gex_flip_point or 'none'} "
        f"condor={condor_assessment}"
    )
    return result


def get_gex_summary(chain: dict) -> str:
    """One-line summary for Discord embeds and trade cards."""
    gex = compute_gex(chain)
    if gex.get("error"):
        return f"GEX: unavailable ({gex['error']})"

    regime = gex["gex_regime"]
    assessment = gex["condor_assessment"]
    flip = gex.get("gex_flip_point")
    flip_str = f"flip @ ${flip:.0f}" if flip else "no flip"

    emoji = {"favorable": "🟢", "neutral": "🟡", "unfavorable": "🔴"}.get(assessment, "⚪")
    return f"{emoji} GEX {regime} ({assessment}) — {flip_str}"
