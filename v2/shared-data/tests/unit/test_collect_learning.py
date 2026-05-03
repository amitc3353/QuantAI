"""Unit tests for collect_learning.py — grouping, sorting, ID stability."""
from __future__ import annotations

import hashlib
import importlib
import json
from datetime import datetime
from pathlib import Path

import pytest

from conftest import assert_learning_state_schema


@pytest.fixture(autouse=True)
def _reload_modules(tmp_root):
    import _paths, _decision_helpers
    for mod in (_paths, _decision_helpers):
        importlib.reload(mod)
    import collect_learning
    importlib.reload(collect_learning)
    yield


def _make_capability_request(tmp_root: Path, agent: str, trade_id: str,
                              dimension: str = "data_freshness",
                              priority: str = "would_help",
                              impact: float | None = 100.0,
                              timestamp: str | None = None) -> Path:
    d = tmp_root / "capability_requests" / agent
    d.mkdir(parents=True, exist_ok=True)
    ts = timestamp or "2026-04-28T10:00:00+00:00"
    payload = {
        "trade_id": trade_id,
        "timestamp": ts,
        "diagnosis": {
            "gaps_identified": [
                {
                    "dimension": dimension,
                    "request": f"Need better {dimension} for {trade_id}",
                    "evidence": f"Evidence from {trade_id}",
                    "priority": priority,
                    "estimated_impact_dollars": impact,
                }
            ],
            "no_gaps_note": None,
        },
    }
    fp = d / f"{trade_id}.json"
    fp.write_text(json.dumps(payload))
    return fp


def _make_review_md(tmp_root: Path, agent: str, trade_id: str,
                    param: str = "rsi_threshold",
                    current: str = "30",
                    suggested: str = "28",
                    reason: str = "oversold threshold too conservative",
                    timestamp: str | None = None) -> Path:
    d = tmp_root / "trade_reviews" / agent
    d.mkdir(parents=True, exist_ok=True)
    ts = timestamp or "2026-04-28T10:00:00+00:00"
    md = (
        f"# Trade Review: {trade_id}\n\n"
        "## Parameter Suggestions\n"
        f"- **{param}:** {current} → {suggested} — {reason}\n"
    )
    fp = d / f"{trade_id}.md"
    fp.write_text(md)
    # Set a known mtime
    import time
    dt_ts = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    import os
    os.utime(fp, (dt_ts, dt_ts))
    return fp


class TestSlug:
    def test_basic(self):
        import collect_learning as cl
        assert cl._slug("data freshness") == "data-freshness"

    def test_special_chars(self):
        import collect_learning as cl
        assert cl._slug("hello/world.test") == "hello-world-test"

    def test_empty(self):
        import collect_learning as cl
        assert cl._slug("") == "item"

    def test_already_clean(self):
        import collect_learning as cl
        assert cl._slug("abc123") == "abc123"


class TestWeekStartConsistency:
    def test_matches_decision_helpers(self):
        import collect_learning as cl
        import _decision_helpers as dh
        ts = "2026-04-29T12:00:00+00:00"
        assert cl._week_start(ts) == dh.week_start_for(ts)

    def test_consistent_for_all_days_of_week(self):
        import collect_learning as cl
        import _decision_helpers as dh
        days = [
            "2026-04-27", "2026-04-28", "2026-04-29",
            "2026-04-30", "2026-05-01", "2026-05-02", "2026-05-03",
        ]
        for d in days:
            assert cl._week_start(d) == dh.week_start_for(d), (
                f"divergence for {d}"
            )


class TestStableId:
    def test_deterministic(self):
        import collect_learning as cl
        id1 = cl._stable_id("cap", "2026-04-27", "agent_alpha", "data_freshness")
        id2 = cl._stable_id("cap", "2026-04-27", "agent_alpha", "data_freshness")
        assert id1 == id2

    def test_prefix_in_id(self):
        import collect_learning as cl
        item_id = cl._stable_id("cap", "2026-04-27", "agent_alpha", "data_freshness")
        assert item_id.startswith("cap-")

    def test_different_inputs_different_ids(self):
        import collect_learning as cl
        id1 = cl._stable_id("cap", "2026-04-27", "agent_alpha", "data_freshness")
        id2 = cl._stable_id("cap", "2026-04-27", "agent_beta", "data_freshness")
        assert id1 != id2


