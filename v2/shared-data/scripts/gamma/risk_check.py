"""Risk gates for Agent Gamma.

Independent from Alpha and Beta — counts only entries with
`source == 'agent_gamma'`.

Rules (spec § 3):
  - MAX_OPEN_POSITIONS = 3 simultaneous Gamma positions
  - MAX_DAILY_ENTRIES  = 2 new entries per calendar day
  - MAX_POSITIONS_SAME_SECTOR = 2
  - Circuit breaker: 3 consecutive losses → 48h pause
  - No double-entry on the same symbol
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import (
    CIRCUIT_BREAKER_HOURS,
    CIRCUIT_BREAKER_LOSSES,
    INSTRUMENT_CONFIG,
    MAX_DAILY_ENTRIES,
    MAX_OPEN_POSITIONS,
    MAX_POSITIONS_SAME_SECTOR,
)
import sys as _sys
_sys.path.insert(0, "/home/trader/QuantAI/v2/shared-data/scripts")
from _concentration_gate import MAX_OPEN_PER_SYMBOL as _MAX_OPEN_PER_SYMBOL
from _cooldown_gate import is_in_cooldown as _is_in_cooldown
from _gate_logger import log_gate_block

ET = ZoneInfo("America/New_York")
JOURNAL = Path("/root/quantai-v2/shared-data/journal/paper/trades.jsonl")


def _is_gamma(t: dict) -> bool:
    """True for any Gamma trade (legacy single-arm OR per-arm A/B/C/D)."""
    src = t.get("source") or ""
    return src == "agent_gamma" or src.startswith("agent_gamma_arm_")


def _is_arm_gamma(t: dict, arm_id: str) -> bool:
    """True for trades belonging to a SPECIFIC arm. Used by filter_setups_for_arm
    to scope the journal so each arm's caps are independent."""
    return t.get("arm_id") == arm_id and t.get("source") == f"agent_gamma_arm_{arm_id}"


def open_arm_positions(journal: list, arm_id: str) -> list:
    """All status=OPEN trades in this arm's slice of the journal."""
    return [t for t in journal if _is_arm_gamma(t, arm_id) and t.get("status") == "OPEN"]


def consecutive_arm_losses(journal: list, arm_id: str) -> tuple[int, str]:
    """Per-arm version of consecutive_gamma_losses — scoped via _is_arm_gamma."""
    closed = sorted(
        [t for t in journal if _is_arm_gamma(t, arm_id) and t.get("status") == "CLOSED"],
        key=lambda t: t.get("close_timestamp") or t.get("timestamp", ""),
        reverse=True,
    )
    consec = 0
    for t in closed:
        if (t.get("pnl") or 0) < 0:
            consec += 1
        else:
            break
    last_ts = ""
    if closed:
        last_ts = closed[0].get("close_timestamp") or closed[0].get("timestamp", "")
    return consec, last_ts


def _today_iso() -> str:
    return datetime.now(ET).date().isoformat()


def _hours_since(ts_iso: str) -> float:
    try:
        ts = datetime.fromisoformat(ts_iso)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=ET)
        return (datetime.now(ET) - ts).total_seconds() / 3600.0
    except Exception:
        return 1e9


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


def open_gamma_positions(journal: list) -> list:
    return [t for t in journal if _is_gamma(t) and t.get("status") == "OPEN"]


def consecutive_gamma_losses(journal: list) -> tuple[int, str]:
    """Count consecutive Gamma losses going back from most recent close.
    Returns (count, last_close_timestamp)."""
    closed = sorted(
        [t for t in journal if _is_gamma(t) and t.get("status") == "CLOSED"],
        key=lambda t: t.get("close_timestamp") or t.get("timestamp", ""),
        reverse=True,
    )
    consec = 0
    for t in closed:
        if (t.get("pnl") or 0) < 0:
            consec += 1
        else:
            break
    last_ts = ""
    if closed:
        last_ts = closed[0].get("close_timestamp") or closed[0].get("timestamp", "")
    return consec, last_ts


