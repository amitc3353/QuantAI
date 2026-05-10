"""Tests for gamma/arm_state.py — per-arm state tracking.

Per the 4-arm A/B/C/D test plan (docs/gamma-four-arm-ab-test-plan.md §B),
each arm runs as a fully isolated virtual portfolio. This file covers:

* Load/save roundtrip + atomic write semantics
* Per-arm equity isolation (Arm A trade doesn't touch B/C/D)
* Per-arm circuit breaker (only the affected arm pauses)
* Per-arm sector cap (Arm A's holdings don't block Arm B's)
* Compounding position sizing (1% of CURRENT equity, scales)
* Reconciliation (cash + open_max_risk == current_equity, ±$1)
* Reconciliation alerts (Discord callback fires on drift)
* Clean restart (reset_arm) zeroes equity, archives old data
* Trade ID prefix per arm (Ga/Gb/Gc/Gd counters independent)
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from gamma.arm_state import (  # noqa: E402
    ARM_TO_RANKER_NAME,
    DEFAULT_STARTING_EQUITY,
    RECONCILE_DOLLAR_THRESHOLD,
    VALID_ARM_IDS,
    append_arm_trade,
    arm_open_positions,
    compute_experiment_day,
    init_all_arms,
    init_arm_state,
    load_arm_journal,
    load_arm_state,
    next_arm_trade_id,
    reconcile,
    reconcile_and_alert,
    reset_arm,
    save_arm_state,
)


# ─────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────


@pytest.fixture
def tmp_cache(tmp_path):
    """Isolated state-file directory per test."""
    cache = tmp_path / "cache"
    cache.mkdir()
    return cache


@pytest.fixture
def tmp_journal(tmp_path):
    """Isolated journal-file directory per test."""
    j = tmp_path / "journal"
    j.mkdir()
    return j


def _trade(arm_id, sym, *, status="OPEN", max_risk=185.0, sector="Technology",
            trade_id=None, realized_pnl=None, close_ts=None):
    """Build a synthetic gamma trade dict matching the production schema."""
    t = {
        "id": trade_id or "GX001",
        "arm_id": arm_id,
        "source": f"agent_gamma_arm_{arm_id}" if arm_id else "agent_gamma",
        "symbol": sym,
        "strategy": "rsi_pullback_debit_spread",
        "max_risk": max_risk,
        "sector": sector,
        "status": status,
    }
    if realized_pnl is not None:
        t["pnl"] = realized_pnl
        t["realized_pnl"] = realized_pnl
    if close_ts:
        t["close_timestamp"] = close_ts
    return t


# ─────────────────────────────────────────────────────────
# Load/save roundtrip
# ─────────────────────────────────────────────────────────


class TestArmStateLoadSave:
    def test_arm_state_load_save_roundtrip(self, tmp_cache):
        state = init_arm_state("a", starting_equity=10_000.0, base_dir=tmp_cache)
        state["current_equity"] = 10_342.18
        state["total_realized_pnl"] = 342.18
        save_arm_state("a", state, base_dir=tmp_cache)

        loaded = load_arm_state("a", base_dir=tmp_cache)
        assert loaded["arm_id"] == "a"
        assert loaded["current_equity"] == 10_342.18
        assert loaded["total_realized_pnl"] == 342.18
        assert loaded["ranker_used"] == "rsi_only"

    def test_load_missing_returns_default(self, tmp_cache):
        """No state file → default state at $10K, not an exception."""
        state = load_arm_state("b", base_dir=tmp_cache)
        assert state["arm_id"] == "b"
        assert state["current_equity"] == DEFAULT_STARTING_EQUITY
        assert state["total_trades"] == 0
        assert state["ranker_used"] == "composite"

    def test_load_corrupt_file_returns_default(self, tmp_cache):
        """Malformed JSON → default, with warning logged."""
        path = tmp_cache / "gamma_arm_c_account.json"
        path.write_text("{not valid json")
        state = load_arm_state("c", base_dir=tmp_cache)
        assert state["arm_id"] == "c"
        assert state["current_equity"] == DEFAULT_STARTING_EQUITY

    def test_invalid_arm_id_raises(self, tmp_cache):
        with pytest.raises(ValueError):
            load_arm_state("z", base_dir=tmp_cache)
        with pytest.raises(ValueError):
            init_arm_state("e", base_dir=tmp_cache)

    def test_save_updates_last_updated(self, tmp_cache):
        state = init_arm_state("a", base_dir=tmp_cache)
        first = state["last_updated"]
        time.sleep(0.01)
        save_arm_state("a", state, base_dir=tmp_cache)
        loaded = load_arm_state("a", base_dir=tmp_cache)
        assert loaded["last_updated"] >= first


# ─────────────────────────────────────────────────────────
# Atomic write (tempfile + os.replace)
# ─────────────────────────────────────────────────────────


class TestStateFileAtomicWrite:
    def test_state_file_atomic_write(self, tmp_cache):
        """Pre-existing file gets cleanly replaced (no partial-write window)."""
        path = tmp_cache / "gamma_arm_a_account.json"
        path.write_text(json.dumps({"arm_id": "a", "current_equity": 9999.99,
                                     "old_field": "before"}))
        # Save a new state — should fully replace, not merge
        init_arm_state("a", starting_equity=10_000.0, base_dir=tmp_cache)
        loaded = json.loads(path.read_text())
        assert loaded["current_equity"] == 10_000.0
        assert "old_field" not in loaded

    def test_save_does_not_leave_temp_files(self, tmp_cache):
        """After save, no .tmp residuals in the cache dir."""
        init_arm_state("a", base_dir=tmp_cache)
        save_arm_state("a", load_arm_state("a", base_dir=tmp_cache),
                        base_dir=tmp_cache)
        tmps = list(tmp_cache.glob("*.tmp"))
        assert tmps == []

    def test_save_recovers_from_full_disk(self, tmp_cache, monkeypatch):
        """If os.fdopen fails mid-write, no broken file is left behind."""
        path = tmp_cache / "gamma_arm_a_account.json"
        original = b'{"existing": "ok"}'
        path.write_bytes(original)

        # Patch json.dump to raise mid-write
        import gamma.arm_state as mod
        original_dump = mod.json.dump

        def boom(*args, **kwargs):
            raise OSError("disk full simulation")

        monkeypatch.setattr(mod.json, "dump", boom)
        with pytest.raises(OSError):
            save_arm_state("a", {"arm_id": "a"}, base_dir=tmp_cache)

        # Original file untouched
        assert path.read_bytes() == original
        # No tmp residue
        assert list(tmp_cache.glob("*.tmp")) == []


# ─────────────────────────────────────────────────────────
# Init all arms (idempotent)
# ─────────────────────────────────────────────────────────


class TestInitAllArms:
    def test_init_creates_all_4_arms(self, tmp_cache, tmp_journal):
        states = init_all_arms(cache_dir=tmp_cache, journal_dir=tmp_journal)
        assert set(states.keys()) == set(VALID_ARM_IDS)
        for aid in VALID_ARM_IDS:
            assert states[aid]["current_equity"] == DEFAULT_STARTING_EQUITY
            assert states[aid]["total_trades"] == 0
            assert states[aid]["arm_id"] == aid
            # State file written
            assert (tmp_cache / f"gamma_arm_{aid}_account.json").exists()
            # Journal file created (empty)
            jpath = tmp_journal / f"gamma_arm_{aid}_trades.jsonl"
            assert jpath.exists()
            assert jpath.read_text() == ""

    def test_init_is_idempotent(self, tmp_cache, tmp_journal):
        init_all_arms(cache_dir=tmp_cache, journal_dir=tmp_journal)
        # Append a fake journal line — re-init should NOT clobber it
        journal_a = tmp_journal / "gamma_arm_a_trades.jsonl"
        journal_a.write_text('{"id": "Ga001"}\n')
        # Re-run init_all_arms → state files reset to $10K (overwrite),
        # but existing journals preserved.
        init_all_arms(cache_dir=tmp_cache, journal_dir=tmp_journal)
        assert journal_a.read_text() == '{"id": "Ga001"}\n'

    def test_init_uses_correct_ranker_per_arm(self, tmp_cache, tmp_journal):
        states = init_all_arms(cache_dir=tmp_cache, journal_dir=tmp_journal)
        assert states["a"]["ranker_used"] == "rsi_only"
        assert states["b"]["ranker_used"] == "composite"
        assert states["c"]["ranker_used"] == "weighted_blend"
        assert states["d"]["ranker_used"] == "reward_risk_first"


# ─────────────────────────────────────────────────────────
# Per-arm isolation
# ─────────────────────────────────────────────────────────


class TestPerArmEquityIsolation:
    def test_per_arm_equity_isolated(self, tmp_cache):
        """Modifying Arm A's equity must not affect B/C/D state files."""
        for aid in VALID_ARM_IDS:
            init_arm_state(aid, base_dir=tmp_cache)
        # Touch Arm A
        sa = load_arm_state("a", base_dir=tmp_cache)
        sa["current_equity"] = 12_500.00
        sa["total_realized_pnl"] = 2500.00
        sa["total_trades"] = 7
        save_arm_state("a", sa, base_dir=tmp_cache)
        # B/C/D still untouched
        for aid in ("b", "c", "d"):
            s = load_arm_state(aid, base_dir=tmp_cache)
            assert s["current_equity"] == DEFAULT_STARTING_EQUITY
            assert s["total_trades"] == 0


