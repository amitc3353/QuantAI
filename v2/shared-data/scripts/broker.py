"""Broker abstraction layer for QuantAI.

Pluggable adapter so trading scripts can target Alpaca or IBKR transparently.
This module provides:
  - BrokerBase   : abstract interface
  - AlpacaBroker : thin REST wrapper (legacy; will be retired post-migration)
  - get_broker() : factory driven by BROKER_TYPE env var (alpaca|ibkr)
  - _parse_occ() : OCC option-symbol parser shared by all adapters

IBKRBroker lives in _broker_ibkr.py and is lazy-imported by the factory so
ib_insync's ~200ms import cost is paid only when actually used.

Design notes:
  - All methods return None / [] on failure; never raise into the caller.
  - place_mleg_order is NEVER auto-retried. Pass a deterministic
    client_order_id and let the caller reconcile on timeout.
  - BROKER_DRY_RUN=1 forces all order-placing methods to log payload + return
    a dry-run sentinel without hitting the network.

Usage:
    from broker import get_broker
    broker = get_broker()
    if not broker.connect():
        sys.exit(1)
    acct = broker.get_account()
"""

import logging
import sys

sys.path.insert(0, "/home/trader/QuantAI/v2/shared-data/scripts")
from _logger import setup as _logger_setup

_logger_setup("broker")

import abc
import atexit
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

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

try:
    import requests
except ImportError:
    requests = None

BROKER_TYPE = os.environ.get("BROKER_TYPE", "alpaca").lower()
BROKER_DRY_RUN = os.environ.get("BROKER_DRY_RUN", "0") == "1"

DRY_RUN_SENTINEL = {
    "order_id": "DRY_RUN",
    "status": "dry_run",
    "filled_qty": 0,
    "avg_fill_price": 0.0,
    "client_order_id": None,
}


# ── OCC parser (shared) ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _OptionSpec:
    root: str           # e.g. "SPY", "XSP", "SPXW"
    expiry: str         # "YYYYMMDD"
    right: str          # "C" or "P"
    strike: float       # decimal


def _parse_occ(occ: str) -> Optional[_OptionSpec]:
    """Parse an OCC-21 option symbol into its components.

    Accepts both padded ("SPY   251220C00500000") and stripped
    ("SPY251220C00500000") forms. Returns None on invalid input.
    """
    if not occ or not isinstance(occ, str):
        return None
    s = occ
    # Padded 21-char form has alphabetic root left-justified to 6 chars.
    if len(s) == 21 and s[:6].rstrip().isalpha() and s[6:].lstrip().isdigit() is False:
        root = s[:6].rstrip()
        rest = s[6:]
    else:
        s = s.strip()
        i = 0
        while i < len(s) and s[i].isalpha():
            i += 1
        root, rest = s[:i], s[i:]
    if len(rest) != 15 or not root:
        return None
    yymmdd, right, strike_str = rest[:6], rest[6:7], rest[7:]
    if right not in ("C", "P") or not yymmdd.isdigit() or not strike_str.isdigit():
        return None
    try:
        strike = int(strike_str) / 1000.0
    except ValueError:
        return None
    return _OptionSpec(root=root, expiry="20" + yymmdd, right=right, strike=strike)


def _build_occ(root: str, expiry_yyyymmdd: str, right: str, strike: float) -> str:
    """Inverse of _parse_occ. Produces the stripped (un-padded) form."""
    return f"{root}{expiry_yyyymmdd[2:]}{right.upper()}{int(round(strike * 1000)):08d}"


def _safe_mid(bid: Optional[float], ask: Optional[float]) -> Optional[float]:
    """Return midpoint only when bid/ask are sane. Crossed/locked → None."""
    if bid is None or ask is None:
        return None
    if bid <= 0 or ask <= 0 or ask <= bid:
        return None
    return (bid + ask) / 2.0


def _safe_int(x) -> Optional[int]:
    try:
        return int(x) if x is not None else None
    except (TypeError, ValueError):
        return None


# ── Abstract base ──────────────────────────────────────────────────────────────


