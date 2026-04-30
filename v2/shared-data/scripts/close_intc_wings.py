#!/usr/bin/env python3
"""
Manual close for A011 orphan INTC wings.

The A011 INTC iron condor was stopped out on 2026-04-29 but position_monitor
only matched the 2 short legs (89P, 100C). The 2 long protective wings
(88P, 101C, May 8 expiry) remained open in IBKR with no matching journal entry.

This script closes both legs via broker and appends a manual-close journal entry.

Usage:
  python3 close_intc_wings.py          # live (run at Monday 9:35 ET)
  python3 close_intc_wings.py --dry-run
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, "/home/trader/QuantAI/v2/shared-data/scripts")

import pathlib as _pl
for _ef in [_pl.Path("/home/trader/QuantAI/.env"), _pl.Path("/root/quantai-v2/.env")]:
    if _ef.exists():
        for _line in _ef.read_text().splitlines():
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.partition("=")
                if not os.environ.get(_k.strip()):
                    os.environ[_k.strip()] = _v.strip()
        break

from broker import get_broker

ET = ZoneInfo("America/New_York")
JOURNAL = Path("/root/quantai-v2/shared-data/journal/paper/trades.jsonl")

WINGS = [
    {"symbol": "INTC260508P00088000", "side": "SELL", "label": "88P", "avg_cost": 335.80},
    {"symbol": "INTC260508C00101000", "side": "SELL", "label": "101C", "avg_cost": 245.80},
]

DRY_RUN = "--dry-run" in sys.argv


def log(msg):
    print(f"[{datetime.now(ET).strftime('%H:%M:%S')}] {msg}")


def next_manual_id() -> str:
    """Find next M### id not already in the journal."""
    used = set()
    try:
        if JOURNAL.exists():
            for line in JOURNAL.read_text().splitlines():
                try:
                    t = json.loads(line)
                    tid = t.get("id", "")
                    if tid.startswith("M"):
                        used.add(tid)
                except Exception:
                    pass
    except PermissionError:
        pass  # journal is root-owned; script runs as root in cron; ok to skip for dry-run
    n = 1
    while f"M{n:03d}" in used:
        n += 1
    return f"M{n:03d}"


def close_wing(broker, leg, dry_run) -> dict:
    log(f"closing {leg['label']} ({leg['symbol']}) ...")
    if dry_run:
        log(f"  [DRY] would submit sell order for {leg['label']}")
        return {"order_id": "DRY_RUN", "status": "dry_run", "avg_fill_price": 0.0}
    result = broker.place_mleg_order(
        [{"symbol": leg["symbol"], "side": leg["side"]}],
        qty=1,
        tif="day",
        client_order_id=f"manual-intc-{leg['label'].lower()}-close",
    )
    if result is None:
        log(f"  ERROR: broker returned None for {leg['label']}")
        return {}
    log(f"  order_id={result.get('order_id')} status={result.get('status')}")
    return result


def write_journal_entry(journal_id, leg, order_result, dry_run):
    now = datetime.now(ET).isoformat()
    entry = {
        "id": journal_id,
        "timestamp": now,
        "mode": "paper",
        "source": "manual",
        "symbol": "INTC",
        "strategy": "iron_condor",
        "legs": [
            {
                "action": "sell",
                "type": leg["label"][-1].lower(),  # "p" or "c"
                "strike": float(leg["label"][:-1]),
                "expiry": "2026-05-08",
                "symbol": leg["symbol"],
            }
        ],
        "qty": 1,
        "status": "CLOSED",
        "close_reason": "manual",
        "notes": "Orphan wings from A011 iron condor partial close",
        "order_id": order_result.get("order_id"),
        "fill_status": order_result.get("status", "?"),
        "avg_fill_price": order_result.get("avg_fill_price", 0.0),
        "entry_avg_cost": leg["avg_cost"],
        "parent_trade_id": "A011",
    }
    if dry_run:
        log(f"  [DRY] would append journal entry {journal_id}: {json.dumps(entry)[:120]}")
        return
    with open(JOURNAL, "a") as f:
        f.write(json.dumps(entry) + "\n")
    log(f"  appended journal entry {journal_id}")


def main():
    log(f"close_intc_wings start{' [DRY-RUN]' if DRY_RUN else ''}")

    broker = get_broker()
    if not broker.connect():
        log("ERROR: broker connect failed")
        sys.exit(1)

    # Verify positions still exist before submitting orders
    if not DRY_RUN:
        positions = broker.get_positions()
        pos_symbols = {p["symbol"] for p in positions}
        for leg in WINGS:
            if leg["symbol"] not in pos_symbols:
                log(f"WARN: {leg['symbol']} not found in IBKR positions — may already be closed")

    for leg in WINGS:
        journal_id = next_manual_id()
        result = close_wing(broker, leg, DRY_RUN)
        write_journal_entry(journal_id, leg, result, DRY_RUN)

    log("done")


if __name__ == "__main__":
    main()
