"""Smoke tests: live LLM round-trips through ClawRoute.

Gated behind SMOKE_TEST=1 environment variable.
These tests make REAL LLM API calls via ClawRoute (localhost:18790).
They are NOT run in normal pytest invocations.

Usage:
  SMOKE_TEST=1 pytest tests/smoke/ -v

What each test checks:
  1. Haiku is reachable and responds to a minimal prompt (< 5s).
  2. agent_self_diagnosis.diagnose() produces a valid schema on a real trade.
  3. trade_reviewer.review() produces a valid schema on a real trade.
  4. Both hooks together complete within 50s (worst-case 2× 20s timeout + buffer).
"""
from __future__ import annotations

import importlib
import json
import os
import time

import pytest

from conftest import assert_diagnosis_schema, assert_review_schema

pytestmark = pytest.mark.skipif(
    os.environ.get("SMOKE_TEST") != "1",
    reason="Set SMOKE_TEST=1 to run live LLM smoke tests",
)


@pytest.fixture(autouse=True)
def _reload_modules(tmp_root):
    import _paths, _journal_update
    for mod in (_paths, _journal_update):
        importlib.reload(mod)
    yield


@pytest.fixture()
def smoke_trade(populated_journal):
    """A trade in the sandbox journal ready for diagnosis/review."""
    return populated_journal


class TestClawRouteReachable:
    def test_haiku_responds(self):
        """Confirm Haiku via ClawRoute is reachable and answers in < 10 seconds."""
        from _llm_client import Client
        client = Client()
        t0 = time.monotonic()
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=10,
            messages=[{"role": "user", "content": "Reply with OK only."}],
            timeout=10,
        )
        elapsed = time.monotonic() - t0
        assert elapsed < 10, f"Haiku took {elapsed:.1f}s — ClawRoute may be slow"
        assert resp.content[0].text.strip()


class TestLiveDiagnose:
    def test_diagnose_returns_valid_schema(self, smoke_trade):
        import agent_self_diagnosis as asd
        importlib.reload(asd)
        result = asd.diagnose("A001")
        assert result is not None, "diagnose() returned None — check LLM output"
        assert_diagnosis_schema(result)

    def test_diagnose_completes_under_25s(self, smoke_trade):
        import agent_self_diagnosis as asd
        importlib.reload(asd)
        t0 = time.monotonic()
        asd.diagnose("A001")
        elapsed = time.monotonic() - t0
        assert elapsed < 25, (
            f"diagnose() took {elapsed:.1f}s — exceeds 20s LLM timeout + 5s buffer"
        )


class TestLiveReview:
    def test_review_returns_valid_schema(self, smoke_trade):
        import trade_reviewer as tr
        importlib.reload(tr)
        result = tr.review("A001")
        assert result is not None, "review() returned None — check LLM output"
        assert_review_schema(result)

    def test_review_completes_under_25s(self, smoke_trade):
        import trade_reviewer as tr
        importlib.reload(tr)
        t0 = time.monotonic()
        tr.review("A001")
        elapsed = time.monotonic() - t0
        assert elapsed < 25, (
            f"review() took {elapsed:.1f}s — exceeds 20s LLM timeout + 5s buffer"
        )


class TestBothHooksTogether:
    def test_both_complete_under_50s(self, smoke_trade):
        """Full hook chain (diagnose + review) must finish under 50s total."""
        import agent_self_diagnosis as asd
        import trade_reviewer as tr
        for mod in (asd, tr):
            importlib.reload(mod)

        t0 = time.monotonic()
        asd.diagnose("A001")
        tr.review("A001")
        elapsed = time.monotonic() - t0
        assert elapsed < 50, (
            f"Both hooks took {elapsed:.1f}s — may be delaying monitor cycle"
        )

    def test_both_write_to_journal(self, smoke_trade, tmp_root):
        """After both hooks, journal must have capability_diagnosis and post_trade."""
        import agent_self_diagnosis as asd
        import trade_reviewer as tr
        from _journal_update import find_trade
        for mod in (asd, tr):
            importlib.reload(mod)

        asd.diagnose("A001")
        tr.review("A001")

        t = find_trade("A001", str(tmp_root / "journal/paper/trades.jsonl"))
        assert t is not None
        assert "capability_diagnosis" in t
        assert "post_trade" in t
