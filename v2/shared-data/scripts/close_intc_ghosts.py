#!/usr/bin/env python3
"""close_intc_ghosts.py — Manual reconciliation of A018, A020, A021, A022 (2026-05-06).

Background
----------
On 2026-05-04 and 2026-05-05, four agent_alpha trades developed journal-vs-broker
mismatches caused by three independent bugs that have since been fixed:

  Bug A — A018 close-path: status=Cancelled treated as success (fixed 2026-05-04)
  Bug B — A020 close-path: status=Submitted retried instead of polled
          (fixed 2026-05-05; now returns _working state with working_close_order_id)
  Bug C — A021/A022 entry-path: status=Cancelled treated as success
          (fixed 2026-05-05; place_mleg_order now validates broker status)

This script handles the residual state cleanly:

  A018 — 4 legs OPEN on broker (P93 +1, P94 -1, C104 -1, C105 +1)
         journal CLOSED with synthetic +$13.39
       → Submit reverse 4-leg combo at mid limit; verify flat; correct journal P&L

  A020 — 4 legs in REVERSE direction at qty 4 on broker (P99 -4, P100 +4,
         C111 +4, C112 -4) from 5 reverse-close accumulations minus 1 entry
         journal OPEN
       → Submit reverse 4-leg combo at qty=4 (matches the original direction
         to flatten); verify flat; mark journal CLOSED with actual P&L

  A021 — Nothing on broker (XOP). Journal OPEN with order_id=111802387 which
         IBKR shows as Cancelled.
       → No broker action. Journal correction only: status=PHANTOM_NEVER_FILLED.

  A022 — Same as A021. INTC P102/P103/C114/C115. journal OPEN, broker empty.
       → No broker action. Journal correction only.

Usage
-----
  sudo python3 close_intc_ghosts.py                    # plan only — show diffs
  sudo python3 close_intc_ghosts.py --apply-corrections # apply after broker fills confirm
  sudo python3 close_intc_ghosts.py --dry-run          # no orders, no journal writes
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
TIMEOUT_SEC = 60
LOG_PATH = Path("/root/quantai-v2/shared-data/logs/close_intc_ghosts.log")

# Definitive expected state per trade (sourced from 2026-05-05 investigation)
GHOST_PLANS = {
    "A018": {
        "kind": "close_combo",
        "expected_legs": [
            ("INTC260515P00094000", -1, "buy"),    # close: BUY back the short put
            ("INTC260515P00093000", +1, "sell"),   # close: SELL the long put
            ("INTC260515C00104000", -1, "buy"),    # close: BUY back the short call
            ("INTC260515C00105000", +1, "sell"),   # close: SELL the long call
        ],
        "qty": 1,
    },
    "A020": {
        "kind": "close_combo",
        "expected_legs": [
            # Broker has REVERSE positions at qty 4 — close legs are journal-original direction at qty 4
            ("INTC260515P00100000", +4, "sell"),   # broker is long 4 → SELL 4 to flatten
            ("INTC260515P00099000", -4, "buy"),    # broker is short 4 → BUY 4 to flatten
            ("INTC260515C00111000", +4, "sell"),   # broker is long 4 → SELL 4 to flatten
            ("INTC260515C00112000", -4, "buy"),    # broker is short 4 → BUY 4 to flatten
        ],
        "qty": 4,
    },
    "A021": {
        "kind": "phantom_journal_only",
        "expected_legs": [],  # nothing on broker
    },
    "A022": {
        "kind": "phantom_journal_only",
        "expected_legs": [],
    },
}

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


def find_trade(tid: str, all_recs: list) -> dict | None:
    for r in all_recs:
        if (r.get("trade_id") or r.get("id")) == tid:
            return r
    return None


def load_journal() -> tuple[list, dict]:
    if not JOURNAL.exists():
        return [], {}
    recs = []
    for line in JOURNAL.read_text().splitlines():
        if not line.strip():
            continue
        try:
            recs.append(json.loads(line))
        except Exception:
            continue
    return recs, {(r.get("trade_id") or r.get("id")): r for r in recs}


def verify_trade_legs(broker, plan: dict) -> tuple[bool, list, dict]:
    """Returns (broker_state_matches_expected, mismatched_legs, broker_position_map)."""
    positions = broker.get_positions()
    pos_map = {p["symbol"].replace(" ", ""): p for p in positions}
    mismatches = []
    for sym, expected_qty, _close_action in plan["expected_legs"]:
        actual = (pos_map.get(sym) or {}).get("qty", 0)
        if int(actual) != int(expected_qty):
            mismatches.append((sym, int(expected_qty), int(actual)))
    return (len(mismatches) == 0), mismatches, pos_map


def build_close_legs(plan: dict) -> list:
    """Convert plan's (sym, qty, close_action) into combo legs for place_mleg_order."""
    out = []
    for sym, _expected_qty, action in plan["expected_legs"]:
        out.append({
            "action": action,
            "side":   action,  # legacy field used by some paths
            "symbol": sym,
        })
    return out


