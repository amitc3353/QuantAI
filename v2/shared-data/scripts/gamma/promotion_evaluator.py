"""Promotion-decision evaluator for the Gamma 4-arm A/B/C/D test.

Pure deterministic function: given per-arm state files + journals + an
evaluation date, returns the same decision every time. No randomness, no
time-dependent quirks beyond the supplied date. This determinism is
critical — the evaluator is the rule book the experiment commits to
BEFORE the test starts; any drift would amount to moving the goalposts.

Win criteria from docs/gamma-four-arm-ab-test-plan.md §F (committed
pre-test, not negotiable post-hoc):

1. **Sample size floor** — every arm must have ≥ 80 closed trades.
   Any arm short → `extend` (extension granted in 30-day chunks).
2. **Win margin** — best arm beats runner-up by ≥ 15% in total
   realized P&L AND has equal-or-better Sharpe → `promote <best>`.
3. **Near-tie fallback** — best two arms within 5% of each other on
   P&L → `promote <simpler>` (Ockham order: A > D > B > C).
4. **Inconclusive band** — 5% < margin < 15% → `extend`.
5. **180-day hard cap** — past day 180 with no resolution →
   `hard_cap_default` (Arm A wins by default; status quo preserved).

The 180-day hard cap is the ONLY exception to "any arm < 80 →
extend": if we've already extended to day 180 and still don't have
80 trades per arm, we declare the test inconclusive and ship Arm A.

Sharpe ratio:

* Per arm, on closed trades only.
* Daily return series = ``daily_realized_pnl / equity_at_start_of_day``.
* Annualized = ``(mean × 252) / (stdev × sqrt(252))`` with risk-free 0.
* Edge case: < 5 distinct trading days of closed-trade returns →
  Sharpe is None and the evaluator falls back to ``extend``.

Simplicity order for near-tie fallback:

* A (RSI_ONLY) — control, simplest single factor
* D (REWARD_RISK_FIRST) — single factor, same simplicity tier as A
* B (COMPOSITE) — 4-factor weighted blend
* C (WEIGHTED_BLEND) — 4-factor blend + ensemble layer (most complex)

When two arms tie within 5%, the EARLIER arm in this list wins.
"""
from __future__ import annotations

import json
import statistics
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional


# ── Constants ─────────────────────────────────────────────────────────


SAMPLE_SIZE_FLOOR = 80              # min closed trades per arm
WIN_MARGIN_PCT = 0.15               # 15% P&L gap for clear winner
NEAR_TIE_PCT = 0.05                 # 5% gap → fall back to simpler arm
HARD_CAP_DAYS = 180                 # absolute deadline for the experiment
SHARPE_MIN_TRADING_DAYS = 5         # need at least 5 distinct days of returns
ANNUALIZATION_FACTOR = 252          # trading days per year

# Simpler-arm precedence for the near-tie fallback rule (Ockham's razor).
# Earlier in this list = simpler = wins ties.
SIMPLICITY_ORDER: list[str] = ["a", "d", "b", "c"]

VALID_ARM_IDS: tuple[str, ...] = ("a", "b", "c", "d")


# ── Sharpe computation ────────────────────────────────────────────────


def compute_arm_sharpe(arm_journal: list[dict],
                        starting_equity: float) -> tuple[Optional[float], int]:
    """Annualized Sharpe ratio for one arm. Returns (sharpe, n_distinct_days).

    Implementation: group closed trades' realized P&L by close_date,
    compute per-day returns relative to equity_at_start_of_day (running
    cumulative pnl + starting_equity), annualize.

    Returns (None, n) when:
    * Fewer than SHARPE_MIN_TRADING_DAYS distinct close-days exist
    * All returns identical (stdev = 0) — Sharpe undefined
    """
    closed = []
    for t in arm_journal:
        if (t.get("status") or "").upper() != "CLOSED":
            continue
        ts = t.get("close_timestamp") or t.get("timestamp")
        if not ts:
            continue
        try:
            d = datetime.fromisoformat(ts).date().isoformat()
            pnl = float(t.get("pnl") or t.get("realized_pnl") or 0)
        except (ValueError, TypeError):
            continue
        closed.append((d, pnl))

    if not closed:
        return None, 0

    # Group by date, sum P&L per day
    daily_pnl: dict[str, float] = {}
    for d, pnl in closed:
        daily_pnl[d] = daily_pnl.get(d, 0.0) + pnl

    n_days = len(daily_pnl)
    if n_days < SHARPE_MIN_TRADING_DAYS:
        return None, n_days

    # Walk dates in order, computing equity_at_start_of_day = starting + cumulative_pnl_before_today
    sorted_dates = sorted(daily_pnl.keys())
    cumulative_before = 0.0
    daily_returns: list[float] = []
    for d in sorted_dates:
        equity_start = starting_equity + cumulative_before
        if equity_start <= 0:
            equity_start = starting_equity  # avoid divide-by-zero
        daily_returns.append(daily_pnl[d] / equity_start)
        cumulative_before += daily_pnl[d]

    if len(daily_returns) < 2:
        return None, n_days

    mean_r = statistics.mean(daily_returns)
    stdev_r = statistics.stdev(daily_returns)
    if stdev_r == 0:
        return None, n_days

    # Annualized Sharpe (rf = 0)
    sharpe = (mean_r * ANNUALIZATION_FACTOR) / (
        stdev_r * (ANNUALIZATION_FACTOR ** 0.5)
    )
    return round(sharpe, 4), n_days


