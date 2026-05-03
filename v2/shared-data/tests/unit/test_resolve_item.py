"""Unit tests for resolve_item.py — tracker read/write/flock."""
from __future__ import annotations

import importlib
import json
import threading
import time
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _reload_modules(tmp_root):
    import _paths
    importlib.reload(_paths)
    import resolve_item
    importlib.reload(resolve_item)
    yield


@pytest.fixture()
def tracker_path(tmp_root):
    return tmp_root / "learning_tracker.json"


@pytest.fixture()
def state_path(tmp_root, monkeypatch):
    sp = tmp_root / "quantai-learning.json"
    monkeypatch.setenv("QUANTAI_DASHBOARD_STATE", str(sp))
    import _paths
    importlib.reload(_paths)
    import resolve_item
    importlib.reload(resolve_item)
    return sp


def _write_state(state_path: Path, open_items=None, resolved_items=None):
    state_path.write_text(json.dumps({
        "last_updated": "2026-05-02T00:00:00",
        "status": "ok",
        "data": {
            "open_items": open_items or [],
            "resolved_items": resolved_items or [],
            "stats": {"total_open": len(open_items or []), "total_resolved": 0},
        },
    }))


class TestReadTracker:
    def test_returns_empty_when_missing(self, tracker_path):
        import resolve_item as ri
        result = ri._read_tracker()
        assert result == {"resolved": {}}

    def test_reads_existing_tracker(self, tracker_path):
        import resolve_item as ri
        tracker_path.parent.mkdir(parents=True, exist_ok=True)
        tracker_path.write_text(json.dumps({"resolved": {"id1": {"note": "done"}}}))
        result = ri._read_tracker()
        assert "id1" in result["resolved"]

    def test_adds_resolved_key_if_missing(self, tracker_path):
        import resolve_item as ri
        tracker_path.parent.mkdir(parents=True, exist_ok=True)
        tracker_path.write_text(json.dumps({}))
        result = ri._read_tracker()
        assert "resolved" in result


class TestWriteTracker:
    def test_creates_file(self, tracker_path):
        import resolve_item as ri
        ri._write_tracker({"resolved": {"x": {"note": "done"}}})
        assert tracker_path.exists()
        data = json.loads(tracker_path.read_text())
        assert "x" in data["resolved"]

    def test_atomic_no_tmp_leftover(self, tracker_path):
        import resolve_item as ri
        ri._write_tracker({"resolved": {}})
        tmp = tracker_path.with_suffix(".tmp")
        assert not tmp.exists()


class TestResolve:
    def test_resolve_creates_entry(self, tmp_root, state_path):
        import resolve_item as ri
        open_item = {
            "id": "cap-2026-04-27-agent_alpha-data-abc123",
            "agent": "agent_alpha", "type": "capability_request",
            "title": "Need better data", "priority": "would_help",
        }
        _write_state(state_path, open_items=[open_item])
        rc = ri._resolve("cap-2026-04-27-agent_alpha-data-abc123", "Implemented VIX refresh")
        assert rc == 0
        tracker = ri._read_tracker()
        assert "cap-2026-04-27-agent_alpha-data-abc123" in tracker["resolved"]
        entry = tracker["resolved"]["cap-2026-04-27-agent_alpha-data-abc123"]
        assert entry["resolution_note"] == "Implemented VIX refresh"
        assert "resolved_date" in entry

    def test_resolve_captures_context_from_state(self, tmp_root, state_path):
        import resolve_item as ri
        item_id = "cap-2026-04-27-agent_alpha-data-abc123"
        open_item = {
            "id": item_id, "agent": "agent_alpha",
            "type": "capability_request", "title": "Need better data",
            "priority": "would_help",
        }
        _write_state(state_path, open_items=[open_item])
        ri._resolve(item_id, "done")
        tracker = ri._read_tracker()
        entry = tracker["resolved"][item_id]
        assert entry.get("agent") == "agent_alpha"
        assert entry.get("type") == "capability_request"
        assert entry.get("title") == "Need better data"

    def test_resolve_without_state_still_records(self, tmp_root):
        import resolve_item as ri
        rc = ri._resolve("ghost-id-123", "done without state")
        assert rc == 0
        tracker = ri._read_tracker()
        assert "ghost-id-123" in tracker["resolved"]

    def test_resolve_requires_note(self, tmp_root):
        import resolve_item as ri
        rc = ri._resolve("cap-id", "")
        assert rc == 2

    def test_resolve_requires_id(self, tmp_root):
        import resolve_item as ri
        rc = ri._resolve("", "some note")
        assert rc == 2


class TestUnresolve:
    def test_unresolve_removes_entry(self, tmp_root, state_path):
        import resolve_item as ri
        _write_state(state_path)
        ri._resolve("cap-id-1", "done")
        assert "cap-id-1" in ri._read_tracker()["resolved"]
        rc = ri._unresolve("cap-id-1")
        assert rc == 0
        assert "cap-id-1" not in ri._read_tracker()["resolved"]

    def test_unresolve_missing_returns_error(self, tmp_root):
        import resolve_item as ri
        rc = ri._unresolve("nonexistent-id")
        assert rc == 1


class TestConcurrentWrites:
    def test_two_threads_do_not_corrupt_tracker(self, tmp_root, state_path):
        """Gap 2 regression: two simultaneous _write_tracker calls must not corrupt JSON."""
        import resolve_item as ri
        _write_state(state_path)

        errors = []

        def resolve_n(n):
            try:
                ri._resolve(f"id-{n}", f"note {n}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=resolve_n, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Thread errors: {errors}"
        # Tracker file must be valid JSON with exactly 10 entries
        data = json.loads((tmp_root / "learning_tracker.json").read_text())
        assert len(data["resolved"]) == 10
