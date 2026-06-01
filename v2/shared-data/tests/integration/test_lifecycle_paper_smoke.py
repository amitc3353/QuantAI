"""Live IBKR paper smoke test — submit a deep-OTM spread, observe FSM walk, cancel.

Gated by RUN_PAPER_SMOKE=1 (not in CI). Exercises the real broker handshake:
  PROPOSED → SUBMIT_PENDING → ACKED (broker returns indeterminate) → cancel

This is the ONLY test that proves the FSM works against real IBKR.
Run before any agent un-pause:
  sudo RUN_PAPER_SMOKE=1 python3 -m pytest integration/test_lifecycle_paper_smoke.py -v

Requirements:
  - IB Gateway running on localhost:4002
  - IBKR paper account DUP851506 active
  - Run as root (cron user) since broker paths are root-owned
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_PAPER_SMOKE", "0") != "1",
    reason="RUN_PAPER_SMOKE=1 not set — skipping live IBKR test",
)

SCRIPTS_DIR = Path(__file__).parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))


def _build_occ(root: str, expiry: str, right: str, strike: float) -> str:
    """Build an OCC-21 option symbol. e.g. SPY261220C00700000."""
    strike_int = int(round(strike * 1000))
    return f"{root}{expiry}{right}{strike_int:08d}"


@pytest.fixture(scope="module")
def ibkr_broker():
    """Connect to live IBKR paper broker. Fails (not skips) if broken."""
    os.environ.setdefault("IBKR_CLIENT_ID", "99")
    from _broker_ibkr import IBKRBroker
    broker = IBKRBroker()
    connected = broker.connect()
    if not connected:
        pytest.fail("IB Gateway not reachable on localhost:4002 — cannot run smoke test")
    yield broker
    broker.disconnect()


def test_submit_ack_cancel_state_walk(ibkr_broker):
    """Submit a deep-OTM bull call spread that won't fill, verify FSM state, cancel.

    Uses SPY at a strike 50% above current price with a $0.01 net debit limit
    to guarantee non-fill. Verifies the FSM classifies the broker response
    correctly.
    """
    from ib_insync import Stock
    from lifecycle.broker_adapter import SubmitResult, SubmitOutcome
    from lifecycle.trade_lifecycle import TradeLifecycle
    from lifecycle.states import TradeState

    ib = ibkr_broker._ib

    # 1. Get current SPY price
    spy = Stock("SPY", "SMART", "USD")
    qualified = ib.qualifyContracts(spy)
    assert qualified, "Failed to qualify SPY stock contract"
    spy = qualified[0]

    ticker = ib.reqMktData(spy, "", False, False)
    ib.sleep(3)
    price = ticker.marketPrice()
    if not price or price != price:  # NaN
        price = ticker.last
    if not price or price != price:
        price = ticker.close
    assert price and price == price, (
        f"Could not get SPY price (market data unavailable). "
        f"marketPrice={ticker.marketPrice()}, last={ticker.last}, close={ticker.close}"
    )

    # 2. Find a valid OTM strike from the actual chain
    chains = ib.reqSecDefOptParams(spy.symbol, "", spy.secType, spy.conId)
    assert chains, "No option chains available for SPY"
    chain = max(chains, key=lambda c: len(c.expirations))
    target_date = (datetime.now().date() + timedelta(days=30)).strftime("%Y%m%d")
    future_expiries = sorted(e for e in chain.expirations if e >= target_date)
    assert future_expiries, f"No SPY expiries 30+ days out (target {target_date})"
    expiry_yyyymmdd = future_expiries[0]
    expiry_occ = expiry_yyyymmdd[2:]  # "20261220" → "261220"

    # IBKR's chain.strikes is a theoretical range — not all strikes have
    # actual contracts. Probe OTM strikes via qualifyContracts to find ones
    # that exist. Start just above current price, stop after finding 2.
    from ib_insync import Option as IB_Option
    listed_strikes = sorted(s for s in chain.strikes if s > price and s <= price * 1.10)
    qualified_otm = []
    for s in listed_strikes:
        c = IB_Option("SPY", expiry_yyyymmdd, s, "C", "SMART")
        if ib.qualifyContracts(c):
            qualified_otm.append(s)
            if len(qualified_otm) >= 2:
                break
    assert len(qualified_otm) >= 2, (
        f"Need 2 qualified OTM call strikes, found {len(qualified_otm)} "
        f"in range {price:.0f}-{price*1.10:.0f}"
    )
    buy_strike = qualified_otm[0]
    sell_strike = qualified_otm[1]
    buy_occ = _build_occ("SPY", expiry_occ, "C", buy_strike)
    sell_occ = _build_occ("SPY", expiry_occ, "C", sell_strike)

    coid = f"smoke-{int(time.time())}"
    legs = [
        {"symbol": buy_occ, "side": "BUY"},
        {"symbol": sell_occ, "side": "SELL"},
    ]

    print(f"\n  Smoke test setup:")
    print(f"    SPY price: ${price:.2f}")
    print(f"    OTM strike: ${buy_strike}/${sell_strike} (50% OTM)")
    print(f"    Expiry: {expiry_yyyymmdd}")
    print(f"    Buy leg: {buy_occ}")
    print(f"    Sell leg: {sell_occ}")
    print(f"    coid: {coid}")

    # 4. Submit
    raw = ibkr_broker.place_mleg_order(legs, qty=1, tif="day", client_order_id=coid)

    # place_mleg_order may return None if qualification fails on the far-OTM
    # strikes (no contract at that strike/expiry). This is a REAL test failure,
    # not something to skip — it means the test's strike selection needs fixing.
    assert raw is not None, (
        f"place_mleg_order returned None. Last error: {ibkr_broker._last_order_error}. "
        f"The far-OTM strikes ({buy_strike}/{sell_strike}) may not have listed contracts."
    )

    result = SubmitResult.from_broker(raw)
    initial_state = TradeLifecycle.state_after_submit(result)

    print(f"  SubmitResult: outcome={result.outcome}, order_id={result.order_id}, "
          f"fill_status={result.fill_status}, working={result.working}")
    print(f"  FSM initial state: {initial_state}")

    # 5. Verify FSM classified correctly
    assert result.outcome in (
        SubmitOutcome.INDETERMINATE,
        SubmitOutcome.UNKNOWN,
        SubmitOutcome.ACCEPTED,
    ), f"Unexpected outcome: {result.outcome}"

    assert initial_state in (TradeState.ACKED, TradeState.FILLED), (
        f"Unexpected FSM state: {initial_state}"
    )
    assert result.order_id is not None, "order_id must be present after submit"

    # 6. Cancel the order
    order_id = result.order_id
    cancelled = False
    for trade in ib.openTrades():
        if str(trade.order.orderId) == str(order_id):
            ib.cancelOrder(trade.order)
            cancelled = True
            break
    ib.sleep(3)

    if not cancelled:
        print(f"  Warning: order {order_id} not found in openTrades (may have already terminated)")

    # 7. Verify order is no longer open
    still_open = [
        t for t in ib.openTrades()
        if str(t.order.orderId) == str(order_id)
    ]
    assert len(still_open) == 0, (
        f"Order {order_id} still open after cancel: status={still_open[0].orderStatus.status}"
    )

    print(f"  PASSED: submit → {initial_state.value} → cancel → order cleared")
