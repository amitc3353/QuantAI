"""Unit tests for _escalate_stale_phantoms() — D6 / Commit 4.

Bug context (D6): the existing entry-phantom detection in
reconcile_ghost_positions() posts hourly Discord alerts but has no terminal
state. Real-world impact: Gc001/Gd001 fired 1,466 alerts over 5 days with no
resolution. Arm cash was permanently locked, decrementing the arm's trading
capacity for the full experiment.

Fix (Commit 4): after N hours of sustained phantom condition (default 3h),
auto-mark the journal entry as PHANTOM_NEVER_FILLED, restore the arm's cash
(for Gamma 4-arm trades), and post a single resolution Discord alert.

These tests pin:
- Tracking lifecycle: first observation starts the clock; recovery clears it
- Escalation threshold semantics: < N hours = no-op, >= N hours = escalate
- Side effects: journal status update, arm cash restoration, Discord alert
- Idempotency: a trade escalated this cycle is removed from tracking
- Edge cases: non-arm trades, missing max_risk, corrupt tracking, arm_state
  import failure (graceful degradation)
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

# Bootstrap fake broker before importing position_monitor
_BROKER_MOD = MagicMock()
_BROKER_MOD.BrokerBase = type("FakeBrokerBase", (), {})
_BROKER_MOD.DRY_RUN_SENTINEL = {"DRY_RUN": True}
sys.modules.setdefault("broker", _BROKER_MOD)

import position_monitor as pm  # noqa: E402


# ── Fixtures ──────────────────────────────────────────────────────────────


def _per_arm_phantom_trade(tid="Gtest", arm_id="a", max_risk=55.0) -> dict:
    """Build a Gamma per-arm trade structure matching real phantoms."""
    return {
        "id": tid,
        "source": f"agent_gamma_arm_{arm_id}",
        "arm_id": arm_id,
        "symbol": "USB",
        "strategy": "rsi_pullback_debit_spread",
        "status": "OPEN",
        "max_risk": max_risk,
        "net_debit": max_risk / 100,
        "fill_status": "Submitted",
        "filled_qty": 0,
        "timestamp": "2026-05-26T09:34:45-04:00",
        "legs": [
            {"side": "buy", "type": "C", "strike": 53.0,
             "expiry": "2026-06-15", "symbol": "USB260615C00053000"},
            {"side": "sell", "type": "C", "strike": 54.0,
             "expiry": "2026-06-15", "symbol": "USB260615C00054000"},
        ],
    }


def _non_arm_trade(tid="A001", max_risk=100.0) -> dict:
    """Build a non-arm (legacy Gamma / Alpha / manual) trade."""
    return {
        "id": tid,
        "source": "agent_alpha",
        "symbol": "INTC",
        "strategy": "iron_condor",
        "status": "OPEN",
        "max_risk": max_risk,
        "legs": [
            {"action": "sell", "type": "put", "strike": 100.0,
             "expiry": "2026-06-15", "symbol": "INTC260615P00100000"},
            {"action": "buy", "type": "put", "strike": 99.0,
             "expiry": "2026-06-15", "symbol": "INTC260615P00099000"},
            {"action": "sell", "type": "call", "strike": 110.0,
             "expiry": "2026-06-15", "symbol": "INTC260615C00110000"},
            {"action": "buy", "type": "call", "strike": 111.0,
             "expiry": "2026-06-15", "symbol": "INTC260615C00111000"},
        ],
    }


_NOW = datetime(2026, 5, 29, 12, 0, 0)  # well-defined "now" for all tests


@pytest.fixture(autouse=True)
def stub_discord_and_journal(monkeypatch):
    """Make Discord and journal-write side effects observable, not real."""
    calls = {"discord": [], "journal_updates": []}
    monkeypatch.setattr(pm, "post_discord", lambda msg: calls["discord"].append(msg))
    monkeypatch.setattr(pm, "rewrite_journal_atomic",
                         lambda updates: calls["journal_updates"].append(dict(updates)) or True)
    # Force DRY_RUN=False so the in-test side effects fire (we've stubbed them)
    monkeypatch.setattr(pm, "DRY_RUN", False)
    return calls


@pytest.fixture(autouse=True)
def stub_arm_state(monkeypatch):
    """Stub the gamma.arm_state module so cash restoration is observable."""
    calls = {"loads": [], "saves": [], "journal": []}

    arm_states = {
        "a": {"cash": 9000.0, "current_equity": 10000.0,
              "total_realized_pnl": 0.0, "total_trades": 0,
              "winning_trades": 0, "losing_trades": 0,
              "consecutive_losses": 0},
        "b": {"cash": 9500.0, "current_equity": 10000.0,
              "total_realized_pnl": 0.0, "total_trades": 0,
              "winning_trades": 0, "losing_trades": 0,
              "consecutive_losses": 0},
        "c": {"cash": 9750.0, "current_equity": 10000.0,
              "total_realized_pnl": 0.0, "total_trades": 0,
              "winning_trades": 0, "losing_trades": 0,
              "consecutive_losses": 0},
        "d": {"cash": 9000.0, "current_equity": 10000.0,
              "total_realized_pnl": 0.0, "total_trades": 0,
              "winning_trades": 0, "losing_trades": 0,
              "consecutive_losses": 0},
    }

    fake_arm_state = MagicMock()
    fake_arm_state.load_arm_state = MagicMock(side_effect=lambda aid: (
        calls["loads"].append(aid) or arm_states[aid]))
    fake_arm_state.save_arm_state = MagicMock(side_effect=lambda aid, state: (
        calls["saves"].append((aid, dict(state))) or arm_states.__setitem__(aid, state)))
    fake_arm_state.update_arm_journal_entry = MagicMock(side_effect=lambda aid, tid, upd: (
        calls["journal"].append((aid, tid, dict(upd)))))

    fake_gamma_pkg = MagicMock()
    fake_gamma_pkg.arm_state = fake_arm_state
    monkeypatch.setitem(sys.modules, "gamma", fake_gamma_pkg)
    monkeypatch.setitem(sys.modules, "gamma.arm_state", fake_arm_state)

    calls["arm_states"] = arm_states
    return calls


# ── Tracking lifecycle ────────────────────────────────────────────────────


class TestPhantomTrackingLifecycle:
    """Tracking dict semantics — first observation starts the clock, recovery clears."""

    def test_first_observation_starts_tracking_no_escalation(self, stub_discord_and_journal):
        """A trade seen as phantom for the first time gets a tracking entry
        and is NOT escalated this cycle."""
        trade = _per_arm_phantom_trade(tid="Gx001", arm_id="a", max_risk=55.0)
        tracking = {}
        escalated = pm._escalate_stale_phantoms(
            [trade], {"Gx001"}, now=_NOW, tracking=tracking)
        assert escalated == set()
        assert "Gx001" in tracking
        assert tracking["Gx001"] == _NOW.isoformat()
        # No Discord alert, no journal update
        assert stub_discord_and_journal["discord"] == []
        assert stub_discord_and_journal["journal_updates"] == []

    def test_below_threshold_no_escalation(self, stub_discord_and_journal):
        """A trade with first_seen < N hours ago is NOT escalated."""
        trade = _per_arm_phantom_trade(tid="Gx002", arm_id="b")
        # First seen 1 hour ago — below 3h threshold
        tracking = {"Gx002": (_NOW - timedelta(hours=1)).isoformat()}
        escalated = pm._escalate_stale_phantoms(
            [trade], {"Gx002"}, now=_NOW, tracking=tracking)
        assert escalated == set()
        assert "Gx002" in tracking  # still tracked
        assert stub_discord_and_journal["discord"] == []

    def test_at_threshold_escalates(self, stub_discord_and_journal):
        """A trade with first_seen exactly N hours ago IS escalated."""
        trade = _per_arm_phantom_trade(tid="Gx003", arm_id="c", max_risk=55.0)
        tracking = {"Gx003": (_NOW - timedelta(hours=pm._PHANTOM_ESCALATION_HOURS)).isoformat()}
        escalated = pm._escalate_stale_phantoms(
            [trade], {"Gx003"}, now=_NOW, tracking=tracking)
        assert escalated == {"Gx003"}
        assert "Gx003" not in tracking  # cleared after escalation

    def test_above_threshold_escalates(self, stub_discord_and_journal):
        """A trade with first_seen >> N hours ago IS escalated (e.g. the
        4 live DE phantoms that have been around for 3 days)."""
        trade = _per_arm_phantom_trade(tid="Ga002", arm_id="a", max_risk=508.0)
        # 3 days = 72 hours
        tracking = {"Ga002": (_NOW - timedelta(days=3)).isoformat()}
        escalated = pm._escalate_stale_phantoms(
            [trade], {"Ga002"}, now=_NOW, tracking=tracking)
        assert escalated == {"Ga002"}
        # Discord alert mentions the elapsed time
        msgs = stub_discord_and_journal["discord"]
        assert len(msgs) == 1
        assert "Ga002" in msgs[0]
        assert "72.0h" in msgs[0]

    def test_recovery_clears_tracking(self, stub_discord_and_journal):
        """A tracked trade that's no longer phantom (broker shows legs again,
        or was closed normally) has its tracking entry cleared."""
        tracking = {"Gx004": (_NOW - timedelta(hours=1)).isoformat()}
        # No phantoms this cycle — trade recovered
        escalated = pm._escalate_stale_phantoms(
            [], set(), now=_NOW, tracking=tracking)
        assert escalated == set()
        assert "Gx004" not in tracking  # cleared

    def test_mixed_tracked_some_recover_some_continue(self, stub_discord_and_journal):
        """Two tracked trades — one recovers, one continues to phantom-out.
        Recovery clears, the still-phantom updates."""
        trade = _per_arm_phantom_trade(tid="Gx005", arm_id="a")
        tracking = {
            "Gx005": (_NOW - timedelta(hours=1)).isoformat(),  # still phantom
            "Gx006": (_NOW - timedelta(hours=2)).isoformat(),  # recovered
        }
        escalated = pm._escalate_stale_phantoms(
            [trade], {"Gx005"}, now=_NOW, tracking=tracking)
        assert escalated == set()  # Gx005 below threshold, Gx006 recovered
        assert "Gx005" in tracking  # still phantom, still tracked
        assert "Gx006" not in tracking  # recovered, cleared


# ── Escalation side effects ───────────────────────────────────────────────


class TestEscalationActions:
    """When escalation fires: journal updated, cash restored, Discord alerted."""

    def test_per_arm_trade_restores_cash(self, stub_discord_and_journal, stub_arm_state):
        """Gamma per-arm phantom: arm cash += max_risk on escalation."""
        trade = _per_arm_phantom_trade(tid="Ga002", arm_id="a", max_risk=508.0)
        tracking = {"Ga002": (_NOW - timedelta(hours=4)).isoformat()}
        cash_before = stub_arm_state["arm_states"]["a"]["cash"]
        escalated = pm._escalate_stale_phantoms(
            [trade], {"Ga002"}, now=_NOW, tracking=tracking)
        assert escalated == {"Ga002"}
        # Cash restored by max_risk
        cash_after = stub_arm_state["arm_states"]["a"]["cash"]
        assert cash_after == cash_before + 508.0
        # save_arm_state called with updated state
        assert len(stub_arm_state["saves"]) == 1
        saved_aid, saved_state = stub_arm_state["saves"][0]
        assert saved_aid == "a"
        assert saved_state["cash"] == cash_before + 508.0

    def test_per_arm_journal_mirror_updates_arm_journal(self, stub_discord_and_journal, stub_arm_state):
        """update_arm_journal_entry is called with PHANTOM_NEVER_FILLED status."""
        trade = _per_arm_phantom_trade(tid="Gb002", arm_id="b", max_risk=508.0)
        tracking = {"Gb002": (_NOW - timedelta(hours=5)).isoformat()}
        pm._escalate_stale_phantoms([trade], {"Gb002"}, now=_NOW, tracking=tracking)
        assert len(stub_arm_state["journal"]) == 1
        aid, tid, upd = stub_arm_state["journal"][0]
        assert aid == "b"
        assert tid == "Gb002"
        assert upd["status"] == "PHANTOM_NEVER_FILLED"
        assert "auto_phantom_escalation" in upd["close_reason"]

    def test_main_journal_atomic_update(self, stub_discord_and_journal, stub_arm_state):
        """rewrite_journal_atomic called with status=PHANTOM_NEVER_FILLED for the escalated trade."""
        trade = _per_arm_phantom_trade(tid="Gc003", arm_id="c", max_risk=525.0)
        tracking = {"Gc003": (_NOW - timedelta(hours=24)).isoformat()}
        pm._escalate_stale_phantoms([trade], {"Gc003"}, now=_NOW, tracking=tracking)
        # rewrite_journal_atomic called once with the update
        assert len(stub_discord_and_journal["journal_updates"]) == 1
        updates = stub_discord_and_journal["journal_updates"][0]
        assert "Gc003" in updates
        assert updates["Gc003"]["status"] == "PHANTOM_NEVER_FILLED"
        assert updates["Gc003"]["close_timestamp"] == _NOW.isoformat()
        assert "auto_phantom_escalation" in updates["Gc003"]["close_reason"]

    def test_discord_alert_format(self, stub_discord_and_journal, stub_arm_state):
        """Single Discord alert per escalated trade, containing key details."""
        trade = _per_arm_phantom_trade(tid="Gd003", arm_id="d", max_risk=525.0)
        tracking = {"Gd003": (_NOW - timedelta(hours=72)).isoformat()}
        pm._escalate_stale_phantoms([trade], {"Gd003"}, now=_NOW, tracking=tracking)
        msgs = stub_discord_and_journal["discord"]
        assert len(msgs) == 1
        m = msgs[0]
        assert "Gd003" in m
        assert "PHANTOM_NEVER_FILLED" in m
        assert "Phantom auto-resolved" in m
        assert "525.00" in m  # cash restoration amount
        assert "D" in m  # arm letter (uppercase in message)

    def test_three_per_arm_phantoms_at_once(self, stub_discord_and_journal, stub_arm_state):
        """The 4 live DE phantoms all escalate in the same cycle."""
        trades = [
            _per_arm_phantom_trade(tid="Ga002", arm_id="a", max_risk=508.0),
            _per_arm_phantom_trade(tid="Gb002", arm_id="b", max_risk=508.0),
            _per_arm_phantom_trade(tid="Gc003", arm_id="c", max_risk=525.0),
            _per_arm_phantom_trade(tid="Gd003", arm_id="d", max_risk=525.0),
        ]
        # All 4 have been phantom for 3 days
        tracking = {
            t["id"]: (_NOW - timedelta(days=3)).isoformat() for t in trades
        }
        escalated = pm._escalate_stale_phantoms(
            trades, set(t["id"] for t in trades), now=_NOW, tracking=tracking)
        assert escalated == {"Ga002", "Gb002", "Gc003", "Gd003"}
        # 4 Discord alerts
        assert len(stub_discord_and_journal["discord"]) == 4
        # 4 saves (one per arm)
        assert len(stub_arm_state["saves"]) == 4
        # Cash restored on each arm
        assert stub_arm_state["arm_states"]["a"]["cash"] == 9000.0 + 508.0
        assert stub_arm_state["arm_states"]["b"]["cash"] == 9500.0 + 508.0
        assert stub_arm_state["arm_states"]["c"]["cash"] == 9750.0 + 525.0
        assert stub_arm_state["arm_states"]["d"]["cash"] == 9000.0 + 525.0
        # Tracking cleared for all
        for tid in ("Ga002", "Gb002", "Gc003", "Gd003"):
            assert tid not in tracking


# ── Edge cases ────────────────────────────────────────────────────────────


class TestEdgeCases:
    """Edge cases that must NOT crash or silently corrupt state."""

    def test_non_arm_trade_no_cash_restoration_but_journal_updated(
            self, stub_discord_and_journal, stub_arm_state):
        """Non-arm trade (Alpha or legacy Gamma): no arm cash to restore,
        but journal still gets marked PHANTOM_NEVER_FILLED."""
        trade = _non_arm_trade(tid="A099", max_risk=200.0)
        tracking = {"A099": (_NOW - timedelta(hours=10)).isoformat()}
        escalated = pm._escalate_stale_phantoms(
            [trade], {"A099"}, now=_NOW, tracking=tracking)
        assert escalated == {"A099"}
        # Journal update applied
        assert len(stub_discord_and_journal["journal_updates"]) == 1
        # No arm state touched
        assert len(stub_arm_state["saves"]) == 0
        # Discord alert sent (without cash restoration note)
        msgs = stub_discord_and_journal["discord"]
        assert len(msgs) == 1
        # Note: should not claim cash was restored
        assert "cash restored" not in msgs[0].lower() or "FAILED" in msgs[0]

    def test_missing_max_risk_treated_as_zero(self, stub_discord_and_journal, stub_arm_state):
        """Trade with no max_risk field: cash restoration is +0 (no harm done)."""
        trade = _per_arm_phantom_trade(tid="Gx099", arm_id="a")
        del trade["max_risk"]
        tracking = {"Gx099": (_NOW - timedelta(hours=10)).isoformat()}
        cash_before = stub_arm_state["arm_states"]["a"]["cash"]
        escalated = pm._escalate_stale_phantoms(
            [trade], {"Gx099"}, now=_NOW, tracking=tracking)
        assert escalated == {"Gx099"}
        # Cash unchanged (max_risk treated as 0)
        cash_after = stub_arm_state["arm_states"]["a"]["cash"]
        assert cash_after == cash_before

    def test_corrupt_tracking_timestamp_resets_clock(self, stub_discord_and_journal):
        """Corrupt timestamp in tracking → reset to now, no escalation this cycle."""
        trade = _per_arm_phantom_trade(tid="Gx100", arm_id="a")
        tracking = {"Gx100": "not-a-valid-iso-timestamp"}
        escalated = pm._escalate_stale_phantoms(
            [trade], {"Gx100"}, now=_NOW, tracking=tracking)
        assert escalated == set()
        # Tracking reset to now, not escalated
        assert tracking["Gx100"] == _NOW.isoformat()

    def test_arm_state_import_failure_still_marks_journal(
            self, stub_discord_and_journal, monkeypatch):
        """When gamma.arm_state can't be imported (or fails), the trade is
        STILL marked PHANTOM_NEVER_FILLED in the main journal so it stops
        firing alerts. Operator is warned that cash restoration failed."""
        # Remove the stubbed arm_state to simulate import failure
        monkeypatch.setitem(sys.modules, "gamma", None)
        monkeypatch.setitem(sys.modules, "gamma.arm_state", None)

        trade = _per_arm_phantom_trade(tid="Gx101", arm_id="a", max_risk=508.0)
        tracking = {"Gx101": (_NOW - timedelta(hours=10)).isoformat()}
        escalated = pm._escalate_stale_phantoms(
            [trade], {"Gx101"}, now=_NOW, tracking=tracking)
        # Still escalated despite arm state failure
        assert escalated == {"Gx101"}
        # Main journal still updated
        assert len(stub_discord_and_journal["journal_updates"]) == 1
        updates = stub_discord_and_journal["journal_updates"][0]
        assert updates["Gx101"]["status"] == "PHANTOM_NEVER_FILLED"
        # Discord alert warns about cash failure
        msgs = stub_discord_and_journal["discord"]
        assert len(msgs) == 1
        assert "FAILED" in msgs[0]
        assert "508.00" in msgs[0]

    def test_phantom_id_not_in_open_trades_drops_tracking(self, stub_discord_and_journal):
        """If a phantom ID was tracked but the trade is no longer in
        open_trades (e.g., already closed by another path), drop tracking."""
        tracking = {"Gx200": (_NOW - timedelta(hours=10)).isoformat()}
        escalated = pm._escalate_stale_phantoms(
            [], {"Gx200"}, now=_NOW, tracking=tracking)
        assert escalated == set()
        assert "Gx200" not in tracking
        # No journal update or Discord alert (trade not findable)
        assert stub_discord_and_journal["journal_updates"] == []
        assert stub_discord_and_journal["discord"] == []

    def test_empty_inputs_no_op(self, stub_discord_and_journal):
        """Empty open_trades + empty phantom_ids = no-op."""
        tracking = {}
        escalated = pm._escalate_stale_phantoms(
            [], set(), now=_NOW, tracking=tracking)
        assert escalated == set()
        assert tracking == {}
        assert stub_discord_and_journal["discord"] == []
        assert stub_discord_and_journal["journal_updates"] == []


# ── Idempotency ───────────────────────────────────────────────────────────


class TestIdempotency:
    """A trade can't be double-escalated within or across cycles."""

    def test_escalated_trade_removed_from_tracking(self, stub_discord_and_journal, stub_arm_state):
        """After escalation, the tracking entry is gone — next cycle's phantom
        detection (still True because journal status hasn't been re-loaded)
        would start fresh, NOT immediately escalate again."""
        trade = _per_arm_phantom_trade(tid="Gx300", arm_id="a", max_risk=100.0)
        tracking = {"Gx300": (_NOW - timedelta(hours=10)).isoformat()}
        # First call: escalates
        pm._escalate_stale_phantoms([trade], {"Gx300"}, now=_NOW, tracking=tracking)
        assert "Gx300" not in tracking
        cash_after_first = stub_arm_state["arm_states"]["a"]["cash"]

        # Second call with the same tracking dict (now cleared) and same phantom ID:
        # would re-start tracking from this moment, no second escalation.
        # In production, the trade would no longer be in open_trades (filtered
        # by status change to PHANTOM_NEVER_FILLED). But verify the function
        # is safe even if called with the stale phantom ID:
        escalated2 = pm._escalate_stale_phantoms(
            [trade], {"Gx300"}, now=_NOW + timedelta(minutes=2), tracking=tracking)
        assert escalated2 == set()  # Not escalated — restarting the clock
        # Cash NOT double-restored
        assert stub_arm_state["arm_states"]["a"]["cash"] == cash_after_first
        # Tracking has the new first_seen
        assert "Gx300" in tracking


