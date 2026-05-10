#!/usr/bin/env python3
"""Friday 4:30 PM ET digest for the Gamma 4-arm A/B/C/D test.

Posts a single Discord summary with per-arm equity / trades / win rate /
Sharpe + cumulative leaderboard + trade overlap stats + notable
divergences over the past trading week + sample-size projection.

Per docs/gamma-four-arm-ab-test-plan.md §H. Cron entry will be added in
commit 5 along with the feature flag flip; for commit 4 this file ships
ready-to-use, callable via:

    python3 gamma_weekly_digest.py             # post to Discord
    python3 gamma_weekly_digest.py --dry-run   # print only, no Discord
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# Auto-load .env (mirrors gamma_agent pattern)
for _ef in [Path("/home/trader/QuantAI/.env"), Path("/root/quantai-v2/.env")]:
    if _ef.exists():
        for _line in _ef.read_text().splitlines():
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.partition("=")
                if not os.environ.get(_k.strip()):
                    os.environ[_k.strip()] = _v.strip()
        break

ET = ZoneInfo("America/New_York")
DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
DISCORD_LOGS_CH = os.environ.get("DISCORD_CHANNEL_LOGS", "")
DISCORD_ALERTS_CH = os.environ.get("DISCORD_CHANNEL_ALERTS", "")

ARM_CACHE_DIR = Path("/root/quantai-v2/shared-data/cache")
ARM_JOURNAL_DIR = Path("/root/quantai-v2/shared-data/journal/paper")
RANKING_DECISIONS_PATH = Path("/root/quantai-v2/shared-data/logs/gamma_ranking_decisions.jsonl")

VALID_ARM_IDS = ("a", "b", "c", "d")
ARM_RANKER_LABELS = {
    "a": "RSI_ONLY", "b": "COMPOSITE",
    "c": "WEIGHTED_BLEND", "d": "REWARD_RISK_FIRST",
}

DRY_RUN = "--dry-run" in sys.argv


# ── Helpers ────────────────────────────────────────────────────────────


def _post_discord(msg: str, channel: str | None = None) -> None:
    """Post to Discord. DRY_RUN prints to stdout only."""
    chan = channel or DISCORD_LOGS_CH or DISCORD_ALERTS_CH
    if DRY_RUN or not DISCORD_BOT_TOKEN or not chan:
        print(msg)
        return
    try:
        import urllib.request
        req = urllib.request.Request(
            f"https://discord.com/api/v10/channels/{chan}/messages",
            data=json.dumps({"content": msg[:1900]}).encode(),
            headers={
                "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10).read()
    except Exception as e:
        print(f"[gamma_weekly_digest] discord post failed: {e}")
        print(msg)


def _load_arm_state(arm_id: str) -> dict | None:
    path = ARM_CACHE_DIR / f"gamma_arm_{arm_id}_account.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _load_arm_journal(arm_id: str) -> list[dict]:
    path = ARM_JOURNAL_DIR / f"gamma_arm_{arm_id}_trades.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def _is_arm_gamma(t: dict, arm_id: str) -> bool:
    return t.get("arm_id") == arm_id and t.get("source") == f"agent_gamma_arm_{arm_id}"


def _experiment_day(state: dict | None) -> int:
    if not state or not state.get("experiment_started_at"):
        return 0
    try:
        started = datetime.fromisoformat(state["experiment_started_at"])
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        return max(0, (datetime.now(timezone.utc) - started).days)
    except Exception:
        return 0


def _week_window() -> tuple[datetime, datetime]:
    """Last 7 calendar days ending now, in ET."""
    now = datetime.now(ET)
    return now - timedelta(days=7), now


def _read_decisions_window(start: datetime, end: datetime) -> list[dict]:
    """Read ranking_decisions.jsonl entries within the [start, end] window."""
    if not RANKING_DECISIONS_PATH.exists():
        return []
    out = []
    for line in RANKING_DECISIONS_PATH.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except Exception:
            continue
        ts_str = entry.get("scan_timestamp")
        if not ts_str:
            continue
        try:
            ts = datetime.fromisoformat(ts_str)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=ET)
        except Exception:
            continue
        if start <= ts <= end:
            out.append(entry)
    return out


def _trade_overlap_stats(decisions: list[dict]) -> dict:
    """Across the week's scans, how often did all 4 arms agree vs diverge?"""
    if not decisions:
        return {"total_scans": 0, "all_agree": 0, "diverged": 0,
                "all_agree_pct": None, "diverged_pct": None}
    all_agree = 0
    diverged = 0
    for entry in decisions:
        picks = entry.get("picked_per_arm") or {}
        if not picks:
            continue
        sets = [frozenset(picks.get(a, []) or []) for a in VALID_ARM_IDS]
        if len(set(sets)) == 1:
            all_agree += 1
        else:
            diverged += 1
    total = all_agree + diverged
    return {
        "total_scans": total,
        "all_agree": all_agree,
        "diverged": diverged,
        "all_agree_pct": round(all_agree / total * 100, 0) if total else None,
        "diverged_pct": round(diverged / total * 100, 0) if total else None,
    }


