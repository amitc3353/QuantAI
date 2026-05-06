#!/usr/bin/env python3
"""reconcile_audit.py — Full broker-vs-journal audit (added 2026-05-05).

Read-only script that surfaces every journal-vs-broker mismatch:

  1. True ghosts        — broker has positions with no journal reference
  2. Journal lies       — journal CLOSED but broker still has the legs
  3. Entry phantoms     — journal OPEN but broker has none of its legs
  4. Quantity mismatch  — journal OPEN with leg qty != broker qty (incl. sign)
  5. Action mismatch    — journal claims SELL but broker is long (or vice versa)

Used as the verification gate before re-enabling trading agent crons.

Exit code:
  0 — clean (no mismatches across any of the 5 modes)
  1 — at least one mismatch found

Usage:
  sudo python3 reconcile_audit.py
  sudo python3 reconcile_audit.py --json    # machine-readable output
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

SCRIPT_DIR = Path("/home/trader/QuantAI/v2/shared-data/scripts")
JOURNAL = Path("/root/quantai-v2/shared-data/journal/paper/trades.jsonl")

# Auto-load .env
for ef in [Path("/home/trader/QuantAI/.env"), Path("/root/quantai-v2/.env")]:
    if ef.exists():
        for line in ef.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                if not os.environ.get(k.strip()):
                    os.environ[k.strip()] = v.strip()
        break

sys.path.insert(0, str(SCRIPT_DIR))


def load_journal() -> list:
    if not JOURNAL.exists():
        return []
    out = []
    for line in JOURNAL.read_text().splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def expected_qty_from_leg(leg: dict, qty_per_contract: int) -> int:
    """Convert journal leg + contract qty into expected broker position qty (signed)."""
    action = (leg.get("action") or leg.get("side") or "").lower()
    if action in ("sell", "short"):
        return -qty_per_contract
    if action in ("buy", "long"):
        return qty_per_contract
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = ap.parse_args()

    journal = load_journal()
    open_trades = [t for t in journal if t.get("status") == "OPEN"]

    try:
        from broker import get_broker
    except Exception as e:
        print(f"ERROR: cannot import broker: {e}", file=sys.stderr)
        return 1
    broker = get_broker()
    try:
        positions = broker.get_positions() if hasattr(broker, "get_positions") else []
    except Exception as e:
        print(f"ERROR: broker.get_positions failed: {e}", file=sys.stderr)
        return 1

    # Build broker symbol → qty map (only non-zero positions)
    broker_qty: dict = {}
    for p in positions:
        sym = (p.get("symbol") or "").replace(" ", "").upper()
        qty = int(p.get("qty") or 0)
        if sym and qty != 0:
            broker_qty[sym] = qty

    # Build maps of journal references
    open_leg_owners: dict = defaultdict(list)   # occ → [trade_ids that own this leg]
    closed_occ_to_tid: dict = {}                # occ → most recent closed tid
    journal_expected: dict = defaultdict(int)   # occ → net expected qty from open trades

    # Sort closed trades by close time desc to find most-recent closer
    def _close_ts(t):
        return t.get("close_timestamp") or t.get("exit_timestamp") or ""
    for t in sorted(journal, key=_close_ts, reverse=True):
        if t.get("status") != "CLOSED":
            continue
        tid = t.get("trade_id") or t.get("id")
        if not tid:
            continue
        for leg in t.get("legs") or []:
            sym = (leg.get("symbol") or "").replace(" ", "").upper()
            if sym and sym not in closed_occ_to_tid:
                closed_occ_to_tid[sym] = tid

    for t in open_trades:
        tid = t.get("trade_id") or t.get("id")
        if not tid:
            continue
        qty_per_contract = int(t.get("qty") or 1)
        for leg in t.get("legs") or []:
            sym = (leg.get("symbol") or "").replace(" ", "").upper()
            if not sym:
                continue
            open_leg_owners[sym].append(tid)
            journal_expected[sym] += expected_qty_from_leg(leg, qty_per_contract)

    # ── Detect each failure mode ─────────────────────────────────────────
    findings = {
        "true_ghosts": [],          # (sym, qty) — broker has, no journal at all
        "journal_lies": [],         # (sym, qty, claimed_closed_tid)
        "entry_phantoms": [],       # tid — journal OPEN but no legs on broker
        "qty_mismatches": [],       # (sym, expected, actual, owners)
        "action_mismatches": [],    # (sym, expected, actual, owners) — sign flip
    }

    # 1. True ghosts + 2. Journal lies (broker has, journal-OPEN doesn't)
    for sym, qty in broker_qty.items():
        if sym in open_leg_owners:
            continue  # legitimate — referenced by an open trade
        # Not in open trades — is it in a closed trade? (journal lie) or unknown? (true ghost)
        if sym in closed_occ_to_tid:
            findings["journal_lies"].append({
                "symbol": sym,
                "broker_qty": qty,
                "claimed_closed_tid": closed_occ_to_tid[sym],
            })
        else:
            findings["true_ghosts"].append({"symbol": sym, "broker_qty": qty})

    # 3. Entry phantoms — open journal entry, none of its legs on broker
    by_tid_legs: dict = defaultdict(set)
    for t in open_trades:
        tid = t.get("trade_id") or t.get("id")
        for leg in t.get("legs") or []:
            sym = (leg.get("symbol") or "").replace(" ", "").upper()
            if sym:
                by_tid_legs[tid].add(sym)
    for tid, legs in by_tid_legs.items():
        if not (legs & set(broker_qty.keys())):
            findings["entry_phantoms"].append({"trade_id": tid, "expected_legs": sorted(legs)})

    # 4 + 5. Quantity / action mismatches on open trades that ARE on broker
    for sym, expected in journal_expected.items():
        actual = broker_qty.get(sym, 0)
        if expected == actual:
            continue
        owners = open_leg_owners.get(sym, [])
        if expected != 0 and actual != 0 and (expected > 0) != (actual > 0):
            findings["action_mismatches"].append({
                "symbol": sym, "expected_qty": expected, "actual_qty": actual,
                "owners": owners,
            })
        else:
            findings["qty_mismatches"].append({
                "symbol": sym, "expected_qty": expected, "actual_qty": actual,
                "owners": owners,
            })

    # ── Output ───────────────────────────────────────────────────────────
    total = sum(len(v) for v in findings.values())

    if args.json:
        print(json.dumps({
            "summary": {k: len(v) for k, v in findings.items()},
            "total_mismatches": total,
            "findings": findings,
        }, indent=2))
        return 0 if total == 0 else 1

    print()
    print("═" * 70)
    print("  RECONCILIATION AUDIT — broker vs journal")
    print("═" * 70)
    print(f"  Open journal trades: {len(open_trades)}")
    print(f"  Broker positions (non-zero): {len(broker_qty)}")
    print()

    if total == 0:
        print("  ✅ CLEAN — no mismatches across any of the 5 detection modes")
        return 0

    print(f"  ❌ {total} MISMATCHES FOUND")
    print()
    if findings["true_ghosts"]:
        print(f"  True ghosts ({len(findings['true_ghosts'])}) — broker has, no journal reference:")
        for g in findings["true_ghosts"]:
            print(f"    {g['symbol']:30s} qty={g['broker_qty']:+d}")
        print()
    if findings["journal_lies"]:
        print(f"  Journal lies ({len(findings['journal_lies'])}) — broker has, journal claims CLOSED:")
        for g in findings["journal_lies"]:
            print(f"    {g['symbol']:30s} qty={g['broker_qty']:+d}  claimed_closed_by={g['claimed_closed_tid']}")
        print()
    if findings["entry_phantoms"]:
        print(f"  Entry phantoms ({len(findings['entry_phantoms'])}) — journal OPEN, no legs on broker:")
        for g in findings["entry_phantoms"]:
            print(f"    trade_id={g['trade_id']:8s} expected_legs={g['expected_legs']}")
        print()
    if findings["qty_mismatches"]:
        print(f"  Qty mismatches ({len(findings['qty_mismatches'])}) — same direction, wrong qty:")
        for g in findings["qty_mismatches"]:
            print(f"    {g['symbol']:30s} expected={g['expected_qty']:+d}  actual={g['actual_qty']:+d}  owners={g['owners']}")
        print()
    if findings["action_mismatches"]:
        print(f"  Action mismatches ({len(findings['action_mismatches'])}) — sign-flipped (long/short reversed):")
        for g in findings["action_mismatches"]:
            print(f"    {g['symbol']:30s} expected={g['expected_qty']:+d}  actual={g['actual_qty']:+d}  owners={g['owners']}")
        print()

    return 1


if __name__ == "__main__":
    sys.exit(main())
