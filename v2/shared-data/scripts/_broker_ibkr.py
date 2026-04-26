"""IBKR adapter for the BrokerBase interface.

Lazy-imported by broker.get_broker() only when BROKER_TYPE=ibkr so the
ib_insync import (~200ms) is paid only when needed.

Lifecycle:
  - One IB() instance per process. Lazy-connect on first call. atexit handles
    disconnect. Do NOT use util.startLoop (Jupyter-only) or asyncio.run.
  - Use ib.sleep() between reqMktData and reading tickers — never time.sleep().

Index option routing (XSP/SPX/VIX): exchange=CBOE, tradingClass disambiguates
SPX (monthly) vs SPXW (weekly) and VIX vs VIXW. Use reqSecDefOptParams to
discover (exchange, tradingClass) pairs rather than hardcoding the rule.

Daily 23:30-00:15 ET restart window: refuse to connect with a clear log
rather than retry-storming during IB Gateway's nightly restart.

Credential safety: NEVER log host/port pairs that include credentials. The
ib_insync library does not transmit credentials over the API socket — those
live in IB Gateway's startup config — so connection probes here are safe.
"""

import logging
import os
import sys
import time
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

sys.path.insert(0, "/home/trader/QuantAI/v2/shared-data/scripts")
from broker import (
    BrokerBase,
    DRY_RUN_SENTINEL,
    BROKER_DRY_RUN,
    _parse_occ,
    _safe_mid,
)

try:
    from ib_insync import (
        IB,
        Stock,
        Index,
        Option,
        Bag,
        ComboLeg,
        MarketOrder,
        util,
    )
except ImportError as e:
    raise ImportError(
        "ib_insync not available. Install with: pip3 install ib_insync"
    ) from e

ET = ZoneInfo("America/New_York")

_INDEX_ROOTS = {"XSP", "SPX", "SPXW", "VIX", "VIXW", "RUT", "NDX"}


def _is_in_restart_window(now: Optional[datetime] = None) -> bool:
    """IB Gateway restarts at 23:45 ET. Refuse to connect 23:30-00:15."""
    n = now or datetime.now(ET)
    if n.hour == 23 and n.minute >= 30:
        return True
    if n.hour == 0 and n.minute < 15:
        return True
    return False


