"""
Memory System — Persistent Context for All Agents
====================================================
Every trade, decision, bug fix, and lesson gets logged here.
Before every Claude call, relevant memory is loaded into context.

Directory structure:
  data/memory/
    paper/              — Paper trading memory
      trade_journal.jsonl
      decisions.jsonl
    live/               — Live trading memory (Phase 3)
      trade_journal.jsonl
      decisions.jsonl
    shared/             — Cross-mode knowledge (lessons survive mode switch)
      lessons.jsonl
      system_events.jsonl
      conversation_context.jsonl

Naming convention:
  - trade_journal: individual trade records with outcomes
  - decisions: architectural/rule/strategy decisions with reasoning
  - lessons: extracted patterns (auto + manual)
  - system_events: deploys, restarts, errors, config changes
  - conversation_context: recent #chat conversations for continuity
"""

import os
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, asdict

log = logging.getLogger("memory")

# ---------------------------------------------------------------------------
# Paths — mode-aware
# ---------------------------------------------------------------------------
MEMORY_ROOT = Path(os.getenv("MEMORY_ROOT", "/app/data/memory"))
TRADING_MODE = os.getenv("TRADING_MODE", "paper")  # paper | live


def mode_path() -> Path:
    """Return the current trading mode's memory directory."""
    p = MEMORY_ROOT / TRADING_MODE
    p.mkdir(parents=True, exist_ok=True)
    return p


def shared_path() -> Path:
    """Return the shared memory directory (survives mode switches)."""
    p = MEMORY_ROOT / "shared"
    p.mkdir(parents=True, exist_ok=True)
    return p


# ---------------------------------------------------------------------------
# Core: append-only JSONL writers
# ---------------------------------------------------------------------------
def _append(filepath: Path, record: dict):
    """Append a JSON record to a JSONL file."""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "a") as f:
        f.write(json.dumps(record) + "\n")


def _read_recent(filepath: Path, n: int = 50) -> list[dict]:
    """Read the last N records from a JSONL file."""
    if not filepath.exists():
        return []
    records = []
    with open(filepath) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return records[-n:]


def _read_all(filepath: Path) -> list[dict]:
    """Read all records from a JSONL file."""
    if not filepath.exists():
        return []
    records = []
    with open(filepath) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return records


def _search(filepath: Path, query: str, fields: list[str] = None, limit: int = 20) -> list[dict]:
    """Search records by keyword across specified fields."""
    query_lower = query.lower()
    fields = fields or ["symbol", "strategy", "description", "lesson", "category", "message"]
    results = []
    for record in _read_all(filepath):
        for field in fields:
            value = str(record.get(field, "")).lower()
            if query_lower in value:
                results.append(record)
                break
    return results[-limit:]


# ---------------------------------------------------------------------------
# Trade Journal — one record per trade
# ---------------------------------------------------------------------------
def log_trade(
    symbol: str,
    strategy: str,
    direction: str,
    entry_price: float = 0,
    exit_price: float = 0,
    pnl: float = 0,
    pnl_pct: float = 0,
    contracts: int = 1,
    dte_at_entry: int = 0,
    iv_rank_at_entry: float = 0,
    guard_result: str = "",
    thesis: str = "",
    outcome_notes: str = "",
    tags: list[str] = None,
):
    """Log a trade to the mode-specific journal."""
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mode": TRADING_MODE,
        "symbol": symbol.upper(),
        "strategy": strategy,
        "direction": direction,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "contracts": contracts,
        "dte_at_entry": dte_at_entry,
        "iv_rank_at_entry": iv_rank_at_entry,
        "guard_result": guard_result,
        "thesis": thesis,
        "outcome_notes": outcome_notes,
        "tags": tags or [],
    }
    _append(mode_path() / "trade_journal.jsonl", record)
    log.info(f"Trade logged [{TRADING_MODE}]: {symbol} {strategy} P&L={pnl}")
    return record


def get_recent_trades(n: int = 20) -> list[dict]:
    """Get recent trades for current mode."""
    return _read_recent(mode_path() / "trade_journal.jsonl", n)


def search_trades(query: str) -> list[dict]:
    """Search trade journal by keyword."""
    return _search(mode_path() / "trade_journal.jsonl", query)


def get_trade_stats() -> dict:
    """Calculate summary stats for current mode's trades."""
    trades = _read_all(mode_path() / "trade_journal.jsonl")
    if not trades:
        return {"total": 0, "message": "No trades logged yet."}

    winners = [t for t in trades if t.get("pnl", 0) > 0]
    losers = [t for t in trades if t.get("pnl", 0) < 0]
    total_pnl = sum(t.get("pnl", 0) for t in trades)

    return {
        "mode": TRADING_MODE,
        "total_trades": len(trades),
        "winners": len(winners),
        "losers": len(losers),
        "win_rate": f"{len(winners)/len(trades)*100:.1f}%" if trades else "0%",
        "total_pnl": round(total_pnl, 2),
        "avg_pnl": round(total_pnl / len(trades), 2) if trades else 0,
        "strategies": list(set(t.get("strategy", "?") for t in trades)),
        "symbols": list(set(t.get("symbol", "?") for t in trades)),
    }


# ---------------------------------------------------------------------------
# Decisions Log — architectural, rule, and strategy decisions
# ---------------------------------------------------------------------------
def log_decision(
    category: str,
    description: str,
    reasoning: str,
    outcome: str = "pending",
    tags: list[str] = None,
):
    """
    Log a decision with full reasoning.
    Categories: rule_change, strategy, architecture, bug_fix, config, feature
    """
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mode": TRADING_MODE,
        "category": category,
        "description": description,
        "reasoning": reasoning,
        "outcome": outcome,
        "tags": tags or [],
    }
    _append(mode_path() / "decisions.jsonl", record)
    log.info(f"Decision logged [{category}]: {description[:80]}")
    return record


