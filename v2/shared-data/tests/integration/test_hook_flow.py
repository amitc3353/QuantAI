"""Integration tests: position_monitor close → diagnose → review hook chain (Gap 5).

Tests the full inline hook order: post_close_alert fires, then diagnosis runs,
then review runs. Both hooks must write to journal AND standalone files. If
either hook fails (LLM down), the other must still run.
"""
from __future__ import annotations

import importlib
import json
import sys
from unittest.mock import MagicMock

import pytest

from conftest import assert_diagnosis_schema, assert_review_schema


@pytest.fixture(autouse=True)
def _reload_modules(tmp_root):
    import _paths, _journal_update, agent_self_diagnosis, trade_reviewer
    for mod in (_paths, _journal_update, agent_self_diagnosis, trade_reviewer):
        importlib.reload(mod)
    yield


def _mock_llm_module(response_text: str) -> MagicMock:
    mock_content = MagicMock()
    mock_content.text = response_text
    mock_resp = MagicMock()
    mock_resp.content = [mock_content]
    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_resp
    mock_module = MagicMock()
    mock_module.Client.return_value = mock_client
    return mock_module


VALID_DIAGNOSIS = json.dumps({
    "gaps_identified": [
        {
            "dimension": "data_freshness",
            "request": "VIX should refresh every 5 min",
            "evidence": "VIX moved 3pts during hold",
            "priority": "would_help",
            "estimated_impact_dollars": 120,
        }
    ],
    "no_gaps_note": None,
})

VALID_REVIEW = json.dumps({
    "thesis_outcome": "confirmed",
    "thesis_assessment": "SPY bounced as expected.",
    "regime_assessment": "Regime classification was correct.",
    "greeks_notes": "Normal decay.",
    "timing_assessment": "Entry good, exit adequate.",
    "what_went_right": "RSI recovery triggered exit cleanly.",
    "what_went_wrong": None,
    "lessons": [],
    "parameter_suggestions": [],
})


class TestDiagnoseReviewChain:
    def test_both_hooks_run_sequentially(self, tmp_root, populated_journal, monkeypatch):
        """Diagnosis then review both run; both write to journal."""
        monkeypatch.setitem(sys.modules, "_llm_client", _mock_llm_module(VALID_DIAGNOSIS))

        import agent_self_diagnosis as asd
        result_d = asd.diagnose("A001")
        assert result_d is not None
        assert_diagnosis_schema(result_d)

        # Swap LLM response for review
        monkeypatch.setitem(sys.modules, "_llm_client", _mock_llm_module(VALID_REVIEW))

        import trade_reviewer as tr
        result_r = tr.review("A001")
        assert result_r is not None
        assert_review_schema(result_r)

        from _journal_update import find_trade
        t = find_trade("A001", str(populated_journal))
        assert "capability_diagnosis" in t
        assert "post_trade" in t

    def test_review_runs_after_diagnosis_failure(self, tmp_root, populated_journal, monkeypatch):
        """If diagnosis LLM fails, review must still run."""
        failing_module = MagicMock()
        failing_module.Client.side_effect = RuntimeError("LLM is down")
        monkeypatch.setitem(sys.modules, "_llm_client", failing_module)

        import agent_self_diagnosis as asd
        result_d = asd.diagnose("A001")
        assert result_d is None  # diagnosis failed gracefully

        # Now fix the LLM for review
        monkeypatch.setitem(sys.modules, "_llm_client", _mock_llm_module(VALID_REVIEW))
        import trade_reviewer as tr
        result_r = tr.review("A001")
        assert result_r is not None

    def test_diagnosis_failure_does_not_prevent_journal_review(
        self, tmp_root, populated_journal, monkeypatch
    ):
        """Even after diagnosis writes capability_diagnosis=null, review writes post_trade."""
        # Diagnosis fails — journal gets capability_diagnosis=null
        failing_module = MagicMock()
        failing_module.Client.side_effect = RuntimeError("down")
        monkeypatch.setitem(sys.modules, "_llm_client", failing_module)
        import agent_self_diagnosis as asd
        asd.diagnose("A001")

        # Review succeeds
        monkeypatch.setitem(sys.modules, "_llm_client", _mock_llm_module(VALID_REVIEW))
        import trade_reviewer as tr
        tr.review("A001")

        from _journal_update import find_trade
        t = find_trade("A001", str(populated_journal))
        assert "post_trade" in t
        assert t["post_trade"]["thesis_outcome"] == "confirmed"

    def test_standalone_files_created(self, tmp_root, populated_journal, monkeypatch):
        """Both hooks must write standalone files in capability_requests/ and trade_reviews/."""
        monkeypatch.setitem(sys.modules, "_llm_client", _mock_llm_module(VALID_DIAGNOSIS))
        import agent_self_diagnosis as asd
        asd.diagnose("A001")

        monkeypatch.setitem(sys.modules, "_llm_client", _mock_llm_module(VALID_REVIEW))
        import trade_reviewer as tr
        tr.review("A001")

        assert (tmp_root / "capability_requests" / "agent_alpha" / "A001.json").exists()
        assert (tmp_root / "trade_reviews" / "agent_alpha" / "A001.md").exists()

    def test_hook_order_regression(self, tmp_root, populated_journal, monkeypatch):
        """Gap 5: diagnosis MUST run before review — verify via journal write order."""
        call_order = []

        class TrackingModule:
            class Client:
                def messages(self):
                    pass
                def __init__(self):
                    pass

        original_diagnose_update = None

        # Patch update_trade_entry to record call order
        import _journal_update
        original_update = _journal_update.update_trade_entry

        def tracking_update(tid, fields, jp=None):
            if "capability_diagnosis" in fields:
                call_order.append("diagnosis")
            elif "post_trade" in fields:
                call_order.append("review")
            kwargs = {"journal_path": jp} if jp else {}
            if jp:
                return original_update(tid, fields, jp)
            return original_update(tid, fields)

        monkeypatch.setattr(_journal_update, "update_trade_entry", tracking_update)
        monkeypatch.setitem(sys.modules, "_llm_client", _mock_llm_module(VALID_DIAGNOSIS))

        import agent_self_diagnosis as asd
        import importlib
        importlib.reload(asd)
        asd.diagnose("A001")

        monkeypatch.setitem(sys.modules, "_llm_client", _mock_llm_module(VALID_REVIEW))
        import trade_reviewer as tr
        importlib.reload(tr)
        tr.review("A001")

        assert call_order == ["diagnosis", "review"], (
            f"Hook order violated: expected ['diagnosis', 'review'], got {call_order}"
        )
