"""Unit tests for beta/risk_engine.py — the Beta agent's pre-trade risk gates.

Each test class names the §16 rule it enforces (docs/architecture.md §16).
Tests exercise real guard logic against synthetic journal/account state.
No broker mocking required: check_risk accepts a plain journal list, not a
file path, so every gate can be reached without touching /root/quantai-v2/.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import beta.risk_engine as re_mod
from beta.risk_engine import (
    CIRCUIT_BREAKER_LOSSES,
    DRAWDOWN_HALT_DAILY,
    DRAWDOWN_HALF_SIZE_WEEKLY,
    MAX_OPEN_POSITIONS,
    MAX_TRADES_PER_DAY,
    _hours_since,
    _is_beta,
    _monday_iso,
    _today_iso,
    check_risk,
    load_journal,
    open_beta_positions,
)

ET = ZoneInfo("America/New_York")

# ── Fixed timestamps aligned with the frozen_today fixture ────────────────────
# Freeze: today = 2026-05-06 (Wednesday), week_start = 2026-05-04 (Monday)
_TODAY     = "2026-05-06T10:00:00-04:00"
_YESTERDAY = "2026-05-05T10:00:00-04:00"   # within this week, not today
_MONDAY    = "2026-05-04T10:00:00-04:00"   # week start
_LAST_WEEK = "2026-04-28T10:00:00-04:00"   # previous week


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def frozen_today(monkeypatch):
    """Pin _today_iso / _monday_iso to deterministic dates.

    Any test that calls check_risk should use this fixture so that
    date-string comparisons inside the gate functions are reproducible
    regardless of when the test suite runs.
    """
    monkeypatch.setattr(re_mod, "_today_iso", lambda: "2026-05-06")
    monkeypatch.setattr(re_mod, "_monday_iso", lambda: "2026-05-04")


# ── Factories ────────────────────────────────────────────────────────────────


def _beta(status: str = "OPEN", pnl: float = 0.0, timestamp: str = _YESTERDAY,
           close_timestamp: str | None = None, **kw) -> dict:
    """Synthetic agent_beta journal entry.

    Defaults timestamp to yesterday so entries don't accidentally count
    toward gate 2's daily-trade limit unless the caller passes
    timestamp=_TODAY explicitly.

    close_timestamp is omitted from the dict when None so that gate 4/5's
    `t.get("close_timestamp", t.get("timestamp", ""))` fallback works
    correctly and doesn't raise TypeError on None[:10].
    """
    entry: dict = {
        "id": "B001",
        "source": "agent_beta",
        "status": status,
        "pnl": pnl,
        "timestamp": timestamp,
        "net_delta": 0.0,
        "net_vega": 0.0,
        "risk_pct": 0.01,
        **kw,
    }
    if close_timestamp is not None:
        entry["close_timestamp"] = close_timestamp
    return entry


def _account(equity: float = 100_000.0) -> dict:
    return {"equity": equity}


def _trade(**kw) -> dict:
    """Minimal new_trade dict for the check_risk call-under-test."""
    return {"source": "agent_beta", "net_delta": 0.0, "net_vega": 0.0,
            "risk_pct": 0.01, **kw}


# ── Helper unit tests ─────────────────────────────────────────────────────────


class TestIsBeta:
    """Internal predicate that scopes every gate to agent_beta entries only."""

    def test_agent_beta_source_returns_true(self):
        assert _is_beta({"source": "agent_beta"}) is True

    def test_agent_alpha_source_returns_false(self):
        assert _is_beta({"source": "agent_alpha"}) is False

    def test_missing_source_returns_false(self):
        assert _is_beta({}) is False


class TestDateHelpers:
    """_today_iso and _monday_iso produce well-formed ISO date strings."""

    def test_today_iso_returns_valid_date_string(self):
        parts = _today_iso().split("-")
        assert len(parts) == 3 and len(parts[0]) == 4

    def test_monday_iso_returns_a_monday(self):
        from datetime import date
        assert date.fromisoformat(_monday_iso()).weekday() == 0  # 0 = Monday

    def test_monday_iso_at_or_before_today(self):
        from datetime import date
        assert date.fromisoformat(_monday_iso()) <= date.fromisoformat(_today_iso())


class TestHoursSince:
    """_hours_since drives the 24-hour cooldown on the circuit-breaker gate."""

    def test_current_timestamp_under_one_minute(self):
        assert _hours_since(datetime.now(ET).isoformat()) < 0.02

    def test_twenty_five_hours_ago_exceeds_24h(self):
        old = (datetime.now(ET) - timedelta(hours=25)).isoformat()
        assert _hours_since(old) > 24

    def test_naive_timestamp_assumed_ET(self):
        naive = datetime.now(ET).replace(tzinfo=None).isoformat()
        assert _hours_since(naive) < 0.02

    def test_invalid_string_returns_sentinel_999(self):
        """Corrupt timestamps must return 999 (fail-open: cooldown treated as expired)."""
        assert _hours_since("not-a-date") == 999.0

    def test_empty_string_returns_sentinel_999(self):
        assert _hours_since("") == 999.0


class TestLoadJournal:
    """Journal loader feeds every downstream gate; corrupt lines must never crash."""

    def test_missing_file_returns_empty_list(self, tmp_path):
        assert load_journal(tmp_path / "nonexistent.jsonl") == []

    def test_reads_valid_entries(self, tmp_path):
        p = tmp_path / "trades.jsonl"
        p.write_text('{"id":"B001"}\n{"id":"B002"}\n')
        assert [r["id"] for r in load_journal(p)] == ["B001", "B002"]

    def test_skips_blank_lines(self, tmp_path):
        p = tmp_path / "trades.jsonl"
        p.write_text('{"id":"B001"}\n\n\n{"id":"B002"}\n')
        assert len(load_journal(p)) == 2

    def test_skips_malformed_json_lines(self, tmp_path):
        p = tmp_path / "trades.jsonl"
        p.write_text('{"id":"B001"}\nnot json\n{"id":"B002"}\n')
        result = load_journal(p)
        assert len(result) == 2
        assert result[0]["id"] == "B001"


class TestOpenBetaPositions:
    """Filter used by the position-limit and correlation gates."""

    def test_returns_only_open_beta_trades(self):
        journal = [
            _beta(status="OPEN"),
            _beta(status="CLOSED", pnl=-100),
            {"source": "agent_alpha", "status": "OPEN", "net_delta": 0, "net_vega": 0},
        ]
        result = open_beta_positions(journal)
        assert len(result) == 1 and result[0]["status"] == "OPEN"

    def test_empty_journal_returns_empty_list(self):
        assert open_beta_positions([]) == []


# ── Gate tests ────────────────────────────────────────────────────────────────


class TestGate1PositionLimit:
    """§16 Rule: no more than 3 simultaneous open Beta positions."""

    def test_allowed_with_no_open_positions(self, frozen_today):
        ok, _, _ = check_risk(_trade(), {}, _account(), [])
        assert ok

    def test_allowed_with_two_open_positions(self, frozen_today):
        journal = [_beta(status="OPEN") for _ in range(2)]
        ok, _, _ = check_risk(_trade(), {}, _account(), journal)
        assert ok

    def test_blocked_exactly_at_limit(self, frozen_today):
        """At exactly MAX_OPEN_POSITIONS the gate must deny entry."""
        journal = [_beta(status="OPEN") for _ in range(MAX_OPEN_POSITIONS)]
        ok, reason, _ = check_risk(_trade(), {}, _account(), journal)
        assert not ok
        assert "max" in reason and "positions" in reason

    def test_closed_trades_do_not_consume_slots(self, frozen_today):
        journal = [_beta(status="CLOSED", pnl=-100) for _ in range(10)]
        ok, _, _ = check_risk(_trade(), {}, _account(), journal)
        assert ok

    def test_alpha_positions_not_counted_toward_beta_cap(self, frozen_today):
        """Alpha open positions are independent; they must not block Beta entry."""
        journal = [
            {"source": "agent_alpha", "status": "OPEN", "timestamp": _YESTERDAY,
             "net_delta": 0, "net_vega": 0}
            for _ in range(MAX_OPEN_POSITIONS)
        ]
        ok, _, _ = check_risk(_trade(), {}, _account(), journal)
        assert ok


class TestGate2DailyTradeLimit:
    """§16 Rule: no more than 2 Beta entries per calendar day (ET)."""

    def test_blocked_when_two_beta_trades_already_entered_today(self, frozen_today):
        journal = [
            _beta(status="OPEN", timestamp=_TODAY),
            _beta(status="OPEN", timestamp=_TODAY),
        ]
        ok, reason, _ = check_risk(_trade(), {}, _account(), journal)
        assert not ok
        assert "today" in reason.lower()

    def test_allowed_when_only_one_trade_today(self, frozen_today):
        journal = [_beta(status="OPEN", timestamp=_TODAY)]
        ok, _, _ = check_risk(_trade(), {}, _account(), journal)
        assert ok

    def test_yesterday_trades_do_not_count(self, frozen_today):
        journal = [
            _beta(status="OPEN", timestamp=_YESTERDAY),
            _beta(status="OPEN", timestamp=_YESTERDAY),
        ]
        ok, _, _ = check_risk(_trade(), {}, _account(), journal)
        assert ok

    def test_alpha_trades_do_not_count_toward_beta_daily_limit(self, frozen_today):
        journal = [
            {"source": "agent_alpha", "status": "OPEN", "timestamp": _TODAY,
             "net_delta": 0, "net_vega": 0},
            {"source": "agent_alpha", "status": "OPEN", "timestamp": _TODAY,
             "net_delta": 0, "net_vega": 0},
        ]
        ok, _, _ = check_risk(_trade(), {}, _account(), journal)
        assert ok


class TestGate3CircuitBreaker:
    """§16 Rule: halt after 5 consecutive Beta losses within 24 h of the last close."""

    @pytest.fixture
    def recent_close(self):
        return (datetime.now(ET) - timedelta(hours=1)).isoformat()

    @pytest.fixture
    def old_close(self):
        return (datetime.now(ET) - timedelta(hours=25)).isoformat()

    def _loss_journal(self, n: int, close_ts: str) -> list:
        return [
            _beta(status="CLOSED", pnl=-100,
                  timestamp=f"2026-05-0{i + 1}T12:00:00-04:00",
                  close_timestamp=close_ts)
            for i in range(n)
        ]

    def test_blocked_on_exactly_five_consecutive_losses(self, frozen_today, recent_close):
        journal = self._loss_journal(CIRCUIT_BREAKER_LOSSES, recent_close)
        ok, reason, _ = check_risk(_trade(), {}, _account(), journal)
        assert not ok
        assert "circuit breaker" in reason

    def test_six_consecutive_losses_also_blocked(self, frozen_today, recent_close):
        journal = self._loss_journal(CIRCUIT_BREAKER_LOSSES + 1, recent_close)
        ok, reason, _ = check_risk(_trade(), {}, _account(), journal)
        assert not ok
        assert "circuit breaker" in reason

    def test_four_consecutive_losses_not_blocked(self, frozen_today, recent_close):
        """One loss short of the threshold must not halt trading."""
        journal = self._loss_journal(CIRCUIT_BREAKER_LOSSES - 1, recent_close)
        ok, _, _ = check_risk(_trade(), {}, _account(), journal)
        assert ok

    def test_five_losses_older_than_24h_not_blocked(self, frozen_today, old_close):
        """Cooldown expires after 24 h; trading must resume automatically."""
        journal = self._loss_journal(CIRCUIT_BREAKER_LOSSES, old_close)
        ok, _, _ = check_risk(_trade(), {}, _account(), journal)
        assert ok

    def test_win_resets_consecutive_loss_streak(self, frozen_today, recent_close):
        """A single profitable trade in the streak must reset the consecutive count."""
        journal = [
            _beta(status="CLOSED", pnl=-100, timestamp="2026-05-01T12:00:00-04:00", close_timestamp=recent_close),
            _beta(status="CLOSED", pnl=-100, timestamp="2026-05-02T12:00:00-04:00", close_timestamp=recent_close),
            _beta(status="CLOSED", pnl=+500, timestamp="2026-05-03T12:00:00-04:00", close_timestamp=recent_close),
            _beta(status="CLOSED", pnl=-100, timestamp="2026-05-04T12:00:00-04:00", close_timestamp=recent_close),
            _beta(status="CLOSED", pnl=-100, timestamp="2026-05-05T12:00:00-04:00", close_timestamp=recent_close),
        ]
        # Sorted most-recent first: 05→04→03(win)→streak breaks at 2, not 5
        ok, _, _ = check_risk(_trade(), {}, _account(), journal)
        assert ok


class TestGate4DailyDrawdownHalt:
    """§16 Rule: halt Beta trading when today's realized P&L < -2% of equity."""

    def test_blocked_when_daily_pnl_exceeds_2pct_threshold(self, frozen_today):
        equity = 100_000.0  # 2% = $2,000; losses total $2,001
        journal = [
            _beta(status="CLOSED", pnl=-1_200, close_timestamp=_TODAY),
            _beta(status="CLOSED", pnl=-801,   close_timestamp=_TODAY),
        ]
        ok, reason, _ = check_risk(_trade(), {}, _account(equity), journal)
        assert not ok
        assert "drawdown" in reason.lower()

    def test_just_under_threshold_is_allowed(self, frozen_today):
        equity = 100_000.0
        journal = [_beta(status="CLOSED", pnl=-1_999, close_timestamp=_TODAY)]
        ok, _, _ = check_risk(_trade(), {}, _account(equity), journal)
        assert ok

    def test_exactly_at_threshold_not_blocked(self, frozen_today):
        """P&L == -2% must not halt — the gate uses strict less-than, not <=."""
        equity = 100_000.0
        journal = [_beta(status="CLOSED", pnl=-2_000.0, close_timestamp=_TODAY)]
        ok, _, _ = check_risk(_trade(), {}, _account(equity), journal)
        assert ok

    def test_zero_equity_skips_gate(self, frozen_today):
        """Drawdown gate must be skipped when equity is zero or missing."""
        journal = [_beta(status="CLOSED", pnl=-99_999, close_timestamp=_TODAY)]
        ok, _, _ = check_risk(_trade(), {}, {"equity": 0}, journal)
        assert ok

    def test_yesterday_losses_do_not_trigger_today_halt(self, frozen_today):
        equity = 100_000.0
        journal = [_beta(status="CLOSED", pnl=-9_999, close_timestamp=_YESTERDAY)]
        ok, _, _ = check_risk(_trade(), {}, _account(equity), journal)
        assert ok


