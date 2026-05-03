"""Integration tests: weekly_synthesis aggregates trade data + posts to Discord."""
from __future__ import annotations

import importlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

ET = ZoneInfo("America/New_York")


@pytest.fixture(autouse=True)
def _reload_modules(tmp_root):
    import _paths, _journal_update, _decision_helpers, weekly_synthesis
    for mod in (_paths, _journal_update, _decision_helpers, weekly_synthesis):
        importlib.reload(mod)
    yield


def _make_trade(trade_id, source, pnl, week_ts="2026-04-28T10:00:00+00:00"):
    return {
        "id": trade_id,
        "source": source,
        "symbol": "SPY",
        "strategy": "test_strategy",
        "pnl": pnl,
        "pnl_pct": pnl / 500 * 100,
        "close_reason": "TAKE_PROFIT" if pnl > 0 else "STOP_LOSS",
        "timestamp": week_ts,
        "close_timestamp": week_ts,
        "status": "CLOSED",
        "decision": {
            "thesis": "test thesis",
            "vix_at_entry": 18.0,
            "regime_at_entry": "neutral_trending",
        },
    }


def _write_trades(journal_path, trades):
    with open(journal_path, "w") as f:
        for t in trades:
            f.write(json.dumps(t) + "\n")


def _make_diagnosis_file(tmp_root, agent, trade_id, week_ts="2026-04-28T10:00:00+00:00"):
    d = tmp_root / "capability_requests" / agent
    d.mkdir(parents=True, exist_ok=True)
    payload = {
        "trade_id": trade_id,
        "timestamp": week_ts,
        "diagnosis": {
            "gaps_identified": [
                {
                    "dimension": "data_freshness",
                    "request": "Need better VIX data",
                    "evidence": "test",
                    "priority": "would_help",
                    "estimated_impact_dollars": 150,
                }
            ],
            "no_gaps_note": None,
        },
    }
    (d / f"{trade_id}.json").write_text(json.dumps(payload))


MOCK_SYNTHESIS = (
    "1. PERFORMANCE SUMMARY: 2 trades, 100% win rate, $350 P&L.\n"
    "2. CAPABILITY REQUESTS: VIX data freshness flagged.\n"
    "3. PARAMETER SUGGESTIONS: None.\n"
    "4. KNOWLEDGE UPDATES: None.\n"
    "5. INFRASTRUCTURE REQUESTS: None."
)


class TestSynthesizeWithData:
    def test_dry_run_no_llm_no_file(self, tmp_root, journal_path):
        _write_trades(journal_path, [
            _make_trade("A001", "agent_alpha", 180),
            _make_trade("A002", "agent_alpha", 170),
        ])
        import weekly_synthesis as ws
        importlib.reload(ws)
        week_start = datetime(2026, 4, 27, 0, 0, tzinfo=ET)
        rc = ws.synthesize(week_start, dry_run=True)
        assert rc == 0
        reports = list((tmp_root / "weekly_reports").glob("*.md"))
        assert reports == []

    def test_writes_report_file(self, tmp_root, journal_path, monkeypatch):
        _write_trades(journal_path, [_make_trade("A001", "agent_alpha", 180)])
        _make_diagnosis_file(tmp_root, "agent_alpha", "A001")

        mock_llm_content = MagicMock()
        mock_llm_content.text = MOCK_SYNTHESIS
        mock_llm_resp = MagicMock()
        mock_llm_resp.content = [mock_llm_content]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_llm_resp
        mock_module = MagicMock()
        mock_module.Client.return_value = mock_client
        monkeypatch.setitem(sys.modules, "_llm_client", mock_module)

        # Disable Discord
        monkeypatch.setenv("DISCORD_CHANNEL_ALERTS", "")

        import weekly_synthesis as ws
        importlib.reload(ws)
        week_start = datetime(2026, 4, 27, 0, 0, tzinfo=ET)
        rc = ws.synthesize(week_start, dry_run=False)
        assert rc == 0
        reports = list((tmp_root / "weekly_reports").glob("*.md"))
        assert len(reports) == 1

    def test_report_contains_pnl(self, tmp_root, journal_path, monkeypatch):
        _write_trades(journal_path, [
            _make_trade("A001", "agent_alpha", 180),
            _make_trade("A002", "agent_alpha", -80),
        ])

        mock_content = MagicMock()
        mock_content.text = MOCK_SYNTHESIS
        mock_resp = MagicMock()
        mock_resp.content = [mock_content]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_resp
        mock_module = MagicMock()
        mock_module.Client.return_value = mock_client
        monkeypatch.setitem(sys.modules, "_llm_client", mock_module)
        monkeypatch.setenv("DISCORD_CHANNEL_ALERTS", "")

        import weekly_synthesis as ws
        importlib.reload(ws)
        week_start = datetime(2026, 4, 27, 0, 0, tzinfo=ET)
        ws.synthesize(week_start, dry_run=False)
        report_path = list((tmp_root / "weekly_reports").glob("*.md"))[0]
        content = report_path.read_text()
        assert "100" in content  # net P&L $100

    def test_discord_post_called_with_channel(self, tmp_root, journal_path, monkeypatch):
        _write_trades(journal_path, [_make_trade("A001", "agent_alpha", 100)])

        mock_content = MagicMock()
        mock_content.text = MOCK_SYNTHESIS
        mock_resp = MagicMock()
        mock_resp.content = [mock_content]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_resp
        mock_module = MagicMock()
        mock_module.Client.return_value = mock_client
        monkeypatch.setitem(sys.modules, "_llm_client", mock_module)

        discord_calls = []

        def mock_post(channel, msg):
            discord_calls.append((channel, msg))
            return True

        mock_discord = MagicMock()
        mock_discord.post_to_channel = mock_post
        monkeypatch.setitem(sys.modules, "_discord", mock_discord)
        monkeypatch.setenv("DISCORD_CHANNEL_ALERTS", "test-channel-id")

        import weekly_synthesis as ws
        importlib.reload(ws)
        week_start = datetime(2026, 4, 27, 0, 0, tzinfo=ET)
        ws.synthesize(week_start, dry_run=False)

        assert any(ch == "test-channel-id" for ch, _ in discord_calls)

    def test_llm_failure_uses_fallback_text(self, tmp_root, journal_path, monkeypatch):
        """Sonnet failure after retry → fallback text, still writes report."""
        _write_trades(journal_path, [_make_trade("A001", "agent_alpha", 180)])
        _make_diagnosis_file(tmp_root, "agent_alpha", "A001")

        mock_module = MagicMock()
        mock_module.Client.return_value.messages.create.return_value.content = []

        def raising_client(*args, **kwargs):
            raise RuntimeError("Sonnet is down")

        mock_module.Client.return_value.messages.create.side_effect = raising_client
        monkeypatch.setitem(sys.modules, "_llm_client", mock_module)
        monkeypatch.setenv("DISCORD_CHANNEL_ALERTS", "")

        import weekly_synthesis as ws
        importlib.reload(ws)
        week_start = datetime(2026, 4, 27, 0, 0, tzinfo=ET)
        rc = ws.synthesize(week_start, dry_run=False)
        assert rc == 0  # should not fail; fallback text used
        reports = list((tmp_root / "weekly_reports").glob("*.md"))
        assert len(reports) == 1
        content = reports[0].read_text()
        assert "Synthesis failed" in content or "failed" in content.lower()
