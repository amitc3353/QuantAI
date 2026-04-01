#!/usr/bin/env python3
"""
QuantAI Pattern Engine
Reads trade journal and finds statistically significant patterns.
Requires 20+ closed trades before drawing conclusions.

Usage: python3 pattern_engine.py
Output: /home/trader/QuantAI/v2/shared-data/cache/patterns.json
"""
import json, os, math
from datetime import datetime
from zoneinfo import ZoneInfo

# Auto-load .env
import pathlib as _pl
for _ef in [_pl.Path("/home/trader/QuantAI/.env"), _pl.Path("/root/quantai-v2/.env")]:
    if _ef.exists():
        for _line in _ef.read_text().splitlines():
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.partition("=")
                if not os.environ.get(_k.strip()):
                    os.environ[_k.strip()] = _v.strip()
        break

ET = ZoneInfo("America/New_York")
JOURNAL = "/root/quantai-v2/shared-data/journal/paper/trades.jsonl"
CACHE = "/root/quantai-v2/shared-data/cache"
os.makedirs(CACHE, exist_ok=True)

def load_closed_trades():
    if not os.path.exists(JOURNAL):
        return []
    trades = []
    with open(JOURNAL) as f:
        for line in f:
            if line.strip():
                try:
                    t = json.loads(line)
                    if t.get("status") == "CLOSED" and t.get("pnl") is not None:
                        trades.append(t)
                except:
                    continue
    return trades

def win_rate(trades):
    if not trades:
        return 0.0
    return len([t for t in trades if t.get("pnl", 0) > 0]) / len(trades)

def z_score(p1, n1, p2, n2):
    """Two-proportion z-test. Returns z-score."""
    if n1 == 0 or n2 == 0:
        return 0.0
    p_pool = (p1 * n1 + p2 * n2) / (n1 + n2)
    if p_pool in (0, 1):
        return 0.0
    se = math.sqrt(p_pool * (1 - p_pool) * (1/n1 + 1/n2))
    if se == 0:
        return 0.0
    return (p1 - p2) / se

def is_significant(z, min_trades=5):
    """z > 1.96 = p < 0.05 (95% confidence)."""
    return abs(z) >= 1.96

