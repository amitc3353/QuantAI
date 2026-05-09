"""Same-underlying concentration gate.

Blocks entry when >= MAX_OPEN_PER_SYMBOL open positions already exist on the
same symbol, regardless of agent source or strategy.

Source-agnostic: counts Alpha (A###), Beta (B###), Gamma (G###), and
manual (M###) positions. Missing journal → fail-closed (block).
Corrupt JSONL lines → skipped.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

JOURNAL = Path("/root/quantai-v2/shared-data/journal/paper/trades.jsonl")
MAX_OPEN_PER_SYMBOL = 2

logger = logging.getLogger(__name__)


@dataclass
class ConcentrationResult:
    allowed: bool
    reason: str
    open_ids: list[str] = field(default_factory=list)
    symbol: str = ""


def check_concentration(symbol: str, journal_path: Path = JOURNAL) -> ConcentrationResult:
    """Return ConcentrationResult for the proposed symbol.

    Reads JOURNAL, counts open positions on symbol (case-insensitive).
    Returns allowed=False when count >= MAX_OPEN_PER_SYMBOL or journal unreadable.
    """
    sym = symbol.upper().strip()

    if not journal_path.exists():
        logger.warning(
            "concentration_gate: journal not found at %s — failing closed", journal_path
        )
        return ConcentrationResult(
            allowed=False,
            reason=f"journal_unavailable: cannot verify concentration for {sym}",
            symbol=sym,
        )

    open_ids: list[str] = []
    try:
        with journal_path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    trade = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (
                    trade.get("status") == "OPEN"
                    and trade.get("symbol", "").upper().strip() == sym
                ):
                    open_ids.append(trade.get("id", "?"))
    except OSError as exc:
        logger.error("concentration_gate: failed to read journal: %s", exc)
        return ConcentrationResult(
            allowed=False,
            reason=f"journal_read_error: {exc}",
            symbol=sym,
        )

    if len(open_ids) >= MAX_OPEN_PER_SYMBOL:
        return ConcentrationResult(
            allowed=False,
            reason=(
                f"concentration_limit: {len(open_ids)} open positions on {sym} "
                f"(max {MAX_OPEN_PER_SYMBOL}): {', '.join(open_ids)}"
            ),
            open_ids=open_ids,
            symbol=sym,
        )

    return ConcentrationResult(
        allowed=True,
        reason="passed",
        open_ids=open_ids,
        symbol=sym,
    )