class BrokerBase(abc.ABC):
    """Common interface implemented by AlpacaBroker and IBKRBroker."""

    name: str = "base"

    @abc.abstractmethod
    def connect(self) -> bool: ...

    @abc.abstractmethod
    def disconnect(self) -> None: ...

    @abc.abstractmethod
    def get_account(self) -> Optional[dict]: ...

    @abc.abstractmethod
    def get_positions(self) -> list: ...

    @abc.abstractmethod
    def fetch_option_chain(
        self,
        symbol: str,
        dte_range: tuple,
        strike_range: Optional[tuple] = None,
        include_quotes: bool = False,
    ) -> list: ...

    @abc.abstractmethod
    def get_quote(self, symbol: str) -> Optional[dict]: ...

    @abc.abstractmethod
    def get_option_quote(self, occ: str) -> Optional[dict]: ...

    @abc.abstractmethod
    def place_mleg_order(
        self,
        legs: list,
        qty: int = 1,
        tif: str = "day",
        client_order_id: Optional[str] = None,
    ) -> Optional[dict]: ...

    @abc.abstractmethod
    def close_position(
        self,
        legs: list,
        qty: int = 1,
        client_order_id: Optional[str] = None,
    ) -> Optional[dict]: ...

    @abc.abstractmethod
    def get_order_status(self, order_id: str) -> Optional[dict]: ...


# ── Alpaca implementation ──────────────────────────────────────────────────────