def analyze_patterns(trades):
    patterns = []

    if len(trades) < 10:
        return [], f"Only {len(trades)} closed trades — need 20+ for reliable patterns. Keep trading."

    # ── Day of week ──────────────────────────────────────────────────
    days = {0:"Mon",1:"Tue",2:"Wed",3:"Thu",4:"Fri"}
    for day_num, day_name in days.items():
        day_trades = [t for t in trades
                      if datetime.fromisoformat(t["timestamp"]).weekday() == day_num]
        other_trades = [t for t in trades if t not in day_trades]
        if len(day_trades) >= 5 and len(other_trades) >= 5:
            wr_day = win_rate(day_trades)
            wr_other = win_rate(other_trades)
            z = z_score(wr_day, len(day_trades), wr_other, len(other_trades))
            if is_significant(z):
                direction = "outperforms" if wr_day > wr_other else "underperforms"
                patterns.append({
                    "pattern": f"{day_name} entries {direction} other days",
                    "win_rate_segment": round(wr_day * 100, 1),
                    "win_rate_baseline": round(wr_other * 100, 1),
                    "sample_size": len(day_trades),
                    "z_score": round(z, 2),
                    "recommended_rule": f"{'Prefer' if wr_day > wr_other else 'Avoid'} {day_name} entries" if abs(wr_day - wr_other) > 0.15 else None,
                    "confidence": "high" if abs(z) >= 2.5 else "moderate"
                })

    # ── Strategy type ────────────────────────────────────────────────
    strategies = set(t.get("strategy", "unknown") for t in trades)
    for strat in strategies:
        strat_trades = [t for t in trades if t.get("strategy") == strat]
        other_trades = [t for t in trades if t.get("strategy") != strat]
        if len(strat_trades) >= 5 and len(other_trades) >= 5:
            wr_s = win_rate(strat_trades)
            wr_o = win_rate(other_trades)
            z = z_score(wr_s, len(strat_trades), wr_o, len(other_trades))
            if is_significant(z):
                patterns.append({
                    "pattern": f"{strat} strategy has {'higher' if wr_s > wr_o else 'lower'} win rate",
                    "win_rate_segment": round(wr_s * 100, 1),
                    "win_rate_baseline": round(wr_o * 100, 1),
                    "sample_size": len(strat_trades),
                    "z_score": round(z, 2),
                    "recommended_rule": f"{'Scale up' if wr_s > wr_o else 'Reduce'} {strat} allocation",
                    "confidence": "high" if abs(z) >= 2.5 else "moderate"
                })

    # ── Hold time ────────────────────────────────────────────────────
    trades_with_close = [t for t in trades if t.get("timestamp_close")]
    if len(trades_with_close) >= 10:
        def hold_days(t):
            try:
                open_t = datetime.fromisoformat(t["timestamp"])
                close_t = datetime.fromisoformat(t["timestamp_close"])
                return (close_t - open_t).days
            except:
                return None

        short_hold = [t for t in trades_with_close if (hold_days(t) or 0) <= 2]
        long_hold = [t for t in trades_with_close if (hold_days(t) or 0) > 2]
        if len(short_hold) >= 5 and len(long_hold) >= 5:
            wr_s = win_rate(short_hold)
            wr_l = win_rate(long_hold)
            z = z_score(wr_s, len(short_hold), wr_l, len(long_hold))
            if is_significant(z):
                patterns.append({
                    "pattern": f"Trades held ≤2 days {'outperform' if wr_s > wr_l else 'underperform'} longer holds",
                    "win_rate_segment": round(wr_s * 100, 1),
                    "win_rate_baseline": round(wr_l * 100, 1),
                    "sample_size": len(short_hold),
                    "z_score": round(z, 2),
                    "recommended_rule": "Close positions earlier" if wr_s > wr_l else "Let winners run longer",
                    "confidence": "high" if abs(z) >= 2.5 else "moderate"
                })

    # ── Premium size ─────────────────────────────────────────────────
    trades_with_premium = [t for t in trades if t.get("premium")]
    if len(trades_with_premium) >= 10:
        premiums = sorted([t["premium"] for t in trades_with_premium])
        median_premium = premiums[len(premiums)//2]
        high_credit = [t for t in trades_with_premium if t["premium"] >= median_premium]
        low_credit = [t for t in trades_with_premium if t["premium"] < median_premium]
        if len(high_credit) >= 5 and len(low_credit) >= 5:
            wr_h = win_rate(high_credit)
            wr_l = win_rate(low_credit)
            z = z_score(wr_h, len(high_credit), wr_l, len(low_credit))
            if is_significant(z):
                patterns.append({
                    "pattern": f"Higher premium trades (≥${median_premium:.2f}) {'outperform' if wr_h > wr_l else 'underperform'}",
                    "win_rate_segment": round(wr_h * 100, 1),
                    "win_rate_baseline": round(wr_l * 100, 1),
                    "sample_size": len(high_credit),
                    "z_score": round(z, 2),
                    "recommended_rule": f"Raise min_credit to ${median_premium:.2f}" if wr_h > wr_l else f"Don't chase premium above ${median_premium:.2f}",
                    "confidence": "high" if abs(z) >= 2.5 else "moderate"
                })

    return patterns, None

# ── Main ─────────────────────────────────────────────────────────────
trades = load_closed_trades()
print(f"[pattern_engine] Loaded {len(trades)} closed trades")

patterns, message = analyze_patterns(trades)

result = {
    "timestamp": datetime.now(ET).isoformat(),
    "closed_trades_analyzed": len(trades),
    "patterns_found": len(patterns),
    "patterns": patterns,
    "message": message,
    "min_trades_needed": 20,
    "ready": len(trades) >= 20,
}

out_path = f"{CACHE}/patterns.json"
with open(out_path, "w") as f:
    json.dump(result, f, indent=2)

if message:
    print(f"[pattern_engine] {message}")
else:
    print(f"[pattern_engine] Found {len(patterns)} significant patterns:")
    for p in patterns:
        print(f"  [{p['confidence'].upper()}] {p['pattern']}")
        print(f"    Win rate: {p['win_rate_segment']}% vs baseline {p['win_rate_baseline']}% (n={p['sample_size']}, z={p['z_score']})")
        if p.get("recommended_rule"):
            print(f"    → {p['recommended_rule']}")

print(f"[pattern_engine] Saved → {out_path}")
