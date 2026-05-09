"""Centralized gate-block logger.

Every time a Phase 1 gate blocks an entry, write a structured line
to gate_blocks.jsonl for Friday review and false-positive auditing.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

GATE_LOG = Path("/root/quantai-v2/shared-data/logs/gate_blocks.jsonl")


def log_gate_block(
    gate: str,
    symbol: str,
    agent: str,
    reason: str,
    strategy: str = "",
) -> None:
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "gate": gate,
        "symbol": symbol,
        "agent": agent,
        "reason": reason,
        "would_have_been_strategy": strategy,
    }
    try:
        GATE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(GATE_LOG, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        logging.warning("gate_logger: write failed: %s", e)