# ── Per-arm metrics ───────────────────────────────────────────────────


def _arm_metrics(arm_id: str, state: dict, journal: list[dict]) -> dict:
    """Bundle all per-arm metrics the evaluator and reports need."""
    closed_trades = [
        t for t in journal
        if (t.get("status") or "").upper() == "CLOSED"
    ]
    pnl_sum = sum(float(t.get("pnl") or t.get("realized_pnl") or 0)
                   for t in closed_trades)
    sharpe, n_days = compute_arm_sharpe(
        journal, float(state.get("starting_equity") or 10000.0)
    )
    return {
        "arm_id": arm_id,
        "ranker_used": state.get("ranker_used", ""),
        "trade_count": len(closed_trades),
        "starting_equity": float(state.get("starting_equity") or 0),
        "current_equity": float(state.get("current_equity") or 0),
        "total_realized_pnl": round(pnl_sum, 2),
        "win_rate": _win_rate(closed_trades),
        "sharpe": sharpe,
        "sharpe_n_days": n_days,
        "circuit_breaker_active": bool(state.get("circuit_breaker_active") or False),
    }


def _win_rate(closed_trades: list[dict]) -> Optional[float]:
    if not closed_trades:
        return None
    wins = sum(
        1 for t in closed_trades
        if float(t.get("pnl") or t.get("realized_pnl") or 0) > 0
    )
    return round(wins / len(closed_trades), 4)


# ── Decision helpers ──────────────────────────────────────────────────


def _best_and_runner_up_by_pnl(metrics: dict[str, dict]) -> tuple[str, str]:
    """Return (best_arm, runner_up_arm) by total_realized_pnl descending.
    Tiebreaks alphabetically by arm_id for full determinism."""
    sorted_arms = sorted(
        VALID_ARM_IDS,
        key=lambda a: (-metrics[a]["total_realized_pnl"], a),
    )
    return sorted_arms[0], sorted_arms[1]


def _margin_pct(best_pnl: float, runner_up_pnl: float) -> float:
    """(best - runner_up) / |runner_up|. Returns inf if runner_up == 0
    and best > 0; -inf if both 0; sign reflects winner direction."""
    if runner_up_pnl == 0:
        if best_pnl == 0:
            return 0.0
        return float("inf") if best_pnl > 0 else float("-inf")
    return (best_pnl - runner_up_pnl) / abs(runner_up_pnl)


def _simpler_arm(arm1: str, arm2: str) -> str:
    """Earlier in SIMPLICITY_ORDER wins. Used for near-tie fallback."""
    return arm1 if SIMPLICITY_ORDER.index(arm1) < SIMPLICITY_ORDER.index(arm2) else arm2


# ── Main evaluator ────────────────────────────────────────────────────


