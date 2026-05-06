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


# IBKR error codes that are subscription-noise on paper accounts. ib_insync
# logs these at ERROR level via `ib_insync.wrapper`; our `_logger.setup`
# captures WARNING+ from all loggers, so without a filter each contract that
# misses a market-data subscription floods the dashboard error catalog
# (one event per contract × 50+ contracts per chain fetch). The data still
# flows (delayed); `_check_md_type` already logs ONE warning per process.
# Set IBKR_LOG_RAW=1 to disable the filter for diagnostics.
_IBKR_NOISE_CODES = (
    "Error 354,",    # "Requested market data is not subscribed; displaying delayed."
    "Error 10090,",  # "Part of requested market data is not subscribed. Delayed available."
    "Error 10168,",  # alt phrasing of subscription gap
    "Error 10182,",  # "Failed to request live updates (disconnected)" — transient
    "Error 10197,",  # "No market data during competing live session" — paper acct + reqMDType(4) fallback
    "Error 10091,",  # "Part of requested market data requires additional subscription" — paper acct OPRA gap
)

# Connection-refused chatter from ib_insync.client / ib_insync.ib. Each
# connect() attempt emits 2-3 of these via separate loggers. With our 3-retry
# wrapper × 32 cron ticks/day × multiple callers, an offline IB Gateway
# generates 3000+ events of pure noise drowning real signals. We log ONE
# WARNING per connect() attempt ourselves; the rest is filtered.
_IBKR_CONNECT_NOISE = (
    "API connection failed: ConnectionRefusedError",
    "Make sure API port on TWS/IBG is open",
    "peer closed connection",
    "Connect call failed",
    # qualifyContracts logs a WARNING when a strike spec doesn't resolve to a
    # listed contract — common with tight strike grids on indexes (XSP/SPX).
    # The caller already gets None back and skips; the warning is pure noise
    # and floods at 200+ events per chain query.
    "Unknown contract: Option(",
)


