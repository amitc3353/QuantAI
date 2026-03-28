"""
Alpaca Trading Client — Execution Layer
=========================================
Handles all broker communication: order placement, portfolio queries,
market data, and position management.

Mode-aware: reads TRADING_MODE env var.
  paper → uses paper keys + paper=True
  live  → uses live keys + paper=False (Phase 3)

All orders go through the guard engine BEFORE reaching this module.
This module does NOT do its own validation — that's the guard's job.
"""

import os
import json
import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional
from pathlib import Path

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    MarketOrderRequest,
    LimitOrderRequest,
    GetOrdersRequest,
)
from alpaca.trading.enums import (
    OrderSide,
    TimeInForce,
    OrderStatus,
    QueryOrderStatus,
)
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import (
    StockLatestQuoteRequest,
    StockBarsRequest,
    StockSnapshotRequest,
)
from alpaca.data.timeframe import TimeFrame

log = logging.getLogger("alpaca-client")

# ---------------------------------------------------------------------------
# Config — mode-aware
# ---------------------------------------------------------------------------
TRADING_MODE = os.getenv("TRADING_MODE", "paper")

if TRADING_MODE == "live":
    API_KEY = os.getenv("ALPACA_LIVE_API_KEY", "")
    SECRET_KEY = os.getenv("ALPACA_LIVE_SECRET_KEY", "")
    IS_PAPER = False
else:
    API_KEY = os.getenv("ALPACA_API_KEY", "")
    SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")
    IS_PAPER = True

# Journal path — separated by mode
JOURNAL_PATH = Path(f"/app/data/memory/{TRADING_MODE}/trade_journal.jsonl")


# ---------------------------------------------------------------------------
# Client initialization
# ---------------------------------------------------------------------------
def get_trading_client() -> Optional[TradingClient]:
    if not API_KEY or not SECRET_KEY:
        log.error(f"Alpaca {TRADING_MODE} keys not configured")
        return None
    return TradingClient(API_KEY, SECRET_KEY, paper=IS_PAPER)


def get_data_client() -> Optional[StockHistoricalDataClient]:
    if not API_KEY or not SECRET_KEY:
        return None
    return StockHistoricalDataClient(API_KEY, SECRET_KEY)


# ---------------------------------------------------------------------------
# Account & Portfolio
# ---------------------------------------------------------------------------
async def get_account() -> dict:
    """Get account details: buying power, equity, P&L, etc."""
    client = get_trading_client()
    if not client:
        return {"error": "Alpaca client not configured"}
    try:
        account = client.get_account()
        return {
            "mode": TRADING_MODE,
            "status": account.status.value if account.status else "unknown",
            "buying_power": float(account.buying_power),
            "cash": float(account.cash),
            "portfolio_value": float(account.portfolio_value),
            "equity": float(account.equity),
            "last_equity": float(account.last_equity),
            "daily_pnl": float(account.equity) - float(account.last_equity),
            "daily_pnl_pct": round(
                (float(account.equity) - float(account.last_equity))
                / float(account.last_equity) * 100, 2
            ) if float(account.last_equity) > 0 else 0,
            "pattern_day_trader": account.pattern_day_trader,
            "trading_blocked": account.trading_blocked,
            "account_blocked": account.account_blocked,
        }
    except Exception as e:
        log.error(f"Failed to get account: {e}")
        return {"error": str(e)}


async def get_positions() -> list[dict]:
    """Get all open positions."""
    client = get_trading_client()
    if not client:
        return [{"error": "Alpaca client not configured"}]
    try:
        positions = client.get_all_positions()
        return [
            {
                "symbol": p.symbol,
                "qty": float(p.qty),
                "side": p.side.value if p.side else "long",
                "market_value": float(p.market_value),
                "cost_basis": float(p.cost_basis),
                "unrealized_pl": float(p.unrealized_pl),
                "unrealized_plpc": float(p.unrealized_plpc),
                "current_price": float(p.current_price),
                "avg_entry_price": float(p.avg_entry_price),
                "change_today": float(p.change_today),
            }
            for p in positions
        ]
    except Exception as e:
        log.error(f"Failed to get positions: {e}")
        return [{"error": str(e)}]


# ---------------------------------------------------------------------------
# Order Placement
# ---------------------------------------------------------------------------
def _generate_hash(order_data: dict) -> str:
    """Generate an 8-char hash for trade tracking."""
    raw = json.dumps(order_data, sort_keys=True) + datetime.now(timezone.utc).isoformat()
    return hashlib.sha256(raw.encode()).hexdigest()[:8]


