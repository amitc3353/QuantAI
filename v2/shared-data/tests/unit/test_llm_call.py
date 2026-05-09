"""Tests for _llm_call.py — retry + parse hardening for LLM calls."""
import json
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import _llm_call as llm


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_response(text: str):
    """Build a fake _llm_client._Response-like object."""
    block = MagicMock()
    block.text = text
    resp = MagicMock()
    resp.content = [block]
    return resp


def _mock_client(responses):
    """Return a mock Client whose messages.create returns responses in order.

    Each item in `responses` is either a string (success text) or an Exception to raise.
    """
    client = MagicMock()
    side_effects = []
    for r in responses:
        if isinstance(r, Exception):
            side_effects.append(r)
        else:
            side_effects.append(_mock_response(r))
    client.messages.create.side_effect = side_effects
    return client


# ---------------------------------------------------------------------------
# JSON parsing (_parse_json)
# ---------------------------------------------------------------------------

class TestParseJson:
    def test_valid_json(self):
        result = llm._parse_json('{"key": "value"}')
        assert result == {"key": "value"}

    def test_json_with_markdown_fences(self):
        text = '```json\n{"key": "value"}\n```'
        result = llm._parse_json(text)
        assert result == {"key": "value"}

    def test_json_with_plain_fences(self):
        text = '```\n{"key": "value"}\n```'
        result = llm._parse_json(text)
        assert result == {"key": "value"}

    def test_json_with_surrounding_text(self):
        text = 'Here is the result:\n{"key": "value"}\nDone.'
        result = llm._parse_json(text)
        assert result == {"key": "value"}

    def test_nested_json_extraction(self):
        text = 'blah {"a": {"b": 1}} blah'
        result = llm._parse_json(text)
        assert result == {"a": {"b": 1}}

    def test_completely_invalid(self):
        with pytest.raises(ValueError):
            llm._parse_json("no json here at all")

    def test_empty_string(self):
        with pytest.raises(ValueError):
            llm._parse_json("")

    def test_array_json(self):
        text = '[{"a": 1}, {"b": 2}]'
        result = llm._parse_json(text)
        assert result == [{"a": 1}, {"b": 2}]

    def test_fenced_with_leading_whitespace(self):
        text = '  ```json\n  {"x": 1}\n  ```  '
        result = llm._parse_json(text)
        assert result == {"x": 1}


# ---------------------------------------------------------------------------
# call_llm_json
# ---------------------------------------------------------------------------

