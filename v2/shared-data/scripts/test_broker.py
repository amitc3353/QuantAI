#!/usr/bin/env python3
"""Smoke test for broker.py + _broker_ibkr.py.

Print-based check() helper, matching system_test.py style. Exits non-zero if
any check fails so CI / cron can detect regressions.

Test asymmetry: Alpaca paper rejects SPX/XSP/VIX (HTTP 422). The cross-broker
parity check is therefore SPY only on AlpacaBroker; IBKRBroker is tested
against SPY plus all three index roots since that is the actual reason for
the migration.

Order tests are dry-run only — sets BROKER_DRY_RUN=1 in-process before
constructing the broker so no network POSTs happen.

Usage:
  python3 test_broker.py                # both adapters
  python3 test_broker.py --alpaca-only  # skip IBKR
  python3 test_broker.py --ibkr-only    # skip Alpaca
"""

import os
import sys
from datetime import datetime
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

ET = ZoneInfo("America/New_York")
ALPACA_ONLY = "--alpaca-only" in sys.argv
IBKR_ONLY = "--ibkr-only" in sys.argv

results = []

REQUIRED_ACCOUNT_KEYS = {"equity", "buying_power", "cash", "options_buying_power", "pattern_day_trader"}
REQUIRED_POSITION_KEYS = {"symbol", "qty", "side", "avg_cost", "current_price", "unrealized_pnl", "market_value"}
REQUIRED_CHAIN_KEYS = {
    "symbol", "underlying", "strike", "expiry", "right",
    "bid", "ask", "mid", "last",
    "delta", "gamma", "theta", "vega",
    "open_interest", "volume",
}
REQUIRED_QUOTE_KEYS = {"bid", "ask", "last", "mid"}
REQUIRED_ORDER_KEYS = {"order_id", "status", "filled_qty", "avg_fill_price", "client_order_id"}


def check(name, passed, detail=""):
    status = "PASS" if passed else "FAIL"
    icon = "✅" if passed else "❌"
    results.append((name, passed, detail))
    line = f"  {icon} {status}  {name}"
    if detail:
        line += f" — {detail}"
    print(line)


def section(title):
    print(f"\n{title}")


def check_keys(name, obj, required, sample_extra=False):
    if obj is None:
        check(name, False, "got None")
        return False
    if not isinstance(obj, dict):
        check(name, False, f"got {type(obj).__name__}")
        return False
    missing = required - set(obj.keys())
    if missing:
        check(name, False, f"missing: {sorted(missing)}")
        return False
    check(name, True, "" if not sample_extra else f"sample={dict(list(obj.items())[:3])}")
    return True


# ── 1. OCC parser ──────────────────────────────────────────────────────────────

print("=" * 60)
print("  Broker Adapter Smoke Test")
print(f"  {datetime.now(ET).strftime('%Y-%m-%d %H:%M ET')}")
print("=" * 60)

section("📐 OCC parser")
from broker import _parse_occ, _build_occ

cases = [
    ("SPY251220C00500000", "SPY", "20251220", "C", 500.0),
    ("XSP260117P00450000", "XSP", "20260117", "P", 450.0),
    ("SPY   251220C00500000", "SPY", "20251220", "C", 500.0),
    ("VIXW260121C00020000", "VIXW", "20260121", "C", 20.0),
    ("SPXW260620C05000000", "SPXW", "20260620", "C", 5000.0),
]
for raw, root, expiry, right, strike in cases:
    spec = _parse_occ(raw)
    ok = (
        spec is not None
        and spec.root == root
        and spec.expiry == expiry
        and spec.right == right
        and abs(spec.strike - strike) < 1e-9
    )
    check(f"parse {raw!r}", ok, "" if ok else f"got {spec}")

rebuilt = _build_occ("XSP", "20260117", "P", 450.0)
check("build XSP P 450 round-trip", rebuilt == "XSP260117P00450000", f"got {rebuilt!r}")

check("invalid OCC returns None", _parse_occ("garbage") is None)