class TestGroupCapabilityItems:
    def test_groups_by_week_agent_dimension(self, tmp_root):
        _make_capability_request(tmp_root, "agent_alpha", "A001", "data_freshness")
        _make_capability_request(tmp_root, "agent_alpha", "A002", "data_freshness")
        _make_capability_request(tmp_root, "agent_alpha", "A003", "execution_timing")
        import collect_learning as cl
        raw = cl._load_capability_requests()
        grouped = cl._group_capability_items(raw)
        # Should have 2 groups: data_freshness (freq=2) and execution_timing (freq=1)
        by_dim = {g["dimension"]: g for g in grouped
                  if g["agent"] == "agent_alpha"}
        assert "data_freshness" in by_dim
        assert by_dim["data_freshness"]["frequency"] == 2
        assert "execution_timing" in by_dim

    def test_impact_summed(self, tmp_root):
        _make_capability_request(tmp_root, "agent_alpha", "A001", impact=100.0)
        _make_capability_request(tmp_root, "agent_alpha", "A002", impact=150.0)
        import collect_learning as cl
        raw = cl._load_capability_requests()
        grouped = cl._group_capability_items(raw)
        g = next(g for g in grouped if g["agent"] == "agent_alpha")
        assert g["estimated_impact"] == pytest.approx(250.0)

    def test_worst_priority_wins(self, tmp_root):
        _make_capability_request(tmp_root, "agent_alpha", "A001", priority="nice_to_have")
        _make_capability_request(tmp_root, "agent_alpha", "A002", priority="critical")
        import collect_learning as cl
        raw = cl._load_capability_requests()
        grouped = cl._group_capability_items(raw)
        g = next(g for g in grouped if g["agent"] == "agent_alpha")
        assert g["priority"] == "critical"

    def test_returns_empty_when_no_requests(self, tmp_root):
        import collect_learning as cl
        assert cl._group_capability_items([]) == []


class TestSortOpen:
    def test_critical_before_would_help(self):
        import collect_learning as cl
        items = [
            {"id": "a", "priority": "would_help", "frequency": 5,
             "estimated_impact": 0, "date": "2026-04-27"},
            {"id": "b", "priority": "critical", "frequency": 1,
             "estimated_impact": 0, "date": "2026-04-27"},
        ]
        sorted_items = cl._sort_open(items)
        assert sorted_items[0]["id"] == "b"

    def test_higher_frequency_first_within_priority(self):
        import collect_learning as cl
        items = [
            {"id": "a", "priority": "would_help", "frequency": 1,
             "estimated_impact": 0, "date": "2026-04-27"},
            {"id": "b", "priority": "would_help", "frequency": 5,
             "estimated_impact": 0, "date": "2026-04-27"},
        ]
        sorted_items = cl._sort_open(items)
        assert sorted_items[0]["id"] == "b"


class TestMainOutput:
    def test_produces_valid_state_file(self, tmp_root, monkeypatch):
        _make_capability_request(tmp_root, "agent_alpha", "A001")
        state_path = tmp_root / "quantai-learning.json"
        monkeypatch.setenv("QUANTAI_DASHBOARD_STATE", str(state_path))
        import _paths
        importlib.reload(_paths)
        import collect_learning as cl
        importlib.reload(cl)
        rc = cl.main()
        assert rc == 0
        assert state_path.exists()
        state = json.loads(state_path.read_text())
        assert_learning_state_schema(state)

    def test_idle_status_when_empty(self, tmp_root, monkeypatch):
        state_path = tmp_root / "quantai-learning.json"
        monkeypatch.setenv("QUANTAI_DASHBOARD_STATE", str(state_path))
        import _paths
        importlib.reload(_paths)
        import collect_learning as cl
        importlib.reload(cl)
        cl.main()
        state = json.loads(state_path.read_text())
        assert state["status"] == "idle"

    def test_warning_status_when_many_open(self, tmp_root, monkeypatch):
        # Create 11+ capability request files for different dimensions
        for i in range(12):
            _make_capability_request(tmp_root, "agent_alpha", f"A{i:03d}",
                                     dimension=f"dim_{i}")
        state_path = tmp_root / "quantai-learning.json"
        monkeypatch.setenv("QUANTAI_DASHBOARD_STATE", str(state_path))
        import _paths
        importlib.reload(_paths)
        import collect_learning as cl
        importlib.reload(cl)
        cl.main()
        state = json.loads(state_path.read_text())
        assert state["status"] == "warning"
