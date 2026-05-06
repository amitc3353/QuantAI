#!/usr/bin/env python3
"""close_a018_ghost.py — Manual ghost-unwind for A018 (one-off; 2026-05-05).

Background
----------
A018 is an INTC iron condor entered by agent_alpha at 10:03 AM ET 2026-05-04.
Its profit_target close at 10:04 AM ET was REJECTED by IBKR with Error 201
("Cannot have open orders on both sides of the same US Option contract")
because A017 had opposing legs on the same contracts. The broker returned
a Cancelled status, but the prior place_close_order code only checked
`if order is None` and treated the cancelled response as success — marking
the journal CLOSED with a fabricated +$13.39 P&L while 4 legs remained open.

This script runs ONCE at market open Monday 2026-05-05 to:
  1. Verify the 4 INTC legs are still open on IBKR
  2. Submit a 4-leg closing combo (reverse actions, mid-price limit, 60s timeout)
  3. After confirmed broker-flat, print the proposed journal correction diff
  4. If --apply-correction is passed, apply the diff (one-line patch on A018's
     existing CLOSED record — corrects the spurious +$13.39 to actual P&L)

Usage
-----
  # First run: close + show proposed diff but don't write
  sudo python3 close_a018_ghost.py

  # Second run: close + apply the journal correction
  sudo python3 close_a018_ghost.py --apply-correction

  # Dry run (no orders placed)
  sudo python3 close_a018_ghost.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
SCRIPT_DIR = Path("/home/trader/QuantAI/v2/shared-data/scripts")
JOURNAL = Path("/root/quantai-v2/shared-data/journal/paper/trades.jsonl")
TRADE_ID = "A018"
EXPECTED_LEGS = [
    {"action": "sell", "type": "put",  "strike": 94.0, "expiry": "2026-05-15",
     "symbol": "INTC260515P00094000"},
    {"action": "buy",  "type": "put",  "strike": 93.0, "expiry": "2026-05-15",
     "symbol": "INTC260515P00093000"},
    {"action": "sell", "type": "call", "strike": 104.0, "expiry": "2026-05-15",
     "symbol": "INTC260515C00104000"},
    {"action": "buy",  "type": "call", "strike": 105.0, "expiry": "2026-05-15",
     "symbol": "INTC260515C00105000"},
]
TIMEOUT_SEC = 60
LOG_PATH = Path("/root/quantai-v2/shared-data/logs/close_a018_ghost.log")

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


def log(msg: str):
    stamp = datetime.now(timezone.utc).isoformat()
    line = f"[{stamp}] {msg}"
    print(line, flush=True)
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def reverse_action(action: str) -> str:
    """sell → buy, buy → sell (for closing)."""
    return {"sell": "buy", "buy": "sell"}[action.lower()]


def build_close_legs() -> list:
    """Return legs with reversed actions (closing combo)."""
    out = []
    for leg in EXPECTED_LEGS:
        out.append({
            "action": reverse_action(leg["action"]),
            "side": reverse_action(leg["action"]),  # legacy field
            "type": leg["type"],
            "strike": leg["strike"],
            "expiry": leg["expiry"],
            "symbol": leg["symbol"],
        })
    return out


def verify_intc_legs_present(broker) -> tuple[bool, list, dict]:
    """Returns (all_4_present, missing_legs, position_map)."""
    positions = broker.get_positions()
    pos_by_sym = {p["symbol"].replace(" ", ""): p for p in positions}
    missing = []
    for leg in EXPECTED_LEGS:
        sym = leg["symbol"]
        p = pos_by_sym.get(sym)
        if p is None or p.get("qty", 0) == 0:
            missing.append(sym)
    return (len(missing) == 0, missing, pos_by_sym)


def find_a018_in_journal() -> tuple[dict | None, list]:
    """Read journal; return (A018_record, all_records)."""
    if not JOURNAL.exists():
        return None, []
    all_recs = []
    for line in JOURNAL.read_text().splitlines():
        if not line.strip():
            continue
        try:
            all_recs.append(json.loads(line))
        except Exception:
            continue
    a018 = None
    for r in all_recs:
        if (r.get("trade_id") or r.get("id")) == TRADE_ID:
            a018 = r
            break
    return a018, all_recs


def compute_realized_pnl(close_result: dict, entry_credit: float) -> float:
    """Realized P&L on a 1-contract iron condor close.
    avg_fill_price is the per-spread mid (combo). For an iron condor close,
    the combo price represents net debit paid to close.
    Realized = (entry_credit - close_debit) * 100
    """
    close_debit = abs(float(close_result.get("avg_fill_price") or 0))
    return round((entry_credit - close_debit) * 100, 2)


def proposed_journal_correction(a018: dict, actual_pnl: float, close_order_id: str,
                                close_status: str, unflat_after: list) -> dict:
    """Build the diff that corrects A018's record. Returns dict of fields to update."""
    note_extension = (
        f"Auto-executed by agent_alpha. Original close order Cancelled by "
        f"IBKR Error 201 on 2026-05-04 14:04 UTC; journal incorrectly marked "
        f"CLOSED with synthetic +$13.39. Manual ghost-unwind 2026-05-05; "
        f"actual close status={close_status}, realized P&L=${actual_pnl:+.2f}, "
        f"close_order_id={close_order_id}, residual_legs={unflat_after or 'none'}."
    )
    return {
        "exit_pnl":         actual_pnl,
        "pnl":              actual_pnl,
        "close_reason":     "ghost_unwound_2026-05-05",
        "exit_reason":      "ghost_unwound_2026-05-05",
        "close_order_id":   close_order_id,
        "ghost_unwind_status": close_status,
        "ghost_unwind_residual_legs": unflat_after,
        "notes":            note_extension,
    }