class AlpacaBroker(BrokerBase):
    """Thin wrapper over the Alpaca paper REST API.

    Mirrors the existing direct calls in autonomous_execution.py (chain, mleg
    order) and position_monitor.py (positions, close). Preserves both quirks:
    top-level qty, no position_intent.
    """

    name = "alpaca"
    BASE = "https://paper-api.alpaca.markets"
    DATA = "https://data.alpaca.markets"

    def __init__(self) -> None:
        self.api_key = os.environ.get("ALPACA_API_KEY", "")
        self.secret_key = os.environ.get("ALPACA_SECRET_KEY", "")
        self._connected = False
        if BROKER_DRY_RUN:
            logging.warning("AlpacaBroker: BROKER_DRY_RUN=1 — orders will not be submitted")

    def _hdrs(self) -> dict:
        return {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.secret_key,
            "Content-Type": "application/json",
        }

    def connect(self) -> bool:
        if self._connected:
            return True
        if not self.api_key or not self.secret_key:
            logging.error("AlpacaBroker: ALPACA_API_KEY / ALPACA_SECRET_KEY not set")
            return False
        if requests is None:
            logging.error("AlpacaBroker: requests library unavailable")
            return False
        for attempt in range(2):
            try:
                r = requests.get(f"{self.BASE}/v2/account", headers=self._hdrs(), timeout=10)
                if r.status_code == 200:
                    self._connected = True
                    return True
                logging.error("AlpacaBroker: connect probe %s: %s", r.status_code, r.text[:120])
                return False
            except Exception as e:
                if attempt == 0:
                    time.sleep(2)
                    continue
                logging.error("AlpacaBroker: connect failed: %s", e)
                return False
        return False

    def disconnect(self) -> None:
        self._connected = False

    def get_account(self) -> Optional[dict]:
        if not self.connect():
            return None
        try:
            r = requests.get(f"{self.BASE}/v2/account", headers=self._hdrs(), timeout=10)
            if r.status_code != 200:
                logging.error("AlpacaBroker.get_account %s: %s", r.status_code, r.text[:120])
                return None
            j = r.json()
            return {
                "equity": float(j.get("equity", 0)),
                "buying_power": float(j.get("buying_power", 0)),
                "cash": float(j.get("cash", 0)),
                "options_buying_power": float(
                    j.get("options_buying_power", j.get("buying_power", 0))
                ),
                "pattern_day_trader": bool(j.get("pattern_day_trader", False)),
            }
        except Exception as e:
            logging.error("AlpacaBroker.get_account failed: %s", e)
            return None

    def get_positions(self) -> list:
        if not self.connect():
            return []
        try:
            r = requests.get(f"{self.BASE}/v2/positions", headers=self._hdrs(), timeout=15)
            if r.status_code != 200:
                logging.error("AlpacaBroker.get_positions %s: %s", r.status_code, r.text[:120])
                return []
            out = []
            for p in r.json():
                qty = int(float(p.get("qty", 0)))
                out.append({
                    "symbol": p.get("symbol", ""),
                    "qty": qty,
                    "side": "long" if qty >= 0 else "short",
                    "avg_cost": float(p.get("avg_entry_price", 0) or 0),
                    "current_price": float(p.get("current_price", 0) or 0),
                    "unrealized_pnl": float(p.get("unrealized_pl", 0) or 0),
                    "market_value": float(p.get("market_value", 0) or 0),
                })
            return out
        except Exception as e:
            logging.error("AlpacaBroker.get_positions failed: %s", e)
            return []

    def fetch_option_chain(
        self,
        symbol: str,
        dte_range: tuple,
        strike_range: Optional[tuple] = None,
        include_quotes: bool = False,
    ) -> list:
        """Fetch the option chain. Alpaca returns no Greeks via this endpoint —
        Greek/quote fields are None. include_quotes=True triggers a separate
        bid/ask enrichment call (still no Greeks).
        """
        if not self.connect():
            return []
        min_dte, max_dte = dte_range
        today = datetime.utcnow().date()
        out = []
        try:
            params = {
                "underlying_symbols": symbol,
                "status": "active",
                "limit": 1000,
                "expiration_date_gte": (today + timedelta(days=int(min_dte))).isoformat(),
                "expiration_date_lte": (today + timedelta(days=int(max_dte))).isoformat(),
            }
            if strike_range:
                params["strike_price_gte"] = str(strike_range[0])
                params["strike_price_lte"] = str(strike_range[1])
            page_token = None
            while True:
                if page_token:
                    params["page_token"] = page_token
                r = requests.get(
                    f"{self.BASE}/v2/options/contracts",
                    headers=self._hdrs(),
                    params=params,
                    timeout=20,
                )
                if r.status_code != 200:
                    logging.error(
                        "AlpacaBroker.fetch_option_chain %s: %s",
                        r.status_code,
                        r.text[:160],
                    )
                    return out
                j = r.json()
                for c in j.get("option_contracts", []):
                    out.append({
                        "symbol": c.get("symbol", ""),
                        "underlying": c.get("underlying_symbol", symbol),
                        "strike": float(c.get("strike_price", 0)),
                        "expiry": c.get("expiration_date", ""),
                        "right": "C" if str(c.get("type", "")).lower().startswith("c") else "P",
                        "bid": None,
                        "ask": None,
                        "mid": None,
                        "last": None,
                        "delta": None,
                        "gamma": None,
                        "theta": None,
                        "vega": None,
                        "open_interest": _safe_int(c.get("open_interest")),
                        "volume": None,
                    })
                page_token = j.get("next_page_token")
                if not page_token:
                    break
        except Exception as e:
            logging.error("AlpacaBroker.fetch_option_chain failed: %s", e)
        if include_quotes and out:
            self._enrich_with_quotes(out)
        return out

    def _enrich_with_quotes(self, chain: list) -> None:
        """Best-effort latest-quote enrichment in batches of 100 (no Greeks)."""
        try:
            symbols = [e["symbol"] for e in chain if e["symbol"]]
            by_symbol = {e["symbol"]: e for e in chain}
            for i in range(0, len(symbols), 100):
                batch = symbols[i:i + 100]
                r = requests.get(
                    f"{self.DATA}/v1beta1/options/quotes/latest",
                    headers=self._hdrs(),
                    params={"symbols": ",".join(batch)},
                    timeout=15,
                )
                if r.status_code != 200:
                    continue
                quotes = (r.json() or {}).get("quotes", {})
                for sym, q in quotes.items():
                    e = by_symbol.get(sym)
                    if not e:
                        continue
                    bid = float(q.get("bp") or 0) or None
                    ask = float(q.get("ap") or 0) or None
                    e["bid"], e["ask"] = bid, ask
                    e["mid"] = _safe_mid(bid, ask)
        except Exception as e:
            logging.warning("AlpacaBroker quote enrichment failed: %s", e)

    def get_quote(self, symbol: str) -> Optional[dict]:
        """Underlying equity quote via /v2/stocks/{symbol}/quotes/latest."""
        if not self.connect():
            return None
        try:
            r = requests.get(
                f"{self.DATA}/v2/stocks/{symbol}/quotes/latest",
                headers=self._hdrs(),
                timeout=10,
            )
            if r.status_code != 200:
                return None
            q = (r.json() or {}).get("quote", {})
            bid = float(q.get("bp") or 0) or None
            ask = float(q.get("ap") or 0) or None
            return {"bid": bid, "ask": ask, "last": None, "mid": _safe_mid(bid, ask)}
        except Exception as e:
            logging.warning("AlpacaBroker.get_quote(%s) failed: %s", symbol, e)
            return None

    def get_option_quote(self, occ: str) -> Optional[dict]:
        if not self.connect():
            return None
        try:
            r = requests.get(
                f"{self.DATA}/v1beta1/options/quotes/latest",
                headers=self._hdrs(),
                params={"symbols": occ},
                timeout=10,
            )
            if r.status_code != 200:
                return None
            q = (r.json() or {}).get("quotes", {}).get(occ, {})
            if not q:
                return None
            bid = float(q.get("bp") or 0) or None
            ask = float(q.get("ap") or 0) or None
            return {"bid": bid, "ask": ask, "last": None, "mid": _safe_mid(bid, ask)}
        except Exception as e:
            logging.warning("AlpacaBroker.get_option_quote(%s) failed: %s", occ, e)
            return None

    def place_mleg_order(
        self,
        legs: list,
        qty: int = 1,
        tif: str = "day",
        client_order_id: Optional[str] = None,
    ) -> Optional[dict]:
        """Submit a multi-leg options order. Preserves Alpaca quirks:
        top-level qty, no position_intent.
        """
        if not legs:
            logging.error("AlpacaBroker.place_mleg_order: empty legs list")
            return None
        if len(legs) == 1:
            return self._place_single_leg(legs[0], qty, tif, client_order_id)
        payload = {
            "qty": str(qty),
            "type": "market",
            "time_in_force": tif,
            "order_class": "mleg",
            "legs": legs,
        }
        if client_order_id:
            payload["client_order_id"] = client_order_id
        if BROKER_DRY_RUN:
            logging.warning("AlpacaBroker.place_mleg_order DRY_RUN payload=%s", payload)
            return dict(DRY_RUN_SENTINEL, client_order_id=client_order_id)
        if not self.connect():
            return None
        return self._post_order(payload)

    def _place_single_leg(
        self,
        leg: dict,
        qty: int,
        tif: str,
        client_order_id: Optional[str],
    ) -> Optional[dict]:
        payload = {
            "symbol": leg["symbol"],
            "qty": str(qty),
            "side": leg["side"],
            "type": "market",
            "time_in_force": tif,
        }
        if client_order_id:
            payload["client_order_id"] = client_order_id
        if BROKER_DRY_RUN:
            logging.warning(
                "AlpacaBroker.place_mleg_order(single-leg) DRY_RUN payload=%s", payload
            )
            return dict(DRY_RUN_SENTINEL, client_order_id=client_order_id)
        if not self.connect():
            return None
        return self._post_order(payload)

    def _post_order(self, payload: dict) -> Optional[dict]:
        try:
            r = requests.post(
                f"{self.BASE}/v2/orders",
                headers=self._hdrs(),
                json=payload,
                timeout=20,
            )
            j = r.json() if r.content else {}
            if r.status_code in (200, 201):
                return {
                    "order_id": j.get("id", ""),
                    "status": j.get("status", "submitted"),
                    "filled_qty": int(float(j.get("filled_qty", 0) or 0)),
                    "avg_fill_price": float(j.get("filled_avg_price", 0) or 0),
                    "client_order_id": j.get("client_order_id"),
                }
            msg = (j.get("message") if isinstance(j, dict) else None) or str(j)[:200]
            logging.error("AlpacaBroker order failed %s: %s", r.status_code, msg[:200])
            return None
        except Exception as e:
            logging.error("AlpacaBroker.post order exception: %s", e)
            return None

    def close_position(
        self,
        legs: list,
        qty: int = 1,
        client_order_id: Optional[str] = None,
    ) -> Optional[dict]:
        """Caller is responsible for reversing leg sides — same shape as place_mleg_order.
        Single-leg falls back to a plain market order (Alpaca rejects 1-leg mleg).
        """
        return self.place_mleg_order(legs, qty=qty, client_order_id=client_order_id)

    def get_order_status(self, order_id: str) -> Optional[dict]:
        if not self.connect():
            return None
        try:
            r = requests.get(
                f"{self.BASE}/v2/orders/{order_id}",
                headers=self._hdrs(),
                timeout=10,
            )
            if r.status_code != 200:
                return None
            j = r.json()
            return {
                "order_id": j.get("id", ""),
                "status": j.get("status", "unknown"),
                "filled_qty": int(float(j.get("filled_qty", 0) or 0)),
                "avg_fill_price": float(j.get("filled_avg_price", 0) or 0),
                "client_order_id": j.get("client_order_id"),
            }
        except Exception as e:
            logging.warning("AlpacaBroker.get_order_status(%s) failed: %s", order_id, e)
            return None


# ── Factory ────────────────────────────────────────────────────────────────────


_singleton: Optional[BrokerBase] = None


def get_broker(broker_type: Optional[str] = None) -> BrokerBase:
    """Return the active broker singleton.

    Reads BROKER_TYPE env var (alpaca|ibkr) unless overridden. The IBKR
    implementation is lazy-imported so ib_insync is loaded only when needed.
    """
    global _singleton
    bt = (broker_type or BROKER_TYPE).lower()
    if _singleton is not None and _singleton.name == bt:
        return _singleton
    if bt == "alpaca":
        _singleton = AlpacaBroker()
    elif bt == "ibkr":
        from _broker_ibkr import IBKRBroker
        _singleton = IBKRBroker()
    else:
        raise ValueError(f"Unknown BROKER_TYPE={bt!r} (expected 'alpaca' or 'ibkr')")
    atexit.register(_singleton.disconnect)
    return _singleton