def _notable_divergences(decisions: list[dict], journals: dict) -> list[str]:
    """Symbols this week that ONLY 1 arm picked (or only 2 of 4) — high-info."""
    notable: list[str] = []
    for entry in decisions[-7:]:  # last 7 scans
        picks = entry.get("picked_per_arm") or {}
        if not picks:
            continue
        sym_to_arms: dict[str, list[str]] = {}
        for aid in VALID_ARM_IDS:
            for sym in picks.get(aid, []) or []:
                sym_to_arms.setdefault(sym, []).append(aid)
        for sym, arms in sym_to_arms.items():
            if 1 <= len(arms) <= 2:
                # Look up the trade outcome (if any) in the journals
                pnl_per_arm: dict[str, float | None] = {}
                for aid in arms:
                    matches = [t for t in journals.get(aid, [])
                                if t.get("symbol") == sym
                                and t.get("status") == "CLOSED"]
                    if matches:
                        # Take most recent
                        m = max(matches, key=lambda t: t.get("close_timestamp", ""))
                        pnl_per_arm[aid] = float(m.get("pnl") or 0)
                    else:
                        pnl_per_arm[aid] = None
                arms_label = "/".join(a.upper() for a in arms)
                pnl_summary = ", ".join(
                    f"{a.upper()}: ${pnl:+.0f}" if pnl is not None
                    else f"{a.upper()}: open"
                    for a, pnl in pnl_per_arm.items()
                )
                notable.append(f"{sym} ({arms_label}): {pnl_summary}")
    return notable[:5]  # top 5


def _projection(states: dict) -> dict:
    """Estimate when all arms will hit 80 trades at current pace."""
    from gamma.promotion_evaluator import SAMPLE_SIZE_FLOOR
    min_trades = min(int(s.get("total_trades") or 0) for s in states.values())
    if min_trades >= SAMPLE_SIZE_FLOOR:
        return {"on_track": True, "min_trades": min_trades,
                "projected_day_to_floor": None}
    # Per-arm trade rate = trades/experiment_day
    # Use the SLOWEST arm to project days to floor
    state_a = states.get("a") or {}
    exp_day = _experiment_day(state_a)
    if exp_day <= 0:
        return {"on_track": False, "min_trades": min_trades,
                "projected_day_to_floor": None,
                "reason": "experiment not yet day 1"}
    rate_per_day = min_trades / exp_day  # slowest arm's rate
    if rate_per_day <= 0:
        return {"on_track": False, "min_trades": min_trades,
                "projected_day_to_floor": None,
                "reason": "no trades yet"}
    days_to_floor = int((SAMPLE_SIZE_FLOOR - min_trades) / rate_per_day) + 1
    projected_day = exp_day + days_to_floor
    return {
        "on_track": projected_day <= 60,
        "min_trades": min_trades,
        "projected_day_to_floor": projected_day,
        "rate_per_day": round(rate_per_day, 2),
    }


# ── Main digest builder ───────────────────────────────────────────────