def evaluate_promotion(arm_states: dict[str, dict],
                       arm_journals: dict[str, list[dict]],
                       experiment_day: int) -> dict:
    """Pure deterministic promotion decision.

    Args:
      arm_states: {arm_id: state_dict} for all 4 arms (a/b/c/d)
      arm_journals: {arm_id: list of trade entries} for all 4 arms
      experiment_day: integer day count from experiment_started_at

    Returns:
      Decision dict with keys:
        - decision: one of "extend" | "promote" | "hard_cap_default"
        - winner: arm_id ("a"/"b"/"c"/"d") or None
        - reason: human-readable explanation
        - rule_applied: which rule fired (sample_floor / win_margin /
          near_tie / inconclusive_band / hard_cap)
        - metrics: per-arm summary + best/runner_up + margin
        - experiment_day
    """
    # Validate inputs
    for aid in VALID_ARM_IDS:
        if aid not in arm_states:
            raise ValueError(f"missing state for arm {aid}")
        if aid not in arm_journals:
            raise ValueError(f"missing journal for arm {aid}")

    # 1. Compute per-arm metrics
    metrics = {
        aid: _arm_metrics(aid, arm_states[aid], arm_journals[aid])
        for aid in VALID_ARM_IDS
    }

    # 2. Sample size floor — any arm < 80 → extend (UNLESS hard cap)
    short_arms = [
        aid for aid in VALID_ARM_IDS
        if metrics[aid]["trade_count"] < SAMPLE_SIZE_FLOOR
    ]
    sample_size_short = len(short_arms) > 0

    # 3. Best/runner-up by P&L
    best, runner_up = _best_and_runner_up_by_pnl(metrics)
    margin = _margin_pct(
        metrics[best]["total_realized_pnl"],
        metrics[runner_up]["total_realized_pnl"],
    )

    # 4. Sharpe gate — both best and runner-up need defined Sharpe to compare
    best_sharpe = metrics[best]["sharpe"]
    runner_sharpe = metrics[runner_up]["sharpe"]
    sharpe_undefined = best_sharpe is None or runner_sharpe is None

    base_metrics = {
        "experiment_day": experiment_day,
        "trade_counts": {a: metrics[a]["trade_count"] for a in VALID_ARM_IDS},
        "pnl": {a: metrics[a]["total_realized_pnl"] for a in VALID_ARM_IDS},
        "win_rate": {a: metrics[a]["win_rate"] for a in VALID_ARM_IDS},
        "sharpe": {a: metrics[a]["sharpe"] for a in VALID_ARM_IDS},
        "sharpe_n_days": {a: metrics[a]["sharpe_n_days"] for a in VALID_ARM_IDS},
        "best": best,
        "runner_up": runner_up,
        "best_pnl": metrics[best]["total_realized_pnl"],
        "runner_up_pnl": metrics[runner_up]["total_realized_pnl"],
        "margin_pct": round(margin, 4) if margin not in (float("inf"), float("-inf")) else margin,
        "short_arms": short_arms,
        "per_arm": metrics,
    }

    # ── Determine the would-be decision (without hard-cap override) ──
    if sample_size_short:
        would_be = {
            "decision": "extend",
            "winner": None,
            "rule_applied": "sample_floor",
            "reason": (
                f"sample size floor: arm(s) {short_arms} have "
                f"< {SAMPLE_SIZE_FLOOR} closed trades "
                f"(counts: {base_metrics['trade_counts']})"
            ),
        }
    elif sharpe_undefined:
        would_be = {
            "decision": "extend",
            "winner": None,
            "rule_applied": "sharpe_undefined",
            "reason": (
                f"Sharpe undefined for at least one of best/runner_up "
                f"(arm {best}={best_sharpe}, arm {runner_up}={runner_sharpe}); "
                f"need ≥ {SHARPE_MIN_TRADING_DAYS} distinct close-days"
            ),
        }
    elif margin >= WIN_MARGIN_PCT and best_sharpe >= runner_sharpe:
        would_be = {
            "decision": "promote",
            "winner": best,
            "rule_applied": "win_margin",
            "reason": (
                f"arm {best} P&L ${metrics[best]['total_realized_pnl']:.2f} beats "
                f"arm {runner_up} ${metrics[runner_up]['total_realized_pnl']:.2f} "
                f"by {margin * 100:.1f}% (≥ {WIN_MARGIN_PCT * 100:.0f}%) AND "
                f"Sharpe {best_sharpe:.2f} ≥ {runner_sharpe:.2f}"
            ),
        }
    elif margin >= WIN_MARGIN_PCT and best_sharpe < runner_sharpe:
        # Top arm by P&L lost the Sharpe gate — extend (don't fall through to near-tie)
        would_be = {
            "decision": "extend",
            "winner": None,
            "rule_applied": "sharpe_gate",
            "reason": (
                f"arm {best} leads on P&L by {margin * 100:.1f}% but its "
                f"Sharpe {best_sharpe:.2f} < arm {runner_up}'s {runner_sharpe:.2f} "
                f"— Sharpe gate enforced, extend"
            ),
        }
    elif abs(margin) <= NEAR_TIE_PCT:
        # Within 5% in either direction (best could be ahead of runner_up by
        # only $5 on $1000 of profit — both essentially tied). Pick simpler.
        winner = _simpler_arm(best, runner_up)
        would_be = {
            "decision": "promote",
            "winner": winner,
            "rule_applied": "near_tie",
            "reason": (
                f"arm {best} and arm {runner_up} within "
                f"{NEAR_TIE_PCT * 100:.0f}% on P&L (margin {margin * 100:.1f}%); "
                f"Ockham's razor → promote simpler arm {winner} "
                f"(simplicity order: {SIMPLICITY_ORDER})"
            ),
        }
    else:
        # 5% < margin < 15% — inconclusive band
        would_be = {
            "decision": "extend",
            "winner": None,
            "rule_applied": "inconclusive_band",
            "reason": (
                f"arm {best} leads by {margin * 100:.1f}% — "
                f"inconclusive band ({NEAR_TIE_PCT * 100:.0f}%–"
                f"{WIN_MARGIN_PCT * 100:.0f}%); extend 30 days"
            ),
        }

    # ── 180-day hard cap override ──
    # If we've already extended to or past day 180 and the would-be decision
    # is still "extend", declare hard-cap default (Arm A wins).
    if experiment_day >= HARD_CAP_DAYS and would_be["decision"] == "extend":
        return {
            "decision": "hard_cap_default",
            "winner": "a",
            "rule_applied": "hard_cap",
            "reason": (
                f"day {experiment_day} ≥ {HARD_CAP_DAYS} hard cap — "
                f"would-be decision was '{would_be['rule_applied']}' but the "
                f"experiment cannot extend further. Default to Arm A "
                f"(status quo: pre-experiment Gamma logic)."
            ),
            "metrics": base_metrics,
            "experiment_day": experiment_day,
            "would_be_decision": would_be,
        }

    return {
        **would_be,
        "metrics": base_metrics,
        "experiment_day": experiment_day,
    }