def check_portfolio_gates(journal: list) -> tuple[bool, str]:
    """Pre-flight check: are we allowed to enter ANYTHING right now?

    Called once before iterating over candidate setups. Returns
    (allowed, reason). If False, skip this cycle entirely.
    """
    open_gamma = open_gamma_positions(journal)
    if len(open_gamma) >= MAX_OPEN_POSITIONS:
        return False, f"max {MAX_OPEN_POSITIONS} Gamma positions already open"

    today = _today_iso()
    todays = [t for t in journal if _is_gamma(t) and t.get("timestamp", "")[:10] == today]
    if len(todays) >= MAX_DAILY_ENTRIES:
        return False, f"max {MAX_DAILY_ENTRIES} Gamma entries already today"

    consec, last_ts = consecutive_gamma_losses(journal)
    if consec >= CIRCUIT_BREAKER_LOSSES and last_ts:
        hours = _hours_since(last_ts)
        if hours < CIRCUIT_BREAKER_HOURS:
            return False, (f"circuit breaker: {consec} consecutive losses, "
                           f"only {hours:.1f}h since last (need {CIRCUIT_BREAKER_HOURS}h)")

    return True, "ok"


def filter_setups(setups: list[dict], journal: list,
                  pending: list[dict] | None = None) -> list[dict]:
    """Filter setups against per-instrument and sector limits.

    `pending` lets the caller include in-flight entries (e.g. proposals
    queued earlier in this same cycle) toward the sector / open counts.
    """
    pending = pending or []
    open_gamma = open_gamma_positions(journal)
    today = _today_iso()
    todays = [t for t in journal if _is_gamma(t) and t.get("timestamp", "")[:10] == today]

    used_slots = len(open_gamma) + len(pending)
    available_slots = MAX_OPEN_POSITIONS - used_slots
    todays_remaining = MAX_DAILY_ENTRIES - len(todays) - len(pending)

    if available_slots <= 0 or todays_remaining <= 0:
        return []

    open_symbols = {p.get("symbol") for p in open_gamma + pending}
    sector_count: dict[str, int] = {}
    for p in open_gamma + pending:
        sec = p.get("sector") or INSTRUMENT_CONFIG.get(p.get("symbol", ""), {}).get("sector", "unknown")
        sector_count[sec] = sector_count.get(sec, 0) + 1

    out = []
    for s in setups:
        if len(out) >= min(available_slots, todays_remaining):
            break
        sym = s["symbol"]
        if sym in open_symbols:
            continue
        cross_open = sum(
            1 for t in journal
            if t.get("status") == "OPEN" and (t.get("symbol") or "").upper() == sym.upper()
        )
        if cross_open >= _MAX_OPEN_PER_SYMBOL:
            logging.info(
                "concentration_gate: %d cross-agent open on %s — skipping Gamma entry",
                cross_open, sym,
            )
            log_gate_block("concentration", sym, "gamma",
                           f"{cross_open} cross-agent open positions on {sym}",
                           "rsi_pullback_debit_spread")
            continue
        cool = _is_in_cooldown(sym, journal)
        if not cool.allowed:
            logging.info("cooldown_gate: %s — skipping Gamma entry", cool.reason)
            log_gate_block("cooldown", sym, "gamma", cool.reason, "rsi_pullback_debit_spread")
            continue
        sec = s.get("sector", "unknown")
        if sector_count.get(sec, 0) >= MAX_POSITIONS_SAME_SECTOR:
            continue
        out.append(s)
        sector_count[sec] = sector_count.get(sec, 0) + 1
        open_symbols.add(sym)

    return out


# ──────────────────────────────────────────────────────────────────────
# Per-arm variants (added 2026-05-10 for the 4-arm A/B/C/D test).
# Same cap values as the legacy functions; journal scope is filtered
# to a SPECIFIC arm via ``_is_arm_gamma``. When GAMMA_AB_TEST_ENABLED=0
# nothing in this section runs — production uses the legacy paths.
# ──────────────────────────────────────────────────────────────────────


