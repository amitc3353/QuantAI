"""Unit tests for trade_reviewer.py — mocking LLM calls."""
from __future__ import annotations

import importlib
import json

import pytest

from conftest import assert_review_schema


@pytest.fixture(autouse=True)
def _reload_modules(tmp_root):
    import _paths, _journal_update, trade_reviewer
    for mod in (_paths, _journal_update, trade_reviewer):
        importlib.reload(mod)
    yield


def _mock_llm(monkeypatch, response_text: str):
    import sys
    from unittest.mock import MagicMock

    mock_content = MagicMock()
    mock_content.text = response_text
    mock_resp = MagicMock()
    mock_resp.content = [mock_content]

    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_resp

    mock_module = MagicMock()
    mock_module.Client.return_value = mock_client
    monkeypatch.setitem(sys.modules, "_llm_client", mock_module)
    return mock_client


class TestParseJsonResponse:
    """JSON parsing now lives in _llm_call._parse_json."""

    def test_valid_json(self):
        from _llm_call import _parse_json
        result = _parse_json('{"thesis_outcome": "confirmed"}')
        assert result == {"thesis_outcome": "confirmed"}

    def test_strips_markdown_fences(self):
        from _llm_call import _parse_json
        text = '```json\n{"thesis_outcome": "confirmed"}\n```'
        result = _parse_json(text)
        assert result == {"thesis_outcome": "confirmed"}

    def test_returns_none_on_garbage(self):
        import pytest
        from _llm_call import _parse_json
        with pytest.raises(ValueError):
            _parse_json("not json")

    def test_returns_none_on_empty(self):
        import pytest
        from _llm_call import _parse_json
        with pytest.raises(ValueError):
            _parse_json("")

    def test_extracts_embedded_json(self):
        from _llm_call import _parse_json
        text = 'Analysis:\n{"thesis_outcome": "invalidated"}\nEnd.'
        result = _parse_json(text)
        assert result["thesis_outcome"] == "invalidated"


class TestHoldingDays:
    def test_uses_explicit_field(self):
        import trade_reviewer as tr
        t = {"holding_days": 5}
        assert tr._holding_days(t) == 5

    def test_computes_from_timestamps(self):
        import trade_reviewer as tr
        t = {
            "timestamp": "2026-04-20T09:30:00+00:00",
            "close_timestamp": "2026-04-23T09:30:00+00:00",
        }
        assert tr._holding_days(t) == 3

    def test_returns_none_when_missing(self):
        import trade_reviewer as tr
        assert tr._holding_days({}) is None

    def test_clamps_to_zero_for_same_day(self):
        import trade_reviewer as tr
        t = {
            "timestamp": "2026-04-20T09:30:00+00:00",
            "close_timestamp": "2026-04-20T15:30:00+00:00",
        }
        assert tr._holding_days(t) == 0


class TestReview:
    def test_review_returns_none_when_trade_missing(self, tmp_root):
        import trade_reviewer as tr
        assert tr.review("ZZZZ") is None

    def test_review_dry_run(self, populated_journal):
        import trade_reviewer as tr
        result = tr.review("A001", dry_run=True)
        assert result is not None
        assert result["dry_run"] is True

    def test_review_happy_path(self, populated_journal, monkeypatch, sample_review):
        import trade_reviewer as tr
        _mock_llm(monkeypatch, json.dumps(sample_review))
        result = tr.review("A001")
        assert result is not None
        assert_review_schema(result)

    def test_review_writes_markdown(self, tmp_root, populated_journal,
                                     monkeypatch, sample_review):
        import trade_reviewer as tr
        _mock_llm(monkeypatch, json.dumps(sample_review))
        tr.review("A001")
        md_path = tmp_root / "trade_reviews" / "agent_alpha" / "A001.md"
        assert md_path.exists()
        content = md_path.read_text()
        assert "A001" in content
        assert "confirmed" in content

    def test_review_writes_to_journal(self, populated_journal, monkeypatch,
                                       sample_review):
        import trade_reviewer as tr
        from _journal_update import find_trade
        _mock_llm(monkeypatch, json.dumps(sample_review))
        tr.review("A001")
        t = find_trade("A001", str(populated_journal))
        assert "post_trade" in t
        assert t["post_trade"]["thesis_outcome"] == "confirmed"

    def test_review_never_raises_on_llm_failure(self, populated_journal, monkeypatch):
        import trade_reviewer as tr
        import sys
        from unittest.mock import MagicMock
        mock_module = MagicMock()
        mock_module.Client.side_effect = RuntimeError("LLM exploded")
        monkeypatch.setitem(sys.modules, "_llm_client", mock_module)
        try:
            result = tr.review("A001")
            assert result is None
        except Exception as e:
            pytest.fail(f"review() raised: {e}")

    def test_review_returns_none_on_unparseable_response(self, populated_journal,
                                                           monkeypatch):
        import trade_reviewer as tr
        _mock_llm(monkeypatch, "this is not json either")
        result = tr.review("A001")
        assert result is None


class TestWriteReviewMd:
    def test_parameter_suggestions_in_md(self, tmp_root, sample_review):
        import trade_reviewer as tr
        tr._write_review_md("agent_alpha", "A001", sample_review)
        md = (tmp_root / "trade_reviews" / "agent_alpha" / "A001.md").read_text()
        assert "rsi_exit_threshold" in md
        assert "40" in md
        assert "45" in md

    def test_empty_lessons_shows_placeholder(self, tmp_root):
        import trade_reviewer as tr
        review_no_lessons = {
            "thesis_outcome": "confirmed",
            "thesis_assessment": "ok",
            "regime_assessment": "ok",
            "greeks_notes": "N/A",
            "timing_assessment": "ok",
            "what_went_right": None,
            "what_went_wrong": None,
            "lessons": [],
            "parameter_suggestions": [],
        }
        tr._write_review_md("agent_alpha", "A002", review_no_lessons)
        md = (tmp_root / "trade_reviews" / "agent_alpha" / "A002.md").read_text()
        assert "No new lessons" in md
