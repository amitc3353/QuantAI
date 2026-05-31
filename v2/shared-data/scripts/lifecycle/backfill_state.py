#!/usr/bin/env python3
"""One-shot status→state migration for the lifecycle FSM.

Reads trades.jsonl, derives the FSM `state` field from each record's
existing `status` field, and rewrites the journal atomically. Creates a
.bak file before writing.

The 21 known phantoms from Phase 0 are handled explicitly:
  O5_CLOSE_FAILURE (7): Ga002, Gb002, Gc003, Gd003, Gb003, Gc004, Gd004
    → PHANTOM_NEVER_FILLED (had working_close_order_id; close stuck)
  O3_STUCK_INDETERMINATE (14): M001, M002, A021, A022, A025, Ga003,
    Ga004, Gb004, Gc005, Gd005, Ga005, Gb005, Gc006, Gd006
    → PHANTOM_NEVER_FILLED (submitted/pending but never terminal)

Usage:
    python3 backfill_state.py [--dry-run] [--journal PATH]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

# Allow running standalone (before lifecycle is on sys.path)
_HERE = Path(__file__).parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from lifecycle.states import TradeState

DEFAULT_JOURNAL = "/root/quantai-v2/shared-data/journal/paper/trades.jsonl"

# Phase 0 known zombie inventory — explicit mapping for correctness
_KNOWN_ZOMBIES: dict[str, TradeState] = {
    # O5_CLOSE_FAILURE
    "Ga002": TradeState.PHANTOM_NEVER_FILLED,
    "Gb002": TradeState.PHANTOM_NEVER_FILLED,
    "Gc003": TradeState.PHANTOM_NEVER_FILLED,
    "Gd003": TradeState.PHANTOM_NEVER_FILLED,
    "Gb003": TradeState.PHANTOM_NEVER_FILLED,
    "Gc004": TradeState.PHANTOM_NEVER_FILLED,
    "Gd004": TradeState.PHANTOM_NEVER_FILLED,
    # O3_STUCK_INDETERMINATE
    "M001":  TradeState.PHANTOM_NEVER_FILLED,
    "M002":  TradeState.PHANTOM_NEVER_FILLED,
    "A021":  TradeState.PHANTOM_NEVER_FILLED,
    "A022":  TradeState.PHANTOM_NEVER_FILLED,
    "A025":  TradeState.PHANTOM_NEVER_FILLED,
    "Ga003": TradeState.PHANTOM_NEVER_FILLED,
    "Ga004": TradeState.PHANTOM_NEVER_FILLED,
    "Gb004": TradeState.PHANTOM_NEVER_FILLED,
    "Gc005": TradeState.PHANTOM_NEVER_FILLED,
    "Gd005": TradeState.PHANTOM_NEVER_FILLED,
    "Ga005": TradeState.PHANTOM_NEVER_FILLED,
    "Gb005": TradeState.PHANTOM_NEVER_FILLED,
    "Gc006": TradeState.PHANTOM_NEVER_FILLED,
    "Gd006": TradeState.PHANTOM_NEVER_FILLED,
}


def derive_state(record: dict) -> TradeState:
    """Derive the FSM state from a record's fields."""
    tid = record.get("id", "")
    if tid in _KNOWN_ZOMBIES:
        return _KNOWN_ZOMBIES[tid]

    status = record.get("status") or ""
    has_order_id = bool(record.get("order_id"))
    has_wco = bool(record.get("working_close_order_id"))

    if status == "CLOSED":
        return TradeState.CLOSED
    if status == "EXPIRED":
        return TradeState.EXPIRED
    if status == "PHANTOM_NEVER_FILLED":
        # Not in known_zombies → classify by fields
        if has_wco:
            return TradeState.PHANTOM_NEVER_FILLED
        if record.get("fill_confirmed_at") or (record.get("filled_qty") or 0) > 0:
            return TradeState.PHANTOM_VANISHED
        if not has_order_id:
            return TradeState.REJECTED
        return TradeState.PHANTOM_NEVER_FILLED
    if status == "OPEN":
        if has_wco:
            return TradeState.EXIT_SUBMITTED
        return TradeState.OPEN
    if status == "PENDING":
        return TradeState.ACKED if has_order_id else TradeState.SUBMIT_PENDING

    # Unrecognised status — default OPEN (monitor will sort it out)
    return TradeState.OPEN


def backfill(journal_path: str, dry_run: bool = False) -> dict:
    """Read journal, derive state for every record, write back atomically.

    Returns stats dict with counts per derived state.
    """
    if not os.path.exists(journal_path):
        return {"error": "journal not found", "path": journal_path}

    now_tag = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    bak_path = journal_path + f".bak.{now_tag}"

    if not dry_run:
        shutil.copy2(journal_path, bak_path)

    records = []
    with open(journal_path) as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                records.append(json.loads(raw))
            except json.JSONDecodeError:
                records.append(raw)  # preserve malformed lines

    stats: Counter = Counter()
    updated_records = []
    for r in records:
        if isinstance(r, str):
            updated_records.append(r)
            stats["malformed"] += 1
            continue
        if "state" in r and r["state"]:
            stats["already_has_state"] += 1
            updated_records.append(json.dumps(r))
            continue
        state = derive_state(r)
        r["state"] = state.value
        r["last_transition_at"] = r.get("timestamp") or datetime.now(timezone.utc).isoformat()
        r["transitions_count"] = r.get("transitions_count", 0)
        stats[state.value] += 1
        updated_records.append(json.dumps(r))

    if not dry_run:
        tmp = journal_path + ".tmp"
        with open(tmp, "w") as f:
            f.write("\n".join(updated_records) + "\n")
        os.replace(tmp, journal_path)

    return {
        "dry_run": dry_run,
        "total": len(records),
        "bak": bak_path if not dry_run else "(skipped)",
        "stats": dict(stats),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="Print what would change; don't write")
    ap.add_argument("--journal", default=DEFAULT_JOURNAL, help="Path to trades.jsonl")
    args = ap.parse_args(argv)

    result = backfill(args.journal, dry_run=args.dry_run)
    print(json.dumps(result, indent=2))
    if "error" in result:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