class TestCallLlmJson:
    def test_success_first_attempt(self, monkeypatch):
        client = _mock_client(['{"proposals": []}'])
        monkeypatch.setattr(llm, "_get_client", lambda: client)

        result = llm.call_llm_json(
            model="test", system="sys", user="usr", caller="test"
        )
        assert result == {"proposals": []}
        assert client.messages.create.call_count == 1

    def test_markdown_fenced_response(self, monkeypatch):
        client = _mock_client(['```json\n{"ok": true}\n```'])
        monkeypatch.setattr(llm, "_get_client", lambda: client)

        result = llm.call_llm_json(
            model="test", system="sys", user="usr", caller="test"
        )
        assert result == {"ok": True}

    def test_retry_on_parse_failure(self, monkeypatch):
        client = _mock_client(["not json", '{"fixed": true}'])
        monkeypatch.setattr(llm, "_get_client", lambda: client)
        monkeypatch.setattr(llm, "_RETRY_DELAYS", [0, 0])

        result = llm.call_llm_json(
            model="test", system="sys", user="usr", caller="test"
        )
        assert result == {"fixed": True}
        assert client.messages.create.call_count == 2

    def test_retry_on_exception(self, monkeypatch):
        client = _mock_client([TimeoutError("timeout"), '{"ok": true}'])
        monkeypatch.setattr(llm, "_get_client", lambda: client)
        monkeypatch.setattr(llm, "_RETRY_DELAYS", [0, 0])

        result = llm.call_llm_json(
            model="test", system="sys", user="usr", caller="test"
        )
        assert result == {"ok": True}

    def test_all_retries_exhausted_returns_none(self, monkeypatch, tmp_path):
        client = _mock_client(["bad", "bad", "bad"])
        monkeypatch.setattr(llm, "_get_client", lambda: client)
        monkeypatch.setattr(llm, "_RETRY_DELAYS", [0, 0])
        log_file = tmp_path / "llm_failures.jsonl"
        monkeypatch.setattr(llm, "FAILURE_LOG", log_file)
        monkeypatch.setattr(llm, "_discord_alert", lambda *a, **kw: None)

        result = llm.call_llm_json(
            model="test", system="sys", user="usr", caller="test"
        )
        assert result is None
        assert client.messages.create.call_count == 3

    def test_failure_log_written_on_exhaustion(self, monkeypatch, tmp_path):
        client = _mock_client([Exception("fail1"), Exception("fail2"), Exception("fail3")])
        monkeypatch.setattr(llm, "_get_client", lambda: client)
        monkeypatch.setattr(llm, "_RETRY_DELAYS", [0, 0])
        log_file = tmp_path / "llm_failures.jsonl"
        monkeypatch.setattr(llm, "FAILURE_LOG", log_file)
        monkeypatch.setattr(llm, "_discord_alert", lambda *a, **kw: None)

        llm.call_llm_json(model="test", system="sys", user="usr", caller="test_caller")

        assert log_file.exists()
        entry = json.loads(log_file.read_text().strip())
        assert entry["caller"] == "test_caller"
        assert entry["model"] == "test"
        assert "error" in entry

    def test_discord_alert_on_exhaustion(self, monkeypatch, tmp_path):
        client = _mock_client([Exception("x"), Exception("x"), Exception("x")])
        monkeypatch.setattr(llm, "_get_client", lambda: client)
        monkeypatch.setattr(llm, "_RETRY_DELAYS", [0, 0])
        monkeypatch.setattr(llm, "FAILURE_LOG", tmp_path / "f.jsonl")
        alerts = []
        monkeypatch.setattr(llm, "_discord_alert", lambda msg, caller: alerts.append((msg, caller)))

        llm.call_llm_json(model="test", system="sys", user="usr", caller="test_caller")

        assert len(alerts) == 1
        assert "test_caller" in alerts[0][0]

    def test_permanent_error_no_retry(self, monkeypatch, tmp_path):
        """401/403 should fail immediately without retrying."""
        import httpx
        err = httpx.HTTPStatusError(
            "401 Unauthorized",
            request=MagicMock(),
            response=MagicMock(status_code=401),
        )
        client = _mock_client([err])
        monkeypatch.setattr(llm, "_get_client", lambda: client)
        monkeypatch.setattr(llm, "_RETRY_DELAYS", [0, 0])
        monkeypatch.setattr(llm, "FAILURE_LOG", tmp_path / "f.jsonl")
        monkeypatch.setattr(llm, "_discord_alert", lambda *a, **kw: None)

        result = llm.call_llm_json(model="test", system="sys", user="usr", caller="test")
        assert result is None
        assert client.messages.create.call_count == 1

    def test_custom_max_retries(self, monkeypatch, tmp_path):
        client = _mock_client([Exception("x"), Exception("x")])
        monkeypatch.setattr(llm, "_get_client", lambda: client)
        monkeypatch.setattr(llm, "_RETRY_DELAYS", [0])
        monkeypatch.setattr(llm, "FAILURE_LOG", tmp_path / "f.jsonl")
        monkeypatch.setattr(llm, "_discord_alert", lambda *a, **kw: None)

        result = llm.call_llm_json(
            model="test", system="sys", user="usr", caller="test",
            max_retries=2,
        )
        assert result is None
        assert client.messages.create.call_count == 2

    def test_empty_response_text_triggers_retry(self, monkeypatch):
        client = _mock_client(["", '{"ok": true}'])
        monkeypatch.setattr(llm, "_get_client", lambda: client)
        monkeypatch.setattr(llm, "_RETRY_DELAYS", [0, 0])

        result = llm.call_llm_json(
            model="test", system="sys", user="usr", caller="test"
        )
        assert result == {"ok": True}
        assert client.messages.create.call_count == 2


# ---------------------------------------------------------------------------
# call_llm_text
# ---------------------------------------------------------------------------

