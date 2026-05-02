"""Shared helpers + constants for trade-entry sizing and decision context.

Used by autonomous_execution.py (Alpha), scan_options.py (Alpha scanner),
debate_chamber.py (Alpha LLM prompt), beta_agent.py (Beta), and gamma_agent.py
(Gamma). The `decision` object captures entry-time thesis, conviction, data
freshness, and pipeline timing — the raw material for agent_self_diagnosis.py.

Fields default to None / 0 when source data isn't available; the diagnosis layer
treats missing freshness data as itself a capability gap (correct behavior).
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo


_ET = ZoneInfo("America/New_York")


# ── Position sizing cap ──────────────────────────────────────────────────────
# All agents size positions against this cap, NOT against real broker equity.
# IBKR paper account holds ~$1M; sizing against that produces trades 20× too
# big for the strategies' design parameters. This cap brings actual sizing in
# line with each agent's mandate:
#   Alpha — 2% × $50k = $1,000 max loss per trade
#   Beta  — 1% × $50k = $500   max loss per trade
#   Gamma — 1% × $50k = $500   max loss per trade
#
# Real broker equity is still used for:
#   - Dashboard / monitoring (collect_alpaca, collect_beta, etc.)
#   - Risk-gate drawdown halts in beta/risk_engine.py (real losses)
#   - pre_trade_check.py display
# Only the qty-calculation path uses this cap.
AGENT_ACCOUNT_CAP = 50_000


def effective_equity(broker_equity: float | int | None) -> float:
    """Return min(broker_equity, AGENT_ACCOUNT_CAP), floored at 0.

    Treats None or non-positive input as 0. Use ONLY for position sizing
    calculations — dashboards and drawdown gates should use real equity.
    """
    try:
        eq = float(broker_equity or 0)
    except (TypeError, ValueError):
        return 0.0
    if eq <= 0:
        return 0.0
    return min(eq, AGENT_ACCOUNT_CAP)


def age_of(timestamp_iso: str | None) -> int:
    """Return age in seconds of an ISO8601 timestamp. 0 if missing/unparseable."""
    if not timestamp_iso:
        return 0
    try:
        ts = datetime.fromisoformat(str(timestamp_iso).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=_ET)
        now = datetime.now(timezone.utc)
        delta = (now - ts).total_seconds()
        return max(0, int(delta))
    except Exception:
        return 0


def rsi_depth_score(rsi_value: float | None) -> int:
    """Map RSI(10) reading to a 1-10 conviction score.

    Connors threshold is 30. Deeper oversold = higher conviction (within reason).
    """
    if rsi_value is None:
        return 5
    try:
        r = float(rsi_value)
    except (TypeError, ValueError):
        return 5
    if r >= 30:
        return 4
    if r >= 25:
        return 6
    if r >= 20:
        return 7
    if r >= 15:
        return 8
    return 9


def signal_strength_score(strikes: dict | None, regime: str | None = None) -> int:
    """Map Beta strike-selection comfort to a 1-10 conviction score.

    Heuristic: how many strike-selection flags are unambiguously favorable
    (delta in target range, R:R >= 3, IV consistent with regime). When the
    strategy module returns rich detail this can become more accurate; for
    now we bucket by R:R as the most reliable proxy.
    """
    if not strikes:
        return 5
    rr = strikes.get("reward_to_risk") or strikes.get("rr") or 0
    try:
        rr = float(rr)
    except (TypeError, ValueError):
        rr = 0
    if rr >= 5.0:
        return 9
    if rr >= 4.0:
        return 8
    if rr >= 3.0:
        return 7
    if rr >= 2.0:
        return 5
    if rr >= 1.5:
        return 4
    return 3


def alpha_conviction_from_judge(judge_score: int | float | None) -> int:
    """Map debate-chamber judge score (0-100) to a 1-10 conviction score."""
    if judge_score is None:
        return 5
    try:
        s = float(judge_score)
    except (TypeError, ValueError):
        return 5
    return max(1, min(10, int(round(s / 10))))