class TestPerArmCircuitBreakerIsolation:
    def test_per_arm_circuit_breaker_isolated(self, tmp_cache):
        """Circuit-breaker on Arm A must not flag B/C/D."""
        for aid in VALID_ARM_IDS:
            init_arm_state(aid, base_dir=tmp_cache)
        sa = load_arm_state("a", base_dir=tmp_cache)
        sa["consecutive_losses"] = 3
        sa["circuit_breaker_active"] = True
        sa["circuit_breaker_until"] = (
            datetime.now() + timedelta(hours=48)
        ).isoformat()
        save_arm_state("a", sa, base_dir=tmp_cache)

        for aid in ("b", "c", "d"):
            s = load_arm_state(aid, base_dir=tmp_cache)
            assert s["circuit_breaker_active"] is False
            assert s["consecutive_losses"] == 0
            assert s["circuit_breaker_until"] is None


class TestPerArmSectorCapIsolation:
    def test_per_arm_sector_cap_isolated(self, tmp_cache, tmp_journal):
        """Arm A holds 2 Healthcare positions; arm_open_positions(b) returns
        none — Arm B's sector cap is unaffected."""
        init_all_arms(cache_dir=tmp_cache, journal_dir=tmp_journal)

        # Arm A: 2 healthcare opens (would hit Arm A's sector cap)
        for sym in ("UNH", "JNJ"):
            t = _trade("a", sym, sector="Healthcare", trade_id=f"Ga{sym[0]}01")
            append_arm_trade("a", t, journal_dir=tmp_journal)

        # Arm B: nothing
        a_open = arm_open_positions("a", journal_dir=tmp_journal)
        b_open = arm_open_positions("b", journal_dir=tmp_journal)
        assert len(a_open) == 2
        assert all(t["sector"] == "Healthcare" for t in a_open)
        assert len(b_open) == 0
        # Arm B's "sector cap" check (using its own journal) sees 0
        b_healthcare = [t for t in b_open if t.get("sector") == "Healthcare"]
        assert b_healthcare == []


