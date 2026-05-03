"""Unit tests for _paths.py — env-var override and constant structure."""
from __future__ import annotations

import importlib
import os
from pathlib import Path


def test_defaults_point_under_root():
    import _paths
    importlib.reload(_paths)
    assert str(_paths.JOURNAL).startswith("/root/quantai-v2/shared-data")
    assert str(_paths.CAPABILITY_REQUESTS_DIR).startswith("/root/quantai-v2/shared-data")
    assert str(_paths.TRADE_REVIEWS_DIR).startswith("/root/quantai-v2/shared-data")
    assert str(_paths.WEEKLY_REPORTS_DIR).startswith("/root/quantai-v2/shared-data")
    assert str(_paths.LEARNING_TRACKER).startswith("/root/quantai-v2/shared-data")


def test_env_override_redirects_all_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("QUANTAI_RUNTIME_ROOT", str(tmp_path))
    import _paths
    importlib.reload(_paths)
    assert _paths.JOURNAL.parent.parent.parent == tmp_path
    assert _paths.CAPABILITY_REQUESTS_DIR.parent == tmp_path
    assert _paths.TRADE_REVIEWS_DIR.parent == tmp_path
    assert _paths.WEEKLY_REPORTS_DIR.parent == tmp_path
    assert _paths.LEARNING_TRACKER.parent == tmp_path
    # Reload to defaults so other tests aren't affected
    monkeypatch.delenv("QUANTAI_RUNTIME_ROOT")
    importlib.reload(_paths)


def test_dashboard_state_override(tmp_path, monkeypatch):
    custom = tmp_path / "custom_state.json"
    monkeypatch.setenv("QUANTAI_DASHBOARD_STATE", str(custom))
    import _paths
    importlib.reload(_paths)
    assert _paths.LEARNING_STATE == custom
    monkeypatch.delenv("QUANTAI_DASHBOARD_STATE")
    importlib.reload(_paths)


def test_journal_ends_with_jsonl():
    import _paths
    importlib.reload(_paths)
    assert _paths.JOURNAL.suffix == ".jsonl"


def test_learning_tracker_ends_with_json():
    import _paths
    importlib.reload(_paths)
    assert _paths.LEARNING_TRACKER.suffix == ".json"