# ── Format helpers (for CLI output) ───────────────────────────────────


def format_decision_human_readable(decision: dict) -> str:
    """Render a decision dict as a human-readable multi-line string for
    CLI output and Discord."""
    lines = [
        "─" * 60,
        f"Gamma 4-arm Promotion Evaluation — day {decision['experiment_day']}",
        "─" * 60,
    ]
    metrics = decision.get("metrics", {})
    pnl = metrics.get("pnl", {})
    sharpe = metrics.get("sharpe", {})
    counts = metrics.get("trade_counts", {})
    win_rate = metrics.get("win_rate", {})

    for aid in VALID_ARM_IDS:
        ranker = metrics.get("per_arm", {}).get(aid, {}).get("ranker_used", "?")
        s = sharpe.get(aid)
        s_str = f"{s:.2f}" if s is not None else "n/a"
        wr = win_rate.get(aid)
        wr_str = f"{wr * 100:.0f}%" if wr is not None else "n/a"
        marker = ""
        if aid == decision.get("winner"):
            marker = "  ← WINNER"
        elif aid == metrics.get("best"):
            marker = "  ← best (P&L)"
        lines.append(
            f"  Arm {aid.upper()} ({ranker}): "
            f"P&L ${pnl.get(aid, 0):.2f} | "
            f"trades {counts.get(aid, 0)} | "
            f"win {wr_str} | "
            f"Sharpe {s_str}{marker}"
        )

    lines.append("")
    lines.append(f"  Decision: {decision['decision'].upper()}")
    if decision.get("winner"):
        lines.append(f"  Winner: arm {decision['winner']}")
    lines.append(f"  Rule: {decision['rule_applied']}")
    lines.append(f"  Reason: {decision['reason']}")
    return "\n".join(lines)


# ── Divergence rate (auxiliary metric for reports) ────────────────────


def compute_divergence_rate(decisions_log_lines: list[str],
                             window: int = 30) -> Optional[float]:
    """Read the master ranking_decisions.jsonl and compute the fraction of
    recent scans where AT LEAST ONE arm picked a different set than the
    others.

    Args:
      decisions_log_lines: file content split by newlines (or list of
        already-parsed dicts via JSON loading)
      window: number of most-recent entries to consider (default 30)

    Returns:
      Fraction in [0.0, 1.0], or None if no parseable entries.
    """
    if not decisions_log_lines:
        return None

    parsed = []
    for line in decisions_log_lines:
        if isinstance(line, dict):
            parsed.append(line)
            continue
        line_str = str(line).strip()
        if not line_str:
            continue
        try:
            parsed.append(json.loads(line_str))
        except (json.JSONDecodeError, ValueError):
            continue

    recent = parsed[-window:] if len(parsed) > window else parsed
    if not recent:
        return None

    diverged = 0
    for entry in recent:
        picks = entry.get("picked_per_arm", {}) or {}
        if not picks:
            continue
        pick_sets = [
            frozenset(picks.get(a, []) or []) for a in VALID_ARM_IDS
        ]
        # All-agree means every arm produced the same set (could be empty)
        if len(set(pick_sets)) > 1:
            diverged += 1
    return round(diverged / len(recent), 4)