class TestCompoundingPositionSizing:
    def test_compounding_position_sizing(self, tmp_cache):
        """1% of current_equity scales with realized P&L."""
        state = init_arm_state("a", starting_equity=10_000.0, base_dir=tmp_cache)
        # At $10K: 1% = $100
        assert state["current_equity"] * 0.01 == 100.0

        # After winning streak to $11K: 1% = $110
        state["current_equity"] = 11_000.0
        state["total_realized_pnl"] = 1_000.0
        save_arm_state("a", state, base_dir=tmp_cache)
        loaded = load_arm_state("a", base_dir=tmp_cache)
        assert loaded["current_equity"] * 0.01 == 110.0

        # After losses to $9K: 1% = $90
        loaded["current_equity"] = 9_000.0
        loaded["total_realized_pnl"] = -1_000.0
        save_arm_state("a", loaded, base_dir=tmp_cache)
        again = load_arm_state("a", base_dir=tmp_cache)
        assert again["current_equity"] * 0.01 == 90.0


# ─────────────────────────────────────────────────────────
# Reset (clean restart)
# ─────────────────────────────────────────────────────────


class TestResetArm:
    def test_starting_equity_after_reset(self, tmp_cache, tmp_journal):
        """Clean restart zeroes equity back to $10K and resets all counters."""
        init_arm_state("a", base_dir=tmp_cache)
        # Simulate an active state with trades
        s = load_arm_state("a", base_dir=tmp_cache)
        s["current_equity"] = 10_500.0
        s["total_realized_pnl"] = 500.0
        s["total_trades"] = 14
        s["winning_trades"] = 9
        s["losing_trades"] = 5
        s["consecutive_losses"] = 2
        save_arm_state("a", s, base_dir=tmp_cache)
        # Append a journal entry so reset has something to archive
        append_arm_trade("a", _trade("a", "AAPL", trade_id="Ga001"),
                          journal_dir=tmp_journal)

        # Reset
        archive_dir = tmp_journal / "archive"
        new_state = reset_arm(  # noqa: F841
            "a",
            cache_dir=tmp_cache,
            journal_dir=tmp_journal,
            archive_dir=archive_dir,
        )

        loaded = load_arm_state("a", base_dir=tmp_cache)
        assert loaded["current_equity"] == DEFAULT_STARTING_EQUITY
        assert loaded["total_realized_pnl"] == 0.0
        assert loaded["total_trades"] == 0
        assert loaded["consecutive_losses"] == 0
        assert loaded["circuit_breaker_active"] is False
        # Journal truncated
        assert (tmp_journal / "gamma_arm_a_trades.jsonl").read_text() == ""
        # Archive contains the old data
        archived = list(archive_dir.glob("gamma_arm_a_*"))
        assert len(archived) >= 1


