"""Integration tests for _memory.py — full close→reflect→retrieve loop."""
from __future__ import annotations

import importlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _reload_all(tmp_root):
    import _paths, _journal_update
    importlib.reload(_paths)
    importlib.reload(_journal_update)
    yield


def _write_trade(tmp_root, trade: dict):
    journal = tmp_root / "journal" / "paper" / "trades.jsonl"
    with open(journal, "a") as f:
        f.write(json.dumps(trade) + "\n")


def _make_alpha_trade(trade_id="A050"):
    now = datetime.now(timezone.utc).isoformat()
    return {
        "id": trade_id,
        "timestamp": now,
        "close_timestamp": now,
        "mode": "paper",
        "source": "agent_alpha",
        "symbol": "SPY",
        "strategy": "iron_condor",
        "legs": [],
        "status": "CLOSED",
        "pnl": -42.50,
        "pnl_pct": -1.7,
        "close_reason": "stop_loss",
        "holding_days": 3,
        "underlying_price": 555.0,
        "vix_at_entry": 16.5,
        "regime_at_entry": "normal",
        "thesis": "SPY range-bound thesis",
        "invalidation": "SPY breaks 558 or 552",
        "decision": {
            "conviction_score": 7,
            "thesis": "SPY range-bound thesis",
            "key_risk": "CPI in 2 days",
            "invalidation": "SPY breaks 558 or 552",
            "regime_at_entry": "normal",
            "vix_at_entry": 16.5,
        },
        "full_trajectory": {
            "bull_case": "SPY held support at 550.",
            "bear_case": "CPI could gap through support.",
            "judge_reasoning": "Selected condor due to low VIX and range compression.",
            "judge_score": 74,
            "invalidation_clause": "SPY breaks 558 or 552.",
            "skills_consulted": ["regime-classification"],
        },
    }


def _make_beta_trade(trade_id="B050"):
    now = datetime.now(timezone.utc).isoformat()
    return {
        "id": trade_id,
        "timestamp": now,
        "close_timestamp": now,
        "mode": "paper",
        "source": "agent_beta",
        "symbol": "SPX",
        "strategy": "event_strangle",
        "legs": [],
        "status": "CLOSED",
        "pnl": -117.0,
        "pnl_pct": -4.5,
        "close_reason": "stop_loss",
        "holding_days": 1,
        "decision": {
            "conviction_score": 6,
            "thesis": "Pre-CPI event strangle",
            "regime_at_entry": "pre_event",
            "vix_at_entry": 22.0,
        },
        "full_trajectory": {
            "bull_case": None,
            "bear_case": None,
            "judge_reasoning": None,
            "judge_score": None,
            "invalidation_clause": "VIX inverts",
            "skills_consulted": ["regime-classification"],
        },
    }


class TestFullCloseReflectRetrieveLoop:
    def test_round_trip(self, tmp_root, monkeypatch):
        """Write trade → write_reflection → get_lessons → verify round-trip."""
        _write_trade(tmp_root, _make_alpha_trade("A050"))
        import _memory
        importlib.reload(_memory)
        monkeypatch.setattr("_llm_call.call_llm_text", lambda *a, **kw: "CPI gap invalidated the condor thesis.")
        _memory.write_reflection("A050")

        lessons = _memory.get_lessons("agent_alpha", "SPY", k_same=5, k_cross=5)
        assert len(lessons) == 1
        assert lessons[0]["trade_id"] == "A050"
        assert lessons[0]["reflection_text"] == "CPI gap invalidated the condor thesis."
        assert lessons[0]["full_trajectory"]["judge_score"] == 74

    def test_format_output_includes_judge(self, tmp_root, monkeypatch):
        """format_lessons for the most recent same-symbol includes judge_reasoning."""
        _write_trade(tmp_root, _make_alpha_trade("A050"))
        import _memory
        importlib.reload(_memory)
        monkeypatch.setattr("_llm_call.call_llm_text", lambda *a, **kw: "Reflection for A050.")
        _memory.write_reflection("A050")

        text = _memory.format_lessons("agent_alpha", "SPY")
        assert "Judge reasoning:" in text
        assert "Selected condor due to low VIX" in text
        assert "Reflection:" in text