# ── Persistence layer (load/save) ─────────────────────────────────────────


class TestPersistence:
    """Disk IO functions degrade gracefully on errors."""

    def test_load_returns_empty_on_missing_file(self, monkeypatch, tmp_path):
        """Missing tracking file → empty dict."""
        monkeypatch.setattr(pm, "_PHANTOM_TRACKING_FILE",
                             tmp_path / "does-not-exist.json")
        assert pm._load_phantom_tracking() == {}

    def test_load_returns_empty_on_corrupt_file(self, monkeypatch, tmp_path):
        """Corrupt JSON → empty dict (graceful degradation)."""
        path = tmp_path / "corrupt.json"
        path.write_text("not valid json {{{")
        monkeypatch.setattr(pm, "_PHANTOM_TRACKING_FILE", path)
        assert pm._load_phantom_tracking() == {}

    def test_save_then_load_roundtrip(self, monkeypatch, tmp_path):
        """Round-trip: save dict, load it back."""
        path = tmp_path / "roundtrip.json"
        monkeypatch.setattr(pm, "_PHANTOM_TRACKING_FILE", path)
        original = {"X001": "2026-05-29T12:00:00", "X002": "2026-05-29T13:00:00"}
        pm._save_phantom_tracking(original)
        loaded = pm._load_phantom_tracking()
        assert loaded == original