# ─────────────────────────────────────────────────────────
# Reconciliation
# ─────────────────────────────────────────────────────────


class TestReconcile:
    def _state(self, **overrides):
        s = {
            "arm_id": "a",
            "starting_equity": 10_000.0,
            "current_equity": 10_000.0,
            "cash": 10_000.0,
            "total_realized_pnl": 0.0,
        }
        s.update(overrides)
        return s

    def test_reconcile_passes_when_consistent_no_open_trades(self):
        ok, details = reconcile(self._state(), [])
        assert ok is True
        assert details == {}

    def test_reconcile_passes_with_open_trades(self):
        """One $185 open position: cash $9815 + max_risk $185 = $10K equity."""
        state = self._state(cash=9_815.0)
        opens = [_trade("a", "AAPL", max_risk=185.0)]
        ok, details = reconcile(state, opens)
        assert ok is True

    def test_reconcile_passes_within_dollar_tolerance(self):
        """A $0.50 drift is below the $1.00 threshold."""
        state = self._state(cash=9_815.50)  # 50 cents extra
        opens = [_trade("a", "AAPL", max_risk=185.0)]
        ok, details = reconcile(state, opens, threshold=1.00)
        assert ok is True

    def test_reconcile_fails_when_invariant_2_breaks(self):
        """Cash + max_risk doesn't match equity → fail."""
        state = self._state(cash=9_500.0)  # missing $315
        opens = [_trade("a", "AAPL", max_risk=185.0)]
        ok, details = reconcile(state, opens)
        assert ok is False
        inv2 = details["invariant_2_cash_vs_positions"]
        assert inv2["ok"] is False
        assert abs(inv2["delta"] + 315.0) < 0.01

    def test_reconcile_fails_when_invariant_1_breaks(self):
        """Equity != starting + realized → fail."""
        state = self._state(
            current_equity=10_500.0,
            total_realized_pnl=200.0,  # $300 unaccounted
            cash=10_500.0,
        )
        ok, details = reconcile(state, [])
        assert ok is False
        inv1 = details["invariant_1_equity_vs_realized"]
        assert inv1["ok"] is False
        assert abs(inv1["delta"] - 300.0) < 0.01

    def test_reconcile_reports_both_invariants_when_both_fail(self):
        state = self._state(
            current_equity=11_000.0,
            total_realized_pnl=500.0,  # invariant 1: $500 unaccounted
            cash=10_500.0,             # invariant 2: cash short
        )
        opens = [_trade("a", "AAPL", max_risk=185.0)]
        ok, details = reconcile(state, opens)
        assert ok is False
        assert details["invariant_1_equity_vs_realized"]["ok"] is False
        assert details["invariant_2_cash_vs_positions"]["ok"] is False


class TestReconcileAndAlert:
    def test_state_file_reconciliation_alert_fires_discord(self):
        """Per the user spec: drift > $1 triggers Discord post."""
        state = {
            "arm_id": "a",
            "starting_equity": 10_000.0,
            "current_equity": 10_000.0,
            "cash": 9_500.0,  # missing $315
            "total_realized_pnl": 0.0,
        }
        opens = [_trade("a", "AAPL", max_risk=185.0)]
        post_discord = MagicMock()
        ok, details = reconcile_and_alert(
            "a", state, opens, post_discord=post_discord
        )
        assert ok is False
        post_discord.assert_called_once()
        msg = post_discord.call_args[0][0]
        assert "Gamma Arm A reconciliation drift" in msg
        assert "cash + open_max_risk" in msg

    def test_alert_does_not_fire_when_consistent(self):
        """No drift → no Discord call."""
        state = {
            "arm_id": "a",
            "starting_equity": 10_000.0,
            "current_equity": 10_000.0,
            "cash": 10_000.0,
            "total_realized_pnl": 0.0,
        }
        post_discord = MagicMock()
        ok, _ = reconcile_and_alert("a", state, [], post_discord=post_discord)
        assert ok is True
        post_discord.assert_not_called()

    def test_alert_handles_callback_exception(self):
        """If post_discord raises, reconcile_and_alert still returns
        (False, details) without crashing."""
        state = {
            "arm_id": "b",
            "starting_equity": 10_000.0,
            "current_equity": 10_000.0,
            "cash": 9_000.0,  # drift
            "total_realized_pnl": 0.0,
        }
        post_discord = MagicMock(side_effect=ConnectionError("discord down"))
        ok, details = reconcile_and_alert(
            "b", state, [], post_discord=post_discord
        )
        assert ok is False
        post_discord.assert_called_once()


