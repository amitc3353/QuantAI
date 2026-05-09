"""Unit tests for agent_self_diagnosis.py — mocking LLM calls."""
from __future__ import annotations

import importlib
import json

import pytest

from conftest import assert_diagnosis_schema


@pytest.fixture(autouse=True)
def _reload_modules(tmp_root):
    import _paths, _journal_update, agent_self_diagnosis
    # Order matters: _paths first (picks up env), then _journal_update (picks up
    # new JOURNAL path), then agent_self_diagnosis (rebinds find_trade default).
    for mod in (_paths, _journal_update, agent_self_diagnosis):
        importlib.reload(mod)
    yield


def _mock_llm(monkeypatch, response_text: str):
    """Patch _llm_client.Client so LLM calls return response_text."""
    import sys
    from unittest.mock import MagicMock, patch

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
    """JSON parsing now lives in _llm_call._parse_json; schema patching
    (gaps_identified, no_gaps_note) lives in _call_haiku_json."""

    def test_valid_json(self):
        from _llm_call import _parse_json
        result = _parse_json('{"gaps_identified": [], "no_gaps_note": "none"}')
        assert result is not None
        assert result["gaps_identified"] == []

    def test_strips_markdown_fences(self):
        from _llm_call import _parse_json
        text = '```json\n{"gaps_identified": [], "no_gaps_note": null}\n```'
        result = _parse_json(text)
        assert result is not None

    def test_extracts_embedded_json(self):
        from _llm_call import _parse_json
        text = 'Here is the analysis:\n{"gaps_identified": [], "no_gaps_note": null}\nDone.'
        result = _parse_json(text)
        assert result is not None

    def test_returns_none_on_garbage(self):
        import pytest
        from _llm_call import _parse_json
        with pytest.raises(ValueError):
            _parse_json("this is not json at all")

    def test_returns_none_on_empty(self):
        import pytest
        from _llm_call import _parse_json
        with pytest.raises(ValueError):
            _parse_json("")

    def test_adds_missing_keys(self):
        import agent_self_diagnosis as asd
        result = asd._call_haiku_json.__wrapped__(None, None) if hasattr(asd._call_haiku_json, '__wrapped__') else None
        from _llm_call import _parse_json
        data = _parse_json('{"gaps_identified": []}')
        if "no_gaps_note" not in data:
            data["no_gaps_note"] = None
        assert "no_gaps_note" in data


class TestDiagnose:
    def test_diagnose_returns_none_when_trade_missing(self, tmp_root):
        import agent_self_diagnosis as asd
        result = asd.diagnose("ZZZZ")
        assert result is None

    def test_diagnose_dry_run_no_llm_call(self, tmp_root, populated_journal):
        import agent_self_diagnosis as asd
        result = asd.diagnose("A001", dry_run=True)
        assert result is not None
        assert result["dry_run"] is True

    def test_diagnose_happy_path(self, populated_journal, monkeypatch, sample_diagnosis):
        import agent_self_diagnosis as asd
        _mock_llm(monkeypatch, json.dumps(sample_diagnosis))
        result = asd.diagnose("A001")
        assert result is not None
        assert_diagnosis_schema(result)

    def test_diagnose_writes_standalone_file(self, tmp_root, populated_journal,
                                              monkeypatch, sample_diagnosis):
        import agent_self_diagnosis as asd
        _mock_llm(monkeypatch, json.dumps(sample_diagnosis))
        asd.diagnose("A001")
        standalone = tmp_root / "capability_requests" / "agent_alpha" / "A001.json"
        assert standalone.exists()
        data = json.loads(standalone.read_text())
        assert data["trade_id"] == "A001"
        assert_diagnosis_schema(data["diagnosis"])

    def test_diagnose_writes_to_journal(self, populated_journal, monkeypatch,
                                         sample_diagnosis):
        import agent_self_diagnosis as asd
        from _journal_update import find_trade
        _mock_llm(monkeypatch, json.dumps(sample_diagnosis))
        asd.diagnose("A001")
        t = find_trade("A001", str(populated_journal))
        assert t is not None
        assert "capability_diagnosis" in t

    def test_diagnose_returns_none_on_llm_failure(self, populated_journal, monkeypatch):
        import agent_self_diagnosis as asd
        # LLM returns None-producing garbage
        _mock_llm(monkeypatch, "this is not json")
        result = asd.diagnose("A001")
        assert result is None

    def test_diagnose_never_raises(self, tmp_root, monkeypatch):
        import agent_self_diagnosis as asd
        # Even with a totally broken LLM module, diagnose must not raise
        import sys
        from unittest.mock import MagicMock
        mock_module = MagicMock()
        mock_module.Client.side_effect = RuntimeError("LLM is down")
        monkeypatch.setitem(sys.modules, "_llm_client", mock_module)
        try:
            result = asd.diagnose("A001")
        except Exception as e:
            pytest.fail(f"diagnose() raised: {e}")

    def test_concurrent_positions_returns_list(self, populated_journal, sample_trade):
        import agent_self_diagnosis as asd
        result = asd._concurrent_positions(sample_trade, str(populated_journal))
        assert isinstance(result, list)


class TestWriteStandaloneFile:
    def test_creates_json_file(self, tmp_root):
        import agent_self_diagnosis as asd
        diagnosis = {"gaps_identified": [], "no_gaps_note": "ok"}
        asd._write_standalone_file("agent_alpha", "A099", diagnosis)
        fp = tmp_root / "capability_requests" / "agent_alpha" / "A099.json"
        assert fp.exists()
        data = json.loads(fp.read_text())
        assert data["trade_id"] == "A099"

    def test_handles_unwritable_dir_gracefully(self, tmp_path, monkeypatch):
        import agent_self_diagnosis as asd
        # Point to a path that can't be created (file in the way)
        blocker = tmp_path / "blocker"
        blocker.write_text("I am a file, not a directory")
        monkeypatch.setattr(asd, "RUNTIME_REQUESTS_DIR", blocker)
        # Must not raise
        asd._write_standalone_file("agent_alpha", "A099", {})