class TestGate5WeeklyDrawdownHalfSize:
    """§16 Rule: halve Beta position size when weekly P&L < -5% of equity.

    This gate modifies the outbound trade dict rather than refusing entry.
    """

    def test_risk_pct_halved_on_weekly_drawdown(self, frozen_today):
        equity = 100_000.0  # 5% = $5,000; weekly loss = $6,000
        journal = [_beta(status="CLOSED", pnl=-6_000, close_timestamp=_YESTERDAY)]
        trade = _trade(risk_pct=0.02)
        ok, _, out_trade = check_risk(trade, {}, _account(equity), journal)
        assert ok  # not blocked — just size-reduced
        assert out_trade["risk_pct"] == pytest.approx(0.01)
        assert out_trade.get("risk_note") == "weekly_drawdown_half_size"

    def test_original_trade_dict_not_mutated(self, frozen_today):
        """check_risk must return a modified copy, not mutate the caller's dict."""
        journal = [_beta(status="CLOSED", pnl=-6_000, close_timestamp=_YESTERDAY)]
        trade = _trade(risk_pct=0.02)
        check_risk(trade, {}, _account(100_000.0), journal)
        assert trade["risk_pct"] == 0.02  # original unchanged

    def test_no_halving_when_weekly_pnl_above_threshold(self, frozen_today):
        equity = 100_000.0
        journal = [_beta(status="CLOSED", pnl=-4_999, close_timestamp=_YESTERDAY)]
        trade = _trade(risk_pct=0.02)
        ok, _, out_trade = check_risk(trade, {}, _account(equity), journal)
        assert ok
        assert out_trade.get("risk_pct") == 0.02
        assert out_trade.get("risk_note") is None

    def test_missing_risk_pct_defaults_then_halved(self, frozen_today):
        """Trade with no risk_pct gets the 0.01 fallback, then halved to 0.005."""
        journal = [_beta(status="CLOSED", pnl=-6_000, close_timestamp=_YESTERDAY)]
        trade = {"source": "agent_beta", "net_delta": 0.0, "net_vega": 0.0}
        _, _, out_trade = check_risk(trade, {}, _account(100_000.0), journal)
        assert out_trade["risk_pct"] == pytest.approx(0.005)

    def test_previous_week_losses_do_not_bleed_into_current_week(self, frozen_today):
        equity = 100_000.0
        journal = [_beta(status="CLOSED", pnl=-9_999, close_timestamp=_LAST_WEEK)]
        trade = _trade(risk_pct=0.02)
        ok, _, out_trade = check_risk(trade, {}, _account(equity), journal)
        assert ok
        assert out_trade.get("risk_pct") == 0.02


