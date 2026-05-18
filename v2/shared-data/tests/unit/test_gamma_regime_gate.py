"""Unit tests for the regime gate in gamma_agent.run_scan_4arm().

Tests cover all three decision branches (skip / half / normal), every
fail-open degradation path, and the critical ordering invariant that a
"skip" decision returns from run_scan_4arm BEFORE any
setup["regime_size_multiplier"] tagging.

Added 2026-05-18 as part of commit 1 (regime gate standalone).
"""
from __future__ import annotations

import json
import math
import os
import sys
import time as _time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

# Import after sys.path setup
import gamma_agent as ga


# ── Helpers ──────────────────────────────────────────────────────────────


def _write_intel(path: Path, macro: dict | None = None,
                 symbols: dict | None = None) -> None:
    """Write a synthetic market_intelligence.json."""
    intel: dict = {}
    if macro is not None:
        intel["macro"] = macro
    if symbols is not None:
        intel["symbols"] = symbols
    path.write_text(json.dumps(intel))


def _normal_macro(**overrides) -> dict:
    """Baseline macro dict where everything is calm."""
    m = {
        "vix": 16.0,
        "vix_regime": "normal",
        "vix_term_structure": "contango",
    }
    m.update(overrides)
    return m


def _normal_symbols(**spy_overrides) -> dict:
    spy = {"above_ema200": True}
    spy.update(spy_overrides)
    return {"SPY": spy}


# ── Decision branch tests ───────────────────────────────────────────────


class TestRegimeGateDecisionBranches:
    """Six branches: 2 skips, 2 halfs, 1 explicit normal, 1 elevated-but-normal."""

    def test_skip_vix_danger(self, tmp_path):
        p = tmp_path / "intel.json"
        _write_intel(p, macro=_normal_macro(vix=32.0, vix_regime="danger"),
                     symbols=_normal_symbols())
        decision, mult, detail = ga._check_regime_gate(p)
        assert decision == "skip"
        assert detail["vix"] == 32.0
        assert detail["vix_regime"] == "danger"

    def test_skip_vix_halt(self, tmp_path):
        p = tmp_path / "intel.json"
        _write_intel(p, macro=_normal_macro(vix=38.0, vix_regime="HALT"),
                     symbols=_normal_symbols())
        decision, mult, detail = ga._check_regime_gate(p)
        assert decision == "skip"
        assert detail["vix_regime"] == "HALT"

    def test_skip_high_backwardation(self, tmp_path):
        p = tmp_path / "intel.json"
        _write_intel(p, macro=_normal_macro(vix=26.0, vix_regime="high",
                                            vix_term_structure="backwardation"),
                     symbols=_normal_symbols())
        decision, mult, detail = ga._check_regime_gate(p)
        assert decision == "skip"
        assert detail["term_structure"] == "backwardation"

    def test_half_high_contango(self, tmp_path):
        p = tmp_path / "intel.json"
        _write_intel(p, macro=_normal_macro(vix=26.0, vix_regime="high",
                                            vix_term_structure="contango"),
                     symbols=_normal_symbols())
        decision, mult, detail = ga._check_regime_gate(p)
        assert decision == "half"
        assert mult == 0.5

    def test_half_spy_below_ema200(self, tmp_path):
        p = tmp_path / "intel.json"
        _write_intel(p, macro=_normal_macro(vix=16.0, vix_regime="normal"),
                     symbols=_normal_symbols(above_ema200=False))
        decision, mult, detail = ga._check_regime_gate(p)
        assert decision == "half"
        assert mult == 0.5
        assert detail["spy_above_ema200"] is False

    def test_normal_low_vix(self, tmp_path):
        p = tmp_path / "intel.json"
        _write_intel(p, macro=_normal_macro(vix=14.0, vix_regime="normal"),
                     symbols=_normal_symbols())
        decision, mult, detail = ga._check_regime_gate(p)
        assert decision == "normal"
        assert mult == 1.0

    def test_elevated_vix_above_ema_is_normal(self, tmp_path):
        """VIX 20-24 (elevated, not high) with SPY above EMA200 → normal."""
        p = tmp_path / "intel.json"
        _write_intel(p, macro=_normal_macro(vix=22.0, vix_regime="elevated"),
                     symbols=_normal_symbols(above_ema200=True))
        decision, mult, detail = ga._check_regime_gate(p)
        assert decision == "normal"
        assert mult == 1.0


# ── Fail-open tests ─────────────────────────────────────────────────────


