"""Tests for _gate_logger.py — centralized gate-block logging."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import _gate_logger as gl


def test_log_gate_block_writes_jsonl(tmp_path, monkeypatch):
    log_file = tmp_path / "gate_blocks.jsonl"
    monkeypatch.setattr(gl, "GATE_LOG", log_file)

    gl.log_gate_block("concentration", "SPY", "alpha", "already open", "iron_condor")

    lines = log_file.read_text().strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["gate"] == "concentration"
    assert entry["symbol"] == "SPY"
    assert entry["agent"] == "alpha"
    assert entry["reason"] == "already open"
    assert entry["would_have_been_strategy"] == "iron_condor"
    assert "ts" in entry


def test_log_gate_block_appends(tmp_path, monkeypatch):
    log_file = tmp_path / "gate_blocks.jsonl"
    monkeypatch.setattr(gl, "GATE_LOG", log_file)

    gl.log_gate_block("freshness", "AAPL", "alpha", "VIX stale", "bull_put_spread")
    gl.log_gate_block("cooldown", "GOOGL", "alpha", "3-day wait", "bear_call_spread")

    lines = log_file.read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["gate"] == "freshness"
    assert json.loads(lines[1])["gate"] == "cooldown"


def test_log_gate_block_creates_parent_dirs(tmp_path, monkeypatch):
    log_file = tmp_path / "nested" / "deep" / "gate_blocks.jsonl"
    monkeypatch.setattr(gl, "GATE_LOG", log_file)

    gl.log_gate_block("macro_blackout", "XSP", "beta", "CPI window", "event_strangle")

    assert log_file.exists()
    entry = json.loads(log_file.read_text().strip())
    assert entry["gate"] == "macro_blackout"


def test_log_gate_block_empty_strategy(tmp_path, monkeypatch):
    log_file = tmp_path / "gate_blocks.jsonl"
    monkeypatch.setattr(gl, "GATE_LOG", log_file)

    gl.log_gate_block("conviction", "MSFT", "gamma", "score too low")

    entry = json.loads(log_file.read_text().strip())
    assert entry["would_have_been_strategy"] == ""


def test_log_gate_block_survives_write_failure(tmp_path, monkeypatch):
    log_file = tmp_path / "readonly" / "gate_blocks.jsonl"
    log_file.parent.mkdir()
    log_file.parent.chmod(0o444)
    monkeypatch.setattr(gl, "GATE_LOG", log_file)

    gl.log_gate_block("freshness", "SPY", "alpha", "stale", "test")
    log_file.parent.chmod(0o755)


def test_all_gate_names_valid(tmp_path, monkeypatch):
    log_file = tmp_path / "gate_blocks.jsonl"
    monkeypatch.setattr(gl, "GATE_LOG", log_file)

    for gate in ["concentration", "freshness", "event_timing", "cooldown", "conviction", "macro_blackout"]:
        gl.log_gate_block(gate, "TEST", "alpha", "test reason", "test_strategy")

    lines = log_file.read_text().strip().splitlines()
    assert len(lines) == 6
    gates = [json.loads(l)["gate"] for l in lines]
    assert gates == ["concentration", "freshness", "event_timing", "cooldown", "conviction", "macro_blackout"]


def test_ts_is_iso_format(tmp_path, monkeypatch):
    log_file = tmp_path / "gate_blocks.jsonl"
    monkeypatch.setattr(gl, "GATE_LOG", log_file)

    gl.log_gate_block("cooldown", "AAPL", "beta", "waiting", "debit_spread")

    entry = json.loads(log_file.read_text().strip())
    from datetime import datetime, timezone
    parsed = datetime.fromisoformat(entry["ts"])
    assert parsed.tzinfo is not None
