"""Integration tests: collect_learning → resolve_item → collect_learning cycle."""
from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from conftest import assert_learning_state_schema


@pytest.fixture(autouse=True)
def _reload_all(tmp_root, monkeypatch):
    """Reload all path-sensitive modules into the sandbox."""
    import _paths, _decision_helpers
    for mod in (_paths, _decision_helpers):
        importlib.reload(mod)

    state_path = tmp_root / "quantai-learning.json"
    monkeypatch.setenv("QUANTAI_DASHBOARD_STATE", str(state_path))
    importlib.reload(_paths)

    import collect_learning, resolve_item
    for mod in (collect_learning, resolve_item):
        importlib.reload(mod)

    yield state_path


def _make_capability_request(tmp_root: Path, agent: str, trade_id: str,
                              dimension: str = "data_freshness",
                              timestamp: str = "2026-04-28T10:00:00+00:00"):
    d = tmp_root / "capability_requests" / agent
    d.mkdir(parents=True, exist_ok=True)
    payload = {
        "trade_id": trade_id,
        "timestamp": timestamp,
        "diagnosis": {
            "gaps_identified": [
                {
                    "dimension": dimension,
                    "request": f"Better {dimension}",
                    "evidence": "test evidence",
                    "priority": "would_help",
                    "estimated_impact_dollars": 100.0,
                }
            ],
            "no_gaps_note": None,
        },
    }
    (d / f"{trade_id}.json").write_text(json.dumps(payload))


class TestCollectThenResolve:
    def test_collect_produces_open_item(self, tmp_root, _reload_all):
        state_path = _reload_all
        _make_capability_request(tmp_root, "agent_alpha", "A001")

        import collect_learning as cl
        importlib.reload(cl)
        cl.main()

        state = json.loads(state_path.read_text())
        assert_learning_state_schema(state)
        assert len(state["data"]["open_items"]) >= 1
        item = state["data"]["open_items"][0]
        assert item["agent"] == "agent_alpha"
        assert item["status"] == "open"

    def test_resolve_moves_item_to_resolved(self, tmp_root, _reload_all):
        state_path = _reload_all
        _make_capability_request(tmp_root, "agent_alpha", "A001")

        import collect_learning as cl
        importlib.reload(cl)
        cl.main()

        state = json.loads(state_path.read_text())
        item_id = state["data"]["open_items"][0]["id"]

        import resolve_item as ri
        importlib.reload(ri)
        rc = ri._resolve(item_id, "Implemented VIX refresh every 5 min")
        assert rc == 0

        # Re-run collector
        cl.main()
        state2 = json.loads(state_path.read_text())

        open_ids = [i["id"] for i in state2["data"]["open_items"]]
        resolved_ids = [r["id"] for r in state2["data"]["resolved_items"]]
        assert item_id not in open_ids
        assert item_id in resolved_ids

    def test_resolved_item_shows_note(self, tmp_root, _reload_all):
        state_path = _reload_all
        _make_capability_request(tmp_root, "agent_alpha", "A001")

        import collect_learning as cl
        importlib.reload(cl)
        cl.main()

        state = json.loads(state_path.read_text())
        item_id = state["data"]["open_items"][0]["id"]

        import resolve_item as ri
        importlib.reload(ri)
        ri._resolve(item_id, "Upgraded VIX feed to 5-minute polling")
        cl.main()

        state2 = json.loads(state_path.read_text())
        resolved = next(r for r in state2["data"]["resolved_items"] if r["id"] == item_id)
        assert "Upgraded VIX feed" in resolved["resolution_note"]

    def test_unresolve_reopens_item(self, tmp_root, _reload_all):
        state_path = _reload_all
        _make_capability_request(tmp_root, "agent_alpha", "A001")

        import collect_learning as cl
        import resolve_item as ri
        for mod in (cl, ri):
            importlib.reload(mod)

        cl.main()
        state = json.loads(state_path.read_text())
        item_id = state["data"]["open_items"][0]["id"]

        ri._resolve(item_id, "done")
        cl.main()
        state2 = json.loads(state_path.read_text())
        assert item_id not in [i["id"] for i in state2["data"]["open_items"]]

        ri._unresolve(item_id)
        cl.main()
        state3 = json.loads(state_path.read_text())
        open_ids = [i["id"] for i in state3["data"]["open_items"]]
        assert item_id in open_ids

    def test_multiple_agents_appear_in_stats(self, tmp_root, _reload_all):
        state_path = _reload_all
        _make_capability_request(tmp_root, "agent_alpha", "A001")
        _make_capability_request(tmp_root, "agent_beta", "B001")

        import collect_learning as cl
        importlib.reload(cl)
        cl.main()

        state = json.loads(state_path.read_text())
        by_agent = state["data"]["stats"]["by_agent"]
        assert "agent_alpha" in by_agent
        assert "agent_beta" in by_agent

    def test_item_id_stable_across_runs(self, tmp_root, _reload_all):
        state_path = _reload_all
        _make_capability_request(tmp_root, "agent_alpha", "A001")

        import collect_learning as cl
        importlib.reload(cl)
        cl.main()
        state1 = json.loads(state_path.read_text())
        id1 = state1["data"]["open_items"][0]["id"]

        cl.main()
        state2 = json.loads(state_path.read_text())
        id2 = state2["data"]["open_items"][0]["id"]

        assert id1 == id2, "Item ID must be stable across collector runs"

    def test_resolved_items_without_source_preserved_in_history(self, tmp_root, _reload_all):
        """Resolved items whose source files have aged out stay visible in history."""
        state_path = _reload_all
        _make_capability_request(tmp_root, "agent_alpha", "A001")

        import collect_learning as cl
        import resolve_item as ri
        for mod in (cl, ri):
            importlib.reload(mod)

        cl.main()
        state = json.loads(state_path.read_text())
        item_id = state["data"]["open_items"][0]["id"]

        ri._resolve(item_id, "done")

        # Remove source file (simulate aging out)
        src = tmp_root / "capability_requests" / "agent_alpha" / "A001.json"
        src.unlink()

        cl.main()
        state2 = json.loads(state_path.read_text())
        resolved_ids = [r["id"] for r in state2["data"]["resolved_items"]]
        assert item_id in resolved_ids, "Resolved item must persist after source ages out"
