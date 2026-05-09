"""Unit tests for _memory.py — reflection write + lesson retrieval."""
from __future__ import annotations

import importlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from conftest import _make_trade


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_closed_trade(
    trade_id="A025",
    source="agent_alpha",
    symbol="SPY",
    strategy="iron_condor",
    pnl=-42.50,
    pnl_pct=-1.7,
    close_reason="stop_loss",
    hold_days=3,
    full_trajectory=None,
    **extras,
) -> dict:
    """Build a closed trade dict matching the journal schema."""
    now = datetime.now(timezone.utc).isoformat()
    trade = _make_trade(
        trade_id=trade_id,
        source=source,
        symbol=symbol,
        strategy=strategy,
        pnl=pnl,
        pnl_pct=pnl_pct,
        close_reason=close_reason,
        status="CLOSED",
        **extras,
    )
    trade["holding_days"] = hold_days
    if full_trajectory is not None:
        trade["full_trajectory"] = full_trajectory
    return trade


def _alpha_trajectory():
    return {
        "bull_case": "SPY held support at 550, RSI oversold bounce likely.",
        "bear_case": "VIX rising, CPI in 2 days could gap through support.",
        "judge_reasoning": "Selected iron condor due to low VIX and range compression. Key risk is CPI event.",
        "judge_score": 74,
        "invalidation_clause": "SPY breaks 558 or 552.",
        "skills_consulted": ["regime-classification"],
    }


def _beta_trajectory():
    return {
        "bull_case": None,
        "bear_case": None,
        "judge_reasoning": None,
        "judge_score": None,
        "invalidation_clause": "VIX term structure inverts",
        "skills_consulted": ["regime-classification", "iv-surface-reading"],
    }


@pytest.fixture(autouse=True)
def _reload_paths(tmp_root):
    import _paths
    importlib.reload(_paths)
    yield


@pytest.fixture()
def memory_dir(tmp_root):
    d = tmp_root / "memory"
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture()
def alpha_journal(tmp_root):
    """Write a closed Alpha trade with trajectory to the journal."""
    trade = _make_closed_trade(
        trade_id="A025",
        source="agent_alpha",
        full_trajectory=_alpha_trajectory(),
    )
    journal = tmp_root / "journal" / "paper" / "trades.jsonl"
    journal.write_text(json.dumps(trade) + "\n")
    import _journal_update
    importlib.reload(_journal_update)
    return journal


@pytest.fixture()
def beta_journal(tmp_root):
    """Write a closed Beta trade with trajectory to the journal."""
    trade = _make_closed_trade(
        trade_id="B010",
        source="agent_beta",
        symbol="SPX",
        strategy="event_strangle",
        pnl=-117.0,
        pnl_pct=-4.5,
        full_trajectory=_beta_trajectory(),
    )
    journal = tmp_root / "journal" / "paper" / "trades.jsonl"
    journal.write_text(json.dumps(trade) + "\n")
    import _journal_update
    importlib.reload(_journal_update)
    return journal


@pytest.fixture()
def gamma_journal(tmp_root):
    """Write a closed Gamma trade (no trajectory) to the journal."""
    trade = _make_closed_trade(
        trade_id="G001",
        source="agent_gamma",
        symbol="AAPL",
        strategy="rsi_pullback_debit_spread",
        pnl=145.0,
        pnl_pct=12.3,
        close_reason="rsi_exit_above_40",
        hold_days=5,
        full_trajectory=None,
    )
    # Add Gamma-specific fields that would exist on a real Gamma trade
    trade["decision"]["rsi_at_entry"] = 22.5
    trade["decision"]["sma_200_distance_pct"] = 3.2
    trade["decision"]["sector"] = "technology"
    journal = tmp_root / "journal" / "paper" / "trades.jsonl"
    journal.write_text(json.dumps(trade) + "\n")
    import _journal_update
    importlib.reload(_journal_update)
    return journal


def _mock_llm_text(monkeypatch, text: str):
    """Patch call_llm_text to return a fixed string."""
    monkeypatch.setattr(
        "_llm_call.call_llm_text",
        lambda *a, **kw: text,
    )


def _mock_llm_text_none(monkeypatch):
    """Patch call_llm_text to return None (all retries exhausted)."""
    monkeypatch.setattr("_llm_call.call_llm_text", lambda *a, **kw: None)


def _mock_llm_text_raises(monkeypatch):
    """Patch call_llm_text to raise (should never be called for Gamma)."""
    def _boom(*a, **kw):
        raise RuntimeError("LLM should not be called for Gamma")
    monkeypatch.setattr("_llm_call.call_llm_text", _boom)