def check_portfolio_gates_for_arm(journal: list, arm_id: str) -> tuple[bool, str]:
    """Per-arm portfolio gate: max open, daily cap, circuit breaker — all
    scoped to ``arm_id``'s slice of the union journal. Other arms' state
    has zero effect on this check."""
    open_arm = open_arm_positions(journal, arm_id)
    if len(open_arm) >= MAX_OPEN_POSITIONS:
        return False, f"arm {arm_id}: max {MAX_OPEN_POSITIONS} positions already open"

    today = _today_iso()
    todays = [
        t for t in journal
        if _is_arm_gamma(t, arm_id) and t.get("timestamp", "")[:10] == today
    ]
    if len(todays) >= MAX_DAILY_ENTRIES:
        return False, f"arm {arm_id}: max {MAX_DAILY_ENTRIES} entries already today"

    consec, last_ts = consecutive_arm_losses(journal, arm_id)
    if consec >= CIRCUIT_BREAKER_LOSSES and last_ts:
        hours = _hours_since(last_ts)
        if hours < CIRCUIT_BREAKER_HOURS:
            return False, (
                f"arm {arm_id}: circuit breaker {consec} consecutive losses, "
                f"{hours:.1f}h < {CIRCUIT_BREAKER_HOURS}h"
            )

    return True, "ok"


def filter_setups_for_arm(setups: list[dict], journal: list, arm_id: str,
                           pending: list[dict] | None = None) -> list[dict]:
    """Per-arm version of ``filter_setups``. Same cap values as legacy
    (daily=2, sector=2, position=3) but the journal is scoped via
    ``_is_arm_gamma`` so each arm's caps are independent of the others.

    The cross-agent concentration gate (``cross_open >= MAX_OPEN_PER_SYMBOL``)
    still uses the FULL journal: a position held by ANY agent (including
    other arms of Gamma) counts. This prevents the whole experiment from
    over-concentrating on a single symbol.
    """
    pending = pending or []
    open_arm = open_arm_positions(journal, arm_id)
    today = _today_iso()
    todays = [
        t for t in journal
        if _is_arm_gamma(t, arm_id) and t.get("timestamp", "")[:10] == today
    ]

    used_slots = len(open_arm) + len(pending)
    available_slots = MAX_OPEN_POSITIONS - used_slots
    todays_remaining = MAX_DAILY_ENTRIES - len(todays) - len(pending)

    if available_slots <= 0 or todays_remaining <= 0:
        return []

    open_symbols = {p.get("symbol") for p in open_arm + pending}
    sector_count: dict[str, int] = {}
    for p in open_arm + pending:
        sec = p.get("sector") or INSTRUMENT_CONFIG.get(p.get("symbol", ""), {}).get("sector", "unknown")
        sector_count[sec] = sector_count.get(sec, 0) + 1

    out = []
    for s in setups:
        if len(out) >= min(available_slots, todays_remaining):
            break
        sym = s["symbol"]
        if sym in open_symbols:
            continue
        # Cross-agent concentration: still uses FULL journal (not arm-scoped).
        # An overall cap on simultaneous positions in the same symbol across
        # ALL agents prevents the experiment from over-concentrating.
        cross_open = sum(
            1 for t in journal
            if t.get("status") == "OPEN" and (t.get("symbol") or "").upper() == sym.upper()
        )
        if cross_open >= _MAX_OPEN_PER_SYMBOL:
            logging.info(
                "concentration_gate: %d cross-agent open on %s — skipping arm %s entry",
                cross_open, sym, arm_id,
            )
            log_gate_block(
                "concentration", sym, f"gamma_arm_{arm_id}",
                f"{cross_open} cross-agent open positions on {sym}",
                "rsi_pullback_debit_spread",
            )
            continue
        # Cooldown: shared across the whole agent (an arm shouldn't re-enter
        # right after a different arm's stop-loss on the same symbol)
        cool = _is_in_cooldown(sym, journal)
        if not cool.allowed:
            logging.info("cooldown_gate: %s — skipping arm %s entry", cool.reason, arm_id)
            log_gate_block(
                "cooldown", sym, f"gamma_arm_{arm_id}",
                cool.reason, "rsi_pullback_debit_spread",
            )
            continue
        sec = s.get("sector", "unknown")
        if sector_count.get(sec, 0) >= MAX_POSITIONS_SAME_SECTOR:
            continue
        out.append(s)
        sector_count[sec] = sector_count.get(sec, 0) + 1
        open_symbols.add(sym)

    return out
