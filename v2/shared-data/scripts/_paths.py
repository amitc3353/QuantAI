"""Central path constants for all self-learning scripts.

All scripts that reference /root/quantai-v2/shared-data/ runtime paths import
from here.  Set QUANTAI_RUNTIME_ROOT to a temp dir in tests to sandbox I/O
without touching production files.

Set QUANTAI_DASHBOARD_STATE to redirect the learning JSON state file (used in
tests for resolve_item / collect_learning).
"""
from __future__ import annotations

import os
from pathlib import Path

_ROOT = Path(os.environ.get("QUANTAI_RUNTIME_ROOT", "/root/quantai-v2/shared-data"))

JOURNAL               = _ROOT / "journal/paper/trades.jsonl"
CAPABILITY_REQUESTS_DIR = _ROOT / "capability_requests"
TRADE_REVIEWS_DIR     = _ROOT / "trade_reviews"
WEEKLY_REPORTS_DIR    = _ROOT / "weekly_reports"
LEARNING_TRACKER      = _ROOT / "learning_tracker.json"

LEARNING_STATE = Path(
    os.environ.get("QUANTAI_DASHBOARD_STATE",
                   "/var/dashboard/state/quantai-learning.json")
)
