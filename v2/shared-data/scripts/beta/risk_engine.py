"""Beta-specific risk engine. Implements spec § 5.

Independent from Alpha — counts only entries with `source == 'agent_beta'`.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
JOURNAL = Path("/root/quantai-v2/shared-data/journal/paper/trades.jsonl")

MAX_OPEN_POSITIONS = 3
MAX_TRADES_PER_DAY = 2
CIRCUIT_BREAKER_LOSSES = 5
DRAWDOWN_HALT_DAILY = 0.02
DRAWDOWN_HALF_SIZE_WEEKLY = 0.05


def _is_beta(t: dict) -> bool:
    return t.get("source") == "agent_beta"


def _today_iso() -> str:
    return datetime.now(ET).date().isoformat()


def _monday_iso() -> str:
    today = datetime.now(ET).date()
    return (today - timedelta(days=today.weekday())).isoformat()


def _hours_since(ts_iso: str) -> float:
    try:
        ts = datetime.fromisoformat(ts_iso)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=ET)
        return (datetime.now(ET) - ts).total_seconds() / 3600.0
    except Exception:
        return 999.0


def load_journal(path: Path = JOURNAL) -> list:
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


def open_beta_positions(journal: list) -> list:
    return [t for t in journal if _is_beta(t) and t.get("status") == "OPEN"]


def check_risk(new_trade: dict, intel: dict, account: dict,
               journal: list) -> tuple[bool, str, dict]:
    """Returns (allowed, reason, possibly-modified trade).

    The trade may have its `risk_pct` halved on weekly drawdown but the
    function never silently changes other fields.
    """
    today = _today_iso()
    week_start = _monday_iso()
    open_beta = open_beta_positions(journal)

    # 1. Position limit
    if len(open_beta) >= MAX_OPEN_POSITIONS:
        return False, f"max {MAX_OPEN_POSITIONS} Beta positions open", new_trade

    # 2. Daily trade limit
    todays = [t for t in journal if _is_beta(t) and t.get("timestamp", "")[:10] == today]
    if len(todays) >= MAX_TRADES_PER_DAY:
        return False, f"max {MAX_TRADES_PER_DAY} Beta trades today", new_trade

    # 3. Circuit breaker — 5 consecutive losses
    closed_beta = sorted(
        [t for t in journal if _is_beta(t) and t.get("status") == "CLOSED"],
        key=lambda t: t.get("timestamp", ""),
        reverse=True,
    )
    consec = 0
    for t in closed_beta:
        if (t.get("pnl") or 0) < 0:
            consec += 1
        else:
            break
    if consec >= CIRCUIT_BREAKER_LOSSES and closed_beta:
        last_ts = closed_beta[0].get("close_timestamp") or closed_beta[0].get("timestamp", "")
        if _hours_since(last_ts) < 24:
            return False, f"circuit breaker: {consec} consecutive losses, <24h since last", new_trade

    # 4. Daily drawdown halt
    equity = float(account.get("equity") or 0)
    if equity > 0:
        daily_pnl = sum((t.get("pnl") or 0) for t in journal
                        if _is_beta(t) and t.get("status") == "CLOSED"
                        and t.get("close_timestamp", t.get("timestamp", ""))[:10] == today)
        if daily_pnl < -(equity * DRAWDOWN_HALT_DAILY):
            return False, f"daily drawdown halt: ${daily_pnl:.0f} <= -2% equity", new_trade

    # 5. Weekly drawdown — half size
    if equity > 0:
        weekly_pnl = sum((t.get("pnl") or 0) for t in journal
                         if _is_beta(t) and t.get("status") == "CLOSED"
                         and t.get("close_timestamp", t.get("timestamp", ""))[:10] >= week_start)
        if weekly_pnl < -(equity * DRAWDOWN_HALF_SIZE_WEEKLY):
            new_trade = dict(new_trade)
            new_trade["risk_pct"] = (new_trade.get("risk_pct") or 0.01) * 0.5
            new_trade["risk_note"] = "weekly_drawdown_half_size"

    # 6. Correlation
    nd = sum((p.get("net_delta") or 0) for p in open_beta)
    nv = sum((p.get("net_vega") or 0) for p in open_beta)
    nt_d = new_trade.get("net_delta") or 0
    nt_v = new_trade.get("net_vega") or 0
    if nt_d > 0 and nd > 0.5:
        return False, f"portfolio already long delta ({nd:.2f})", new_trade
    if nt_d < 0 and nd < -0.5:
        return False, f"portfolio already short delta ({nd:.2f})", new_trade
    if nt_v > 0 and nv > 1.0:
        return False, f"portfolio already long vega ({nv:.2f})", new_trade

    return True, "passed", new_trade