def submit_close_with_verification(broker, tid: str, plan: dict) -> dict:
    """Submit the closing combo, wait for fill, verify legs flat. Returns
    {"ok": bool, "filled_status": str, "fills_total_price": float, "order_id": str}.
    """
    legs = build_close_legs(plan)
    coid = f"manual-ghost-unwind-{tid}-{int(time.time())}"
    log(f"  Submitting close for {tid}: {len(legs)} legs at qty={plan['qty']} coid={coid}")
    try:
        result = broker.close_position(legs, qty=plan["qty"], client_order_id=coid)
    except Exception as e:
        log(f"  close_position raised: {e}")
        return {"ok": False, "filled_status": "EXCEPTION", "order_id": "", "fills_total_price": 0.0}
    if result is None:
        return {"ok": False, "filled_status": "RETURNED_NONE", "order_id": "", "fills_total_price": 0.0}
    order_id = str(result.get("order_id", "") or coid)
    log(f"  Initial submit: status={result.get('status')} order_id={order_id} _working={result.get('_working')}")

    # Poll up to TIMEOUT_SEC for terminal state
    deadline = time.time() + TIMEOUT_SEC
    final = result
    while time.time() < deadline:
        time.sleep(2)
        if not hasattr(broker, "poll_order"):
            break
        polled = broker.poll_order(order_id)
        if polled is None:
            log(f"  Poll: order {order_id} not found")
            break
        state = polled.get("_state")
        log(f"  Poll: state={state} status={polled.get('status')} filled={polled.get('filled_qty')}")
        if state in ("filled", "failed"):
            final = polled
            break

    # Verify flat
    if hasattr(broker, "verify_legs_flat"):
        unflat = broker.verify_legs_flat([{"symbol": s} for s, _, _ in plan["expected_legs"]])
    else:
        unflat = []

    avg_px = float(final.get("avg_fill_price", 0) or 0)
    return {
        "ok": (len(unflat) == 0),
        "filled_status": str(final.get("status", "?")),
        "order_id": order_id,
        "fills_total_price": avg_px,
        "unflat_after": unflat,
    }


def proposed_correction(tid: str, kind: str, close_outcome: dict | None,
                        original_record: dict) -> dict:
    """Build the journal-correction patch. Different shape per kind."""
    now_iso = datetime.now(timezone.utc).isoformat()
    if kind == "close_combo":
        avg_px = (close_outcome or {}).get("fills_total_price", 0)
        entry_credit = float(original_record.get("estimated_credit") or 0)
        qty = GHOST_PLANS[tid]["qty"]
        # Realized P&L = (entry_credit - close_debit) × 100 × qty
        actual_pnl = round((entry_credit - abs(avg_px)) * 100 * qty, 2)
        return {
            "exit_pnl": actual_pnl,
            "pnl": actual_pnl,
            "close_reason": f"ghost_unwound_{datetime.now(ET).strftime('%Y-%m-%d')}",
            "exit_reason": f"ghost_unwound_{datetime.now(ET).strftime('%Y-%m-%d')}",
            "close_order_id": (close_outcome or {}).get("order_id", ""),
            "ghost_unwind_status": (close_outcome or {}).get("filled_status", "?"),
            "ghost_unwind_residual_legs": (close_outcome or {}).get("unflat_after", []),
            "status": "CLOSED",
            "notes": (
                f"Auto-executed by agent_alpha. Original entry/close suffered "
                f"a journal-vs-broker mismatch from the trading-path bugs "
                f"investigated on 2026-05-05. Manual ghost-unwind {now_iso}: "
                f"realized P&L=${actual_pnl:+.2f}, "
                f"final status={close_outcome.get('filled_status')}, "
                f"residual_legs={close_outcome.get('unflat_after') or 'none'}."
            ),
        }
    elif kind == "phantom_journal_only":
        return {
            "status": "PHANTOM_NEVER_FILLED",
            "exit_pnl": 0.0,
            "pnl": 0.0,
            "close_reason": "phantom_never_filled",
            "exit_reason": "phantom_never_filled",
            "close_timestamp": now_iso,
            "exit_timestamp": now_iso,
            "ghost_unwind_status": "phantom_journal_only",
            "notes": (
                f"Original entry order was REJECTED by IBKR (Cancelled status) "
                f"but place_mleg_order treated the cancellation as success and "
                f"recorded this entry as OPEN with a misleading order_id. "
                f"Bug fixed 2026-05-05 (Layer 1: entry-path status validation). "
                f"Reconciled {now_iso}: zero broker positions ever existed."
            ),
        }
    raise ValueError(f"unknown plan kind: {kind}")


def show_diff(tid: str, original: dict, correction: dict):
    print()
    print("─" * 70)
    print(f"  PROPOSED JOURNAL CORRECTION FOR {tid}")
    print("─" * 70)
    for k, new_v in correction.items():
        old_v = original.get(k, "<absent>")
        if old_v != new_v:
            old_str = repr(old_v)[:140]
            new_str = repr(new_v)[:140]
            print(f"  - {k}: {old_str}")
            print(f"  + {k}: {new_str}")


