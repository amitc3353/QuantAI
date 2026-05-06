#!/usr/bin/env python3
"""scheduled_intc_close_attempt.py — one-shot Option B close attempt for A018 + A020.

Scheduled via `at` to fire tomorrow morning (2026-05-07 09:31 ET = 13:31 UTC)
when the operator is mobile and unavailable to babysit the close manually.

Intent
------
- Probe the IBKR option chain briefly to wake the paper-data feed.
- Submit two LimitOrder combos:
    * A018 close (qty=1) at limit  +0.85  (max debit; mid ~0.685, worst case 1.06)
    * A020 close (qty=4) at limit  -0.50  (combo BUY w/ negative limit = require credit ≥ 0.50)
- Poll up to 60 seconds for both to reach a terminal state.
- If BOTH filled cleanly: cancel any working orders, run close_intc_ghosts.py
  --apply-corrections to write journal corrections, run reconcile_audit.py,
  post Discord summary.
- If either DID NOT fill within 60s: cancel everything, write nothing, post
  Discord "no fill — Option C committed; A018/A020 hold to May 15."

This is a one-shot tool. It is NOT registered in cron. Operator schedules
it via `at` for a specific moment. If the broker state has already changed
when it fires, it logs and exits cleanly.

All actions logged to /root/quantai-v2/shared-data/logs/scheduled_intc_close.log.
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

LOG_PATH = Path("/root/quantai-v2/shared-data/logs/scheduled_intc_close.log")
SCRIPT_DIR = Path("/home/trader/QuantAI/v2/shared-data/scripts")
JOURNAL = Path("/root/quantai-v2/shared-data/journal/paper/trades.jsonl")

# Per-trade close plans (mirror of close_intc_ghosts.py GHOST_PLANS)
PLANS = {
    "A018": {
        "legs": [
            ("INTC260515P00094000", "buy"),
            ("INTC260515P00093000", "sell"),
            ("INTC260515C00104000", "buy"),
            ("INTC260515C00105000", "sell"),
        ],
        "qty": 1,
        "limit_price": 0.85,
        "expected_broker_qty": {
            "INTC260515P00094000": -1, "INTC260515P00093000": +1,
            "INTC260515C00104000": -1, "INTC260515C00105000": +1,
        },
    },
    "A020": {
        "legs": [
            ("INTC260515P00100000", "sell"),
            ("INTC260515P00099000", "buy"),
            ("INTC260515C00111000", "sell"),
            ("INTC260515C00112000", "buy"),
        ],
        "qty": 4,
        "limit_price": -0.50,
        "expected_broker_qty": {
            "INTC260515P00100000": +4, "INTC260515P00099000": -4,
            "INTC260515C00111000": +4, "INTC260515C00112000": -4,
        },
    },
}

POLL_SECONDS = 60


def setup_logging():
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(message)s",
        handlers=[
            logging.FileHandler(LOG_PATH, mode="a"),
            logging.StreamHandler(),
        ],
    )


def load_env():
    for ef in [Path("/home/trader/QuantAI/.env"), Path("/root/quantai-v2/.env")]:
        if not ef.exists():
            continue
        for line in ef.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                if not os.environ.get(k.strip()):
                    os.environ[k.strip()] = v.strip()
        return


def post_discord(message: str):
    """Post to #system-health. Silent if Discord not reachable."""
    try:
        sys.path.insert(0, str(SCRIPT_DIR))
        from _discord import post_to_channel
        channel = os.environ.get("DISCORD_CHANNEL_SYSTEM_HEALTH") \
            or os.environ.get("DISCORD_CHANNEL_ALERTS")
        if channel:
            post_to_channel(channel, message)
    except Exception as e:
        logging.warning("Discord post failed: %s", e)


def pre_verify_broker_state(broker) -> tuple[bool, list]:
    """Confirm broker holds the 8 expected legs before submitting any orders."""
    positions = {p["symbol"].replace(" ", ""): int(p.get("qty") or 0)
                 for p in broker.get_positions()}
    mismatches = []
    for tid, plan in PLANS.items():
        for sym, expected in plan["expected_broker_qty"].items():
            actual = positions.get(sym, 0)
            if actual != expected:
                mismatches.append((tid, sym, expected, actual))
    return (len(mismatches) == 0), mismatches