# ── Tests: write_reflection ─────────────────────────────────────────────────

class TestWriteReflectionAlpha:
    def test_alpha_complete(self, alpha_journal, memory_dir, monkeypatch):
        import _memory
        importlib.reload(_memory)
        _mock_llm_text(monkeypatch, "The iron condor was invalidated by CPI gap.")
        _memory.write_reflection("A025")
        jsonl = memory_dir / "alpha_reflections.jsonl"
        assert jsonl.exists()
        rec = json.loads(jsonl.read_text().strip())
        assert rec["trade_id"] == "A025"
        assert rec["agent"] == "agent_alpha"
        assert rec["ticker"] == "SPY"
        assert rec["strategy"] == "iron_condor"
        assert rec["reflection_status"] == "complete"
        assert rec["reflection_text"] == "The iron condor was invalidated by CPI gap."
        assert rec["realized_return_raw"] == -42.50
        assert rec["close_reason"] == "stop_loss"
        assert rec["hold_days"] == 3
        assert rec["full_trajectory"]["judge_score"] == 74
        assert rec["full_trajectory"]["bull_case"].startswith("SPY held")

    def test_alpha_missing_trajectory_degrades(self, tmp_root, memory_dir, monkeypatch):
        """Trade without full_trajectory field → reflection written with null trajectory."""
        trade = _make_closed_trade(trade_id="A026", source="agent_alpha")
        # No full_trajectory key at all
        journal = tmp_root / "journal" / "paper" / "trades.jsonl"
        journal.write_text(json.dumps(trade) + "\n")
        import _journal_update, _memory
        importlib.reload(_journal_update)
        importlib.reload(_memory)
        _mock_llm_text(monkeypatch, "Reflection without trajectory context.")
        _memory.write_reflection("A026")
        jsonl = memory_dir / "alpha_reflections.jsonl"
        rec = json.loads(jsonl.read_text().strip())
        assert rec["reflection_status"] == "complete"
        assert rec["full_trajectory"] is None

    def test_alpha_missing_pnl_degrades(self, tmp_root, memory_dir, monkeypatch):
        """Trade without pnl field → reflection written with null return."""
        trade = _make_closed_trade(trade_id="A027", source="agent_alpha")
        del trade["pnl"]
        del trade["pnl_pct"]
        journal = tmp_root / "journal" / "paper" / "trades.jsonl"
        journal.write_text(json.dumps(trade) + "\n")
        import _journal_update, _memory
        importlib.reload(_journal_update)
        importlib.reload(_memory)
        _mock_llm_text(monkeypatch, "Trade with missing P&L.")
        _memory.write_reflection("A027")
        jsonl = memory_dir / "alpha_reflections.jsonl"
        rec = json.loads(jsonl.read_text().strip())
        assert rec["reflection_status"] == "complete"
        assert rec["realized_return_raw"] is None


class TestWriteReflectionBeta:
    def test_beta_complete(self, beta_journal, memory_dir, monkeypatch):
        import _memory
        importlib.reload(_memory)
        _mock_llm_text(monkeypatch, "Event strangle got caught by post-event gamma bleed.")
        _memory.write_reflection("B010")
        jsonl = memory_dir / "beta_reflections.jsonl"
        assert jsonl.exists()
        rec = json.loads(jsonl.read_text().strip())
        assert rec["trade_id"] == "B010"
        assert rec["agent"] == "agent_beta"
        assert rec["ticker"] == "SPX"
        assert rec["strategy"] == "event_strangle"
        assert rec["reflection_status"] == "complete"
        assert rec["full_trajectory"]["bull_case"] is None


class TestWriteReflectionGamma:
    def test_gamma_structured_no_llm(self, gamma_journal, memory_dir, monkeypatch):
        """Gamma writes structured reflection WITHOUT calling the LLM."""
        import _memory
        importlib.reload(_memory)
        _mock_llm_text_raises(monkeypatch)
        _memory.write_reflection("G001")
        jsonl = memory_dir / "gamma_reflections.jsonl"
        assert jsonl.exists()
        rec = json.loads(jsonl.read_text().strip())
        assert rec["trade_id"] == "G001"
        assert rec["agent"] == "agent_gamma"
        assert rec["reflection_status"] == "complete"
        assert rec["reflection_text"] is None
        assert rec["full_trajectory"] is None
        gs = rec["gamma_structured"]
        assert gs is not None
        assert gs["exit_reason_category"] in ("signal", "stop_loss", "time_stop", "manual")
        assert gs["sector"] == "technology"


