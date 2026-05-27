"""Unit tests: 429-aware retry logic in _llm_call.py.

Tests cover:
  1. 429 with Retry-After header → uses header value
  2. 429 without header → exponential backoff (30s, 60s, 120s)
  3. 429 records rate-limit state file
  4. Cross-run backoff skips call when state file active
  5. Cross-run backoff clears after expiry
  6. Non-429 errors use default delays (2s, 4s)
  7. 401/403 still break immediately (permanent)
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

SCRIPTS = str(Path(__file__).resolve().parents[2] / "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import httpx


def _make_http_error(status_code: int, headers: dict | None = None):
    """Build a fake httpx.HTTPStatusError with the given status and headers."""
    response = MagicMock(spec=httpx.Response)
    response.status_code = status_code
    response.headers = headers or {}
    request = MagicMock(spec=httpx.Request)
    return httpx.HTTPStatusError(
        message=f"HTTP {status_code}",
        request=request,
        response=response,
    )


class TestGetRetryDelay:
    """Test _get_retry_delay() returns correct backoff times."""

    def test_429_uses_retry_after_header(self):
        """429 with Retry-After: 45 should return 45s."""
        from _llm_call import _get_retry_delay
        exc = _make_http_error(429, {"Retry-After": "45"})
        assert _get_retry_delay(exc, 0) == 45.0

    def test_429_retry_after_capped_at_120(self):
        """429 with Retry-After: 999 should be capped at 120s."""
        from _llm_call import _get_retry_delay
        exc = _make_http_error(429, {"Retry-After": "999"})
        assert _get_retry_delay(exc, 0) == 120.0

    def test_429_exponential_backoff_no_header(self):
        """429 without Retry-After → 30s, 60s, 120s by attempt."""
        from _llm_call import _get_retry_delay
        exc = _make_http_error(429)
        assert _get_retry_delay(exc, 0) == 30.0
        assert _get_retry_delay(exc, 1) == 60.0
        assert _get_retry_delay(exc, 2) == 120.0
        # Attempt 3+ capped at 120
        assert _get_retry_delay(exc, 5) == 120.0

    def test_non_429_uses_default_delays(self):
        """500 error should use _RETRY_DELAYS (2s, 4s)."""
        from _llm_call import _get_retry_delay
        exc = _make_http_error(500)
        assert _get_retry_delay(exc, 0) == 2
        assert _get_retry_delay(exc, 1) == 4


class TestRateLimitState:
    """Test cross-run rate-limit backoff via state file."""

    def test_429_records_rate_limit_state(self, tmp_path):
        """After 429, state file should be written with timestamp and backoff."""
        state_file = tmp_path / "llm_rate_limit_state.json"
        with patch("_llm_call._RATE_LIMIT_STATE", state_file):
            from _llm_call import _record_rate_limit
            _record_rate_limit(300)

        assert state_file.exists()
        data = json.loads(state_file.read_text())
        assert "last_429_ts" in data
        assert data["backoff_seconds"] == 300

    def test_cross_run_backoff_skips_call(self, tmp_path):
        """State file present + not expired → returns remaining seconds."""
        state_file = tmp_path / "llm_rate_limit_state.json"
        # Write state file as if 429 happened 60 seconds ago, 300s backoff
        ts = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
        state_file.write_text(json.dumps({
            "last_429_ts": ts,
            "backoff_seconds": 300,
        }))

        with patch("_llm_call._RATE_LIMIT_STATE", state_file):
            from _llm_call import _check_rate_limit_backoff
            remaining = _check_rate_limit_backoff("test_caller")

        assert remaining is not None
        # Should be approximately 240s (300 - 60), allow some tolerance
        assert 230 < remaining < 250

    def test_cross_run_backoff_clears_after_expiry(self, tmp_path):
        """State file expired → cleared, returns None."""
        state_file = tmp_path / "llm_rate_limit_state.json"
        # Write state file as if 429 happened 400 seconds ago, 300s backoff
        ts = (datetime.now(timezone.utc) - timedelta(seconds=400)).isoformat()
        state_file.write_text(json.dumps({
            "last_429_ts": ts,
            "backoff_seconds": 300,
        }))

        with patch("_llm_call._RATE_LIMIT_STATE", state_file):
            from _llm_call import _check_rate_limit_backoff
            remaining = _check_rate_limit_backoff("test_caller")

        assert remaining is None
        # State file should have been removed
        assert not state_file.exists()

    def test_no_state_file_returns_none(self, tmp_path):
        """No state file → returns None (no backoff)."""
        state_file = tmp_path / "nonexistent_rate_limit.json"
        with patch("_llm_call._RATE_LIMIT_STATE", state_file):
            from _llm_call import _check_rate_limit_backoff
            result = _check_rate_limit_backoff("test_caller")
        assert result is None


class TestPermanentErrors:
    """Verify 401/403 still break immediately."""

    def test_401_is_permanent(self):
        from _llm_call import _is_permanent_error
        exc = _make_http_error(401)
        assert _is_permanent_error(exc) is True

    def test_403_is_permanent(self):
        from _llm_call import _is_permanent_error
        exc = _make_http_error(403)
        assert _is_permanent_error(exc) is True

    def test_429_is_not_permanent(self):
        from _llm_call import _is_permanent_error
        exc = _make_http_error(429)
        assert _is_permanent_error(exc) is False

    def test_500_is_not_permanent(self):
        from _llm_call import _is_permanent_error
        exc = _make_http_error(500)
        assert _is_permanent_error(exc) is False


class TestIsRateLimitError:
    """Verify _is_rate_limit_error() detection."""

    def test_429_detected(self):
        from _llm_call import _is_rate_limit_error
        exc = _make_http_error(429)
        assert _is_rate_limit_error(exc) is True

    def test_500_not_rate_limit(self):
        from _llm_call import _is_rate_limit_error
        exc = _make_http_error(500)
        assert _is_rate_limit_error(exc) is False

    def test_non_http_error_not_rate_limit(self):
        from _llm_call import _is_rate_limit_error
        assert _is_rate_limit_error(TimeoutError("timeout")) is False


class TestCallLlmWithRateLimit:
    """Integration: verify call_llm_text/json skip when backoff active."""

    def test_call_llm_text_skips_on_active_backoff(self, tmp_path):
        """call_llm_text should return None immediately if backoff active."""
        state_file = tmp_path / "llm_rate_limit_state.json"
        ts = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
        state_file.write_text(json.dumps({
            "last_429_ts": ts,
            "backoff_seconds": 300,
        }))

        with patch("_llm_call._RATE_LIMIT_STATE", state_file), \
             patch("_llm_call._get_client") as mock_client, \
             patch("_llm_call._log_failure"):
            from _llm_call import call_llm_text
            result = call_llm_text(
                model="test-model",
                system="test",
                user="test",
                caller="test_integration",
            )

        assert result is None
        # Client should never have been called
        mock_client.assert_not_called()

    def test_call_llm_json_skips_on_active_backoff(self, tmp_path):
        """call_llm_json should return None immediately if backoff active."""
        state_file = tmp_path / "llm_rate_limit_state.json"
        ts = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
        state_file.write_text(json.dumps({
            "last_429_ts": ts,
            "backoff_seconds": 300,
        }))

        with patch("_llm_call._RATE_LIMIT_STATE", state_file), \
             patch("_llm_call._get_client") as mock_client, \
             patch("_llm_call._log_failure"):
            from _llm_call import call_llm_json
            result = call_llm_json(
                model="test-model",
                system="test",
                user="test",
                caller="test_integration",
            )

        assert result is None
        mock_client.assert_not_called()