# ── 2. Factory ─────────────────────────────────────────────────────────────────

section("🏭 Factory")

# Force dry-run BEFORE first get_broker() call so AlpacaBroker is constructed
# with BROKER_DRY_RUN=1 visible.
os.environ["BROKER_DRY_RUN"] = "1"

# Reload broker module so it picks up the env change (it reads at import).
import importlib
import broker as broker_mod
importlib.reload(broker_mod)
from broker import get_broker, BrokerBase

a = get_broker("alpaca")
check("get_broker('alpaca') returns AlpacaBroker", a.__class__.__name__ == "AlpacaBroker")
check("AlpacaBroker is BrokerBase", isinstance(a, BrokerBase))

if not ALPACA_ONLY:
    try:
        i = get_broker("ibkr")
        check("get_broker('ibkr') returns IBKRBroker", i.__class__.__name__ == "IBKRBroker")
        check("IBKRBroker is BrokerBase", isinstance(i, BrokerBase))
    except Exception as e:
        check("get_broker('ibkr')", False, f"exception: {e}")
        i = None
else:
    i = None

try:
    get_broker("nope")
    check("get_broker('nope') raises", False)
except ValueError:
    check("get_broker('nope') raises", True)

# Reset singleton so each adapter's connect() runs fresh.
broker_mod._singleton = None

# ── 3. AlpacaBroker ────────────────────────────────────────────────────────────

if not IBKR_ONLY:
    section("🟦 AlpacaBroker (SPY)")
    from broker import AlpacaBroker
    alp = AlpacaBroker()
    connected = alp.connect()
    check("AlpacaBroker.connect()", connected)
    if connected:
        acct = alp.get_account()
        check_keys("get_account shape", acct, REQUIRED_ACCOUNT_KEYS)
        if acct:
            check("equity > 0", acct["equity"] > 0, f"equity={acct['equity']}")

        pos = alp.get_positions()
        check("get_positions returns list", isinstance(pos, list), f"len={len(pos)}")
        if pos:
            check_keys("position[0] shape", pos[0], REQUIRED_POSITION_KEYS)

        chain = alp.fetch_option_chain("SPY", (1, 30))
        check("fetch_option_chain SPY (1,30) non-empty", len(chain) > 0, f"{len(chain)} contracts")
        if chain:
            entry = {k: v for k, v in chain[0].items() if not k.startswith("_")}
            check_keys("chain[0] shape", entry, REQUIRED_CHAIN_KEYS)
            check("strikes are floats", all(isinstance(c["strike"], float) for c in chain[:10]))
            check("rights are C/P", all(c["right"] in ("C", "P") for c in chain[:10]))

        # Quote (markets may be closed → may be None; only fail on shape mismatch).
        q = alp.get_quote("SPY")
        if q is not None:
            check_keys("get_quote('SPY') shape", q, REQUIRED_QUOTE_KEYS)
        else:
            check("get_quote('SPY') returned None", True, "OK if markets closed")

        # Dry-run mleg
        legs = [
            {"ratio_qty": "1", "side": "sell", "symbol": "SPY260620P00400000"},
            {"ratio_qty": "1", "side": "buy",  "symbol": "SPY260620P00390000"},
        ]
        order = alp.place_mleg_order(legs, qty=1, client_order_id="test-broker-001")
        check_keys("dry-run mleg result", order, REQUIRED_ORDER_KEYS)
        check("dry-run order_id == DRY_RUN", order and order["order_id"] == "DRY_RUN")
        check("dry-run client_order_id preserved", order and order.get("client_order_id") == "test-broker-001")

    alp.disconnect()

# ── 4. IBKRBroker ──────────────────────────────────────────────────────────────