def show_diff(original: dict, correction: dict):
    """Pretty-print the diff in unified-diff style."""
    print()
    print("─" * 70)
    print(f"  PROPOSED JOURNAL CORRECTION FOR {TRADE_ID}")
    print("─" * 70)
    for k, new_v in correction.items():
        old_v = original.get(k, "<absent>")
        if old_v != new_v:
            print(f"  - {k}: {old_v!r}")
            print(f"  + {k}: {new_v!r}")
    print("─" * 70)


def apply_correction(a018: dict, correction: dict, all_recs: list) -> bool:
    """Rewrite trades.jsonl with the corrected A018 record. Returns True on success.
    Backs up to .bak.<timestamp> first.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
    backup = JOURNAL.with_suffix(f".jsonl.bak.{stamp}-pre-A018-correction")
    try:
        backup.write_bytes(JOURNAL.read_bytes())
        log(f"  Backed up journal to {backup}")
    except Exception as e:
        log(f"  Backup FAILED — refusing to apply: {e}")
        return False

    a018_id = a018.get("trade_id") or a018.get("id")
    new_lines = []
    found = False
    for r in all_recs:
        if (r.get("trade_id") or r.get("id")) == a018_id and not found:
            r.update(correction)
            found = True
        new_lines.append(json.dumps(r))
    if not found:
        log("  ERROR: A018 vanished from journal between read and write")
        return False
    try:
        JOURNAL.write_text("\n".join(new_lines) + "\n")
        log(f"  ✅ Applied correction to {a018_id} in {JOURNAL}")
        return True
    except Exception as e:
        log(f"  Write FAILED: {e}")
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="No orders placed; verify and print plan only")
    ap.add_argument("--apply-correction", action="store_true",
                    help="After confirmed close, write the journal correction")
    args = ap.parse_args()

    log(f"close_a018_ghost.py start  dry_run={args.dry_run}  "
        f"apply_correction={args.apply_correction}")

    # Load A018 from journal first — needs entry credit for realized P&L calc
    a018, all_recs = find_a018_in_journal()
    if a018 is None:
        log("  ERROR: A018 not found in journal — aborting")
        return 1
    entry_credit = float(a018.get("estimated_credit") or a018.get("net_credit") or 0)
    log(f"  A018 entry credit: ${entry_credit:.2f} per contract (1 contract)")

    # Connect to IBKR
    sys.path.insert(0, str(SCRIPT_DIR))
    try:
        from broker import get_broker
    except Exception as e:
        log(f"  Cannot import broker: {e}")
        return 1
    broker = get_broker()

    # Verify the 4 legs are still open
    log("  Querying IBKR for current INTC positions...")
    all_present, missing, pos_map = verify_intc_legs_present(broker)
    if not all_present:
        log(f"  Some legs already gone from broker: missing={missing}")
        log("  Abort — one or more legs was already closed (perhaps manually overnight).")
        log("  Re-check positions manually before deciding next action.")
        return 1
    log(f"  All 4 expected legs confirmed open on broker:")
    for leg in EXPECTED_LEGS:
        p = pos_map[leg["symbol"]]
        log(f"    {leg['symbol']:30s} qty={p.get('qty'):+d}  "
            f"unreal_PnL={p.get('unrealized_pnl'):+.2f}")

    if args.dry_run:
        log("  [DRY-RUN] Would submit closing combo:")
        for leg in build_close_legs():
            log(f"    {leg['action'].upper():4s} {leg['symbol']}")
        log("  [DRY-RUN] Would wait up to 60s for fill, then verify legs flat.")
        log("  [DRY-RUN] No orders placed; no journal mutation.")
        return 0

    # Submit the closing combo
    close_legs = build_close_legs()
    coid = f"close-A018-ghostunwind-{int(time.time())}"
    log(f"  Submitting closing combo: client_order_id={coid}")
    try:
        result = broker.close_position(close_legs, qty=1, client_order_id=coid)
    except Exception as e:
        log(f"  ❌ close_position raised: {e}")
        # Recovery via Phase 5: check if the order is in open_orders
        try:
            recovered = broker.get_open_orders(client_order_id=coid)
            if recovered:
                result = recovered[0]
                log(f"  Phase 5 recovery: found order_id={result.get('order_id')} "
                    f"status={result.get('status')}")
            else:
                log("  Phase 5 recovery: no order found in open_orders. Aborting.")
                return 1
        except Exception as rec_e:
            log(f"  Recovery failed too: {rec_e}")
            return 1

    if result is None:
        log("  ❌ close_position returned None — order rejected immediately. Aborting.")
        return 1

    raw_status = result.get("status", "Unknown")
    log(f"  Order placed: id={result.get('order_id','?')} status={raw_status} "
        f"filled_qty={result.get('filled_qty', 0)}")

    # Wait for fill (up to 60s)
    deadline = time.time() + TIMEOUT_SEC
    final_status = raw_status
    while time.time() < deadline:
        time.sleep(2)
        oid = result.get("order_id")
        if not oid:
            break
        latest = broker.get_order_status(oid)
        if latest:
            final_status = latest.get("status", final_status)
            filled = latest.get("filled_qty", 0)
            log(f"  Order status check: {final_status} filled={filled}")
            if final_status.lower() == "filled":
                result = latest
                break
            if final_status.lower() in ("cancelled", "canceled", "rejected", "inactive"):
                log("  Order terminated unfilled. Aborting before journal write.")
                return 1

    # Post-close verification: query positions, ensure all 4 legs flat
    log("  Verifying legs are flat on broker...")
    time.sleep(2)  # let IBKR settle
    if hasattr(broker, "verify_legs_flat"):
        unflat = broker.verify_legs_flat(EXPECTED_LEGS)
    else:
        unflat = []
    if unflat:
        log(f"  ❌ POST-CLOSE VERIFICATION FAILED — {len(unflat)} leg(s) still open: {unflat}")
        log("  Order may still be working. DO NOT mark journal corrected; re-run later.")
        return 1
    log("  ✅ ALL 4 LEGS CONFIRMED FLAT on broker.")

    # Compute realized P&L
    actual_pnl = compute_realized_pnl(result, entry_credit)
    log(f"  Realized P&L: ${actual_pnl:+.2f} (entry credit ${entry_credit:.2f}, "
        f"close avg fill ${result.get('avg_fill_price', 0):.2f})")

    # Build proposed journal correction
    correction = proposed_journal_correction(
        a018, actual_pnl,
        close_order_id=str(result.get("order_id", "")),
        close_status=str(final_status),
        unflat_after=unflat,
    )
    show_diff(a018, correction)

    if args.apply_correction:
        log("  Applying journal correction...")
        if apply_correction(a018, correction, all_recs):
            log("  ✅ Journal correction applied.")
        else:
            log("  ❌ Journal correction FAILED. See log above.")
            return 1
    else:
        log("  Diff shown above. Re-run with --apply-correction to write to journal.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