class IBKRBroker(BrokerBase):
    """Adapter targeting IB Gateway via ib_insync."""

    name = "ibkr"

    def __init__(self) -> None:
        self.host = os.environ.get("IBKR_HOST", "127.0.0.1")
        self.port = int(os.environ.get("IBKR_PORT", "4002"))
        self.client_id = int(os.environ.get("IBKR_CLIENT_ID", "1"))
        self.account = os.environ.get("IBKR_ACCOUNT", "")
        self._ib: Optional[IB] = None
        self._md_type_warned = False
        if BROKER_DRY_RUN:
            logging.warning("IBKRBroker: BROKER_DRY_RUN=1 — orders will not be submitted")

    # ── connection ──────────────────────────────────────────────────────────

    def connect(self) -> bool:
        if self._ib is not None and self._ib.isConnected():
            return True
        if _is_in_restart_window():
            logging.error(
                "IBKRBroker: in IB Gateway restart window (23:30-00:15 ET) — refusing to connect"
            )
            return False
        last_err: Optional[Exception] = None
        ib: Optional[IB] = None
        for attempt in range(3):
            ib = IB()
            try:
                ib.connect(self.host, self.port, clientId=self.client_id, timeout=15)
                if not ib.isConnected():
                    raise ConnectionError("connect returned without isConnected()")
                ib.reqMarketDataType(1)
                self._ib = ib
                accts = ib.managedAccounts()
                logging.info("IBKRBroker connected: accounts=%s", accts)
                return True
            except Exception as e:
                last_err = e
                logging.warning(
                    "IBKRBroker connect attempt %d/3 failed: %s", attempt + 1, e
                )
                try:
                    if ib.isConnected():
                        ib.disconnect()
                except Exception:
                    pass
                if attempt < 2:
                    time.sleep(5)
        logging.error("IBKRBroker: gave up after 3 connect attempts: %s", last_err)
        return False

    def _check_md_type(self, ticker) -> None:
        """Detect delayed-data fallback (marketDataType=3) once per process."""
        try:
            if ticker.marketDataType == 3 and not self._md_type_warned:
                logging.warning(
                    "IBKRBroker: market data fell back to DELAYED (type 3). "
                    "Likely missing CBOE/OPRA subscription on this account."
                )
                self._md_type_warned = True
        except AttributeError:
            pass

    def disconnect(self) -> None:
        if self._ib is not None:
            try:
                if self._ib.isConnected():
                    self._ib.disconnect()
            except Exception:
                pass
            self._ib = None

    # ── account & positions ─────────────────────────────────────────────────

    def get_account(self) -> Optional[dict]:
        if not self.connect():
            return None
        try:
            rows = self._ib.accountSummary()
            by_tag = {}
            for r in rows:
                if self.account and r.account != self.account:
                    continue
                by_tag[r.tag] = r.value
            return {
                "equity": _to_float(by_tag.get("NetLiquidation")),
                "buying_power": _to_float(by_tag.get("BuyingPower")),
                "cash": _to_float(by_tag.get("TotalCashValue")),
                "options_buying_power": _to_float(
                    by_tag.get("OptionMarketValue")
                    or by_tag.get("BuyingPower")
                ),
                "pattern_day_trader": str(by_tag.get("DayTradesRemaining", "")) == "0",
            }
        except Exception as e:
            logging.error("IBKRBroker.get_account failed: %s", e)
            return None

    def get_positions(self) -> list:
        if not self.connect():
            return []
        try:
            out = []
            for p in self._ib.portfolio():
                c = p.contract
                if c.secType == "OPT":
                    sym = _build_occ_from_contract(c)
                else:
                    sym = c.localSymbol or c.symbol
                qty = int(p.position)
                out.append({
                    "symbol": sym,
                    "qty": qty,
                    "side": "long" if qty >= 0 else "short",
                    "avg_cost": float(p.averageCost or 0),
                    "current_price": float(p.marketPrice or 0),
                    "unrealized_pnl": float(p.unrealizedPNL or 0),
                    "market_value": float(p.marketValue or 0),
                })
            return out
        except Exception as e:
            logging.error("IBKRBroker.get_positions failed: %s", e)
            return []

    # ── option chains ───────────────────────────────────────────────────────

    def fetch_option_chain(
        self,
        symbol: str,
        dte_range: tuple,
        strike_range: Optional[tuple] = None,
        include_quotes: bool = False,
    ) -> list:
        if not self.connect():
            return []
        try:
            underlying = self._make_underlying(symbol)
            qualified = self._ib.qualifyContracts(underlying)
            if not qualified:
                logging.error("IBKRBroker: cannot qualify underlying %s", symbol)
                return []
            underlying = qualified[0]
            params_list = self._ib.reqSecDefOptParams(
                underlying.symbol, "", underlying.secType, underlying.conId
            )
            if not params_list:
                logging.error("IBKRBroker: no option params for %s", symbol)
                return []
            min_dte, max_dte = dte_range
            today = datetime.now(ET).date()
            min_date = today + timedelta(days=int(min_dte))
            max_date = today + timedelta(days=int(max_dte))
            out = []
            for params in params_list:
                exch = params.exchange
                tclass = params.tradingClass
                if symbol.upper() in _INDEX_ROOTS and exch != "CBOE":
                    continue
                strikes = sorted(params.strikes or [])
                if strike_range:
                    lo, hi = strike_range
                    strikes = [s for s in strikes if lo <= s <= hi]
                expiries = []
                for ex in sorted(params.expirations or []):
                    try:
                        d = datetime.strptime(ex, "%Y%m%d").date()
                    except ValueError:
                        continue
                    if min_date <= d <= max_date:
                        expiries.append((ex, d.isoformat()))
                for ex_raw, ex_iso in expiries:
                    for k in strikes:
                        for right in ("C", "P"):
                            occ = f"{tclass}{ex_raw[2:]}{right}{int(round(k * 1000)):08d}"
                            out.append({
                                "symbol": occ,
                                "underlying": underlying.symbol,
                                "strike": float(k),
                                "expiry": ex_iso,
                                "right": right,
                                "bid": None,
                                "ask": None,
                                "mid": None,
                                "last": None,
                                "delta": None,
                                "gamma": None,
                                "theta": None,
                                "vega": None,
                                "open_interest": None,
                                "volume": None,
                                "_exchange": exch,
                                "_tradingClass": tclass,
                            })
            if include_quotes and out:
                self._enrich_with_quotes(out)
            return out
        except Exception as e:
            logging.error("IBKRBroker.fetch_option_chain(%s) failed: %s", symbol, e)
            return []

    def _enrich_with_quotes(self, chain: list) -> None:
        """Snapshot quotes for each chain entry. SLOW — caller should pre-filter
        the chain (typically <50 contracts) before passing include_quotes=True.
        IBKR has a 50-line market-data ceiling on retail accounts."""
        if len(chain) > 50:
            logging.warning(
                "IBKRBroker._enrich_with_quotes: %d entries exceeds 50-line cap; truncating",
                len(chain),
            )
            chain_to_quote = chain[:50]
        else:
            chain_to_quote = chain
        try:
            tickers = []
            for entry in chain_to_quote:
                spec = _parse_occ(entry["symbol"])
                if spec is None:
                    continue
                contract = Option(
                    spec.root,
                    spec.expiry,
                    spec.strike,
                    spec.right,
                    entry.get("_exchange", "SMART"),
                    tradingClass=entry.get("_tradingClass", spec.root),
                )
                self._ib.qualifyContracts(contract)
                t = self._ib.reqMktData(contract, "", snapshot=False, regulatorySnapshot=False)
                tickers.append((entry, t))
            self._ib.sleep(3)
            for entry, t in tickers:
                self._check_md_type(t)
                bid = float(t.bid) if t.bid and t.bid > 0 else None
                ask = float(t.ask) if t.ask and t.ask > 0 else None
                last = float(t.last) if t.last and t.last > 0 else None
                entry["bid"], entry["ask"], entry["last"] = bid, ask, last
                entry["mid"] = _safe_mid(bid, ask)
                if t.modelGreeks:
                    entry["delta"] = _to_float(t.modelGreeks.delta)
                    entry["gamma"] = _to_float(t.modelGreeks.gamma)
                    entry["theta"] = _to_float(t.modelGreeks.theta)
                    entry["vega"] = _to_float(t.modelGreeks.vega)
            for _, t in tickers:
                try:
                    self._ib.cancelMktData(t.contract)
                except Exception:
                    pass
        except Exception as e:
            logging.warning("IBKRBroker quote enrichment failed: %s", e)

    def _make_underlying(self, symbol: str):
        s = symbol.upper()
        if s in _INDEX_ROOTS:
            return Index(s, "CBOE", "USD")
        return Stock(s, "SMART", "USD")

    # ── quotes ──────────────────────────────────────────────────────────────

    def get_quote(self, symbol: str) -> Optional[dict]:
        if not self.connect():
            return None
        try:
            contract = self._make_underlying(symbol)
            self._ib.qualifyContracts(contract)
            t = self._ib.reqMktData(contract, "", snapshot=False, regulatorySnapshot=False)
            self._ib.sleep(2)
            self._check_md_type(t)
            bid = float(t.bid) if t.bid and t.bid > 0 else None
            ask = float(t.ask) if t.ask and t.ask > 0 else None
            last = float(t.last) if t.last and t.last > 0 else None
            try:
                self._ib.cancelMktData(contract)
            except Exception:
                pass
            return {
                "bid": bid,
                "ask": ask,
                "last": last,
                "mid": _safe_mid(bid, ask),
            }
        except Exception as e:
            logging.warning("IBKRBroker.get_quote(%s) failed: %s", symbol, e)
            return None

    def get_option_quote(self, occ: str) -> Optional[dict]:
        if not self.connect():
            return None
        spec = _parse_occ(occ)
        if spec is None:
            logging.error("IBKRBroker.get_option_quote: unparsable OCC %r", occ)
            return None
        try:
            contract = self._option_from_spec(spec)
            self._ib.qualifyContracts(contract)
            t = self._ib.reqMktData(contract, "", snapshot=False, regulatorySnapshot=False)
            self._ib.sleep(2)
            self._check_md_type(t)
            bid = float(t.bid) if t.bid and t.bid > 0 else None
            ask = float(t.ask) if t.ask and t.ask > 0 else None
            last = float(t.last) if t.last and t.last > 0 else None
            try:
                self._ib.cancelMktData(contract)
            except Exception:
                pass
            return {
                "bid": bid,
                "ask": ask,
                "last": last,
                "mid": _safe_mid(bid, ask),
            }
        except Exception as e:
            logging.warning("IBKRBroker.get_option_quote(%s) failed: %s", occ, e)
            return None

    def _option_from_spec(self, spec) -> "Option":
        """Build an Option contract from a parsed OCC spec, routing index roots
        through CBOE with the correct tradingClass."""
        root = spec.root.upper()
        if root in _INDEX_ROOTS:
            return Option(
                root.replace("SPXW", "SPX").replace("VIXW", "VIX"),
                spec.expiry,
                spec.strike,
                spec.right,
                "CBOE",
                tradingClass=root,
            )
        return Option(root, spec.expiry, spec.strike, spec.right, "SMART", "USD")

    # ── orders ──────────────────────────────────────────────────────────────

    def place_mleg_order(
        self,
        legs: list,
        qty: int = 1,
        tif: str = "day",
        client_order_id: Optional[str] = None,
    ) -> Optional[dict]:
        if not legs:
            logging.error("IBKRBroker.place_mleg_order: empty legs")
            return None
        try:
            specs_with_legs = []
            for leg in legs:
                spec = _parse_occ(leg["symbol"])
                if spec is None:
                    logging.error("IBKRBroker.place_mleg_order: unparsable leg %r", leg)
                    return None
                specs_with_legs.append((spec, leg))
            if BROKER_DRY_RUN:
                logging.warning(
                    "IBKRBroker.place_mleg_order DRY_RUN qty=%d tif=%s legs=%s coid=%s",
                    qty, tif, legs, client_order_id,
                )
                return dict(DRY_RUN_SENTINEL, client_order_id=client_order_id)
            if not self.connect():
                return None
            roots = {s.root.upper() for s, _ in specs_with_legs}
            if len(roots) != 1:
                logging.error("IBKRBroker.place_mleg_order: legs span multiple roots %s", roots)
                return None
            root = roots.pop()
            combo_legs = []
            for spec, leg in specs_with_legs:
                contract = self._option_from_spec(spec)
                qualified = self._ib.qualifyContracts(contract)
                if not qualified:
                    logging.error("IBKRBroker: failed to qualify leg %s", leg["symbol"])
                    return None
                qc = qualified[0]
                combo_legs.append(ComboLeg(
                    conId=qc.conId,
                    ratio=int(leg.get("ratio_qty", 1)),
                    action=leg["side"].upper(),
                    exchange=qc.exchange or "SMART",
                ))
            if root in _INDEX_ROOTS:
                bag_underlying = root.replace("SPXW", "SPX").replace("VIXW", "VIX")
                bag_exchange = "CBOE"
                sec_type = "BAG"
            else:
                bag_underlying = root
                bag_exchange = "SMART"
                sec_type = "BAG"
            bag = Bag(
                symbol=bag_underlying,
                exchange=bag_exchange,
                currency="USD",
            )
            bag.secType = sec_type
            bag.comboLegs = combo_legs
            order = MarketOrder("BUY", qty)
            order.tif = tif.upper()
            if client_order_id:
                order.orderRef = client_order_id
            order.smartComboRoutingParams = []
            trade = self._ib.placeOrder(bag, order)
            self._ib.sleep(1)
            return self._trade_to_result(trade, client_order_id)
        except Exception as e:
            logging.error("IBKRBroker.place_mleg_order failed: %s", e)
            return None

    def close_position(
        self,
        legs: list,
        qty: int = 1,
        client_order_id: Optional[str] = None,
    ) -> Optional[dict]:
        return self.place_mleg_order(legs, qty=qty, client_order_id=client_order_id)

    def get_order_status(self, order_id: str) -> Optional[dict]:
        if not self.connect():
            return None
        try:
            for trade in self._ib.trades():
                oid = str(trade.order.permId or trade.order.orderId or "")
                ref = trade.order.orderRef or ""
                if oid == str(order_id) or ref == str(order_id):
                    return self._trade_to_result(trade, ref or None)
            return None
        except Exception as e:
            logging.warning("IBKRBroker.get_order_status(%s) failed: %s", order_id, e)
            return None

    def _trade_to_result(self, trade, client_order_id: Optional[str]) -> dict:
        status = trade.orderStatus.status if trade.orderStatus else "Submitted"
        filled = int(trade.orderStatus.filled or 0) if trade.orderStatus else 0
        avg = float(trade.orderStatus.avgFillPrice or 0) if trade.orderStatus else 0.0
        oid = str(trade.order.permId or trade.order.orderId or "")
        return {
            "order_id": oid,
            "status": status,
            "filled_qty": filled,
            "avg_fill_price": avg,
            "client_order_id": client_order_id or trade.order.orderRef or None,
        }


# ── helpers ────────────────────────────────────────────────────────────────────


def _to_float(v) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _build_occ_from_contract(c) -> str:
    """Reconstruct an OCC symbol from an ib_insync Option contract."""
    expiry = c.lastTradeDateOrContractMonth or ""
    if len(expiry) == 8:
        yymmdd = expiry[2:]
    else:
        yymmdd = expiry
    right = (c.right or "")[:1].upper()
    strike = int(round(float(c.strike or 0) * 1000))
    root = c.tradingClass or c.symbol or ""
    return f"{root}{yymmdd}{right}{strike:08d}"