def submit_combo(ib, plan: dict, tid: str):
    """Build + submit a single combo BUY LimitOrder. Returns the ib_insync Trade or None."""
    from ib_insync import Bag, ComboLeg, LimitOrder, Option
    from _broker_ibkr import _parse_occ

    combo_legs = []
    for sym, action in plan["legs"]:
        spec = _parse_occ(sym)
        contract = Option(
            symbol=spec.root, lastTradeDateOrContractMonth=spec.expiry,
            strike=spec.strike, right=spec.right,
            exchange="SMART", currency="USD",
        )
        qualified = ib.qualifyContracts(contract)
        if not qualified:
            logging.error("  ❌ qualifyContracts failed for %s (trade %s)", sym, tid)
            return None
        qc = qualified[0]
        combo_legs.append(ComboLeg(
            conId=qc.conId, ratio=1, action=action.upper(),
            exchange=qc.exchange or "SMART",
        ))
    bag = Bag(symbol="INTC", exchange="SMART", currency="USD")
    bag.secType = "BAG"
    bag.comboLegs = combo_legs
    coid = f"sched-close-{tid}-{int(time.time())}"
    order = LimitOrder("BUY", plan["qty"], plan["limit_price"])
    order.tif = "DAY"
    order.orderRef = coid
    order.smartComboRoutingParams = []
    try:
        trade = ib.placeOrder(bag, order)
        logging.info("  submitted %s: order_id=%d ref=%s limit=%.2f qty=%d",
                     tid, trade.order.orderId, coid,
                     plan["limit_price"], plan["qty"])
        return trade
    except Exception as e:
        logging.error("  placeOrder for %s raised: %s", tid, e)
        return None


