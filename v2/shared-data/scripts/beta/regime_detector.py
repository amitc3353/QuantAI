#!/usr/bin/env python3
"""Regime detector for Agent Beta.

Pure-Python classifier — no LLM, no external calls beyond reading the
cached market_intelligence.json. Implements spec § 4 priority chain
literally — first match wins.

Twelve regimes: HALT, CRISIS, MEAN_REVERSION_OVERBOUGHT,
MEAN_REVERSION_OVERSOLD, HIGH_VOL, SQUEEZE, PRE_EVENT, TREND_UP,
TREND_DOWN, LOW_VOL, RANGE, NORMAL.

State: tracks `days_since_halt` in beta_regime_state.json so the post-HALT
cooldown rule (refuse to re-engage for 2 days after a HALT) works across
process restarts.
"""
from __future__ import annotations

import json
import os
import sys
import logging
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

sys.path.insert(0, "/home/trader/QuantAI/v2/shared-data/scripts")

ET = ZoneInfo("America/New_York")
CACHE = Path("/root/quantai-v2/shared-data/cache")
INTEL_PATH = CACHE / "market_intelligence.json"
STATE_PATH = CACHE / "beta_regime_state.json"

REGIMES = [
    "HALT", "CRISIS",
    "MEAN_REVERSION_OVERBOUGHT", "MEAN_REVERSION_OVERSOLD",
    "HIGH_VOL", "SQUEEZE", "PRE_EVENT",
    "TREND_UP", "TREND_DOWN",
    "LOW_VOL", "RANGE", "NORMAL",
]


def _g(intel: dict, *keys, default=None):
    """Helper: try nested keys (macro.X then top-level X)."""
    macro = intel.get("macro", {}) if isinstance(intel, dict) else {}
    for k in keys:
        if k in macro and macro[k] is not None:
            return macro[k]
        if k in intel and intel[k] is not None:
            return intel[k]
    return default


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except Exception:
            pass
    return {"last_halt_date": None}


def save_state(state: dict) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    os.replace(tmp, STATE_PATH)


def days_since_halt(state: dict, today: Optional[date] = None) -> int:
    today = today or datetime.now(ET).date()
    last = state.get("last_halt_date")
    if not last:
        return 999
    try:
        last_d = datetime.strptime(last, "%Y-%m-%d").date()
    except Exception:
        return 999
    return (today - last_d).days


def classify_regime(intel: dict, state: Optional[dict] = None,
                    today: Optional[date] = None) -> tuple[str, str]:
    """Returns (regime, reason). Reason is a short human-readable explanation."""
    state = state if state is not None else load_state()
    today = today or datetime.now(ET).date()

    vix = _g(intel, "vix", default=0.0)
    vix_term = _g(intel, "vix_term_structure", default="contango")
    iv_rank = _g(intel, "spx_iv_rank", default=50.0)
    adx = _g(intel, "spx_adx_14", default=20.0)
    rsi = _g(intel, "spx_rsi_14", default=50.0)
    price = _g(intel, "spx_price", default=0.0)
    ema_20 = _g(intel, "spx_ema_20", default=0.0)
    ema_50 = _g(intel, "spx_ema_50", default=0.0)
    ema_20_slope = _g(intel, "spx_ema_20_slope", default="flat")
    bb_pct = _g(intel, "spx_bb_width_percentile_126d", default=50.0)
    atm_spread = _g(intel, "spx_atm_bid_ask_spread", default=0.0) or 0.0
    event_within_3 = bool(_g(intel, "event_within_3_days", default=False))

    dsh = days_since_halt(state, today)

    # 1. HALT — VIX cap.
    if vix >= 35:
        return "HALT", f"VIX {vix:.1f} ≥ 35"

    # 2. Post-HALT cooldown.
    if dsh < 2:
        return "HALT", f"post-HALT cooldown ({dsh}d since last HALT)"

    # 3. Microstructure check — wide ATM spreads = broken market.
    if atm_spread > 0.50:
        return "HALT", f"SPX ATM spread {atm_spread:.2f} > $0.50"

    # 4. CRISIS — institutional panic.
    if vix >= 28 and vix_term == "backwardation":
        return "CRISIS", f"VIX {vix:.1f} + backwardation"

    # 5. Mean reversion — overextended without trend.
    if rsi > 80 and adx < 25:
        return "MEAN_REVERSION_OVERBOUGHT", f"RSI {rsi:.0f} >80, ADX {adx:.0f} <25"
    if rsi < 20 and adx < 25:
        return "MEAN_REVERSION_OVERSOLD", f"RSI {rsi:.0f} <20, ADX {adx:.0f} <25"

    # 6. HIGH_VOL — sell premium.
    if vix >= 22 and iv_rank >= 60:
        return "HIGH_VOL", f"VIX {vix:.1f} + IVR {iv_rank:.0f}"

    # 7. SQUEEZE — BB compression.
    if bb_pct < 10 and adx < 15 and vix < 16:
        return "SQUEEZE", f"BB%{bb_pct:.0f} <10, ADX {adx:.0f} <15, VIX {vix:.1f} <16"

    # 8. PRE_EVENT.
    if event_within_3:
        return "PRE_EVENT", "macro event within 3 days"

    # 9. Trending.
    if adx >= 20:
        if price > ema_20 and ema_20_slope == "positive" and price > ema_50:
            return "TREND_UP", f"ADX {adx:.0f}, price>EMA20 (rising)>EMA50"
        if price < ema_20 and ema_20_slope == "negative" and price < ema_50:
            return "TREND_DOWN", f"ADX {adx:.0f}, price<EMA20 (falling)<EMA50"

    # 10. LOW_VOL.
    if vix < 16 and iv_rank < 25:
        return "LOW_VOL", f"VIX {vix:.1f} <16, IVR {iv_rank:.0f} <25"

    # 11. RANGE — no trend.
    if adx < 20:
        return "RANGE", f"ADX {adx:.0f} <20"

    # 12. Default.
    return "NORMAL", "no specialized regime triggered"