class TestGate6Correlation:
    """§16 Rule: block new trades that concentrate portfolio delta or vega further."""

    def test_blocked_adding_long_delta_to_long_portfolio(self, frozen_today):
        journal = [_beta(status="OPEN", net_delta=0.6)]
        ok, reason, _ = check_risk(_trade(net_delta=0.3), {}, _account(), journal)
        assert not ok
        assert "long delta" in reason.lower()

    def test_blocked_adding_short_delta_to_short_portfolio(self, frozen_today):
        journal = [_beta(status="OPEN", net_delta=-0.6)]
        ok, reason, _ = check_risk(_trade(net_delta=-0.3), {}, _account(), journal)
        assert not ok
        assert "short delta" in reason.lower()

    def test_blocked_adding_long_vega_to_long_vega_portfolio(self, frozen_today):
        journal = [_beta(status="OPEN", net_vega=1.1)]
        ok, reason, _ = check_risk(_trade(net_vega=0.5), {}, _account(), journal)
        assert not ok
        assert "long vega" in reason.lower()

    def test_blocked_adding_short_vega_to_short_vega_portfolio(self, frozen_today):
        journal = [_beta(status="OPEN", net_vega=-1.1)]
        ok, reason, _ = check_risk(_trade(net_vega=-0.5), {}, _account(), journal)
        assert not ok
        assert "short vega" in reason.lower()

    def test_allowed_at_delta_boundary_exactly_0_5(self, frozen_today):
        """Portfolio net_delta of exactly 0.5 must not block (gate uses strict > 0.5)."""
        journal = [_beta(status="OPEN", net_delta=0.5)]
        ok, _, _ = check_risk(_trade(net_delta=0.3), {}, _account(), journal)
        assert ok

    def test_allowed_at_vega_boundary_exactly_1_0(self, frozen_today):
        """Portfolio net_vega of exactly 1.0 must not block (gate uses strict > 1.0)."""
        journal = [_beta(status="OPEN", net_vega=1.0)]
        ok, _, _ = check_risk(_trade(net_vega=0.5), {}, _account(), journal)
        assert ok

    def test_allowed_opposite_delta_reduces_exposure(self, frozen_today):
        """Short-delta trade into a long-delta portfolio reduces concentration → allowed."""
        journal = [_beta(status="OPEN", net_delta=0.8)]
        ok, _, _ = check_risk(_trade(net_delta=-0.2), {}, _account(), journal)
        assert ok

    def test_allowed_opposite_vega_reduces_exposure(self, frozen_today):
        journal = [_beta(status="OPEN", net_vega=1.5)]
        ok, _, _ = check_risk(_trade(net_vega=-0.5), {}, _account(), journal)
        assert ok


class TestHappyPath:
    """Full gate sequence on a clean book — all six §16 gates must pass."""

    def test_empty_journal_passes_all_gates(self, frozen_today):
        """§16: a first Beta trade on a clean book must clear all risk gates."""
        ok, reason, out_trade = check_risk(_trade(), {}, _account(), [])
        assert ok
        assert reason == "passed"

    def test_returned_trade_unmodified_when_no_weekly_drawdown(self, frozen_today):
        trade = _trade(risk_pct=0.02)
        _, _, out_trade = check_risk(trade, {}, _account(), [])
        assert out_trade["risk_pct"] == 0.02
        assert out_trade.get("risk_note") is None
