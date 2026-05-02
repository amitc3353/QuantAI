"""Atomic single-entry update for trades.jsonl.

Mirrors position_monitor.rewrite_journal_atomic but exposed as a standalone
helper for diagnosis/reviewer scripts. Sequential callers only — there's no
concurrency control here. Position monitor calls these scripts inline AFTER
its own rewrite, and diagnosis finishes before reviewer starts.

On any failure, the original file is left untouched (write to .tmp + os.replace).
"""
from __future__ import annotations

import json
import os

DEFAULT_JOURNAL = "/root/quantai-v2/shared-data/journal/paper/trades.jsonl"


def update_trade_entry(trade_id: str, fields: dict, journal_path: str = DEFAULT_JOURNAL) -> bool:
    """Merge `fields` into the entry whose id == trade_id. Atomic rewrite.

    Returns True on success, False on any error (file unchanged on failure).
    Silently no-ops if the trade_id is not found in the file.
    """
    if not os.path.exists(journal_path):
        return False
    tmp_path = journal_path + ".tmp"
    try:
        lines = []
        with open(journal_path) as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    t = json.loads(raw)
                    if t.get("id") == trade_id:
                        t.update(fields)
                    lines.append(json.dumps(t))
                except Exception:
                    lines.append(raw)
        with open(tmp_path, "w") as out:
            out.write("\n".join(lines) + "\n")
        os.replace(tmp_path, journal_path)
        return True
    except Exception:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
        return False


def find_trade(trade_id: str, journal_path: str = DEFAULT_JOURNAL) -> dict | None:
    """Return the entry matching trade_id, or None if absent."""
    if not os.path.exists(journal_path):
        return None
    try:
        with open(journal_path) as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    t = json.loads(raw)
                except Exception:
                    continue
                if t.get("id") == trade_id:
                    return t
    except Exception:
        return None
    return None
