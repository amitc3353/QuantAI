"""Unit tests for gamma/spread_verifier.py — added 2026-05-09 with universe expansion.

The verifier pulls live ATM bid/ask for each universe symbol, computes
spread%, and writes pass/blocked status to gamma_spread_status.json.

Result schema (corrected per 2026-05-09 review):
  - passed: True   → clean, scanner allows
  - passed: False, blocked_reason: "spread_too_wide"          → scanner BLOCKS
  - passed: False, blocked_reason: "fetch_failed"             → scanner ALLOWS (fail-open)
  - passed: False, blocked_reason: "permanent_block_3_strikes" → scanner BLOCKS

Scanner behavior:
  - blocked_reason in {"spread_too_wide", "permanent_block_3_strikes"} → reject
  - blocked_reason == "fetch_failed" → fail-open (allow)
  - state file missing → fail-open (allow all)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from gamma import spread_verifier as sv  # noqa: E402


# ── verify_one ──────────────────────────────────────────────────────────


class TestVerifyOne:
    """Per-symbol verification logic + result schema."""

    def _mock_chain(self, bid_call, ask_call, bid_put, ask_put, last_close=200.0):
        """Build a fake yfinance chain with one expiry and one ATM strike each side."""
        import pandas as pd

        # Use real DataFrames so .iloc/.loc work like production
        calls = pd.DataFrame([{"strike": 200.0, "bid": bid_call, "ask": ask_call}])
        puts = pd.DataFrame([{"strike": 200.0, "bid": bid_put, "ask": ask_put}])
        chain = MagicMock(calls=calls, puts=puts)

        ticker = MagicMock()
        ticker.options = ("2026-05-29",)
        ticker.option_chain.return_value = chain
        # last close for ATM-strike selection
        ticker.history.return_value = pd.DataFrame({"Close": [last_close]})
        return ticker

    def test_passes_clean_spread(self):
        """bid=10, ask=10.10, mid=10.05 → spread = 0.10/10.05 ≈ 1% < 5% → PASS."""
        with patch("gamma.spread_verifier.yf") as yf_mock:
            yf_mock.Ticker.return_value = self._mock_chain(
                bid_call=10.0, ask_call=10.10, bid_put=10.0, ask_put=10.10
            )
            r = sv.verify_one("AAPL")
        assert r["symbol"] == "AAPL"
        assert r["passed"] is True
        assert r["spread_pct"] < 5.0
        assert r.get("blocked_reason") is None
        assert "expiry_used" in r

    def test_blocks_wide_spread(self):
        """bid=10, ask=11, mid=10.5 → spread = 1.0/10.5 ≈ 9.5% > 5% → BLOCKED."""
        with patch("gamma.spread_verifier.yf") as yf_mock:
            yf_mock.Ticker.return_value = self._mock_chain(
                bid_call=10.0, ask_call=11.0, bid_put=10.0, ask_put=11.0
            )
            r = sv.verify_one("XYZ")
        assert r["symbol"] == "XYZ"
        assert r["passed"] is False
        assert r["blocked_reason"] == "spread_too_wide"
        assert r["spread_pct"] > 5.0

    def test_passes_when_one_side_clean(self):
        """If ATM call is clean (1%) but ATM put is wide (10%), still PASS — verifier
        accepts either side per Part B §3 ('< 5% of mid for at least one of the two')."""
        with patch("gamma.spread_verifier.yf") as yf_mock:
            yf_mock.Ticker.return_value = self._mock_chain(
                bid_call=10.0, ask_call=10.10,   # clean
                bid_put=10.0, ask_put=11.0,       # wide
            )
            r = sv.verify_one("AAPL")
        assert r["passed"] is True

    def test_handles_missing_chain_fail_open(self):
        """No expiries returned → fail-OPEN with blocked_reason=fetch_failed."""
        with patch("gamma.spread_verifier.yf") as yf_mock:
            tk = MagicMock()
            tk.options = ()
            yf_mock.Ticker.return_value = tk
            r = sv.verify_one("ABC")
        assert r["symbol"] == "ABC"
        assert r["passed"] is False
        assert r["blocked_reason"] == "fetch_failed"
        assert "error" in r

    def test_handles_fetch_exception_fail_open(self):
        """yfinance raises → fail-OPEN with blocked_reason=fetch_failed."""
        with patch("gamma.spread_verifier.yf") as yf_mock:
            yf_mock.Ticker.side_effect = ConnectionError("network down")
            r = sv.verify_one("DEF")
        assert r["symbol"] == "DEF"
        assert r["passed"] is False
        assert r["blocked_reason"] == "fetch_failed"
        assert "ConnectionError" in r["error"] or "network" in r["error"]


# ── verify_all + state-file aggregation ─────────────────────────────────


class TestVerifyAll:
    def test_aggregates_results(self):
        with patch("gamma.spread_verifier.verify_one") as v1:
            v1.side_effect = [
                {"symbol": "AAPL", "passed": True, "spread_pct": 0.5, "expiry_used": "x"},
                {"symbol": "XYZ", "passed": False, "blocked_reason": "spread_too_wide",
                 "spread_pct": 12.0, "expiry_used": "x"},
                {"symbol": "ABC", "passed": False, "blocked_reason": "fetch_failed",
                 "error": "no chain"},
            ]
            payload = sv.verify_all(["AAPL", "XYZ", "ABC"], previous_state=None)

        assert payload["universe_size"] == 3
        assert payload["n_passed"] == 1
        assert payload["n_blocked"] == 1     # spread_too_wide only
        assert payload["n_fetch_failed"] == 1
        assert len(payload["results"]) == 3

    def test_consecutive_fail_count_increments(self):
        """If a symbol failed last week, this week's fetch-fail bumps counter to 2."""
        prev = {
            "consecutive_fail_counts": {"ABC": 1, "DEF": 0},
            "results": [],
        }
        with patch("gamma.spread_verifier.verify_one") as v1:
            v1.side_effect = [
                {"symbol": "ABC", "passed": False, "blocked_reason": "fetch_failed", "error": "x"},
                {"symbol": "DEF", "passed": True, "spread_pct": 0.5, "expiry_used": "x"},
            ]
            payload = sv.verify_all(["ABC", "DEF"], previous_state=prev)

        assert payload["consecutive_fail_counts"]["ABC"] == 2
        assert payload["consecutive_fail_counts"]["DEF"] == 0  # reset on success

    def test_three_strikes_escalates_to_permanent_block(self):
        """3rd consecutive fetch-fail → blocked_reason becomes permanent_block_3_strikes."""
        prev = {
            "consecutive_fail_counts": {"ABC": 2},
            "results": [],
        }
        with patch("gamma.spread_verifier.verify_one") as v1:
            v1.side_effect = [
                {"symbol": "ABC", "passed": False, "blocked_reason": "fetch_failed", "error": "x"},
            ]
            payload = sv.verify_all(["ABC"], previous_state=prev)

        result = payload["results"][0]
        assert result["passed"] is False
        assert result["blocked_reason"] == "permanent_block_3_strikes"
        assert payload["consecutive_fail_counts"]["ABC"] == 3
        assert payload["n_permanent_blocks"] == 1