def build_digest_lines(arm_states: dict, arm_journals: dict,
                       decisions_this_week: list[dict],
                       overlap: dict, projection: dict,
                       notable: list[str]) -> list[str]:
    """Build the digest as a list of lines. Pure function — no I/O.
    Tested in test_gamma_promotion_logic.py::TestWeeklyDigestFormat."""
    state_a = arm_states.get("a") or {}
    exp_day = _experiment_day(state_a)
    # Week 1 = days 1–7, week 2 = days 8–14, etc. Day 28 → week 4.
    week_num = max(1, (exp_day - 1) // 7 + 1) if exp_day else 0

    lines = [
        f"📊 Gamma A/B/C/D Test — Week {week_num} (day {exp_day})",
        "─" * 50,
    ]

    # Per-arm row
    # Rank arms by current_equity descending for the leaderboard line
    arms_by_pnl = sorted(
        VALID_ARM_IDS,
        key=lambda a: -float((arm_states.get(a) or {}).get("total_realized_pnl") or 0),
    )

    try:
        sys.path.insert(0, "/home/trader/QuantAI/v2/shared-data/scripts")
        from gamma.promotion_evaluator import compute_arm_sharpe
    except Exception:
        compute_arm_sharpe = None

    for aid in VALID_ARM_IDS:
        state = arm_states.get(aid) or {}
        eq = float(state.get("current_equity") or 10000)
        trades = int(state.get("total_trades") or 0)
        wins = int(state.get("winning_trades") or 0)
        win_rate = (wins / trades * 100) if trades > 0 else None
        wr_str = f"{win_rate:.0f}%" if win_rate is not None else "n/a"
        cb = "🛑 CB" if state.get("circuit_breaker_active") else ""
        sharpe_str = "n/a"
        if compute_arm_sharpe:
            starting = float(state.get("starting_equity") or 10000)
            sharpe, _ = compute_arm_sharpe(arm_journals.get(aid, []), starting)
            if sharpe is not None:
                sharpe_str = f"{sharpe:.2f}"
        lines.append(
            f"  Arm {aid.upper()} ({ARM_RANKER_LABELS[aid]}):"
            f" ${eq:,.0f} | {trades} trades | win {wr_str} |"
            f" Sharpe {sharpe_str} {cb}".rstrip()
        )

    # Leaderboard
    leaderboard = " > ".join(
        f"{a.upper()} ${(arm_states.get(a) or {}).get('total_realized_pnl', 0):+.0f}"
        for a in arms_by_pnl
    )
    lines.append("")
    lines.append(f"Cumulative P&L lead: {leaderboard}")

    # Overlap / divergence
    lines.append("")
    if overlap["total_scans"] > 0:
        lines.append(
            f"Trade overlap (week): {overlap['all_agree']}/{overlap['total_scans']} all-agree "
            f"({overlap['all_agree_pct']:.0f}%), "
            f"{overlap['diverged']}/{overlap['total_scans']} diverged "
            f"({overlap['diverged_pct']:.0f}%)"
        )
    else:
        lines.append("Trade overlap: no scans logged this week")

    # Notable divergences
    if notable:
        lines.append("")
        lines.append("Notable divergences this week:")
        for n in notable:
            lines.append(f"  • {n}")

    # Sample size projection
    lines.append("")
    if projection.get("projected_day_to_floor") is not None:
        rate = projection.get("rate_per_day", 0)
        proj_day = projection["projected_day_to_floor"]
        lines.append(
            f"Sample size projection: at {rate} trades/day (slowest arm), "
            f"all arms hit 80-trade floor around day {proj_day}."
        )
    elif projection.get("min_trades", 0) >= 80:
        lines.append(f"Sample size: all arms ≥ 80 trades — eligible for promotion eval.")
    else:
        lines.append(
            f"Sample size projection: insufficient data "
            f"(min trades = {projection.get('min_trades', 0)})"
        )

    return lines


def main() -> int:
    if os.environ.get("GAMMA_AB_TEST_ENABLED", "0") != "1" and not DRY_RUN:
        print("[gamma_weekly_digest] GAMMA_AB_TEST_ENABLED=0 — skipping digest")
        return 0

    arm_states = {aid: (_load_arm_state(aid) or {}) for aid in VALID_ARM_IDS}
    arm_journals = {aid: _load_arm_journal(aid) for aid in VALID_ARM_IDS}

    week_start, week_end = _week_window()
    decisions = _read_decisions_window(week_start, week_end)

    overlap = _trade_overlap_stats(decisions)
    projection = _projection(arm_states)
    notable = _notable_divergences(decisions, arm_journals)

    lines = build_digest_lines(
        arm_states, arm_journals, decisions, overlap, projection, notable,
    )

    msg = "\n".join(lines)
    _post_discord(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