class TestCallLlmText:
    def test_success(self, monkeypatch):
        client = _mock_client(["Hello world"])
        monkeypatch.setattr(llm, "_get_client", lambda: client)

        result = llm.call_llm_text(
            model="test", system="sys", user="usr", caller="test"
        )
        assert result == "Hello world"

    def test_retry_on_exception(self, monkeypatch):
        client = _mock_client([TimeoutError("t"), "recovered"])
        monkeypatch.setattr(llm, "_get_client", lambda: client)
        monkeypatch.setattr(llm, "_RETRY_DELAYS", [0, 0])

        result = llm.call_llm_text(
            model="test", system="sys", user="usr", caller="test"
        )
        assert result == "recovered"

    def test_retry_on_empty_text(self, monkeypatch):
        """Empty text should be treated as retryable (weekly_synthesis failure mode)."""
        client = _mock_client(["", "actual text"])
        monkeypatch.setattr(llm, "_get_client", lambda: client)
        monkeypatch.setattr(llm, "_RETRY_DELAYS", [0, 0])

        result = llm.call_llm_text(
            model="test", system="sys", user="usr", caller="test"
        )
        assert result == "actual text"
        assert client.messages.create.call_count == 2

    def test_retry_on_none_text(self, monkeypatch):
        """None content text should be treated as retryable."""
        block = MagicMock()
        block.text = None
        resp = MagicMock()
        resp.content = [block]
        client = MagicMock()
        client.messages.create.side_effect = [resp, _mock_response("fixed")]
        monkeypatch.setattr(llm, "_get_client", lambda: client)
        monkeypatch.setattr(llm, "_RETRY_DELAYS", [0, 0])

        result = llm.call_llm_text(
            model="test", system="sys", user="usr", caller="test"
        )
        assert result == "fixed"

    def test_all_retries_exhausted(self, monkeypatch, tmp_path):
        client = _mock_client([Exception("x"), Exception("x"), Exception("x")])
        monkeypatch.setattr(llm, "_get_client", lambda: client)
        monkeypatch.setattr(llm, "_RETRY_DELAYS", [0, 0])
        monkeypatch.setattr(llm, "FAILURE_LOG", tmp_path / "f.jsonl")
        monkeypatch.setattr(llm, "_discord_alert", lambda *a, **kw: None)

        result = llm.call_llm_text(
            model="test", system="sys", user="usr", caller="test"
        )
        assert result is None


# ---------------------------------------------------------------------------
# Discord rate limiting
# ---------------------------------------------------------------------------

class TestDiscordRateLimit:
    def test_rate_limited_to_one_per_caller_per_hour(self, monkeypatch):
        posted = []
        monkeypatch.setattr(llm, "_do_discord_post", lambda msg: posted.append(msg))
        llm._discord_alert_timestamps.clear()

        llm._discord_alert("fail 1", "debate_proposal")
        llm._discord_alert("fail 2", "debate_proposal")
        llm._discord_alert("fail 3", "debate_proposal")

        assert len(posted) == 1

    def test_different_callers_not_rate_limited(self, monkeypatch):
        posted = []
        monkeypatch.setattr(llm, "_do_discord_post", lambda msg: posted.append(msg))
        llm._discord_alert_timestamps.clear()

        llm._discord_alert("fail 1", "debate_proposal")
        llm._discord_alert("fail 2", "debate_judge")
        llm._discord_alert("fail 3", "trade_review")

        assert len(posted) == 3

    def test_rate_limit_resets_after_interval(self, monkeypatch):
        posted = []
        monkeypatch.setattr(llm, "_do_discord_post", lambda msg: posted.append(msg))
        monkeypatch.setattr(llm, "DISCORD_ALERT_COOLDOWN_SECONDS", 0)
        llm._discord_alert_timestamps.clear()

        llm._discord_alert("fail 1", "test_caller")
        llm._discord_alert("fail 2", "test_caller")

        assert len(posted) == 2


# ---------------------------------------------------------------------------
# _is_permanent_error
# ---------------------------------------------------------------------------

class TestIsPermanentError:
    def test_401_is_permanent(self):
        import httpx
        err = httpx.HTTPStatusError(
            "401", request=MagicMock(), response=MagicMock(status_code=401)
        )
        assert llm._is_permanent_error(err) is True

    def test_403_is_permanent(self):
        import httpx
        err = httpx.HTTPStatusError(
            "403", request=MagicMock(), response=MagicMock(status_code=403)
        )
        assert llm._is_permanent_error(err) is True

    def test_500_is_not_permanent(self):
        import httpx
        err = httpx.HTTPStatusError(
            "500", request=MagicMock(), response=MagicMock(status_code=500)
        )
        assert llm._is_permanent_error(err) is False

    def test_timeout_is_not_permanent(self):
        assert llm._is_permanent_error(TimeoutError("t")) is False

    def test_generic_exception_is_not_permanent(self):
        assert llm._is_permanent_error(Exception("x")) is False
