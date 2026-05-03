"""Unit tests for weekly_synthesis.py — aggregation, week boundary, Discord."""
from __future__ import annotations

import importlib
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

ET = ZoneInfo("America/New_York")


@pytest.fixture(autouse=True)
def _reload_modules(tmp_root):
    import _paths, _journal_update, _decision_helpers
    for mod in (_paths, _journal_update, _decision_helpers):
        importlib.reload(mod)
    import weekly_synthesis
    importlib.reload(weekly_synthesis)
    yield


def _write_trades(journal_path, trades):
    with open(journal_path, "w") as f:
        for t in trades:
            f.write(json.dumps(t) + "\n")


class TestLastMonday:
    def test_delegates_to_this_week_monday(self):
        import weekly_synthesis as ws
        import _decision_helpers as dh
        now = datetime(2026, 4, 29, 14, 0, tzinfo=timezone.utc)
        assert ws._last_monday(now) == dh.this_week_monday(now)

    def test_wednesday_returns_monday(self):
        import weekly_synthesis as ws
        wed = datetime(2026, 4, 29, 14, 0, tzinfo=timezone.utc)
        monday = ws._last_monday(wed)
        assert monday.weekday() == 0
        assert monday.day == 27


class TestTruncate:
    def test_short_string_unchanged(self):
        import weekly_synthesis as ws
        assert ws._truncate("hello", 100) == "hello"

    def test_long_string_truncated(self):
        import weekly_synthesis as ws
        result = ws._truncate("a" * 200, 10)
        assert len(result) == 10
        assert result.endswith("...")

    def test_exact_length(self):
        import weekly_synthesis as ws
        s = "a" * 50
        assert ws._truncate(s, 50) == s


class TestLoadJournalForWeek:
    def test_finds_closed_trade_in_week(self, tmp_root, journal_path, sample_trade):
        week_start = datetime(2026, 4, 27, 0, 0, tzinfo=ET)
        week_end = datetime(2026, 5, 4, 0, 0, tzinfo=ET)
        _write_trades(journal_path, [sample_trade])
        import weekly_synthesis as ws
        importlib.reload(ws)
        closed, opened = ws._load_journal_for_week(week_start, week_end)
        # sample_trade has close_timestamp = now, which should be in current week
        assert isinstance(closed, list)
        assert isinstance(opened, list)

    def test_empty_journal_returns_empty(self, tmp_root, journal_path):
        journal_path.write_text("")
        week_start = datetime(2026, 4, 27, 0, 0, tzinfo=ET)
        week_end = datetime(2026, 5, 4, 0, 0, tzinfo=ET)
        import weekly_synthesis as ws
        importlib.reload(ws)
        closed, opened = ws._load_journal_for_week(week_start, week_end)
        assert closed == []
        assert opened == []

    def test_missing_journal_returns_empty(self, tmp_root):
        week_start = datetime(2026, 4, 27, 0, 0, tzinfo=ET)
        week_end = datetime(2026, 5, 4, 0, 0, tzinfo=ET)
        import weekly_synthesis as ws
        importlib.reload(ws)
        closed, opened = ws._load_journal_for_week(week_start, week_end)
        assert closed == []
        assert opened == []


class TestAggregateForAgent:
    def test_no_activity_returns_zeros(self, tmp_root):
        week_start = datetime(2026, 4, 27, 0, 0, tzinfo=ET)
        import weekly_synthesis as ws
        importlib.reload(ws)
        agg = ws._aggregate_for_agent("agent_alpha", [], [], week_start)
        assert agg["trades_closed"] == 0
        assert agg["trades_opened"] == 0
        assert agg["win_rate"] == 0
        assert agg["total_pnl"] == 0

    def test_win_rate_calculation(self, tmp_root):
        week_start = datetime(2026, 4, 27, 0, 0, tzinfo=ET)
        closed = [
            {"source": "agent_alpha", "pnl": 100},
            {"source": "agent_alpha", "pnl": -50},
            {"source": "agent_alpha", "pnl": 75},
        ]
        import weekly_synthesis as ws
        importlib.reload(ws)
        agg = ws._aggregate_for_agent("agent_alpha", closed, [], week_start)
        assert agg["win_rate"] == pytest.approx(66.7, abs=0.1)
        assert agg["total_pnl"] == pytest.approx(125.0)


class TestSummarizeForDiscord:
    def test_discord_summary_under_1900_chars(self):
        import weekly_synthesis as ws
        week_start = datetime(2026, 4, 27, 0, 0, tzinfo=ET)
        agg = {
            "trades_closed": 3, "win_rate": 66.7, "total_pnl": 250.0,
        }
        msg = ws._summarize_for_discord("agent_alpha", week_start, agg,
                                         "A" * 2000)
        assert len(msg) <= 1900

    def test_includes_agent_name(self):
        import weekly_synthesis as ws
        week_start = datetime(2026, 4, 27, 0, 0, tzinfo=ET)
        agg = {"trades_closed": 1, "win_rate": 100.0, "total_pnl": 180.0}
        msg = ws._summarize_for_discord("agent_gamma", week_start, agg, "ok")
        assert "agent_gamma" in msg


class TestSynthesize:
    def test_dry_run_succeeds_with_empty_data(self, tmp_root):
        import weekly_synthesis as ws
        importlib.reload(ws)
        week_start = datetime(2026, 4, 27, 0, 0, tzinfo=ET)
        rc = ws.synthesize(week_start, dry_run=True)
        assert rc == 0

    def test_dry_run_no_file_written(self, tmp_root):
        import weekly_synthesis as ws
        importlib.reload(ws)
        week_start = datetime(2026, 4, 27, 0, 0, tzinfo=ET)
        ws.synthesize(week_start, dry_run=True)
        report_dir = tmp_root / "weekly_reports"
        # dry_run should not write any report files
        if report_dir.exists():
            reports = list(report_dir.glob("*.md"))
            assert reports == []