def _log_to_journal(record: dict):
    """Append trade record to mode-specific journal."""
    JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(JOURNAL_PATH, "a") as f:
        f.write(json.dumps(record) + "\n")


async def place_market_order(
    symbol: str,
    qty: float,
    side: str,
    time_in_force: str = "day",
) -> dict:
    """
    Place a market order.
    Side: "buy" or "sell"
    Returns order confirmation with hash.
    """
    client = get_trading_client()
    if not client:
        return {"error": "Alpaca client not configured"}

    order_side = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL
    tif = TimeInForce.DAY if time_in_force == "day" else TimeInForce.GTC

    order_data = {
        "symbol": symbol.upper(),
        "qty": qty,
        "side": side,
        "type": "market",
        "mode": TRADING_MODE,
    }
    trade_hash = _generate_hash(order_data)

    try:
        request = MarketOrderRequest(
            symbol=symbol.upper(),
            qty=qty,
            side=order_side,
            time_in_force=tif,
        )
        order = client.submit_order(request)

        result = {
            "hash": trade_hash,
            "order_id": str(order.id),
            "symbol": order.symbol,
            "qty": float(order.qty) if order.qty else qty,
            "side": order.side.value if order.side else side,
            "type": "market",
            "status": order.status.value if order.status else "submitted",
            "submitted_at": order.submitted_at.isoformat() if order.submitted_at else None,
            "filled_avg_price": float(order.filled_avg_price) if order.filled_avg_price else None,
            "mode": TRADING_MODE,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        _log_to_journal(result)
        log.info(f"Order submitted [{trade_hash}]: {side} {qty} {symbol} ({TRADING_MODE})")
        return result

    except Exception as e:
        log.error(f"Order failed: {e}")
        error_result = {
            "hash": trade_hash,
            "error": str(e),
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "mode": TRADING_MODE,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        _log_to_journal(error_result)
        return error_result


async def place_limit_order(
    symbol: str,
    qty: float,
    side: str,
    limit_price: float,
    time_in_force: str = "day",
) -> dict:
    """Place a limit order."""
    client = get_trading_client()
    if not client:
        return {"error": "Alpaca client not configured"}

    order_side = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL
    tif = TimeInForce.DAY if time_in_force == "day" else TimeInForce.GTC

    order_data = {
        "symbol": symbol.upper(),
        "qty": qty,
        "side": side,
        "type": "limit",
        "limit_price": limit_price,
        "mode": TRADING_MODE,
    }
    trade_hash = _generate_hash(order_data)

    try:
        request = LimitOrderRequest(
            symbol=symbol.upper(),
            qty=qty,
            side=order_side,
            time_in_force=tif,
            limit_price=limit_price,
        )
        order = client.submit_order(request)

        result = {
            "hash": trade_hash,
            "order_id": str(order.id),
            "symbol": order.symbol,
            "qty": float(order.qty) if order.qty else qty,
            "side": order.side.value if order.side else side,
            "type": "limit",
            "limit_price": limit_price,
            "status": order.status.value if order.status else "submitted",
            "submitted_at": order.submitted_at.isoformat() if order.submitted_at else None,
            "mode": TRADING_MODE,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        _log_to_journal(result)
        log.info(f"Limit order [{trade_hash}]: {side} {qty} {symbol} @ ${limit_price} ({TRADING_MODE})")
        return result

    except Exception as e:
        log.error(f"Limit order failed: {e}")
        return {"hash": trade_hash, "error": str(e), "mode": TRADING_MODE}


# ---------------------------------------------------------------------------
# Order Management
# ---------------------------------------------------------------------------
async def get_orders(status: str = "open", limit: int = 20) -> list[dict]:
    """Get orders by status: open, closed, all."""
    client = get_trading_client()
    if not client:
        return [{"error": "Alpaca client not configured"}]
    try:
        status_filter = {
            "open": QueryOrderStatus.OPEN,
            "closed": QueryOrderStatus.CLOSED,
            "all": QueryOrderStatus.ALL,
        }.get(status, QueryOrderStatus.OPEN)

        request = GetOrdersRequest(status=status_filter, limit=limit)
        orders = client.get_orders(request)

        return [
            {
                "order_id": str(o.id),
                "symbol": o.symbol,
                "qty": float(o.qty) if o.qty else 0,
                "side": o.side.value if o.side else "?",
                "type": o.type.value if o.type else "?",
                "status": o.status.value if o.status else "?",
                "filled_avg_price": float(o.filled_avg_price) if o.filled_avg_price else None,
                "submitted_at": o.submitted_at.isoformat() if o.submitted_at else None,
                "filled_at": o.filled_at.isoformat() if o.filled_at else None,
            }
            for o in orders
        ]
    except Exception as e:
        log.error(f"Failed to get orders: {e}")
        return [{"error": str(e)}]


async def cancel_order(order_id: str) -> dict:
    """Cancel a specific order."""
    client = get_trading_client()
    if not client:
        return {"error": "Alpaca client not configured"}
    try:
        client.cancel_order_by_id(order_id)
        log.info(f"Order cancelled: {order_id}")
        return {"status": "cancelled", "order_id": order_id}
    except Exception as e:
        log.error(f"Cancel failed: {e}")
        return {"error": str(e)}


async def cancel_all_orders() -> dict:
    """Cancel all open orders. Used for emergency stop."""
    client = get_trading_client()
    if not client:
        return {"error": "Alpaca client not configured"}
    try:
        client.cancel_orders()
        log.info("All orders cancelled")
        return {"status": "all_cancelled", "mode": TRADING_MODE}
    except Exception as e:
        log.error(f"Cancel all failed: {e}")
        return {"error": str(e)}


async def close_position(symbol: str) -> dict:
    """Close an entire position for a symbol."""
    client = get_trading_client()
    if not client:
        return {"error": "Alpaca client not configured"}
    try:
        client.close_position(symbol.upper())
        log.info(f"Position closed: {symbol}")
        return {"status": "closed", "symbol": symbol, "mode": TRADING_MODE}
    except Exception as e:
        log.error(f"Close position failed: {e}")
        return {"error": str(e)}


async def close_all_positions() -> dict:
    """Close all positions. Nuclear option."""
    client = get_trading_client()
    if not client:
        return {"error": "Alpaca client not configured"}
    try:
        client.close_all_positions(cancel_orders=True)
        log.info("All positions closed")
        return {"status": "all_closed", "mode": TRADING_MODE}
    except Exception as e:
        log.error(f"Close all failed: {e}")
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Market Data
# ---------------------------------------------------------------------------
async def get_latest_quote(symbol: str) -> dict:
    """Get the latest quote for a symbol."""
    client = get_data_client()
    if not client:
        return {"error": "Data client not configured"}
    try:
        request = StockLatestQuoteRequest(symbol_or_symbols=symbol.upper())
        quotes = client.get_stock_latest_quote(request)
        quote = quotes.get(symbol.upper())
        if not quote:
            return {"error": f"No quote for {symbol}"}
        return {
            "symbol": symbol.upper(),
            "bid": float(quote.bid_price) if quote.bid_price else 0,
            "ask": float(quote.ask_price) if quote.ask_price else 0,
            "bid_size": int(quote.bid_size) if quote.bid_size else 0,
            "ask_size": int(quote.ask_size) if quote.ask_size else 0,
            "mid": round((float(quote.bid_price or 0) + float(quote.ask_price or 0)) / 2, 2),
        }
    except Exception as e:
        log.error(f"Quote failed for {symbol}: {e}")
        return {"error": str(e)}


async def get_snapshot(symbol: str) -> dict:
    """Get a full snapshot: latest trade, quote, bar."""
    client = get_data_client()
    if not client:
        return {"error": "Data client not configured"}
    try:
        request = StockSnapshotRequest(symbol_or_symbols=symbol.upper())
        snapshots = client.get_stock_snapshot(request)
        snap = snapshots.get(symbol.upper())
        if not snap:
            return {"error": f"No snapshot for {symbol}"}
        result = {"symbol": symbol.upper()}
        if snap.latest_trade:
            result["price"] = float(snap.latest_trade.price)
        if snap.latest_quote:
            result["bid"] = float(snap.latest_quote.bid_price or 0)
            result["ask"] = float(snap.latest_quote.ask_price or 0)
        if snap.daily_bar:
            result["open"] = float(snap.daily_bar.open)
            result["high"] = float(snap.daily_bar.high)
            result["low"] = float(snap.daily_bar.low)
            result["close"] = float(snap.daily_bar.close)
            result["volume"] = int(snap.daily_bar.volume)
        if hasattr(snap, 'prev_daily_bar') and snap.prev_daily_bar:
            result["prev_close"] = float(snap.prev_daily_bar.close)
            if "price" in result:
                result["change"] = round(result["price"] - result["prev_close"], 2)
                result["change_pct"] = round(result["change"] / result["prev_close"] * 100, 2)
        return result
    except Exception as e:
        log.error(f"Snapshot failed for {symbol}: {e}")
        return {"error": str(e)}
