"""
correlation_analyzer.py — Context Score Correlation Engine
===========================================================
Answers: "Is the context score actually predicting trade outcomes?"

Runs every Friday EOD. Reads last 4 weeks of:
  - Pre-trade context scores (cached in /app/data/cache/context_SPY_*.json)
  - Trade outcomes (from agent journals)

Computes:
  1. Overall: do high-score days (≥60) win more than low-score days (<60)?
  2. Per-signal: which of the 5 signals (VIX, event, macro, sentiment, flow)
     are actually correlated with outcomes?
  3. Threshold tuning: if VIX score is not predictive, should its weight be lower?

After 4+ weeks of data, proposes weight adjustments to context_builder.py.
Posts correlation report to #system-health every Friday.

This is what makes the intelligence layer get smarter over time.
"""

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

log = logging.getLogger("correlation-analyzer")

CACHE_DIR = Path("/app/data/cache")
JOURNAL_DIR = Path("/app/data/memory/paper")
RESULTS_DIR = Path("/app/data/journal")
WEIGHTS_FILE = Path("/app/configs/context_weights.json")

# Default signal weights (must sum to 100)
DEFAULT_WEIGHTS = {
    "vix":       25,
    "event":     15,
    "macro":     20,
    "sentiment": 20,
    "flow":      20,
}

MIN_TRADES_FOR_ANALYSIS = 10  # Need at least this many to draw conclusions
MIN_WEEKS_FOR_TUNING = 4      # Don't auto-tune until 4 weeks of data


