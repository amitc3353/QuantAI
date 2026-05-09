"""Reflection memory — write on close, retrieve for debate injection.

write_reflection(trade_id)  — called from position_monitor after every full close
get_lessons(agent, symbol)  — returns recent reflections for prompt injection
format_lessons(agent, symbol) — formats lessons as markdown for LLM prompts
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from _paths import _ROOT

MEMORY_DIR = _ROOT / "memory"

_AGENT_FILES = {
    "agent_alpha": "alpha_reflections.jsonl",
    "agent_beta": "beta_reflections.jsonl",
    "agent_gamma": "gamma_reflections.jsonl",
}

_CLOSE_REASON_CATEGORY = {
    "stop_loss": "stop_loss",
    "STOP_LOSS": "stop_loss",
    "max_loss": "stop_loss",
    "profit_target": "signal",
    "TAKE_PROFIT": "signal",
    "rsi_exit_above_40": "signal",
    "time_stop": "time_stop",
    "manual": "manual",
    "expiry": "time_stop",
}


def _jsonl_path(agent: str) -> Path:
    fname = _AGENT_FILES.get(agent, f"{agent}_reflections.jsonl")
    return MEMORY_DIR / fname


def _holding_days(trade: dict) -> int | None:
    if "holding_days" in trade:
        return trade["holding_days"]
    ts = trade.get("timestamp")
    cts = trade.get("close_timestamp")
    if ts and cts:
        try:
            from dateutil.parser import parse as dtparse
            return max(0, (dtparse(cts) - dtparse(ts)).days)
        except Exception:
            pass
    return None


def _build_reflection_prompt(trade: dict) -> str:
    """Build the LLM prompt for generating a trade reflection."""
    traj = trade.get("full_trajectory") or {}
    decision = trade.get("decision") or {}
    parts = [
        f"Trade {trade.get('id')} on {trade.get('symbol')} ({trade.get('strategy')}).",
        f"Regime: {decision.get('regime_at_entry', trade.get('regime_at_entry', 'unknown'))}.",
        f"Thesis: {trade.get('thesis', decision.get('thesis', 'N/A'))}.",
        f"Invalidation: {trade.get('invalidation', decision.get('invalidation', 'N/A'))}.",
        f"Conviction: {decision.get('conviction_score', 'N/A')}/10.",
        f"P&L: ${trade.get('pnl', 'N/A')} ({trade.get('pnl_pct', 'N/A')}%).",
        f"Close reason: {trade.get('close_reason', 'unknown')}.",
        f"Hold days: {_holding_days(trade)}.",
    ]
    if traj.get("bull_case"):
        parts.append(f"Bull case: {traj['bull_case'][:500]}")
    if traj.get("bear_case"):
        parts.append(f"Bear case: {traj['bear_case'][:500]}")
    if traj.get("judge_reasoning"):
        parts.append(f"Judge reasoning: {traj['judge_reasoning'][:500]}")
    return "\n".join(parts)


def _gamma_structured(trade: dict) -> dict:
    """Build deterministic structured reflection for Gamma (no LLM)."""
    decision = trade.get("decision") or {}
    rsi = decision.get("rsi_at_entry")
    rsi_bin = None
    if rsi is not None:
        if rsi < 20:
            rsi_bin = "<20"
        elif rsi < 25:
            rsi_bin = "20-25"
        elif rsi < 30:
            rsi_bin = "25-30"
        else:
            rsi_bin = ">=30"

    close_reason = trade.get("close_reason", "")
    exit_cat = _CLOSE_REASON_CATEGORY.get(close_reason, "other")

    ts = trade.get("timestamp", "")
    close_ts = trade.get("close_timestamp", "")
    entry_dow = None
    exit_dow = None
    try:
        from dateutil.parser import parse as dtparse
        if ts:
            entry_dow = dtparse(ts).strftime("%A")
        if close_ts:
            exit_dow = dtparse(close_ts).strftime("%A")
    except Exception:
        pass

    return {
        "entry_rsi_bin": rsi_bin,
        "exit_rsi": decision.get("rsi_at_exit"),
        "exit_reason_category": exit_cat,
        "sector": decision.get("sector"),
        "day_of_week_entry": entry_dow,
        "day_of_week_exit": exit_dow,
    }


def write_reflection(trade_id: str) -> None:
    """Write a reflection record for a closed trade. NEVER raises."""
    try:
        _write_reflection_inner(trade_id)
    except Exception as e:
        logging.exception("write_reflection(%s) failed: %s", trade_id, e)


def _write_reflection_inner(trade_id: str) -> None:
    from _journal_update import find_trade

    trade = find_trade(trade_id)
    if trade is None:
        logging.warning("write_reflection: trade %s not found in journal", trade_id)
        return

    agent = trade.get("source", "")
    is_gamma = agent == "agent_gamma"

    decision = trade.get("decision") or {}
    traj = trade.get("full_trajectory")

    regime = decision.get("regime_at_entry", trade.get("regime_at_entry", "unknown"))
    vix = decision.get("vix_at_entry", trade.get("vix_at_entry"))

    entry_features = {
        "vix": vix,
        "iv_rank": decision.get("iv_rank"),
        "conviction_score": decision.get("conviction_score"),
        "underlying_price": trade.get("underlying_price"),
    }
    if is_gamma:
        entry_features["rsi_10"] = decision.get("rsi_at_entry")
        entry_features["sma_200_distance_pct"] = decision.get("sma_200_distance_pct")
        entry_features["sector"] = decision.get("sector")

    thesis = trade.get("thesis", decision.get("thesis", ""))
    summary = f"{trade.get('strategy', '')} on {trade.get('symbol', '')}. {thesis}".strip()

    record = {
        "trade_id": trade_id,
        "agent": agent,
        "ticker": trade.get("symbol", ""),
        "strategy": trade.get("strategy", ""),
        "regime_at_entry": regime,
        "entry_features": entry_features,
        "decision_summary": summary,
        "full_trajectory": traj,
        "realized_return_raw": trade.get("pnl"),
        "realized_return_pct": trade.get("pnl_pct"),
        "alpha_vs_spy": None,
        "reflection_text": None,
        "reflection_status": "complete",
        "closed_at": trade.get("close_timestamp", datetime.now(timezone.utc).isoformat()),
        "close_reason": trade.get("close_reason", ""),
        "hold_days": _holding_days(trade),
    }

    if is_gamma:
        record["gamma_structured"] = _gamma_structured(trade)
    else:
        prompt = _build_reflection_prompt(trade)
        system = (
            "You are a post-trade analyst for an options trading system. "
            "Write a 2-4 sentence reflection on this closed trade. "
            "Focus on: was the thesis right or wrong? What signal was missed? "
            "What should change next time? Be specific and actionable."
        )
        try:
            from _llm_call import call_llm_text
            text = call_llm_text(
                model="claude-haiku-4-5-20251001",
                system=system,
                user=prompt,
                max_tokens=300,
                timeout=20,
                caller="write_reflection",
            )
        except Exception as e:
            text = None
            logging.warning("write_reflection LLM call failed: %s", e)

        if text:
            record["reflection_text"] = text
        else:
            record["reflection_status"] = "llm_failed"
            record["reflection_error"] = "call_llm_text returned None"
            record["retry_count"] = 0
            record["first_attempt_ts"] = datetime.now(timezone.utc).isoformat()

    _append_record(agent, record)


def _append_record(agent: str, record: dict) -> None:
    path = _jsonl_path(agent)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(record) + "\n")
    except Exception as e:
        logging.error("Failed to write reflection JSONL for %s: %s", agent, e)


def get_lessons(
    agent: str,
    symbol: str,
    k_same: int = 5,
    k_cross: int = 5,
) -> list[dict]:
    """Retrieve recent completed reflections for prompt injection.

    Returns up to k_same same-symbol + k_cross cross-symbol reflections.
    Only returns records with reflection_status == "complete".
    Newest first (by file order, which is append-order).
    """
    path = _jsonl_path(agent)
    if not path.exists():
        return []

    all_records = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    if rec.get("reflection_status") == "complete":
                        all_records.append(rec)
                except Exception:
                    continue
    except Exception:
        return []

    same = [r for r in all_records if r.get("ticker") == symbol]
    cross = [r for r in all_records if r.get("ticker") != symbol]

    same = list(reversed(same))[:k_same]
    cross = list(reversed(cross))[:k_cross]

    return same + cross


def format_lessons(
    agent: str,
    symbol: str,
    k_same: int = 5,
    k_cross: int = 5,
) -> str:
    """Format lessons as markdown for injection into LLM prompts.

    The most recent same-symbol lesson includes judge_reasoning from
    full_trajectory (~150 tokens). All other lessons use reflection-text only.
    """
    lessons = get_lessons(agent, symbol, k_same, k_cross)
    if not lessons:
        return ""

    same = [l for l in lessons if l.get("ticker") == symbol]
    cross = [l for l in lessons if l.get("ticker") != symbol]

    parts = ["## Lessons from recent trades\n"]

    if same:
        parts.append(f"### Same symbol ({symbol}):")
        for i, l in enumerate(same):
            pnl = l.get("realized_return_raw")
            pnl_pct = l.get("realized_return_pct")
            pnl_str = f"${pnl:+.0f} ({pnl_pct:+.1f}%)" if pnl is not None and pnl_pct is not None else "N/A"
            hold = l.get("hold_days", "?")
            header = (
                f"- [{l['trade_id']}] {l.get('strategy', '?')}, "
                f"CLOSED {l.get('close_reason', '?')}, {pnl_str}, {hold}d hold:"
            )

            if i == 0:
                traj = l.get("full_trajectory") or {}
                judge = traj.get("judge_reasoning", "")
                if judge:
                    judge_snippet = judge[:500]
                    parts.append(f"{header}")
                    parts.append(f'  Judge reasoning: "{judge_snippet}"')
                    if l.get("reflection_text"):
                        parts.append(f'  Reflection: "{l["reflection_text"]}"')
                else:
                    parts.append(f"{header}")
                    if l.get("reflection_text"):
                        parts.append(f'  "{l["reflection_text"]}"')
            else:
                parts.append(f"{header}")
                if l.get("reflection_text"):
                    parts.append(f'  "{l["reflection_text"]}"')
            parts.append("")

    if cross:
        parts.append("### Recent cross-symbol lessons:")
        for l in cross:
            pnl = l.get("realized_return_raw")
            pnl_pct = l.get("realized_return_pct")
            pnl_str = f"${pnl:+.0f} ({pnl_pct:+.1f}%)" if pnl is not None and pnl_pct is not None else "N/A"
            hold = l.get("hold_days", "?")
            ticker = l.get("ticker", "?")
            parts.append(
                f"- [{l['trade_id']}] {ticker} {l.get('strategy', '?')}, "
                f"CLOSED {l.get('close_reason', '?')}, {pnl_str}, {hold}d hold:"
            )
            if l.get("reflection_text"):
                parts.append(f'  "{l["reflection_text"]}"')
            parts.append("")

    return "\n".join(parts).strip()