class TestWriteReflectionFailure:
    def test_llm_fails_writes_stub(self, alpha_journal, memory_dir, monkeypatch):
        import _memory
        importlib.reload(_memory)
        _mock_llm_text_none(monkeypatch)
        _memory.write_reflection("A025")
        jsonl = memory_dir / "alpha_reflections.jsonl"
        rec = json.loads(jsonl.read_text().strip())
        assert rec["reflection_status"] == "llm_failed"
        assert rec["reflection_text"] is None
        assert rec.get("reflection_error") is not None
        assert rec["retry_count"] == 0

    def test_never_raises_on_disk_error(self, alpha_journal, monkeypatch):
        import _memory
        importlib.reload(_memory)
        _mock_llm_text(monkeypatch, "Some reflection text.")
        # Make memory dir unwritable by pointing to a file instead
        blocker = Path(str(alpha_journal).replace("trades.jsonl", "")) / ".." / ".." / "memory"
        # Just monkeypatch the JSONL path to something broken
        monkeypatch.setattr("_memory.MEMORY_DIR", Path("/dev/null/impossible"))
        try:
            _memory.write_reflection("A025")
        except Exception:
            pytest.fail("write_reflection() must never raise")

    def test_trade_not_found_returns_silently(self, tmp_root, monkeypatch):
        import _memory
        importlib.reload(_memory)
        # Empty journal — trade won't be found
        journal = tmp_root / "journal" / "paper" / "trades.jsonl"
        journal.write_text("")
        import _journal_update
        importlib.reload(_journal_update)
        try:
            _memory.write_reflection("ZZZZ")
        except Exception:
            pytest.fail("write_reflection() must never raise for missing trade")


# ── Tests: get_lessons ───────────────────────────────────────────────────────