def write_dashboard_state(regime: str, reason: str, intel: dict) -> None:
    """Mirror current regime to /var/dashboard/state/agent-beta-state.json."""
    out_path = Path("/var/dashboard/state/agent-beta-state.json")
    payload = {
        "last_updated": datetime.now(ET).isoformat(),
        "status": "ok",
        "data": {
            "current_regime": regime,
            "regime_reason": reason,
            "vix": _g(intel, "vix"),
            "spx_price": _g(intel, "spx_price"),
            "spx_iv_rank": _g(intel, "spx_iv_rank"),
            "spx_adx_14": _g(intel, "spx_adx_14"),
            "event_within_3_days": _g(intel, "event_within_3_days", default=False),
        },
    }
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = out_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        os.replace(tmp, out_path)
    except Exception as e:
        logging.warning("failed to write %s: %s", out_path, e)


def backtest(days: int = 90) -> dict:
    """Replay the last `days` of SPX history through classify_regime.
    Builds synthetic intel snapshots (technicals from yfinance + current
    VIX history). Skips chain-derived fields (events, ATM spread) since we
    can't reconstruct them historically — those branches simply don't fire.
    Returns a count by regime.
    """
    import yfinance as yf
    sys.path.insert(0, "/home/trader/QuantAI/v2/shared-data/scripts")
    from _beta_intel import (
        compute_adx_14,
        compute_bb_width_percentile,
        compute_iv_rank_252d,
        compute_ema_slope,
    )

    spx = yf.Ticker("^GSPC").history(period="500d")
    vix = yf.Ticker("^VIX").history(period="500d")
    vix3m = yf.Ticker("^VIX3M").history(period="500d")
    if len(spx) < days + 50:
        print(f"insufficient history ({len(spx)} rows)")
        return {}
    counts: dict[str, int] = {r: 0 for r in REGIMES}
    last_halt: Optional[date] = None
    samples = []
    for i in range(len(spx) - days, len(spx)):
        cutoff = spx.iloc[: i + 1]
        if len(cutoff) < 252:
            continue
        close = cutoff["Close"]
        high = cutoff["High"]
        low = cutoff["Low"]
        d_idx = cutoff.index[-1].date()
        # VIX/3M for that date — nearest match
        try:
            v = float(vix.loc[vix.index <= cutoff.index[-1]].iloc[-1]["Close"])
            v3 = float(vix3m.loc[vix3m.index <= cutoff.index[-1]].iloc[-1]["Close"])
        except Exception:
            continue
        ema20_v, ema20_slope = compute_ema_slope(close)
        intel = {"macro": {
            "vix": v,
            "vix_3m": v3,
            "vix_term_structure": "backwardation" if v > v3 else "contango",
            "spx_price": float(close.iloc[-1]),
            "spx_rsi_14": _rsi14(close),
            "spx_adx_14": compute_adx_14(high, low, close) or 20.0,
            "spx_iv_rank": compute_iv_rank_252d(close) or 50.0,
            "spx_ema_20": ema20_v or 0.0,
            "spx_ema_50": float(close.ewm(span=50, adjust=False).mean().iloc[-1]),
            "spx_ema_20_slope": ema20_slope,
            "spx_bb_width_percentile_126d": compute_bb_width_percentile(close) or 50.0,
            "event_within_3_days": False,
            "spx_atm_bid_ask_spread": 0.0,
        }}
        state = {"last_halt_date": last_halt.isoformat() if last_halt else None}
        regime, reason = classify_regime(intel, state, today=d_idx)
        if regime == "HALT" and ("VIX" in reason or "backwardation" in reason):
            last_halt = d_idx
        counts[regime] += 1
        samples.append((d_idx.isoformat(), regime, round(v, 1), reason))
    print(f"\n=== Regime backtest over last {days} trading days ===")
    for r in REGIMES:
        if counts[r]:
            print(f"  {r:30s} {counts[r]:3d} ({counts[r] / days * 100:.0f}%)")
    print("\nLast 10 days:")
    for s in samples[-10:]:
        print(f"  {s[0]}  VIX={s[2]:5.1f}  {s[1]:25s}  {s[3]}")
    return counts


def _rsi14(close) -> float:
    d = close.diff()
    g = d.clip(lower=0).rolling(14).mean()
    l = (-d.clip(upper=0)).rolling(14).mean()
    rs = g / l.replace(0, 1e-10)
    return float((100 - 100 / (1 + rs)).iloc[-1])


def main() -> int:
    if "--backtest" in sys.argv:
        backtest(days=90)
        return 0
    if not INTEL_PATH.exists():
        print(f"ERROR: {INTEL_PATH} not found", file=sys.stderr)
        return 1
    intel = json.loads(INTEL_PATH.read_text())
    state = load_state()
    regime, reason = classify_regime(intel, state)
    if regime == "HALT" and state.get("last_halt_date") != datetime.now(ET).date().isoformat():
        if "VIX" in reason or "spread" in reason or "backwardation" in reason:
            state["last_halt_date"] = datetime.now(ET).date().isoformat()
            save_state(state)
    print(f"[regime_detector] {regime} — {reason}")
    write_dashboard_state(regime, reason, intel)
    return 0


if __name__ == "__main__":
    sys.exit(main())
