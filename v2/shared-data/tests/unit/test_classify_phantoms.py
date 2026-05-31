"""Unit tests for diagnostics/classify_phantoms.py — Phase 0 read-only diagnostic.

Pins the five origin-class detection rules so we can trust the diagnostic's
output before using it to decide where to put the strongest FSM guards.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from diagnostics import classify_phantoms as cp  # noqa: E402


def _phantom(**fields) -> dict:
    base = {
        "id": "X001",
        "status": "PHANTOM_NEVER_FILLED",
        "timestamp": "2026-05-29T10:00:00+00:00",
    }
    base.update(fields)
    return base


# ── classify() rules ─────────────────────────────────────────────────────────

def test_o1_never_submitted_when_no_order_id():
    assert cp.classify(_phantom(order_id=None)) == "O1_NEVER_SUBMITTED"
    assert cp.classify(_phantom(order_id="")) == "O1_NEVER_SUBMITTED"
    # Missing key entirely
    assert cp.classify(_phantom()) == "O1_NEVER_SUBMITTED"


def test_o2_rejected_when_terminal_failure_status():
    for status in ("cancelled", "rejected", "inactive", "apicancelled"):
        r = _phantom(order_id="abc", fill_status=status)
        assert cp.classify(r) == "O2_REJECTED_AT_BROKER", status
    # Case-insensitive
    assert cp.classify(_phantom(order_id="abc", fill_status="REJECTED")) == "O2_REJECTED_AT_BROKER"


def test_o3_stuck_indeterminate_when_submitted_status():
    for status in ("submitted", "presubmitted", "pendingsubmit", "apipending"):
        r = _phantom(order_id="abc", fill_status=status)
        assert cp.classify(r) == "O3_STUCK_INDETERMINATE", status


def test_o4_open_then_vanished_when_fill_landed():
    # fill_confirmed_at present → was OPEN
    r = _phantom(order_id="abc", fill_status="filled",
                 fill_confirmed_at="2026-05-29T10:01:00+00:00")
    assert cp.classify(r) == "O4_OPEN_THEN_VANISHED"

    # Or filled_qty > 0
    r = _phantom(order_id="abc", fill_status="filled", filled_qty=1)
    assert cp.classify(r) == "O4_OPEN_THEN_VANISHED"


def test_o5_close_failure_takes_priority():
    # working_close_order_id wins over everything
    r = _phantom(order_id="abc", fill_status="filled", filled_qty=1,
                 working_close_order_id="close-123")
    assert cp.classify(r) == "O5_CLOSE_FAILURE"

    # close_reason mentioning close
    r = _phantom(order_id="abc", close_reason="close_order_rejected")
    assert cp.classify(r) == "O5_CLOSE_FAILURE"


def test_o3_when_order_id_present_but_fill_status_missing():
    # Production case: A021/A022/A025 — broker returned order_id but the
    # final status never made it onto the journal record (None / "" / "unknown").
    for fs in (None, "", "unknown", "None"):
        r = _phantom(order_id="abc", fill_status=fs)
        assert cp.classify(r) == "O3_STUCK_INDETERMINATE", repr(fs)


def test_o0_unclassified_when_status_is_genuinely_weird():
    # Has order_id, fill_status is a non-empty string not in any known set
    r = _phantom(order_id="abc", fill_status="weirdstatus")
    assert cp.classify(r) == "O0_UNCLASSIFIED"


# ── summarize() over a journal ───────────────────────────────────────────────

def test_summarize_ignores_non_phantom_records():
    records = [
        _phantom(id="P001"),  # phantom
        {"id": "O001", "status": "OPEN"},
        {"id": "C001", "status": "CLOSED"},
        _phantom(id="P002", order_id="abc", fill_status="rejected"),
    ]
    summary = cp.summarize(records)
    assert summary["total_records"] == 4
    assert len(summary["phantoms"]) == 2
    assert {r["id"] for r in summary["bins"]["O1_NEVER_SUBMITTED"]} == {"P001"}
    assert {r["id"] for r in summary["bins"]["O2_REJECTED_AT_BROKER"]} == {"P002"}


# ── render() output ──────────────────────────────────────────────────────────

def test_render_includes_counts_and_recommendation():
    records = [
        _phantom(id="P001"),
        _phantom(id="P002"),
        _phantom(id="P003", order_id="abc", fill_status="rejected"),
    ]
    out = cp.render(cp.summarize(records))
    assert "O1_NEVER_SUBMITTED" in out
    assert "O2_REJECTED_AT_BROKER" in out
    assert "DECISION RECOMMENDATION" in out
    assert "Dominant class" in out


def test_render_handles_empty_journal():
    out = cp.render(cp.summarize([]))
    assert "No phantoms found" in out


# ── load_journal() handles missing/malformed files ──────────────────────────

def test_load_journal_returns_empty_when_missing(tmp_path):
    missing = tmp_path / "no_such_file.jsonl"
    assert cp.load_journal(str(missing)) == []


def test_load_journal_skips_malformed_lines(tmp_path):
    p = tmp_path / "trades.jsonl"
    p.write_text('{"id": "P001", "status": "OPEN"}\n'
                 'not json at all\n'
                 '\n'
                 '{"id": "P002", "status": "CLOSED"}\n')
    records = cp.load_journal(str(p))
    assert [r["id"] for r in records] == ["P001", "P002"]


# ── End-to-end smoke through main() ─────────────────────────────────────────

def test_main_runs_end_to_end_with_journal_arg(tmp_path, capsys):
    p = tmp_path / "trades.jsonl"
    p.write_text(json.dumps(_phantom(id="P001")) + "\n"
                 + json.dumps(_phantom(id="P002", order_id="x",
                                       fill_status="rejected")) + "\n"
                 + json.dumps({"id": "O001", "status": "OPEN"}) + "\n")
    rc = cp.main(["--journal", str(p)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Journal records scanned: 3" in out
    assert "PHANTOM_NEVER_FILLED records: 2" in out
    assert "O1_NEVER_SUBMITTED" in out


def test_main_env_var_fallback(tmp_path, monkeypatch, capsys):
    p = tmp_path / "trades.jsonl"
    p.write_text(json.dumps(_phantom(id="P001")) + "\n")
    monkeypatch.setenv("QUANTAI_JOURNAL", str(p))
    rc = cp.main([])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Journal records scanned: 1" in out