if not ALPACA_ONLY and i is not None:
    section("🟩 IBKRBroker (SPY + XSP + SPX + VIX)")
    from _broker_ibkr import IBKRBroker, _is_in_restart_window
    ibr = IBKRBroker()
    if _is_in_restart_window():
        check("connect skipped (Gateway restart window)", True, "23:30-00:15 ET")
    else:
        connected = ibr.connect()
        check("IBKRBroker.connect()", connected)
        if connected:
            accts = ibr._ib.managedAccounts()
            check("managedAccounts contains DUP851506", "DUP851506" in accts, f"got {accts}")

            acct = ibr.get_account()
            check_keys("IBKR get_account shape", acct, REQUIRED_ACCOUNT_KEYS)

            pos = ibr.get_positions()
            check("IBKR get_positions returns list", isinstance(pos, list), f"len={len(pos)}")
            if pos:
                check_keys("IBKR position[0] shape", pos[0], REQUIRED_POSITION_KEYS)

            for sym, dte in [("SPY", (1, 30)), ("XSP", (1, 30)), ("SPX", (1, 30)), ("VIX", (1, 60))]:
                chain = ibr.fetch_option_chain(sym, dte)
                check(f"IBKR chain {sym} DTE{dte} non-empty", len(chain) > 0, f"{len(chain)} contracts")
                if chain:
                    entry = {k: v for k, v in chain[0].items() if not k.startswith("_")}
                    check_keys(f"IBKR chain[{sym}][0] shape", entry, REQUIRED_CHAIN_KEYS)

            # SPX should have BOTH SPX and SPXW tradingClasses present.
            spx_chain = ibr.fetch_option_chain("SPX", (1, 30))
            tcs = {e.get("_tradingClass") for e in spx_chain}
            check("SPX chain includes SPX and/or SPXW", any(t in ("SPX", "SPXW") for t in tcs),
                  f"tradingClasses={sorted(t for t in tcs if t)}")

            # Underlying quote on VIX (used by VIX_HALT guard).
            vix_q = ibr.get_quote("VIX")
            if vix_q is not None:
                check_keys("IBKR get_quote('VIX') shape", vix_q, REQUIRED_QUOTE_KEYS)
            else:
                check("IBKR get_quote('VIX') returned None", True, "OK if markets closed")

            # Option quote — pick a near-the-money SPY contract from the chain.
            spy_chain = ibr.fetch_option_chain("SPY", (1, 30))
            sample_occ = None
            if spy_chain:
                sample_occ = spy_chain[len(spy_chain) // 2]["symbol"]
                opt_q = ibr.get_option_quote(sample_occ)
                if opt_q is not None:
                    check_keys(f"IBKR get_option_quote({sample_occ})", opt_q, REQUIRED_QUOTE_KEYS)
                else:
                    check(f"IBKR get_option_quote({sample_occ}) returned None", True, "OK if markets closed")

            # Dry-run mleg on SPY (no real submission — BROKER_DRY_RUN=1).
            if sample_occ:
                # Build a synthetic 2-leg pair from the chain.
                puts = [c for c in spy_chain if c["right"] == "P"]
                if len(puts) >= 2:
                    short_p, long_p = puts[len(puts)//2], puts[len(puts)//2 - 5] if len(puts) > 6 else puts[0]
                    legs = [
                        {"ratio_qty": "1", "side": "sell", "symbol": short_p["symbol"]},
                        {"ratio_qty": "1", "side": "buy",  "symbol": long_p["symbol"]},
                    ]
                    order = ibr.place_mleg_order(legs, qty=1, client_order_id="test-ibkr-001")
                    check_keys("IBKR dry-run mleg result", order, REQUIRED_ORDER_KEYS)
                    check("IBKR dry-run order_id == DRY_RUN", order and order["order_id"] == "DRY_RUN")

        ibr.disconnect()

# ── Summary ────────────────────────────────────────────────────────────────────

print("\n" + "=" * 60)
passed = sum(1 for _, p, _ in results if p)
failed = sum(1 for _, p, _ in results if not p)
print(f"  Total: {len(results)}  Passed: {passed}  Failed: {failed}")
print("=" * 60)

if failed:
    print("\nFailed checks:")
    for name, p, detail in results:
        if not p:
            print(f"  ❌ {name} — {detail}")
    sys.exit(1)
sys.exit(0)