class TestStateFile:
    def test_atomic_write(self, tmp_path):
        path = tmp_path / "spread_status.json"
        payload = {
            "verified_at": "2026-05-11T13:30:42-04:00",
            "universe_size": 1,
            "n_passed": 1, "n_blocked": 0, "n_fetch_failed": 0, "n_permanent_blocks": 0,
            "results": [{"symbol": "AAPL", "passed": True, "spread_pct": 0.5}],
            "consecutive_fail_counts": {},
        }
        sv.write_status(payload, path)
        assert path.exists()
        loaded = json.loads(path.read_text())
        assert loaded["results"][0]["symbol"] == "AAPL"

    def test_atomic_write_no_partial_on_crash(self, tmp_path):
        """Writer must use temp+rename so a crash mid-write doesn't leave a partial file."""
        path = tmp_path / "spread_status.json"
        path.write_text('{"old": "data"}')  # pre-existing
        sv.write_status({"results": [{"symbol": "AAPL", "passed": True}]}, path)
        loaded = json.loads(path.read_text())
        assert "old" not in loaded
        assert loaded["results"][0]["symbol"] == "AAPL"

    def test_load_status_missing_returns_none(self, tmp_path):
        assert sv.load_status(tmp_path / "missing.json") is None

    def test_load_status_existing_returns_dict(self, tmp_path):
        path = tmp_path / "spread_status.json"
        path.write_text(json.dumps({"results": [{"symbol": "AAPL", "passed": True}]}))
        loaded = sv.load_status(path)
        assert loaded is not None
        assert loaded["results"][0]["symbol"] == "AAPL"


# ── helpers exposed for scanner integration ────────────────────────────


class TestBlockedSymbolsHelper:
    """sv.blocked_symbols(state) returns the set scanner uses for F0 rejection.
    fetch_failed entries are EXCLUDED (fail-open semantics)."""

    def test_returns_only_actually_blocked(self):
        state = {"results": [
            {"symbol": "AAPL", "passed": True},
            {"symbol": "XYZ", "passed": False, "blocked_reason": "spread_too_wide"},
            {"symbol": "ABC", "passed": False, "blocked_reason": "fetch_failed"},
            {"symbol": "DEF", "passed": False, "blocked_reason": "permanent_block_3_strikes"},
        ]}
        blocked = sv.blocked_symbols(state)
        assert blocked == {"XYZ", "DEF"}
        # Confirm fetch_failed NOT included (fail-open)
        assert "ABC" not in blocked

    def test_returns_empty_set_when_state_none(self):
        assert sv.blocked_symbols(None) == set()

    def test_returns_empty_set_when_no_results(self):
        assert sv.blocked_symbols({"results": []}) == set()