# ─────────────────────────────────────────────────────────
# Trade ID generation (per-arm counters)
# ─────────────────────────────────────────────────────────


class TestTradeIdPrefixPerArm:
    def test_first_trade_id_per_arm(self):
        for aid in VALID_ARM_IDS:
            tid = next_arm_trade_id(aid, journal=[])
            assert tid == f"G{aid}001"

    def test_trade_id_increments_within_arm(self):
        journal_a = [
            _trade("a", "AAPL", trade_id="Ga001"),
            _trade("a", "TMO", trade_id="Ga002"),
            _trade("a", "JNJ", trade_id="Ga003"),
        ]
        assert next_arm_trade_id("a", journal_a) == "Ga004"

    def test_counters_independent_across_arms(self):
        """Mixed journal: Ga has 3 entries, Gb has 1, Gc has 0."""
        journal = [
            _trade("a", "AAPL", trade_id="Ga001"),
            _trade("a", "TMO", trade_id="Ga002"),
            _trade("a", "JNJ", trade_id="Ga003"),
            _trade("b", "MSFT", trade_id="Gb001"),
        ]
        assert next_arm_trade_id("a", journal) == "Ga004"
        assert next_arm_trade_id("b", journal) == "Gb002"
        assert next_arm_trade_id("c", journal) == "Gc001"
        assert next_arm_trade_id("d", journal) == "Gd001"

    def test_legacy_pre_experiment_g001_does_not_collide(self):
        """Pre-experiment trades have id like ``G001`` (no arm letter).
        These must NOT increment the per-arm counter."""
        journal = [
            {"id": "G001", "source": "agent_gamma"},  # legacy
            {"id": "G002", "source": "agent_gamma"},  # legacy
            _trade("a", "AAPL", trade_id="Ga001"),    # new (arm a)
        ]
        # Arm A counter only sees Ga001 → next is Ga002, not Ga003
        assert next_arm_trade_id("a", journal) == "Ga002"
        # Arm B has no per-arm entries → starts at Gb001
        assert next_arm_trade_id("b", journal) == "Gb001"

    def test_trade_id_handles_gaps(self):
        """If Ga001 and Ga003 exist (Ga002 missing), next is Ga004 not Ga002."""
        journal = [
            _trade("a", "AAPL", trade_id="Ga001"),
            _trade("a", "TMO", trade_id="Ga003"),
        ]
        assert next_arm_trade_id("a", journal) == "Ga004"

    def test_trade_id_ignores_malformed_ids(self):
        """Malformed IDs in journal don't crash the counter."""
        journal = [
            _trade("a", "AAPL", trade_id="Ga001"),
            _trade("a", "X", trade_id="Ga_BAD"),   # malformed
            _trade("a", "Y", trade_id="malformed"),  # malformed
        ]
        assert next_arm_trade_id("a", journal) == "Ga002"


# ─────────────────────────────────────────────────────────
# Experiment day computation
# ─────────────────────────────────────────────────────────


class TestExperimentDay:
    def test_zero_when_not_started(self):
        state = {"experiment_started_at": None}
        assert compute_experiment_day(state) == 0

    def test_zero_on_start_day(self):
        # Started just now → day 0
        state = {"experiment_started_at": datetime.now(timezone.utc).isoformat()}
        assert compute_experiment_day(state) == 0

    def test_increments_with_elapsed_days(self):
        # Started 5 days ago → day 5
        five_days_ago = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        state = {"experiment_started_at": five_days_ago}
        assert compute_experiment_day(state) == 5

    def test_handles_naive_timestamp(self):
        """A naive ISO timestamp (no tz) is interpreted as UTC."""
        # Use a naive datetime 3 days ago
        three_days_ago = datetime.utcnow() - timedelta(days=3)
        state = {"experiment_started_at": three_days_ago.isoformat()}
        assert compute_experiment_day(state) == 3

    def test_handles_invalid_timestamp(self):
        state = {"experiment_started_at": "not-a-real-timestamp"}
        assert compute_experiment_day(state) == 0