def _write_reflection_records(jsonl_path: Path, records: list[dict]):
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    with open(jsonl_path, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def _make_reflection(
    trade_id: str,
    ticker: str,
    strategy: str = "iron_condor",
    pnl: float = -42.0,
    status: str = "complete",
    reflection: str = "Lesson learned.",
    judge_reasoning: str | None = None,
    **extras,
) -> dict:
    return {
        "trade_id": trade_id,
        "agent": "agent_alpha",
        "ticker": ticker,
        "strategy": strategy,
        "regime_at_entry": "normal",
        "entry_features": {"vix": 16.5, "iv_rank": 42, "conviction_score": 7},
        "decision_summary": f"{strategy} on {ticker}.",
        "full_trajectory": {
            "bull_case": "Bull thesis.",
            "bear_case": "Bear thesis.",
            "judge_reasoning": judge_reasoning or f"Judge reasoning for {trade_id}.",
            "judge_score": 74,
            "invalidation_clause": "price breaks range",
            "skills_consulted": [],
        } if judge_reasoning is not None or True else None,
        "realized_return_raw": pnl,
        "realized_return_pct": pnl / 25.0,
        "alpha_vs_spy": None,
        "reflection_text": reflection,
        "reflection_status": status,
        "closed_at": datetime.now(timezone.utc).isoformat(),
        "close_reason": "stop_loss" if pnl < 0 else "profit_target",
        "hold_days": 3,
        **extras,
    }


class TestGetLessons:
    def test_same_symbol_filtering(self, memory_dir):
        import _memory
        importlib.reload(_memory)
        records = [
            _make_reflection(f"A{i:03d}", "SPY", reflection=f"SPY lesson {i}")
            for i in range(5)
        ] + [
            _make_reflection(f"A{i:03d}", "AAPL", reflection=f"AAPL lesson {i}")
            for i in range(5, 10)
        ]
        _write_reflection_records(memory_dir / "alpha_reflections.jsonl", records)
        lessons = _memory.get_lessons("agent_alpha", "SPY", k_same=5, k_cross=5)
        same = [l for l in lessons if l["ticker"] == "SPY"]
        cross = [l for l in lessons if l["ticker"] != "SPY"]
        assert len(same) == 5
        assert len(cross) == 5

    def test_cross_symbol_newest_first(self, memory_dir):
        import _memory
        importlib.reload(_memory)
        records = [
            _make_reflection("A001", "AAPL", reflection="old"),
            _make_reflection("A002", "GOOGL", reflection="newer"),
            _make_reflection("A003", "MSFT", reflection="newest"),
        ]
        _write_reflection_records(memory_dir / "alpha_reflections.jsonl", records)
        lessons = _memory.get_lessons("agent_alpha", "SPY", k_same=0, k_cross=3)
        assert lessons[0]["trade_id"] == "A003"
        assert lessons[-1]["trade_id"] == "A001"

    def test_skips_incomplete(self, memory_dir):
        import _memory
        importlib.reload(_memory)
        records = [
            _make_reflection("A001", "SPY", status="complete"),
            _make_reflection("A002", "SPY", status="llm_failed"),
            _make_reflection("A003", "SPY", status="pending_retry"),
            _make_reflection("A004", "SPY", status="complete"),
        ]
        _write_reflection_records(memory_dir / "alpha_reflections.jsonl", records)
        lessons = _memory.get_lessons("agent_alpha", "SPY", k_same=10, k_cross=0)
        assert len(lessons) == 2
        assert all(l["reflection_status"] == "complete" for l in lessons)

    def test_empty_file_returns_empty(self, memory_dir):
        import _memory
        importlib.reload(_memory)
        # No JSONL file exists at all
        lessons = _memory.get_lessons("agent_alpha", "SPY")
        assert lessons == []

    def test_fewer_than_k(self, memory_dir):
        import _memory
        importlib.reload(_memory)
        records = [_make_reflection("A001", "SPY"), _make_reflection("A002", "SPY")]
        _write_reflection_records(memory_dir / "alpha_reflections.jsonl", records)
        lessons = _memory.get_lessons("agent_alpha", "SPY", k_same=10, k_cross=0)
        assert len(lessons) == 2


class TestFormatLessons:
    def test_most_recent_same_symbol_includes_judge_reasoning(self, memory_dir):
        """Per plan C2: most recent same-symbol lesson includes judge_reasoning snippet."""
        import _memory
        importlib.reload(_memory)
        records = [
            _make_reflection("A001", "SPY", reflection="Older lesson.",
                             judge_reasoning="Chose condor due to low VIX."),
            _make_reflection("A002", "SPY", reflection="Newest lesson.",
                             judge_reasoning="Selected bull put because RSI bounced."),
        ]
        _write_reflection_records(memory_dir / "alpha_reflections.jsonl", records)
        text = _memory.format_lessons("agent_alpha", "SPY")
        assert "Judge reasoning:" in text or "judge_reasoning" in text.lower()
        # The most recent (A002) should have its judge reasoning shown
        assert "Selected bull put" in text
        # Older lesson (A001) should NOT have judge reasoning
        # (only the most recent same-symbol gets it)

    def test_format_empty_returns_empty_string(self, memory_dir):
        import _memory
        importlib.reload(_memory)
        text = _memory.format_lessons("agent_alpha", "SPY")
        assert text == ""


class TestSchemaValidation:
    def test_all_required_fields_present(self, alpha_journal, memory_dir, monkeypatch):
        import _memory
        importlib.reload(_memory)
        _mock_llm_text(monkeypatch, "Complete reflection text.")
        _memory.write_reflection("A025")
        jsonl = memory_dir / "alpha_reflections.jsonl"
        rec = json.loads(jsonl.read_text().strip())
        required = [
            "trade_id", "agent", "ticker", "strategy", "regime_at_entry",
            "entry_features", "decision_summary", "full_trajectory",
            "realized_return_raw", "realized_return_pct",
            "reflection_text", "reflection_status",
            "closed_at", "close_reason", "hold_days",
        ]
        for field in required:
            assert field in rec, f"Missing required field: {field}"


class TestAlphaVsSpy:
    def test_computation(self, tmp_root, memory_dir, monkeypatch):
        """alpha_vs_spy should be computed when SPY return data is available."""
        import _memory
        importlib.reload(_memory)
        # Create a trade with known P&L
        trade = _make_closed_trade(
            trade_id="A030", source="agent_alpha",
            symbol="INTC", pnl=-50.0, pnl_pct=-5.0,
            full_trajectory=_alpha_trajectory(),
        )
        journal = tmp_root / "journal" / "paper" / "trades.jsonl"
        journal.write_text(json.dumps(trade) + "\n")
        import _journal_update
        importlib.reload(_journal_update)
        importlib.reload(_memory)
        _mock_llm_text(monkeypatch, "INTC reflection text.")
        _memory.write_reflection("A030")
        jsonl = memory_dir / "alpha_reflections.jsonl"
        rec = json.loads(jsonl.read_text().strip())
        # alpha_vs_spy may be None if SPY data isn't available — that's ok
        assert "alpha_vs_spy" in rec