def load_weights() -> dict:
    """Load current signal weights, fall back to defaults."""
    if WEIGHTS_FILE.exists():
        try:
            with open(WEIGHTS_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return DEFAULT_WEIGHTS.copy()


def save_weights(weights: dict):
    """Save updated weights to config."""
    WEIGHTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(WEIGHTS_FILE, "w") as f:
        json.dump(weights, f, indent=2)
    log.info(f"Context weights updated: {weights}")


# ─────────────────────────────────────────────────────────────────────────────
# DATA LOADING
# ─────────────────────────────────────────────────────────────────────────────

def load_context_scores(days: int = 30) -> list:
    """Load all context score cache files from the last N days."""
    cutoff = (date.today() - timedelta(days=days)).isoformat().replace("-", "")
    scores = []

    for cache_file in CACHE_DIR.glob("context_SPY_*.json"):
        # Filename format: context_SPY_YYYYMMDD_HH.json
        stem = cache_file.stem  # context_SPY_20260321_09
        parts = stem.split("_")
        if len(parts) >= 4:
            date_part = parts[2]  # YYYYMMDD
            if date_part >= cutoff:
                try:
                    with open(cache_file) as f:
                        data = json.load(f)
                    scores.append({
                        "date": f"{date_part[:4]}-{date_part[4:6]}-{date_part[6:8]}",
                        "hour": parts[3] if len(parts) > 3 else "09",
                        "score": data.get("score", 0),
                        "decision": data.get("decision", "unknown"),
                        "components": data.get("components", {}),
                    })
                except Exception:
                    continue

    log.info(f"Loaded {len(scores)} context score records (last {days} days)")
    return scores


def load_trade_outcomes(agent: str, days: int = 30) -> list:
    """Load completed trades (entry+exit pairs) for an agent."""
    journal_file = JOURNAL_DIR / f"{agent}_journal.jsonl"
    if not journal_file.exists():
        return []

    cutoff = (date.today() - timedelta(days=days)).isoformat()
    entries = {}
    exits = []

    with open(journal_file) as f:
        for line in f:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                trade_date = record.get("date", record.get("timestamp", "")[:10])
                if trade_date < cutoff:
                    continue
                if record.get("event") == "entry":
                    entries[trade_date + record.get("symbol", "")] = record
                elif record.get("event") == "exit":
                    exits.append(record)
            except Exception:
                continue

    # Match exits to entries to get context score at entry time
    outcomes = []
    for exit_rec in exits:
        exit_date = exit_rec.get("date", "")
        sym = exit_rec.get("symbol", "")
        entry_rec = entries.get(exit_date + sym)

        outcomes.append({
            "date": exit_date,
            "symbol": sym,
            "pnl": exit_rec.get("pnl_per_contract") or exit_rec.get("pnl", 0),
            "outcome": exit_rec.get("outcome", "unknown"),
            "close_reason": exit_rec.get("close_reason", ""),
            "context_score_at_entry": entry_rec.get("context_score") if entry_rec else None,
            "params_version": entry_rec.get("params_version") if entry_rec else None,
        })

    return outcomes


def match_scores_to_outcomes(scores: list, outcomes: list) -> list:
    """
    Join context scores with trade outcomes by date.
    Returns list of {date, score, component_scores, pnl, outcome} records.
    """
    # Group scores by date (use 9 AM score as the pre-market score for that day)
    daily_scores = {}
    for s in scores:
        d = s["date"]
        hour = int(s.get("hour", "9"))
        if d not in daily_scores or abs(hour - 9) < abs(int(daily_scores[d].get("hour", "9")) - 9):
            daily_scores[d] = s

    matched = []
    for outcome in outcomes:
        d = outcome["date"]
        score_rec = daily_scores.get(d)
        if score_rec:
            matched.append({
                **outcome,
                "context_score": score_rec["score"],
                "context_decision": score_rec["decision"],
                "component_scores": score_rec.get("components", {}),
            })

    log.info(f"Matched {len(matched)} trades with context scores")
    return matched


# ─────────────────────────────────────────────────────────────────────────────
# CORRELATION ANALYSIS
# ─────────────────────────────────────────────────────────────────────────────

def analyze_overall_correlation(matched: list) -> dict:
    """
    Does overall context score predict outcomes?
    Splits into high-score (≥60) vs low-score (<60) days.
    """
    high_score = [m for m in matched if m.get("context_score", 0) >= 60]
    low_score = [m for m in matched if m.get("context_score", 0) < 60]

    def win_rate(records):
        if not records:
            return 0, 0, 0
        wins = sum(1 for r in records if r.get("outcome") == "win")
        avg_pnl = sum(r.get("pnl", 0) for r in records) / len(records)
        return wins / len(records) * 100, avg_pnl, len(records)

    high_wr, high_pnl, high_n = win_rate(high_score)
    low_wr, low_pnl, low_n = win_rate(low_score)
    overall_wr, overall_pnl, overall_n = win_rate(matched)

    win_rate_lift = high_wr - low_wr
    pnl_lift = high_pnl - low_pnl

    is_predictive = win_rate_lift >= 5.0  # High score days beat low score by 5%+

    return {
        "high_score_win_rate": round(high_wr, 1),
        "high_score_avg_pnl": round(high_pnl, 2),
        "high_score_n": high_n,
        "low_score_win_rate": round(low_wr, 1),
        "low_score_avg_pnl": round(low_pnl, 2),
        "low_score_n": low_n,
        "overall_win_rate": round(overall_wr, 1),
        "overall_avg_pnl": round(overall_pnl, 2),
        "overall_n": overall_n,
        "win_rate_lift": round(win_rate_lift, 1),
        "pnl_lift": round(pnl_lift, 2),
        "is_predictive": is_predictive,
        "verdict": (
            f"Context score IS predictive: +{win_rate_lift:.1f}pp win rate on high-score days"
            if is_predictive else
            f"Context score NOT YET predictive: only {win_rate_lift:.1f}pp difference"
            " — need more data or weight adjustment"
        ),
    }


def analyze_per_signal_correlation(matched: list) -> dict:
    """
    For each of the 5 signals, does its individual score correlate with outcomes?
    Returns per-signal predictive power.
    """
    signals = ["vix", "event", "macro", "sentiment", "flow"]
    signal_analysis = {}

    for signal in signals:
        # Extract this signal's score for each matched trade
        signal_data = []
        for m in matched:
            components = m.get("component_scores", {})
            comp = components.get(signal, {})
            pts = comp.get("score", 0)
            max_pts = comp.get("max", 1)
            signal_pct = pts / max_pts * 100 if max_pts > 0 else 50
            signal_data.append({
                "signal_pct": signal_pct,
                "outcome": m.get("outcome", "unknown"),
                "pnl": m.get("pnl", 0),
            })

        if len(signal_data) < MIN_TRADES_FOR_ANALYSIS:
            signal_analysis[signal] = {
                "predictive": None,
                "reason": "Insufficient data",
                "n": len(signal_data),
            }
            continue

        # Split: high signal score (≥70% of max) vs low (<70%)
        high = [s for s in signal_data if s["signal_pct"] >= 70]
        low = [s for s in signal_data if s["signal_pct"] < 70]

        def wr(records):
            if not records:
                return 0
            return sum(1 for r in records if r["outcome"] == "win") / len(records) * 100

        high_wr = wr(high)
        low_wr = wr(low)
        lift = high_wr - low_wr
        predictive = lift >= 5.0

        signal_analysis[signal] = {
            "predictive": predictive,
            "high_score_win_rate": round(high_wr, 1),
            "low_score_win_rate": round(low_wr, 1),
            "win_rate_lift": round(lift, 1),
            "high_n": len(high),
            "low_n": len(low),
            "verdict": f"+{lift:.1f}pp lift" if predictive else f"Only {lift:.1f}pp lift — weak signal",
        }

    return signal_analysis


# ─────────────────────────────────────────────────────────────────────────────
# WEIGHT TUNING
# ─────────────────────────────────────────────────────────────────────────────

def propose_weight_adjustments(
    signal_analysis: dict,
    current_weights: dict,
    weeks_of_data: int,
) -> dict:
    """
    Propose weight adjustments based on signal predictiveness.
    Only tunes after MIN_WEEKS_FOR_TUNING weeks of data.

    Rules:
    - Signals with lift ≥ 10pp: increase weight by 5 (up to max 35)
    - Signals with lift 5-10pp: keep weight
    - Signals with lift < 5pp: decrease weight by 5 (min 5)
    - None predictive yet: keep all weights, flag for review
    - Always normalize so weights sum to 100
    """
    if weeks_of_data < MIN_WEEKS_FOR_TUNING:
        return {
            "proposed": current_weights,
            "changed": False,
            "reason": f"Only {weeks_of_data} week(s) of data — need {MIN_WEEKS_FOR_TUNING} before tuning",
        }

    predictive_signals = {k: v for k, v in signal_analysis.items() if v.get("predictive") is True}
    if not predictive_signals:
        return {
            "proposed": current_weights,
            "changed": False,
            "reason": "No signals are clearly predictive yet — keeping current weights",
        }

    new_weights = current_weights.copy()
    changes = []

    for signal, analysis in signal_analysis.items():
        if analysis.get("predictive") is None:
            continue  # Insufficient data

        lift = analysis.get("win_rate_lift", 0)
        current = current_weights.get(signal, 20)

        if lift >= 10.0:
            new_val = min(current + 5, 35)
            if new_val != current:
                changes.append(f"{signal}: {current} → {new_val} (lift={lift:.1f}pp)")
                new_weights[signal] = new_val
        elif lift < 5.0 and analysis.get("predictive") is False:
            new_val = max(current - 5, 5)
            if new_val != current:
                changes.append(f"{signal}: {current} → {new_val} (weak lift={lift:.1f}pp)")
                new_weights[signal] = new_val

    # Normalize to sum to 100
    total = sum(new_weights.values())
    if total != 100:
        scale = 100 / total
        new_weights = {k: round(v * scale) for k, v in new_weights.items()}
        # Fix rounding to ensure exact 100
        diff = 100 - sum(new_weights.values())
        if diff != 0:
            largest = max(new_weights, key=new_weights.get)
            new_weights[largest] += diff

    changed = new_weights != current_weights

    return {
        "proposed": new_weights,
        "changed": changed,
        "changes": changes,
        "reason": (
            f"Adjusted {len(changes)} signal weight(s) based on {weeks_of_data} weeks of data"
            if changed else "No weight changes needed"
        ),
    }


# ─────────────────────────────────────────────────────────────────────────────
# FULL ANALYSIS — called Friday EOD
# ─────────────────────────────────────────────────────────────────────────────

def run_correlation_analysis(days: int = 28) -> dict:
    """
    Full correlation analysis. Called by scheduler every Friday EOD.
    Returns complete report including weight adjustment proposals.
    """
    log.info("=== Running correlation analysis ===")

    # Load data
    scores = load_context_scores(days=days)
    agent1_outcomes = load_trade_outcomes("agent1_iron_condor", days=days)
    agent2_outcomes = load_trade_outcomes("agent2_covered_call", days=days)
    all_outcomes = agent1_outcomes + agent2_outcomes

    weeks_of_data = max(1, days // 7)

    if len(all_outcomes) < MIN_TRADES_FOR_ANALYSIS:
        result = {
            "status": "insufficient_data",
            "message": f"Only {len(all_outcomes)} trades — need {MIN_TRADES_FOR_ANALYSIS}+ for correlation analysis",
            "trades_total": len(all_outcomes),
            "context_scores_loaded": len(scores),
            "weeks_of_data": weeks_of_data,
        }
        log.info(result["message"])
        return result

    # Match scores to outcomes
    matched = match_scores_to_outcomes(scores, all_outcomes)
    unmatched = len(all_outcomes) - len(matched)

    if len(matched) < MIN_TRADES_FOR_ANALYSIS:
        result = {
            "status": "insufficient_matched",
            "message": f"Only {len(matched)} trades matched to context scores ({unmatched} unmatched) — need more data",
            "trades_total": len(all_outcomes),
            "matched": len(matched),
            "unmatched": unmatched,
        }
        return result

    # Run analyses
    overall = analyze_overall_correlation(matched)
    per_signal = analyze_per_signal_correlation(matched)
    current_weights = load_weights()
    weight_proposal = propose_weight_adjustments(per_signal, current_weights, weeks_of_data)

    # Apply weight changes if proposed
    weights_updated = False
    if weight_proposal.get("changed"):
        save_weights(weight_proposal["proposed"])
        weights_updated = True
        log.info(f"Context weights updated: {weight_proposal['changes']}")

    result = {
        "status": "complete",
        "date": date.today().isoformat(),
        "weeks_of_data": weeks_of_data,
        "trades_analyzed": len(matched),
        "overall_correlation": overall,
        "per_signal_analysis": per_signal,
        "weight_proposal": weight_proposal,
        "weights_updated": weights_updated,
        "current_weights": current_weights,
        "new_weights": weight_proposal["proposed"] if weights_updated else current_weights,
        "summary": _build_summary(overall, per_signal, weight_proposal, weeks_of_data),
    }

    # Save result
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(RESULTS_DIR / f"correlation_{date.today().isoformat()}.json", "w") as f:
        json.dump(result, f, indent=2, default=str)

    log.info(
        f"Correlation analysis: {overall['verdict']} | "
        f"Weights updated: {weights_updated}"
    )
    return result


def _build_summary(overall, per_signal, weight_proposal, weeks) -> str:
    lines = [
        f"**Overall:** {overall['verdict']}",
        f"**Win rates:** High-score days {overall['high_score_win_rate']}% vs Low-score {overall['low_score_win_rate']}% (+{overall['win_rate_lift']}pp)",
        "",
        "**Signal performance:**",
    ]
    for signal, analysis in per_signal.items():
        if analysis.get("predictive") is None:
            lines.append(f"• {signal}: insufficient data")
        else:
            emoji = "✅" if analysis["predictive"] else "⚠️"
            lines.append(f"• {signal}: {emoji} {analysis['verdict']}")

    if weight_proposal.get("changed"):
        lines.append("")
        lines.append(f"**Weights adjusted:** {', '.join(weight_proposal['changes'])}")
    elif weeks < MIN_WEEKS_FOR_TUNING:
        lines.append(f"\n_Need {MIN_WEEKS_FOR_TUNING - weeks} more week(s) before weight tuning_")

    return "\n".join(lines)


def build_correlation_embed(result: dict) -> dict:
    """Build Discord embed for Friday correlation report."""
    status = result.get("status", "unknown")
    if status == "insufficient_data":
        return {
            "title": "📊 Weekly Correlation Analysis",
            "description": result.get("message", ""),
            "color": 0x95A5A6,
            "footer": {"text": "QuantAI Correlation Analyzer · More data needed"},
        }

    overall = result.get("overall_correlation", {})
    weight_proposal = result.get("weight_proposal", {})
    is_predictive = overall.get("is_predictive", False)
    color = 0x2ECC71 if is_predictive else 0xF39C12

    fields = [
        {"name": "Trades Analyzed", "value": str(result.get("trades_analyzed", 0)), "inline": True},
        {"name": "Weeks of Data", "value": str(result.get("weeks_of_data", 0)), "inline": True},
        {"name": "Weights Updated", "value": "✅ Yes" if result.get("weights_updated") else "No change", "inline": True},
        {"name": "Win Rate Lift", "value": f"{overall.get('win_rate_lift', 0):+.1f}pp (high vs low score)", "inline": False},
    ]

    per_signal = result.get("per_signal_analysis", {})
    signal_lines = []
    for sig, analysis in per_signal.items():
        if analysis.get("predictive") is True:
            signal_lines.append(f"✅ {sig}: +{analysis['win_rate_lift']:.1f}pp")
        elif analysis.get("predictive") is False:
            signal_lines.append(f"⚠️ {sig}: {analysis['win_rate_lift']:.1f}pp (weak)")
        else:
            signal_lines.append(f"❓ {sig}: data pending")

    if signal_lines:
        fields.append({"name": "Signal Accuracy", "value": "\n".join(signal_lines), "inline": False})

    if weight_proposal.get("changes"):
        fields.append({
            "name": "Weight Changes",
            "value": "\n".join(f"• {c}" for c in weight_proposal["changes"]),
            "inline": False,
        })

    return {
        "title": f"📊 Correlation Analysis — {'Predictive ✅' if is_predictive else 'Building Data ⏳'}",
        "description": overall.get("verdict", ""),
        "color": color,
        "fields": fields,
        "footer": {"text": "QuantAI Correlation Analyzer · Runs every Friday"},
        "timestamp": datetime.now().isoformat(),
    }


# CLI test
if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO)
    result = run_correlation_analysis(days=28)
    print(json.dumps(result, indent=2, default=str))