class _IBKRNoiseFilter(logging.Filter):
    """Drop ib_insync records matching known-benign codes / connect chatter."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        if msg.startswith(_IBKR_NOISE_CODES):
            return False
        if any(p in msg for p in _IBKR_CONNECT_NOISE):
            return False
        return True


def _install_ib_log_filter() -> None:
    if os.environ.get("IBKR_LOG_RAW") == "1":
        return
    # Filter all ib_insync sub-loggers — `wrapper` for error codes,
    # `client` / `ib` for the connection-refused chatter.
    for name in ("ib_insync", "ib_insync.wrapper", "ib_insync.client", "ib_insync.ib"):
        target = logging.getLogger(name)
        if not any(isinstance(f, _IBKRNoiseFilter) for f in target.filters):
            target.addFilter(_IBKRNoiseFilter())


_install_ib_log_filter()

ET = ZoneInfo("America/New_York")

_INDEX_ROOTS = {"XSP", "SPX", "SPXW", "VIX", "VIXW", "RUT", "NDX"}

# Phase 5b broker status taxonomy (added 2026-05-05 after A021/A022/A020 incidents).
# Reused by entry path (place_mleg_order) and close path (place_close_order).
# IBKR statuses sourced from ib_insync OrderStatus.status canonical values.
_BROKER_TERMINAL_FAILURE_STATUSES = {
    # The order is dead — submission failed or order was canceled.
    "cancelled", "canceled", "apicancelled", "apicanceled",
    "rejected", "inactive",
}
_BROKER_INDETERMINATE_STATUSES = {
    # Order is working but hasn't filled yet. Caller should poll, not resubmit.
    "submitted", "presubmitted", "pendingsubmit", "pendingcancel",
    "apipending",
}
_BROKER_SUCCESS_STATUSES = {"filled", "simulated"}

# How long place_mleg_order will poll an indeterminate-status order before
# returning a _working dict. Combo orders sometimes take 2-3s to settle.
ENTRY_POLL_SECONDS = 5.0


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
        self._last_order_error: Optional[str] = None  # set on place_mleg_order failure
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
                # readonly=True skips ib_insync's auto-sync of open orders +
                # completed orders at connect time. We don't read either (audit
                # in plan); reqCompletedOrders was timing out at 15s on every
                # cron tick (~700 events/day from collect_alpaca alone).
                # placeOrder is unaffected — readonly is a client-side hint.
                ib.connect(self.host, self.port, clientId=self.client_id,
                           timeout=15, readonly=True)
                if not ib.isConnected():
                    raise ConnectionError("connect returned without isConnected()")
                # Type 3 (delayed live) delivers t.last and t.modelGreeks for this paper
                # account; type 4 (frozen) returns -1 because there's no prior live tick to
                # freeze without an OPRA subscription. Bid/ask are still -1 under type 3,
                # but we fall back to t.last / modelGreeks.optPrice in _enrich_with_quotes.
                ib.reqMarketDataType(3)
                self._ib = ib
                accts = ib.managedAccounts()
                logging.info("IBKRBroker connected: accounts=%s", accts)
                return True
            except Exception as e:
                last_err = e
                # Per-attempt failures logged at DEBUG to avoid 3-line floods
                # when the gateway is offline. Single summary ERROR below.
                logging.debug("IBKRBroker connect attempt %d/3 failed: %s", attempt + 1, e)
                try:
                    if ib.isConnected():
                        ib.disconnect()
                except Exception:
                    pass
                if attempt < 2:
                    time.sleep(5)
        # One concise ERROR per process call — picks up port for ops clarity.
        # The 3-attempt detail is in DEBUG above; full ib_insync chatter is
        # filtered by _IBKRNoiseFilter. Operators see one signal, not 96.
        logging.error(
            "IBKRBroker: gave up after 3 connect attempts to %s:%d (last err: %s)",
            self.host, self.port, type(last_err).__name__ if last_err else "?",
        )
        return False

    def _check_md_type(self, ticker) -> None:
        """Detect unexpected live-data fallback. Types 3/4 are expected (we request 4)."""
        try:
            if ticker.marketDataType not in (3, 4) and not self._md_type_warned:
                logging.warning(
                    "IBKRBroker: unexpected market data type %d (expected 3 or 4 delayed).",
                    ticker.marketDataType,
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
                # Alpaca-specific extras — IBKR doesn't expose these in the
                # same form. None signals "not applicable" to callers.
                "last_equity": None,
                "portfolio_value": _to_float(by_tag.get("NetLiquidation")),
                "long_market_value": _to_float(by_tag.get("StockMarketValue")),
                "short_market_value": None,
                "account_status": by_tag.get("AccountReady"),
                "trading_blocked": None,
                "options_approved_level": None,
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

    def verify_legs_flat(self, legs: list) -> list:
        """Phase 5 close-path safeguard (added 2026-05-04 after A018 incident).

        Query current broker positions and return the OCC symbols of any leg in
        *legs* that still has non-zero qty on the broker.

        Empty list = all legs flat = close confirmed = safe to mark journal CLOSED.
        Non-empty list = some legs still open = close did NOT actually complete =
        do NOT mark journal CLOSED; alert + retry.

        *legs* is the journal's leg list; each leg has at minimum `symbol`
        (OCC-formatted, e.g. "INTC260515P00094000").
        Returns: list of OCC symbols still showing non-zero broker qty.
        Never raises — returns ['_verify_failed'] on any unexpected error so the
        caller fails-closed rather than silently marking CLOSED.
        """
        try:
            broker_positions = self.get_positions()
        except Exception as e:
            logging.error("verify_legs_flat: get_positions failed: %s", e)
            return ["_verify_failed"]
        # Normalize broker symbols to OCC format (no spaces)
        broker_qty = {}
        for p in broker_positions:
            sym_norm = (p.get("symbol") or "").replace(" ", "").upper()
            if sym_norm:
                broker_qty[sym_norm] = int(p.get("qty") or 0)
        unflat = []
        for leg in legs:
            leg_sym = (leg.get("symbol") or "").replace(" ", "").upper()
            if not leg_sym:
                continue
            if broker_qty.get(leg_sym, 0) != 0:
                unflat.append(leg_sym)
        return unflat

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
            # IBKR's 50-line snapshot cap is documented expected behavior. Sort
            # by distance from median strike before truncating so ATM strikes
            # (the ones strategies actually need) survive the cut — the previous
            # naive [:50] gave 25 deepest-OTM puts on the nearest expiry, which
            # broke compute_spx_chain_metrics on any chain with >50 entries.
            strikes = sorted({float(c.get("strike", 0)) for c in chain if c.get("strike")})
            anchor = strikes[len(strikes) // 2] if strikes else 0
            logging.info(
                "IBKRBroker._enrich_with_quotes: %d entries exceeds 50-line cap; "
                "truncating to ATM-nearest 50 (anchor=%.2f)",
                len(chain), anchor,
            )
            chain_to_quote = sorted(
                chain, key=lambda c: abs(float(c.get("strike") or 0) - anchor)
            )[:50]
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
            # 5s for delayed feed: bid/ask snapshot + modelGreeks population.
            self._ib.sleep(5)
            for entry, t in tickers:
                self._check_md_type(t)
                # Paper accounts without OPRA see bid/ask=-1; t.last and the IBKR-
                # computed modelGreeks.optPrice (theoretical mid) are still populated.
                bid = float(t.bid) if t.bid and t.bid > 0 else None
                ask = float(t.ask) if t.ask and t.ask > 0 else None
                last = float(t.last) if t.last and t.last > 0 else None
                entry["bid"], entry["ask"], entry["last"] = bid, ask, last
                mid = _safe_mid(bid, ask)
                if mid is None and t.modelGreeks and t.modelGreeks.optPrice:
                    op = _to_float(t.modelGreeks.optPrice)
                    if op and op > 0:
                        mid = round(op, 2)
                if mid is None and last is not None:
                    mid = last
                entry["mid"] = mid
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
            self._ib.sleep(4)  # delayed feed needs more settle time than live
            self._check_md_type(t)
            bid = float(t.bid) if t.bid and t.bid > 0 else None
            ask = float(t.ask) if t.ask and t.ask > 0 else None
            last = float(t.last) if t.last and t.last > 0 else None
            # Paper account fallback: prefer marketPrice() (handles delayed feed)
            # over None. Index spot usually returns last but no bid/ask.
            mid = _safe_mid(bid, ask)
            if mid is None:
                try:
                    mp = float(t.marketPrice())
                    if mp and mp > 0:
                        mid = mp
                except Exception:
                    pass
            if mid is None and last is not None:
                mid = last
            try:
                self._ib.cancelMktData(contract)
            except Exception:
                pass
            return {
                "bid": bid,
                "ask": ask,
                "last": last,
                "mid": mid,
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
            self._ib.sleep(5)  # delayed feed needs time for greeks population
            self._check_md_type(t)
            bid = float(t.bid) if t.bid and t.bid > 0 else None
            ask = float(t.ask) if t.ask and t.ask > 0 else None
            last = float(t.last) if t.last and t.last > 0 else None
            delta = gamma = theta = vega = iv = None
            opt_price = None
            if t.modelGreeks:
                delta = _to_float(t.modelGreeks.delta)
                gamma = _to_float(t.modelGreeks.gamma)
                theta = _to_float(t.modelGreeks.theta)
                vega = _to_float(t.modelGreeks.vega)
                iv = _to_float(getattr(t.modelGreeks, "impliedVol", None))
                opt_price = _to_float(getattr(t.modelGreeks, "optPrice", None))
            # Paper account fallback: bid/ask are -1 without OPRA; use IBKR's
            # theoretical optPrice or last as a single-point mid estimate so
            # downstream strategy code (mid != None gate) still works.
            mid = _safe_mid(bid, ask)
            if mid is None and opt_price and opt_price > 0:
                mid = round(opt_price, 2)
            if mid is None and last is not None:
                mid = last
            try:
                self._ib.cancelMktData(contract)
            except Exception:
                pass
            return {
                "bid": bid,
                "ask": ask,
                "last": last,
                "mid": mid,
                "delta": delta,
                "gamma": gamma,
                "theta": theta,
                "vega": vega,
                "iv": iv,
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
        return Option(root, spec.expiry, spec.strike, spec.right,
                      exchange="SMART", currency="USD")

    # ── orders ──────────────────────────────────────────────────────────────

    def place_mleg_order(
        self,
        legs: list,
        qty: int = 1,
        tif: str = "day",
        client_order_id: Optional[str] = None,
    ) -> Optional[dict]:
        self._last_order_error = None  # reset on each call
        if not legs:
            self._last_order_error = "empty legs list"
            logging.error("IBKRBroker.place_mleg_order: empty legs")
            return None
        try:
            specs_with_legs = []
            for leg in legs:
                spec = _parse_occ(leg["symbol"])
                if spec is None:
                    self._last_order_error = f"unparsable OCC symbol: {leg.get('symbol')!r}"
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
                self._last_order_error = "broker connect failed"
                return None
            roots = {s.root.upper() for s, _ in specs_with_legs}
            if len(roots) != 1:
                self._last_order_error = f"legs span multiple underlying roots: {roots}"
                logging.error("IBKRBroker.place_mleg_order: legs span multiple roots %s", roots)
                return None
            root = roots.pop()
            combo_legs = []
            for spec, leg in specs_with_legs:
                contract = self._option_from_spec(spec)
                qualified = self._ib.qualifyContracts(contract)
                if not qualified:
                    self._last_order_error = (
                        f"qualifyContracts failed for {leg['symbol']} "
                        f"(strike={spec.strike}, expiry={spec.expiry}, right={spec.right})"
                    )
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
            order_submitted = False
            try:
                trade = self._ib.placeOrder(bag, order)
                order_submitted = True
                self._ib.sleep(1)
                # Phase 5b status validation (added 2026-05-05 after A021/A022 incident):
                # Don't blindly return _trade_to_result. Check the broker's status
                # and treat Cancelled/Rejected/Inactive as failures (return None)
                # so the caller doesn't write a phantom journal entry.
                #
                # Indeterminate (Submitted/PreSubmitted) → poll briefly for terminal,
                # then return with _working flag if still indeterminate.
                result = self._trade_to_result(trade, client_order_id)
                raw_status = (result.get("status") or "").strip()
                status_norm = raw_status.lower()
                if status_norm in _BROKER_TERMINAL_FAILURE_STATUSES:
                    self._last_order_error = (
                        f"broker rejected order (status={raw_status})"
                    )
                    logging.error(
                        "IBKRBroker.place_mleg_order: broker REJECTED order "
                        "coid=%s status=%s — returning None so journal stays accurate",
                        client_order_id, raw_status,
                    )
                    return None
                if status_norm in _BROKER_INDETERMINATE_STATUSES:
                    # Poll for up to ENTRY_POLL_SECONDS for terminal state
                    deadline = time.time() + ENTRY_POLL_SECONDS
                    while time.time() < deadline:
                        try:
                            self._ib.sleep(0.5)
                        except Exception:
                            break
                        result = self._trade_to_result(trade, client_order_id)
                        status_norm = (result.get("status") or "").strip().lower()
                        if status_norm in _BROKER_TERMINAL_FAILURE_STATUSES:
                            logging.error(
                                "IBKRBroker.place_mleg_order: order transitioned "
                                "to %s after polling — returning None",
                                result.get("status"),
                            )
                            return None
                        if status_norm == "filled":
                            return result
                    # Still indeterminate — flag _working so caller can decide
                    result["_working"] = True
                    logging.warning(
                        "IBKRBroker.place_mleg_order: order still %s after "
                        "%.1fs polling — returning _working state coid=%s",
                        result.get("status"), ENTRY_POLL_SECONDS, client_order_id,
                    )
                    return result
                # status is Filled or unknown-non-failure → success path
                return result
            except Exception as e:
                self._last_order_error = f"{type(e).__name__}: {e}"
                logging.error("IBKRBroker.place_mleg_order failed after submit=%s: %s",
                              order_submitted, e)
                if order_submitted:
                    # Exception fired AFTER the order was sent to the gateway.
                    # The order may be live at IBKR even though we have no Trade object.
                    # Flush async callbacks then search open orders for the client_order_id.
                    try:
                        self._ib.sleep(0.5)
                    except Exception:
                        pass
                    recovered = self._find_open_order_by_ref(client_order_id)
                    if recovered is not None:
                        logging.warning(
                            "IBKRBroker.place_mleg_order: order recovered from open orders "
                            "(coid=%s orderId=%s) — returning result despite exception",
                            client_order_id, recovered.get("order_id"),
                        )
                        return recovered
                    logging.error(
                        "IBKRBroker.place_mleg_order: order submitted but not found in "
                        "open orders (coid=%s) — caller must reconcile via get_open_orders()",
                        client_order_id,
                    )
                return None
            finally:
                # Always flush any pending async callbacks so subsequent operations
                # see a consistent state (e.g. position_monitor querying right after).
                try:
                    self._ib.sleep(0.5)
                except Exception:
                    pass
        except Exception as e:
            self._last_order_error = f"{type(e).__name__}: {e}"
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

    def poll_order(self, order_id: str) -> Optional[dict]:
        """Phase 5b helper (added 2026-05-05): query an existing order's
        current state and return a status-classified dict.

        Returns dict with extra `_state` field:
          - "filled"      → order has filled, safe to mark CLOSED in journal
          - "working"     → order still pending at broker; do not resubmit
          - "failed"      → order canceled/rejected; safe to retry submission
          - "missing"     → order not found at broker (returns None)

        Used by position_monitor.place_close_order's state machine: instead of
        resubmitting a close that returned Submitted on a prior cycle, poll
        the existing order_id to see if it's now filled.
        """
        result = self.get_order_status(order_id)
        if result is None:
            return None
        status_norm = (result.get("status") or "").strip().lower()
        if status_norm in _BROKER_SUCCESS_STATUSES:
            result["_state"] = "filled"
        elif status_norm in _BROKER_TERMINAL_FAILURE_STATUSES:
            result["_state"] = "failed"
        else:
            # Submitted, PreSubmitted, etc. — still working
            result["_state"] = "working"
        return result

    def _find_open_order_by_ref(self, client_order_id: Optional[str]) -> Optional[dict]:
        """Search open/recent trades for one matching client_order_id (orderRef).

        Called as a recovery path when place_mleg_order throws AFTER placeOrder()
        was already dispatched.  Returns a result dict (same shape as
        _trade_to_result) if found, else None.  Never raises.
        """
        if not client_order_id:
            return None
        try:
            for trade in self._ib.openTrades():
                ref = trade.order.orderRef or ""
                if ref == client_order_id:
                    return self._trade_to_result(trade, client_order_id)
            # openTrades() only covers working orders — also check trades()
            # which covers recently filled / submitted this session.
            for trade in self._ib.trades():
                ref = trade.order.orderRef or ""
                if ref == client_order_id:
                    return self._trade_to_result(trade, client_order_id)
        except Exception as ex:
            logging.warning("IBKRBroker._find_open_order_by_ref(%s) failed: %s",
                            client_order_id, ex)
        return None

    def get_open_orders(self, client_order_id: Optional[str] = None) -> list:
        """Return list of result dicts for open/submitted orders.

        If *client_order_id* is provided, filter to that order only (returns
        a list of 0 or 1 items so callers can use the same pattern as the
        general case).  Used by callers to reconcile after a place_mleg_order
        that returned None.
        """
        if not self.connect():
            return []
        try:
            out = []
            for trade in self._ib.openTrades():
                ref = trade.order.orderRef or ""
                oid = str(trade.order.permId or trade.order.orderId or "")
                if client_order_id and ref != client_order_id and oid != client_order_id:
                    continue
                out.append(self._trade_to_result(trade, ref or None))
            return out
        except Exception as e:
            logging.warning("IBKRBroker.get_open_orders failed: %s", e)
            return []

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