def cancel_all_scheduled(ib):
    """Best-effort cancel of any open orders we just created."""
    cancelled = []
    for t in ib.openTrades():
        ref = t.order.orderRef or ""
        if ref.startswith("sched-close-"):
            try:
                ib.cancelOrder(t.order)
                cancelled.append(t.order.orderId)
            except Exception:
                pass
    if cancelled:
        ib.sleep(3)
    return cancelled


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="Pre-verify only; don't submit any orders.")
    args = ap.parse_args()

    setup_logging()
    load_env()
    logging.info("scheduled_intc_close_attempt.py start  dry_run=%s", args.dry_run)

    sys.path.insert(0, str(SCRIPT_DIR))
    try:
        from broker import get_broker
    except Exception as e:
        logging.error("cannot import broker: %s", e)
        post_discord(f"🔴 scheduled_intc_close: import failed — {e}")
        return 1

    broker = get_broker()
    # Force connect via a positions read
    broker.get_open_orders()
    ib = getattr(broker, "_ib", None)
    if ib is None or not ib.isConnected():
        logging.error("broker not connected")
        post_discord("🔴 scheduled_intc_close: broker not connected — Option C path stays in effect")
        return 1

    ok, mismatches = pre_verify_broker_state(broker)
    if not ok:
        logging.warning("broker state has changed since plan was made; mismatches: %s",
                        mismatches)
        post_discord(
            "🟡 scheduled_intc_close: broker state changed unexpectedly "
            f"({len(mismatches)} leg mismatches) — skipping; manual review required"
        )
        return 0

    logging.info("Broker state matches expected — 8 INTC legs present")

    if args.dry_run:
        logging.info("[DRY-RUN] Would submit two combos at limits "
                     "A018 +%.2f, A020 %+.2f", PLANS["A018"]["limit_price"],
                     PLANS["A020"]["limit_price"])
        return 0

    # Probe quotes briefly to warm the data feed
    logging.info("Warming quote feed (5s)...")
    for sym, _ in PLANS["A018"]["legs"] + PLANS["A020"]["legs"]:
        try:
            broker.get_option_quote(sym)
        except Exception:
            pass
    ib.sleep(5)

    # Submit both combos
    trades = {}
    for tid, plan in PLANS.items():
        trade = submit_combo(ib, plan, tid)
        if trade is not None:
            trades[tid] = trade

    if len(trades) != 2:
        logging.error("Only %d/2 combos submitted; aborting", len(trades))
        cancel_all_scheduled(ib)
        post_discord("🔴 scheduled_intc_close: failed to submit both combos — Option C remains in effect")
        return 1

    # Poll
    deadline = time.time() + POLL_SECONDS
    last_status = {}
    while time.time() < deadline:
        ib.sleep(2)
        all_terminal = True
        for tid, trade in trades.items():
            s = trade.orderStatus.status
            f = int(trade.orderStatus.filled or 0)
            if last_status.get(tid) != (s, f):
                logging.info("  %s: status=%s filled=%d/%d",
                             tid, s, f, PLANS[tid]["qty"])
                last_status[tid] = (s, f)
            if s.lower() not in ("filled", "cancelled", "rejected", "inactive"):
                all_terminal = False
        if all_terminal:
            break

    # Inspect terminal state
    filled = {}
    for tid, trade in trades.items():
        s = trade.orderStatus.status
        f = int(trade.orderStatus.filled or 0)
        avg = float(trade.orderStatus.avgFillPrice or 0.0)
        logging.info("  final %s: status=%s filled=%d/%d avg=%.4f",
                     tid, s, f, PLANS[tid]["qty"], avg)
        if s.lower() == "filled":
            filled[tid] = {"avg_price": avg, "order_id": trade.order.orderId}

    # Always cancel any leftover working orders
    cancelled = cancel_all_scheduled(ib)
    if cancelled:
        logging.info("Cancelled %d still-working orders: %s", len(cancelled), cancelled)

    if len(filled) == 2:
        # Verify legs flat
        unflat_a018 = broker.verify_legs_flat(
            [{"symbol": s} for s, _ in PLANS["A018"]["legs"]])
        unflat_a020 = broker.verify_legs_flat(
            [{"symbol": s} for s, _ in PLANS["A020"]["legs"]])
        if unflat_a018 or unflat_a020:
            logging.error("Both filled but verify_legs_flat shows residue: "
                          "A018=%s A020=%s", unflat_a018, unflat_a020)
            post_discord(
                "🔴 scheduled_intc_close: orders FILLED but verify_legs_flat shows residue. "
                "Manual reconciliation required before re-enabling crons."
            )
            return 1

        # Apply journal corrections via close_intc_ghosts.py
        logging.info("Both combos FILLED. Running close_intc_ghosts.py --apply-corrections")
        import subprocess
        rc = subprocess.run(
            ["sudo", "python3",
             str(SCRIPT_DIR / "close_intc_ghosts.py"), "--apply-corrections"],
            capture_output=True, text=True, timeout=120,
        )
        logging.info("close_intc_ghosts.py exit=%d", rc.returncode)
        if rc.stdout: logging.info("stdout: %s", rc.stdout[-2000:])
        if rc.stderr: logging.warning("stderr: %s", rc.stderr[-2000:])

        # Run reconcile_audit
        rc2 = subprocess.run(
            ["sudo", "python3", str(SCRIPT_DIR / "reconcile_audit.py")],
            capture_output=True, text=True, timeout=60,
        )
        clean = (rc2.returncode == 0)
        logging.info("reconcile_audit exit=%d (%s)",
                     rc2.returncode, "CLEAN" if clean else "MISMATCHES")

        msg = (
            f"🟢 scheduled_intc_close: BOTH FILLED at "
            f"A018={filled['A018']['avg_price']:+.4f}, "
            f"A020={filled['A020']['avg_price']:+.4f}. "
            f"reconcile_audit: {'CLEAN' if clean else 'MISMATCH — review needed'}."
        )
        post_discord(msg)
        return 0 if clean else 1

    # Partial or no fill — Option C path
    n = len(filled)
    if n == 0:
        msg = ("🟡 scheduled_intc_close: NO FILL within 60s — "
               "Option C committed. A018/A020 hold to 2026-05-15 expiry.")
    else:
        # Asymmetric fill is unusual; cancel and alert loudly
        partial = list(filled.keys())[0]
        msg = (f"🔴 scheduled_intc_close: ASYMMETRIC fill — only {partial} filled, "
               f"the other did not. Manual review required immediately.")
    logging.info(msg)
    post_discord(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
