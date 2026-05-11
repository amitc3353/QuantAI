"""Shared fixtures and assertion helpers for the self-learning test suite."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

# ── Path setup ───────────────────────────────────────────────────────────────
SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))


# ── Discord side-effect blocker (autouse) ────────────────────────────────────
# Tests in test_agent_self_diagnosis.py / test_trade_reviewer.py / etc. mock
# the LLM client to return bad responses (e.g. "this is not json"). When the
# mocked-bad response flows through _llm_call.call_llm_json's retry-exhausted
# path, _discord_alert() is called — and on the VPS that posts to the real
# Discord channel because DISCORD_CHANNEL_ALERTS is set in .env (auto-loaded
# by the scripts under test). Symptom: pytest run on the VPS triggers real
# Discord "🔴 LLM call failed after 3 attempts" messages.
#
# Fix: stub only the HTTP-side-effect function `_do_discord_post`, NOT the
# `_discord_alert` rate-limiter wrapper. This preserves rate-limit logic that
# test_llm_call.py:TestDiscordRateLimit exercises (it depends on
# `_discord_alert` running for real and calling `_do_discord_post`, which
# tests then re-patch locally to capture posted messages). Belt-and-braces:
# also clear DISCORD_CHANNEL_ALERTS env so the actual HTTP path
# short-circuits even if a test forgets to patch `_do_discord_post`.
#
# Tests that need to verify Discord-post content re-patch `_do_discord_post`
# in-test; pytest's monkeypatch resolves nearer-to-test overrides over the
# autouse one, so those tests continue to work without modification.
@pytest.fixture(autouse=True)
def _block_real_discord_posts(monkeypatch):
    monkeypatch.setenv("DISCORD_CHANNEL_ALERTS", "")
    try:
        import _llm_call  # noqa: F401
        monkeypatch.setattr(
            "_llm_call._do_discord_post", lambda *a, **kw: None, raising=False,
        )
    except ImportError:
        pass


# ── Runtime sandbox fixture ──────────────────────────────────────────────────
@pytest.fixture()
def tmp_root(tmp_path, monkeypatch):
    """Create a sandboxed QUANTAI_RUNTIME_ROOT and wire all _paths constants.

    Yields the root Path so tests can write fixtures into it without touching
    production dirs under /root/quantai-v2/shared-data/.
    """
    root = tmp_path / "quantai_runtime"
    # Create the expected sub-directories
    for sub in [
        "journal/paper",
        "capability_requests",
        "trade_reviews",
        "weekly_reports",
        "memory",
    ]:
        (root / sub).mkdir(parents=True)

    monkeypatch.setenv("QUANTAI_RUNTIME_ROOT", str(root))

    state_dir = tmp_path / "dashboard_state"
    state_dir.mkdir()
    monkeypatch.setenv("QUANTAI_DASHBOARD_STATE", str(state_dir / "quantai-learning.json"))

    # Re-import _paths so module-level constants pick up the new env vars
    import importlib
    import _paths
    importlib.reload(_paths)

    yield root

    # Reload to production defaults after the test (env var removed by monkeypatch)
    importlib.reload(_paths)


@pytest.fixture()
def journal_path(tmp_root):
    """Return the trade journal path inside the sandbox."""
    return tmp_root / "journal/paper/trades.jsonl"


# ── Sample data fixtures ─────────────────────────────────────────────────────
def _make_trade(
    trade_id: str = "A001",
    source: str = "agent_alpha",
    symbol: str = "SPY",
    strategy: str = "bull_call_spread",
    pnl: float = 180.0,
    pnl_pct: float = 18.0,
    close_reason: str = "TAKE_PROFIT",
    status: str = "CLOSED",
    **extras,
) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "id": trade_id,
        "source": source,
        "symbol": symbol,
        "strategy": strategy,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "close_reason": close_reason,
        "close_timestamp": now,
        "timestamp": now,
        "status": status,
        "decision": {
            "thesis": "SPY oversold RSI pullback in uptrend",
            "key_risk": "Market could continue lower",
            "invalidation": "Close below 200-day SMA",
            "conviction_score": 7,
            "vix_at_entry": 18.5,
            "regime_at_entry": "neutral_trending",
            "vix_data_age_seconds": 120,
            "chain_data_age_seconds": 300,
            "market_intel_age_seconds": 600,
        },
        **extras,
    }


@pytest.fixture()
def sample_trade():
    return _make_trade()


@pytest.fixture()
def sample_trade_loss():
    return _make_trade(
        trade_id="A002",
        pnl=-250.0,
        pnl_pct=-25.0,
        close_reason="STOP_LOSS",
    )


@pytest.fixture()
def sample_diagnosis():
    return {
        "gaps_identified": [
            {
                "dimension": "data_freshness",
                "request": "VIX data should refresh every 5 minutes, not every 90 minutes",
                "evidence": "VIX spiked 3 points during hold period; 90-min cache missed it",
                "priority": "would_help",
                "estimated_impact_dollars": 180,
            }
        ],
        "no_gaps_note": None,
    }


@pytest.fixture()
def sample_review():
    return {
        "thesis_outcome": "confirmed",
        "thesis_assessment": "SPY bounced as expected after RSI(10) < 30. Thesis confirmed.",
        "regime_assessment": "Regime neutral_trending was correct; market continued trending after dip.",
        "greeks_notes": "Delta behaved as expected. Theta was minimal over 3-day hold.",
        "timing_assessment": "Entry timing was good. Exit could have held 1 more day for extra 20%.",
        "what_went_right": "RSI recovery to 41 triggered clean exit at profit target.",
        "what_went_wrong": None,
        "lessons": ["RSI recovery exits in uptrends often leave money on the table"],
        "parameter_suggestions": [
            {
                "parameter": "rsi_exit_threshold",
                "current_value": "40",
                "suggested_value": "45",
                "reasoning": "Recovery often continues above 40; higher exit would capture more",
            }
        ],
    }


@pytest.fixture()
def populated_journal(journal_path, sample_trade, sample_trade_loss):
    """Write two sample trades to the journal and return the path."""
    with open(journal_path, "w") as f:
        f.write(json.dumps(sample_trade) + "\n")
        f.write(json.dumps(sample_trade_loss) + "\n")
    return journal_path


# ── Schema assertion helpers (Gap 7) ─────────────────────────────────────────
def assert_diagnosis_schema(d: dict) -> None:
    """Hand-written assertion that a diagnosis dict matches the expected schema."""
    assert isinstance(d, dict), f"diagnosis must be dict, got {type(d)}"
    assert "gaps_identified" in d, "diagnosis missing 'gaps_identified'"
    assert isinstance(d["gaps_identified"], list), "'gaps_identified' must be list"
    assert "no_gaps_note" in d, "diagnosis missing 'no_gaps_note'"

    valid_dims = {
        "data_freshness", "data_coverage", "execution_timing",
        "analytical_depth", "strategy_gaps", "knowledge_gaps",
    }
    valid_prios = {"critical", "would_help", "nice_to_have"}

    for i, gap in enumerate(d["gaps_identified"]):
        assert isinstance(gap, dict), f"gap[{i}] must be dict"
        for field in ("dimension", "request", "evidence", "priority"):
            assert field in gap, f"gap[{i}] missing '{field}'"
            assert isinstance(gap[field], str), f"gap[{i}]['{field}'] must be str"
            assert gap[field], f"gap[{i}]['{field}'] must not be empty"
        assert gap["dimension"] in valid_dims, (
            f"gap[{i}]['dimension']={gap['dimension']!r} not in {valid_dims}"
        )
        assert gap["priority"] in valid_prios, (
            f"gap[{i}]['priority']={gap['priority']!r} not in {valid_prios}"
        )
        if gap.get("estimated_impact_dollars") is not None:
            assert isinstance(gap["estimated_impact_dollars"], (int, float)), (
                f"gap[{i}]['estimated_impact_dollars'] must be numeric"
            )


def assert_review_schema(r: dict) -> None:
    """Hand-written assertion that a review dict matches the expected schema."""
    assert isinstance(r, dict), f"review must be dict, got {type(r)}"

    required_str = (
        "thesis_outcome", "thesis_assessment", "regime_assessment",
        "greeks_notes", "timing_assessment",
    )
    for field in required_str:
        assert field in r, f"review missing '{field}'"
        assert isinstance(r[field], str), f"review['{field}'] must be str"

    valid_outcomes = {"confirmed", "partially_confirmed", "invalidated", "inconclusive"}
    assert r["thesis_outcome"] in valid_outcomes, (
        f"review['thesis_outcome']={r['thesis_outcome']!r} not in {valid_outcomes}"
    )

    assert "lessons" in r, "review missing 'lessons'"
    assert isinstance(r["lessons"], list), "review['lessons'] must be list"

    assert "parameter_suggestions" in r, "review missing 'parameter_suggestions'"
    assert isinstance(r["parameter_suggestions"], list), (
        "review['parameter_suggestions'] must be list"
    )
    for i, ps in enumerate(r["parameter_suggestions"]):
        for field in ("parameter", "current_value", "suggested_value", "reasoning"):
            assert field in ps, f"parameter_suggestion[{i}] missing '{field}'"


def assert_open_item_schema(item: dict) -> None:
    """Assert a learning open-item dict matches the dashboard contract."""
    assert isinstance(item, dict)
    for field in ("id", "date", "agent", "type", "title", "priority", "status"):
        assert field in item, f"open_item missing '{field}'"
    assert item["status"] == "open"
    assert item["type"] in ("capability_request", "parameter_suggestion")
    assert item["priority"] in ("critical", "would_help", "nice_to_have")
    assert item["id"].startswith(("cap-", "param-")), (
        f"item id {item['id']!r} has unexpected prefix"
    )


def assert_learning_state_schema(state: dict) -> None:
    """Assert the quantai-learning.json state file matches the dashboard contract."""
    assert isinstance(state, dict)
    assert "last_updated" in state
    assert "status" in state
    assert state["status"] in ("ok", "warning", "stale", "idle")
    assert "data" in state
    data = state["data"]
    assert "open_items" in data
    assert "resolved_items" in data
    assert "stats" in data
    assert isinstance(data["open_items"], list)
    assert isinstance(data["resolved_items"], list)
    stats = data["stats"]
    assert "total_open" in stats
    assert "total_resolved" in stats
    for item in data["open_items"]:
        assert_open_item_schema(item)


# Expose helpers as pytest fixtures so tests can inject them directly
@pytest.fixture()
def assert_schema():
    return {
        "diagnosis": assert_diagnosis_schema,
        "review": assert_review_schema,
        "open_item": assert_open_item_schema,
        "learning_state": assert_learning_state_schema,
    }