class TestRegimeGateFailOpen:
    """Every degradation path must return ("normal", 1.0, ...)."""

    def test_missing_file(self, tmp_path):
        p = tmp_path / "nonexistent.json"
        decision, mult, _ = ga._check_regime_gate(p)
        assert decision == "normal"
        assert mult == 1.0

    def test_stale_file(self, tmp_path):
        p = tmp_path / "intel.json"
        _write_intel(p, macro=_normal_macro(vix=32.0, vix_regime="danger"),
                     symbols=_normal_symbols())
        # Backdate mtime by 25 hours
        stale_mtime = _time.time() - (25 * 3600)
        os.utime(p, (stale_mtime, stale_mtime))
        decision, mult, detail = ga._check_regime_gate(p)
        assert decision == "normal"
        assert mult == 1.0
        assert detail["intel_age_hours"] > 24

    def test_missing_macro_key(self, tmp_path):
        p = tmp_path / "intel.json"
        p.write_text(json.dumps({"symbols": {"SPY": {"above_ema200": True}}}))
        decision, mult, _ = ga._check_regime_gate(p)
        assert decision == "normal"

    def test_vix_none(self, tmp_path):
        p = tmp_path / "intel.json"
        _write_intel(p, macro={"vix": None, "vix_regime": "danger"},
                     symbols=_normal_symbols())
        decision, mult, _ = ga._check_regime_gate(p)
        assert decision == "normal"

    def test_vix_nan(self, tmp_path):
        p = tmp_path / "intel.json"
        _write_intel(p, macro={"vix": float("nan"), "vix_regime": "danger"},
                     symbols=_normal_symbols())
        decision, mult, _ = ga._check_regime_gate(p)
        assert decision == "normal"

    def test_vix_regime_missing(self, tmp_path):
        p = tmp_path / "intel.json"
        _write_intel(p, macro={"vix": 32.0},
                     symbols=_normal_symbols())
        decision, mult, _ = ga._check_regime_gate(p)
        assert decision == "normal"

    def test_term_structure_missing_with_high_vix(self, tmp_path):
        """VIX high but term_structure absent → can't check backwardation,
        but VIX-level check still fires → half (not skip)."""
        p = tmp_path / "intel.json"
        _write_intel(p, macro={"vix": 26.0, "vix_regime": "high"},
                     symbols=_normal_symbols())
        decision, mult, _ = ga._check_regime_gate(p)
        assert decision == "half"
        assert mult == 0.5

    def test_spy_ema_missing(self, tmp_path):
        """SPY data absent → skip SPY check, evaluate VIX-level only."""
        p = tmp_path / "intel.json"
        _write_intel(p, macro=_normal_macro(vix=16.0, vix_regime="normal"))
        # No symbols key at all
        decision, mult, detail = ga._check_regime_gate(p)
        assert decision == "normal"
        assert detail["spy_above_ema200"] is None

    def test_json_decode_error(self, tmp_path):
        p = tmp_path / "intel.json"
        p.write_text("NOT VALID JSON {{{")
        decision, mult, _ = ga._check_regime_gate(p)
        assert decision == "normal"


# ── Ordering invariant ──────────────────────────────────────────────────


class TestSkipReturnsBeforeMultiplierTagging:
    """The 'skip' decision path in run_scan_4arm() must return BEFORE any
    setup gets tagged with regime_size_multiplier. This pins the invariant
    so a future refactor can't let multiplier=0.0 flow into sizing math
    (qty calculation uses it as floor(equity * 0.01 * mult / risk)).

    Strategy: monkeypatch run_scan_4arm's dependencies, make the regime
    gate return 'skip', verify that setups are never tagged.
    """

    def test_skip_returns_before_regime_tagging(self, tmp_path, monkeypatch):
        """When regime gate returns 'skip', run_scan_4arm must return 0
        without ever writing regime_size_multiplier to any setup dict."""
        import importlib
        import gamma_agent as _ga

        # Redirect runtime paths so INDICATOR_CACHE etc. don't hit /root
        root = tmp_path / "quantai_runtime"
        for sub in ("journal/paper", "cache", "logs"):
            (root / sub).mkdir(parents=True)

        # Patch INDICATOR_CACHE directly on the module (avoids full reload)
        monkeypatch.setattr(
            _ga, "INDICATOR_CACHE", root / "cache" / "gamma_indicator_cache.json",
        )

        # Controlled setups — we'll check these for tagging after the call
        fake_setups = [
            {"symbol": "AAPL", "rsi_10": 28.0, "close": 200.0, "sma_200": 180.0},
            {"symbol": "MSFT", "rsi_10": 27.0, "close": 350.0, "sma_200": 320.0},
        ]

        # Mock all external dependencies of run_scan_4arm
        monkeypatch.setattr(
            "gamma.scanner.scan_with_indicators",
            lambda *a, **kw: (list(fake_setups), {"AAPL": {}, "MSFT": {}}),
        )
        monkeypatch.setattr(
            "gamma.risk_check.load_journal", lambda *a, **kw: [],
        )
        mock_broker = MagicMock()
        mock_broker.connect.return_value = True
        monkeypatch.setattr("broker.get_broker", lambda: mock_broker)

        monkeypatch.setattr(_ga, "DRY_RUN", True)
        monkeypatch.setattr(_ga, "_post_discord", lambda msg: None)
        monkeypatch.setattr(_ga, "_log_ranking_decision", lambda payload: None)

        # Make regime gate return "skip"
        monkeypatch.setattr(
            _ga, "_check_regime_gate",
            lambda p: ("skip", 1.0, {"vix": 32.0, "vix_regime": "danger",
                                      "intel_present": True, "intel_age_hours": 0.1,
                                      "term_structure": "contango",
                                      "spy_above_ema200": True}),
        )

        rc = _ga.run_scan_4arm()
        assert rc == 0

        # Verify: no setup was tagged with regime_size_multiplier
        for s in fake_setups:
            assert "regime_size_multiplier" not in s, (
                f"Setup {s.get('symbol')} was tagged with regime_size_multiplier "
                f"despite skip decision — multiplier must not flow to sizing math"
            )
