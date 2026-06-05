"""Tests for the PENDING→OPEN promotion fix (2026-06-05).

The bug: broker.get_order_status(oid) returns None for already-filled orders.
The fill arrives after the status poll → entry stays PENDING → 3h timeout
marks it PHANTOM_NEVER_FILLED even though the position is live at the broker.

The fix: when order_status is None, _promote_pending_entries checks
broker.get_positions() for matching OCC leg symbols. If at least one leg
exists at the broker, auto-promote to OPEN.

Four test cases per the operator spec:
1. PENDING with order_status returning fill → promotes (existing path)
2. PENDING with order_status=None but broker has matching legs → promotes (fix)
3. PENDING with order_status=None and no broker legs → stays PENDING
4. PENDING with broker legs that don't match journal → stays PENDING
"""
from __future__ import annotations

import json
import sys
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

SCRIPTS = str(Path(__file__).resolve().parents[2] / "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

_BROKER_MOD = MagicMock()
_BROKER_MOD.BrokerBase = type("FakeBrokerBase", (), {})
_BROKER_MOD.DRY_RUN_SENTINEL = {"DRY_RUN": True}
sys.modules.setdefault("broker", _BROKER_MOD)

import position_monitor as pm


def _pending_trade(tid, symbol, arm_id="a", legs=None, age_hours=0.5):
    ts = (datetime.now(timezone.utc) - timedelta(hours=age_hours)).astimezone().isoformat()
    if legs is None:
        legs = [
            {"symbol": f"{symbol}260618C00100000", "side": "buy"},
            {"symbol": f"{symbol}260618C00110000", "side": "sell"},
        ]
    return {
        "id": tid,
        "status": "PENDING",
        "state": "ACKED",
        "source": f"agent_gamma_arm_{arm_id}",
        "arm_id": arm_id,
        "symbol": symbol,
        "order_id": "123456",
        "fill_status": "Submitted",
        "filled_qty": 0,
        "max_risk": 100.0,
        "timestamp": ts,
        "legs": legs,
    }


@pytest.fixture
def mock_broker():
    b = MagicMock()
    b.get_order_status.return_value = None
    b.get_positions.return_value = []
    return b


@pytest.fixture(autouse=True)
def _no_journal_write(monkeypatch):
    """Block actual journal writes — we inspect the updates dict instead."""
    monkeypatch.setattr(pm, "rewrite_journal_atomic", lambda updates: True)
    monkeypatch.setattr(pm, "DRY_RUN", False)
    monkeypatch.setattr(pm, "post_discord", lambda *a, **k: None)
    monkeypatch.setattr(pm, "_decrement_arm_cash", lambda *a, **k: None)


# ── Test 1: order_status returns fill → promotes ─────────────────────────────

def test_promotes_when_order_status_returns_filled(mock_broker, monkeypatch):
    """Original path: get_order_status sees the fill directly."""
    mock_broker.get_order_status.return_value = {
        "status": "Filled", "filled_qty": 1, "avg_fill_price": 1.50
    }
    trade = _pending_trade("T001", "SPY")
    all_trades = [trade]

    # Capture what rewrite_journal_atomic receives
    captured = {}
    monkeypatch.setattr(pm, "rewrite_journal_atomic", lambda u: captured.update(u) or True)

    pm._promote_pending_entries(mock_broker, all_trades)
    assert "T001" in captured
    assert captured["T001"]["status"] == "OPEN"
    assert captured["T001"]["promotion_method"] == "order_status_filled"


# ── Test 2: order_status=None but broker has matching legs → promotes (FIX) ──

def test_promotes_via_broker_position_match(mock_broker, monkeypatch):
    """THE BUG FIX: get_order_status returns None (fill already done), but
    broker.get_positions() shows the trade's legs → auto-promote."""
    mock_broker.get_order_status.return_value = None
    mock_broker.get_positions.return_value = [
        {"symbol": "SPY260618C00100000", "qty": 1},
        {"symbol": "SPY260618C00110000", "qty": -1},
    ]
    trade = _pending_trade("T002", "SPY")
    all_trades = [trade]

    captured = {}
    monkeypatch.setattr(pm, "rewrite_journal_atomic", lambda u: captured.update(u) or True)

    pm._promote_pending_entries(mock_broker, all_trades)
    assert "T002" in captured
    assert captured["T002"]["status"] == "OPEN"
    assert captured["T002"]["promotion_method"] == "broker_position_match"


# ── Test 3: order_status=None and no broker legs → stays PENDING ─────────────

def test_stays_pending_when_no_fill_no_position(mock_broker, monkeypatch):
    """Neither order_status nor positions match → trade stays PENDING
    (waits for fill or phantom timeout)."""
    mock_broker.get_order_status.return_value = None
    mock_broker.get_positions.return_value = []
    trade = _pending_trade("T003", "SPY", age_hours=0.5)  # not past 3h timeout
    all_trades = [trade]

    captured = {}
    monkeypatch.setattr(pm, "rewrite_journal_atomic", lambda u: captured.update(u) or True)

    pm._promote_pending_entries(mock_broker, all_trades)
    assert "T003" not in captured  # no update = stays PENDING


# ── Test 4: broker has legs but they DON'T match the journal → stays PENDING ─

def test_stays_pending_when_broker_legs_dont_match(mock_broker, monkeypatch):
    """Broker has positions for a DIFFERENT symbol — journal legs don't match
    → don't silently promote the wrong trade."""
    mock_broker.get_order_status.return_value = None
    mock_broker.get_positions.return_value = [
        {"symbol": "AAPL260618C00200000", "qty": 1},  # wrong symbol
    ]
    trade = _pending_trade("T004", "SPY", age_hours=0.5)
    all_trades = [trade]

    captured = {}
    monkeypatch.setattr(pm, "rewrite_journal_atomic", lambda u: captured.update(u) or True)

    pm._promote_pending_entries(mock_broker, all_trades)
    assert "T004" not in captured


# ── Test 5: partial leg match still promotes (at least one leg at broker) ────

def test_promotes_on_partial_leg_match(mock_broker, monkeypatch):
    """If only one leg is at the broker (e.g. the short hasn't settled yet),
    the trade should still promote — at least one leg confirms the fill."""
    mock_broker.get_order_status.return_value = None
    mock_broker.get_positions.return_value = [
        {"symbol": "SPY260618C00100000", "qty": 1},
        # SPY260618C00110000 is missing
    ]
    trade = _pending_trade("T005", "SPY")
    all_trades = [trade]

    captured = {}
    monkeypatch.setattr(pm, "rewrite_journal_atomic", lambda u: captured.update(u) or True)

    pm._promote_pending_entries(mock_broker, all_trades)
    assert "T005" in captured
    assert captured["T005"]["status"] == "OPEN"
    assert captured["T005"]["promotion_method"] == "broker_position_match"


# ── Test 6: broker.get_positions failure → falls through gracefully ──────────

def test_position_fetch_failure_falls_through(mock_broker, monkeypatch):
    """If broker.get_positions raises, promotion still works via order_status
    path; the position-match path is just skipped."""
    mock_broker.get_order_status.return_value = None
    mock_broker.get_positions.side_effect = RuntimeError("broker down")
    trade = _pending_trade("T006", "SPY", age_hours=0.5)
    all_trades = [trade]

    captured = {}
    monkeypatch.setattr(pm, "rewrite_journal_atomic", lambda u: captured.update(u) or True)

    pm._promote_pending_entries(mock_broker, all_trades)
    # Neither path matched → stays PENDING (no error, no crash)
    assert "T006" not in captured


# ── Test 7: timeout still works when no match found ──────────────────────────

def test_phantom_timeout_still_fires_when_no_match(mock_broker, monkeypatch):
    """The timeout path (>3h) still fires if neither order_status nor
    position match produces a promotion."""
    mock_broker.get_order_status.return_value = None
    mock_broker.get_positions.return_value = []
    trade = _pending_trade("T007", "SPY", age_hours=4.0)  # past 3h timeout
    all_trades = [trade]

    captured = {}
    monkeypatch.setattr(pm, "rewrite_journal_atomic", lambda u: captured.update(u) or True)

    pm._promote_pending_entries(mock_broker, all_trades)
    assert "T007" in captured
    assert captured["T007"]["status"] == "PHANTOM_NEVER_FILLED"
    assert "pending_timeout" in captured["T007"]["close_reason"]
