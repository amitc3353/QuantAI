#!/usr/bin/env python3
"""resolve_item — mark a self-learning capability request or parameter
suggestion as resolved.

Edits /root/quantai-v2/shared-data/learning_tracker.json atomically. The
next collect_learning.py run (≤5 min) moves the item from open_items to
resolved_items on the dashboard.

Usage:
  python3 resolve_item.py --id "<item-id>" --note "<resolution note>"
  python3 resolve_item.py --id "<item-id>" --note "..." --unresolve
  python3 resolve_item.py --list                     # list current open IDs
  python3 resolve_item.py --list-resolved            # list resolved IDs

Item IDs come from the dashboard tile's `id` field (see
/var/dashboard/state/quantai-learning.json).
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
from datetime import date
from pathlib import Path

from _paths import LEARNING_TRACKER as TRACKER, LEARNING_STATE as STATE


def _read_tracker_raw() -> dict:
    """Read tracker without acquiring lock (caller must hold lock)."""
    if not TRACKER.exists():
        return {"resolved": {}}
    try:
        data = json.loads(TRACKER.read_text())
    except Exception as e:
        print(f"error: tracker file is not valid JSON: {e}", file=sys.stderr)
        sys.exit(2)
    if not isinstance(data, dict):
        print("error: tracker root is not a dict", file=sys.stderr)
        sys.exit(2)
    if "resolved" not in data or not isinstance(data["resolved"], dict):
        data["resolved"] = {}
    return data


def _read_tracker() -> dict:
    """Read tracker (safe to call without holding lock — read-only use)."""
    return _read_tracker_raw()


def _modify_tracker(fn) -> None:
    """Apply fn(data) -> data to the tracker file under exclusive lock.

    The lock covers the full read-modify-write so concurrent invocations
    cannot interleave their reads and overwrite each other's changes.
    """
    TRACKER.parent.mkdir(parents=True, exist_ok=True)
    lock_path = TRACKER.with_suffix(".lock")
    with open(lock_path, "w") as _lf:
        fcntl.flock(_lf, fcntl.LOCK_EX)
        try:
            data = _read_tracker_raw()
            data = fn(data)
            tmp = TRACKER.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2))
            os.replace(tmp, TRACKER)
        finally:
            fcntl.flock(_lf, fcntl.LOCK_UN)


def _write_tracker(data: dict) -> None:
    """Write a pre-built tracker dict under exclusive lock.

    Prefer _modify_tracker for read-modify-write; use this only when
    the caller has already computed the full new state without needing
    to re-read (e.g. in tests that build the dict from scratch).
    """
    _modify_tracker(lambda _: data)


def _read_state() -> dict | None:
    if not STATE.exists():
        return None
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return None


def _list_open() -> int:
    s = _read_state()
    if not s:
        print("no dashboard state yet — run collect_learning.py first")
        return 1
    open_items = s.get("data", {}).get("open_items", [])
    if not open_items:
        print("no open items")
        return 0
    print(f"{'ID':<60}  {'AGENT':<14}  {'PRIO':<12}  {'TITLE'}")
    print("-" * 120)
    for it in open_items:
        print(f"{it['id']:<60}  {it.get('agent','?'):<14}  {it.get('priority','?'):<12}  {it.get('title','')[:50]}")
    return 0


def _list_resolved() -> int:
    s = _read_state()
    if not s:
        print("no dashboard state yet")
        return 1
    resolved = s.get("data", {}).get("resolved_items", [])
    if not resolved:
        print("no resolved items")
        return 0
    print(f"{'ID':<60}  {'DATE':<12}  {'NOTE'}")
    print("-" * 120)
    for r in resolved:
        print(f"{r['id']:<60}  {r.get('resolved_date',''):<12}  {r.get('resolution_note','')[:50]}")
    return 0


def _resolve(item_id: str, note: str) -> int:
    if not item_id:
        print("error: --id required", file=sys.stderr)
        return 2
    if not note:
        print("error: --note required (1-2 sentences on what was done)", file=sys.stderr)
        return 2

    state = _read_state() or {}
    open_items = state.get("data", {}).get("open_items", []) if state else []
    matched = next((it for it in open_items if it["id"] == item_id), None)

    entry = {
        "resolved_date": date.today().isoformat(),
        "resolution_note": note,
    }
    if matched:
        # Capture extra context so the resolved tile can show it even if the
        # underlying source files age out.
        entry.update({
            "agent": matched.get("agent"),
            "type": matched.get("type"),
            "title": matched.get("title"),
        })

    already_resolved = False

    def _apply(data: dict) -> dict:
        nonlocal already_resolved
        already_resolved = item_id in data["resolved"]
        data["resolved"][item_id] = entry
        return data

    _modify_tracker(_apply)

    if already_resolved:
        print(f"warning: {item_id} is already resolved (overwriting note)")

    print(f"resolved: {item_id}")
    print(f"  date: {entry['resolved_date']}")
    print(f"  note: {note}")
    if not matched:
        print("  (note: id not found in current open list — recorded anyway)")
    print("Dashboard tile will update on next collector run (≤5 min).")
    return 0


def _unresolve(item_id: str) -> int:
    # Check existence before locking to give a fast error without holding the lock
    tracker = _read_tracker()
    if item_id not in tracker["resolved"]:
        print(f"error: {item_id} is not currently resolved", file=sys.stderr)
        return 1

    def _apply(data: dict) -> dict:
        data["resolved"].pop(item_id, None)
        return data

    _modify_tracker(_apply)
    print(f"un-resolved: {item_id}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Mark a self-learning item resolved.")
    p.add_argument("--id", help="Item id from the learning tile")
    p.add_argument("--note", help="Short resolution note (what you did)")
    p.add_argument("--unresolve", action="store_true",
                   help="Remove a previous resolution (re-open the item)")
    p.add_argument("--list", action="store_true", help="List current open items")
    p.add_argument("--list-resolved", action="store_true", help="List resolved items")
    args = p.parse_args()

    if args.list:
        return _list_open()
    if args.list_resolved:
        return _list_resolved()
    if args.unresolve:
        return _unresolve(args.id)
    return _resolve(args.id, args.note)


if __name__ == "__main__":
    sys.exit(main())