class TestMultipleAgentsIndependent:
    def test_no_cross_contamination(self, tmp_root, monkeypatch):
        """Alpha and Beta reflections write to separate files."""
        _write_trade(tmp_root, _make_alpha_trade("A050"))
        _write_trade(tmp_root, _make_beta_trade("B050"))
        import _memory
        importlib.reload(_memory)
        monkeypatch.setattr("_llm_call.call_llm_text", lambda *a, **kw: "Reflection text.")
        _memory.write_reflection("A050")
        _memory.write_reflection("B050")

        alpha_lessons = _memory.get_lessons("agent_alpha", "SPY")
        beta_lessons = _memory.get_lessons("agent_beta", "SPX")
        assert len(alpha_lessons) == 1
        assert alpha_lessons[0]["trade_id"] == "A050"
        assert len(beta_lessons) == 1
        assert beta_lessons[0]["trade_id"] == "B050"

        # Alpha shouldn't see Beta's reflections
        alpha_all = _memory.get_lessons("agent_alpha", "SPX", k_same=10, k_cross=10)
        assert all(l["agent"] == "agent_alpha" for l in alpha_all)


class TestNightlyReconcilerRetriesStub:
    def test_retry_converts_stub_to_complete(self, tmp_root, monkeypatch):
        """Reconciler retries a failed stub and completes it."""
        import _memory
        importlib.reload(_memory)

        # Write a trade
        _write_trade(tmp_root, _make_alpha_trade("A060"))

        # Write a stub reflection directly
        stub = {
            "trade_id": "A060",
            "agent": "agent_alpha",
            "ticker": "SPY",
            "strategy": "iron_condor",
            "regime_at_entry": "normal",
            "entry_features": {},
            "decision_summary": "iron_condor on SPY.",
            "full_trajectory": None,
            "realized_return_raw": -42.50,
            "realized_return_pct": -1.7,
            "alpha_vs_spy": None,
            "reflection_text": None,
            "reflection_status": "llm_failed",
            "reflection_error": "timeout",
            "closed_at": datetime.now(timezone.utc).isoformat(),
            "close_reason": "stop_loss",
            "hold_days": 3,
            "retry_count": 0,
            "first_attempt_ts": datetime.now(timezone.utc).isoformat(),
        }
        jsonl_path = tmp_root / "memory" / "alpha_reflections.jsonl"
        jsonl_path.write_text(json.dumps(stub) + "\n")

        # Mock LLM to succeed on retry
        monkeypatch.setattr("_llm_call.call_llm_text", lambda *a, **kw: "Reconciled reflection.")

        import reflection_reconciler
        importlib.reload(reflection_reconciler)
        r, c, e = reflection_reconciler.reconcile_file(jsonl_path)
        assert r == 1
        assert c == 1
        assert e == 0

        # Verify the file now has a complete record
        rec = json.loads(jsonl_path.read_text().strip())
        assert rec["reflection_status"] == "complete"
        assert rec["reflection_text"] == "Reconciled reflection."


class TestReflectionAfterPositionMonitorClose:
    def test_hook_writes_reflection(self, tmp_root, monkeypatch):
        """Simulate position_monitor calling write_reflection after close."""
        _write_trade(tmp_root, _make_alpha_trade("A070"))
        import _memory
        importlib.reload(_memory)
        monkeypatch.setattr("_llm_call.call_llm_text", lambda *a, **kw: "Post-close reflection.")

        # Simulate what position_monitor does
        try:
            _memory.write_reflection("A070")
        except Exception:
            pytest.fail("write_reflection must never raise")

        jsonl = tmp_root / "memory" / "alpha_reflections.jsonl"
        assert jsonl.exists()
        rec = json.loads(jsonl.read_text().strip())
        assert rec["reflection_status"] == "complete"
        assert rec["trade_id"] == "A070"