def get_recent_decisions(n: int = 20) -> list[dict]:
    return _read_recent(mode_path() / "decisions.jsonl", n)


def search_decisions(query: str) -> list[dict]:
    return _search(mode_path() / "decisions.jsonl", query)


# ---------------------------------------------------------------------------
# Lessons Learned — shared across modes (knowledge survives mode switch)
# ---------------------------------------------------------------------------
def log_lesson(
    lesson: str,
    source: str = "manual",
    confidence: float = 1.0,
    related_trades: list[str] = None,
    tags: list[str] = None,
):
    """
    Log a lesson learned. These persist across paper/live modes.
    Source: manual (you typed it), auto_score (daily scorer), pattern (extracted)
    """
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "lesson": lesson,
        "source": source,
        "confidence": confidence,
        "related_trades": related_trades or [],
        "tags": tags or [],
        "originated_in": TRADING_MODE,
    }
    _append(shared_path() / "lessons.jsonl", record)
    log.info(f"Lesson logged: {lesson[:80]}")
    return record


def get_lessons(n: int = 50) -> list[dict]:
    return _read_recent(shared_path() / "lessons.jsonl", n)


def search_lessons(query: str) -> list[dict]:
    return _search(shared_path() / "lessons.jsonl", query)


# ---------------------------------------------------------------------------
# System Events — deploys, restarts, errors, config changes
# ---------------------------------------------------------------------------
def log_event(
    event_type: str,
    description: str,
    details: dict = None,
):
    """
    Log a system event.
    Types: deploy, restart, error, config_change, guard_rejection, emergency_stop
    """
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mode": TRADING_MODE,
        "event_type": event_type,
        "description": description,
        "details": details or {},
    }
    _append(shared_path() / "system_events.jsonl", record)
    log.info(f"Event [{event_type}]: {description[:80]}")
    return record


def get_recent_events(n: int = 30) -> list[dict]:
    return _read_recent(shared_path() / "system_events.jsonl", n)


# ---------------------------------------------------------------------------
# Conversation Context — recent #chat messages for continuity
# ---------------------------------------------------------------------------
MAX_CONVERSATION_HISTORY = 100  # Keep last N messages


def log_conversation(role: str, content: str, channel: str = "chat"):
    """Log a conversation message for context continuity."""
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "role": role,  # user | assistant
        "content": content,
        "channel": channel,
    }
    _append(shared_path() / "conversation_context.jsonl", record)


def get_conversation_history(n: int = 30) -> list[dict]:
    """Get recent conversation history for context injection."""
    return _read_recent(shared_path() / "conversation_context.jsonl", n)


# ---------------------------------------------------------------------------
# Context Builder — assembles relevant memory for Claude prompts
# ---------------------------------------------------------------------------
def build_context(
    query: str = "",
    include_trades: bool = True,
    include_lessons: bool = True,
    include_decisions: bool = False,
    include_events: bool = False,
    include_conversation: bool = True,
    max_tokens_estimate: int = 3000,
) -> str:
    """
    Build a context string from memory for injection into Claude prompts.
    Token-optimized: only includes what's relevant.
    """
    sections = []

    sections.append(f"[Trading mode: {TRADING_MODE}]")

    # Conversation history (most important for continuity)
    if include_conversation:
        history = get_conversation_history(20)
        if history:
            convo_lines = []
            for msg in history[-15:]:
                role = "You" if msg["role"] == "user" else "Assistant"
                content = msg["content"][:200]
                convo_lines.append(f"{role}: {content}")
            sections.append("Recent conversation:\n" + "\n".join(convo_lines))

    # Lessons (always valuable, shared across modes)
    if include_lessons:
        if query:
            lessons = search_lessons(query)
        else:
            lessons = get_lessons(10)
        if lessons:
            lesson_lines = [f"- {l['lesson']}" for l in lessons[-10:]]
            sections.append("Lessons learned:\n" + "\n".join(lesson_lines))

    # Recent trades
    if include_trades:
        trades = get_recent_trades(10)
        if trades:
            trade_lines = []
            for t in trades[-5:]:
                trade_lines.append(
                    f"- {t['symbol']} {t['strategy']} "
                    f"P&L={t.get('pnl', '?')} ({t.get('outcome_notes', '')})"
                )
            sections.append(f"Recent trades ({TRADING_MODE}):\n" + "\n".join(trade_lines))

    # Decisions
    if include_decisions:
        if query:
            decisions = search_decisions(query)
        else:
            decisions = get_recent_decisions(5)
        if decisions:
            dec_lines = [f"- [{d['category']}] {d['description']}" for d in decisions[-5:]]
            sections.append("Recent decisions:\n" + "\n".join(dec_lines))

    # System events
    if include_events:
        events = get_recent_events(5)
        if events:
            evt_lines = [f"- [{e['event_type']}] {e['description']}" for e in events[-5:]]
            sections.append("Recent system events:\n" + "\n".join(evt_lines))

    context = "\n\n".join(sections)

    # Rough token limit (4 chars ≈ 1 token)
    max_chars = max_tokens_estimate * 4
    if len(context) > max_chars:
        context = context[:max_chars] + "\n... (context truncated)"

    return context


# ---------------------------------------------------------------------------
# Init — ensure directories exist
# ---------------------------------------------------------------------------
def init():
    """Initialize memory directories."""
    mode_path()
    shared_path()
    # Create .gitkeep files
    for d in [mode_path(), shared_path()]:
        gitkeep = d / ".gitkeep"
        if not gitkeep.exists():
            gitkeep.touch()
    log.info(f"Memory initialized: mode={TRADING_MODE}, root={MEMORY_ROOT}")


# Auto-init on import
init()