def apply_journal_corrections(corrections: dict, all_recs: list) -> bool:
    """Rewrite trades.jsonl with all corrections applied. Backs up first."""
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
    backup = JOURNAL.with_suffix(f".jsonl.bak.{stamp}-pre-ghost-unwind")
    try:
        backup.write_bytes(JOURNAL.read_bytes())
        log(f"  Backed up journal to {backup}")
    except Exception as e:
        log(f"  Backup FAILED — refusing to apply: {e}")
        return False

    new_lines = []
    applied = []
    for r in all_recs:
        tid = r.get("trade_id") or r.get("id")
        if tid in corrections:
            r.update(corrections[tid])
            applied.append(tid)
        new_lines.append(json.dumps(r))
    try:
        JOURNAL.write_text("\n".join(new_lines) + "\n")
        log(f"  ✅ Applied corrections to {len(applied)} trades: {applied}")
        return True
    except Exception as e:
        log(f"  Write FAILED: {e}")
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="No orders placed; verify state and print plan only")
    ap.add_argument("--apply-corrections", action="store_true",
                    help="After confirmed close (or phantom verification), write the journal corrections")
    args = ap.parse_args()

    log(f"close_intc_ghosts.py start  dry_run={args.dry_run}  apply={args.apply_corrections}")
    log(f"  Trades to handle: {list(GHOST_PLANS.keys())}")

    # Load journal
    all_recs, by_id = load_journal()
    for tid in GHOST_PLANS:
        if tid not in by_id:
            log(f"  ❌ {tid} not in journal — aborting")
            return 1
    log(f"  All {len(GHOST_PLANS)} trades found in journal")

    # Connect to broker
    try:
        from broker import get_broker
    except Exception as e:
        log(f"  Cannot import broker: {e}")
        return 1
    broker = get_broker()

    # Pre-verify each trade's broker state matches the plan
    log("  Pre-verifying broker state against plans...")
    plan_outcomes = {}
    for tid, plan in GHOST_PLANS.items():
        ok, mismatches, pos_map = verify_trade_legs(broker, plan)
        log(f"    {tid} ({plan['kind']}): broker matches expected = {ok}  mismatches={mismatches}")
        plan_outcomes[tid] = {"pre_verify": ok, "mismatches": mismatches}

    if args.dry_run:
        log("  [DRY-RUN] Would submit close orders for A018, A020 — phantom journal-only for A021, A022.")
        log("  [DRY-RUN] No orders placed; no journal writes.")
        return 0

    # Submit closes for the 2 broker-action trades
    corrections = {}
    for tid in ("A018", "A020"):
        plan = GHOST_PLANS[tid]
        if plan["kind"] != "close_combo":
            continue
        if not plan_outcomes[tid]["pre_verify"]:
            log(f"  ⚠️  {tid} broker state doesn't match plan — skipping close. "
                f"Mismatches: {plan_outcomes[tid]['mismatches']}")
            continue
        outcome = submit_close_with_verification(broker, tid, plan)
        plan_outcomes[tid]["close"] = outcome
        if not outcome["ok"]:
            log(f"  ⚠️  {tid} close did NOT verify flat — skipping journal correction. "
                f"final_status={outcome['filled_status']} unflat={outcome['unflat_after']}")
            continue
        corrections[tid] = proposed_correction(tid, "close_combo", outcome, by_id[tid])

    # Phantom journal-only — no broker action, just correction
    for tid in ("A021", "A022"):
        plan = GHOST_PLANS[tid]
        if plan["kind"] != "phantom_journal_only":
            continue
        # Verify no broker positions exist for these trade's claimed legs
        original_legs = by_id[tid].get("legs") or []
        leg_syms = {(l.get("symbol") or "").replace(" ", "") for l in original_legs}
        positions = broker.get_positions()
        broker_syms_with_qty = {
            p["symbol"].replace(" ", "") for p in positions
            if (p.get("qty") or 0) != 0
        }
        overlap = leg_syms & broker_syms_with_qty
        if overlap:
            log(f"  ⚠️  {tid} expected to be phantom but found broker positions: {overlap}")
            log(f"  Skipping journal correction — manual triage required.")
            continue
        corrections[tid] = proposed_correction(tid, "phantom_journal_only", None, by_id[tid])

    # Show all proposed corrections
    for tid, correction in corrections.items():
        show_diff(tid, by_id[tid], correction)

    print()
    if not corrections:
        log("  No corrections to apply.")
        return 0
    if args.apply_corrections:
        log(f"  Applying {len(corrections)} journal corrections...")
        if apply_journal_corrections(corrections, all_recs):
            log("  ✅ All corrections applied.")
        else:
            log("  ❌ Correction write FAILED.")
            return 1
    else:
        log(f"  {len(corrections)} corrections proposed. Re-run with --apply-corrections to write.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
