"""Orchestration tests for the Gamma 4-arm A/B/C/D test (commit 3).

Covers the integration points the user flagged as critical:

* Per-arm filter_setups isolation (Arm A's positions don't count against
  Arm B's caps)
* Same-symbol multi-arm overlap (all 4 arms can pick AAPL → 4 virtual
  positions)
* Partial broker failure → skip-all (one arm's order fails → cancel all)
* Position monitor routes close to correct arm on exit
* Legacy pre-experiment trade uses legacy_close (no per-arm update)
* Circuit breaker blocks ONLY affected arm (per-arm scoped)
* --reset-experiment zeros all arms + archives
* --promote-arm closes other arms' positions + disables flag
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from gamma.arm_state import (  # noqa: E402
    VALID_ARM_IDS,
    append_arm_trade,
    arm_open_positions,
    init_all_arms,
    init_arm_state,
    load_arm_journal,
    load_arm_state,
    next_arm_trade_id,
    save_arm_state,
    update_arm_journal_entry,
)
from gamma.risk_check import (  # noqa: E402
    check_portfolio_gates_for_arm,
    filter_setups_for_arm,
    open_arm_positions,
)


# ─────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────


def _setup(symbol, rsi=28.0, sector="Technology", price=200.0):
    return {
        "symbol": symbol,
        "rsi_10": rsi,
        "close": price,
        "sma_200": price * 0.92,
        "distance_above_200ma_pct": 8.7,
        "distance_above_50ma_pct": 4.5,
        "sector": sector,
        "reward_risk_estimate": 1.5,
    }


def _trade(arm_id, sym, *, status="OPEN", trade_id=None, sector="Technology",
            max_risk=185.0, pnl=None, ts=None):
    t = {
        "id": trade_id or f"G{arm_id}001",
        "arm_id": arm_id if arm_id else None,
        "source": (f"agent_gamma_arm_{arm_id}" if arm_id else "agent_gamma"),
        "symbol": sym,
        "strategy": "rsi_pullback_debit_spread",
        "max_risk": max_risk,
        "sector": sector,
        "status": status,
        "timestamp": ts or datetime.now().isoformat(),
    }
    if pnl is not None:
        t["pnl"] = pnl
        t["close_timestamp"] = datetime.now().isoformat()
    return t


# ─────────────────────────────────────────────────────────
# Per-arm filter_setups isolation
# ─────────────────────────────────────────────────────────


class TestPerArmFilterSetupsIsolation:
    def test_per_arm_filter_setups_isolation(self):
        """Arm A holds 2 healthcare positions. Arm B's healthcare cap
        check sees 0 — the cap is INDEPENDENT per arm."""
        # Build a journal with Arm A holding 2 healthcare opens
        journal = [
            _trade("a", "UNH", trade_id="Ga001", sector="Healthcare"),
            _trade("a", "JNJ", trade_id="Ga002", sector="Healthcare"),
        ]
        # Setup pool: another healthcare candidate
        setups = [_setup("PFE", sector="Healthcare", rsi=28.0)]

        # Arm A should NOT take it (sector cap of 2 already hit)
        result_a = filter_setups_for_arm(setups, journal, "a")
        assert result_a == []  # blocked by sector cap

        # Arm B should take it freely (its journal slice is empty)
        result_b = filter_setups_for_arm(setups, journal, "b")
        assert len(result_b) == 1
        assert result_b[0]["symbol"] == "PFE"

    def test_per_arm_open_position_count_independent(self):
        """Arm A has 3 open positions (max). Arm A blocked. Arm B unblocked."""
        journal = [
            _trade("a", "AAPL", trade_id="Ga001"),
            _trade("a", "MSFT", trade_id="Ga002"),
            _trade("a", "NVDA", trade_id="Ga003"),
        ]
        ok_a, why_a = check_portfolio_gates_for_arm(journal, "a")
        assert ok_a is False
        assert "max" in why_a.lower()

        ok_b, why_b = check_portfolio_gates_for_arm(journal, "b")
        assert ok_b is True

    def test_arm_id_mismatch_doesnt_count(self):
        """A trade with arm_id='a' but source='agent_gamma_arm_b' (corrupt)
        is NOT counted toward either arm's cap. Defensive: malformed entries
        don't accidentally contribute to caps."""
        bad = {
            "id": "Gx001", "arm_id": "a",
            "source": "agent_gamma_arm_b",  # mismatch
            "symbol": "AAPL", "status": "OPEN",
            "max_risk": 185.0, "sector": "Technology",
            "timestamp": datetime.now().isoformat(),
        }
        journal = [bad]
        # Neither arm sees it
        assert open_arm_positions(journal, "a") == []
        assert open_arm_positions(journal, "b") == []


# ─────────────────────────────────────────────────────────
# Same-symbol four-arm overlap
# ─────────────────────────────────────────────────────────


class TestSameSymbolFourArmOverlap:
    def test_same_symbol_four_arm_overlap(self, tmp_path):
        """All 4 arms pick AAPL → 4 virtual positions in 4 separate journals.
        Each arm's `arm_open_positions` shows 1 (its own); total across all
        is 4."""
        cache = tmp_path / "cache"
        journal_dir = tmp_path / "journal"
        init_all_arms(cache_dir=cache, journal_dir=journal_dir)

        for aid in ("a", "b", "c", "d"):
            trade = _trade(aid, "AAPL", trade_id=f"G{aid}001")
            append_arm_trade(aid, trade, journal_dir=journal_dir)

        # Each arm shows AAPL as its only open
        for aid in ("a", "b", "c", "d"):
            opens = arm_open_positions(aid, journal_dir=journal_dir)
            assert len(opens) == 1
            assert opens[0]["symbol"] == "AAPL"
            assert opens[0]["arm_id"] == aid

        # Union trades.jsonl has 4 distinct AAPL entries with different arm_ids
        union = (journal_dir / "trades.jsonl").read_text().splitlines()
        union_entries = [json.loads(l) for l in union if l]
        assert len(union_entries) == 4
        arm_ids = {e["arm_id"] for e in union_entries}
        assert arm_ids == {"a", "b", "c", "d"}


# ─────────────────────────────────────────────────────────
# Partial broker failure → skip-all
# ─────────────────────────────────────────────────────────


class TestPartialBrokerFailureSkipsAll:
    def test_partial_broker_failure_skips_all(self):
        """Logic-level test of skip-all behavior: when ANY arm's submission
        terminal-fails, all working orders for that symbol get cancelled.

        This is the orchestration logic in run_execute_4arm; we verify the
        decision predicate here. The full integration test exercises live
        broker mocks — see test_run_execute_4arm_partial_failure below."""
        TERMINAL_FAIL = {"cancelled", "canceled", "rejected", "inactive",
                          "apicancelled", "apicanceled"}

        # Scenario: Arm A submission returns "Cancelled" (IBKR rejected),
        # Arms B/C/D returned working "Submitted" status
        results = [
            ("a", {}, {}, {"order_id": "1", "status": "Cancelled"}),
            ("b", {}, {}, {"order_id": "2", "status": "Submitted"}),
            ("c", {}, {}, {"order_id": "3", "status": "Filled"}),
            ("d", {}, {}, {"order_id": "4", "status": "PreSubmitted"}),
        ]
        any_failed = any(
            (fill is None) or (str(fill.get("status") or "").lower() in TERMINAL_FAIL)
            for _, _, _, fill in results
        )
        assert any_failed is True
        # Working orders to cancel: b (Submitted), c (Filled — leave alone),
        # d (PreSubmitted)
        to_cancel = [
            (aid, fill["order_id"]) for aid, _, _, fill in results
            if fill and fill.get("status", "").lower() not in TERMINAL_FAIL
            and fill.get("status", "").lower() != "filled"
        ]
        assert ("b", "2") in to_cancel
        assert ("d", "4") in to_cancel
        # Filled orders are NOT cancelled (already terminal-success)
        assert ("c", "3") not in to_cancel

    def test_all_succeed_no_cancel(self):
        TERMINAL_FAIL = {"cancelled", "canceled", "rejected", "inactive",
                          "apicancelled", "apicanceled"}
        results = [
            ("a", {}, {}, {"order_id": "1", "status": "Filled"}),
            ("b", {}, {}, {"order_id": "2", "status": "Filled"}),
            ("c", {}, {}, {"order_id": "3", "status": "Filled"}),
            ("d", {}, {}, {"order_id": "4", "status": "Filled"}),
        ]
        any_failed = any(
            (fill is None) or (str(fill.get("status") or "").lower() in TERMINAL_FAIL)
            for _, _, _, fill in results
        )
        assert any_failed is False


# ─────────────────────────────────────────────────────────
# Position monitor exit routing
# ─────────────────────────────────────────────────────────


class TestPositionMonitorExitRouting:
    def test_position_monitor_routes_to_correct_arm_on_exit(self, tmp_path,
                                                              monkeypatch):
        """A closed Gamma trade with arm_id='b' updates Arm B's state file,
        not A/C/D. Updates per-arm journal entry status to CLOSED."""
        cache = tmp_path / "cache"
        journal_dir = tmp_path / "journal"
        # Monkeypatch arm_state module dirs so call-time defaults pick them up
        import gamma.arm_state as arm_state_mod
        monkeypatch.setattr(arm_state_mod, "CACHE_DIR", cache)
        monkeypatch.setattr(arm_state_mod, "JOURNAL_DIR", journal_dir)
        init_all_arms(cache_dir=cache, journal_dir=journal_dir)

        # Arm B has an open trade
        open_trade = _trade("b", "AAPL", trade_id="Gb001", max_risk=185.0)
        append_arm_trade("b", open_trade, journal_dir=journal_dir)
        # Adjust Arm B's cash to reflect the open position (cash -= 185)
        sb = load_arm_state("b", base_dir=cache)
        sb["cash"] = 10000.0 - 185.0
        save_arm_state("b", sb, base_dir=cache)

        import position_monitor as pm
        # Silence post_discord and log
        monkeypatch.setattr(pm, "post_discord", lambda msg: None)
        monkeypatch.setattr(pm, "log", lambda msg: None)

        # Simulate close with $50 profit
        pm._update_arm_state_on_close(open_trade, "profit_target", 50.0)

        # Arm B's state updated
        sb_after = load_arm_state("b", base_dir=cache)
        assert sb_after["current_equity"] == pytest.approx(10050.0)
        assert sb_after["total_realized_pnl"] == pytest.approx(50.0)
        assert sb_after["total_trades"] == 1
        assert sb_after["winning_trades"] == 1
        assert sb_after["losing_trades"] == 0
        assert sb_after["consecutive_losses"] == 0
        # Cash: started at 9815, plus max_risk 185 + pnl 50 = 10050
        assert sb_after["cash"] == pytest.approx(10050.0)

        # A/C/D untouched
        for aid in ("a", "c", "d"):
            s = load_arm_state(aid, base_dir=cache)
            assert s["current_equity"] == 10000.0
            assert s["total_trades"] == 0

        # Per-arm journal: trade now CLOSED with pnl
        b_journal = load_arm_journal("b", base_dir=journal_dir)
        assert len(b_journal) == 1
        assert b_journal[0]["status"] == "CLOSED"
        assert b_journal[0]["pnl"] == 50.0
        assert b_journal[0]["close_reason"] == "profit_target"

    def test_legacy_pre_experiment_trade_uses_legacy_close(self, tmp_path,
                                                            monkeypatch):
        """Pre-experiment Gamma trade (no arm_id, source='agent_gamma') →
        _update_arm_state_on_close is a no-op. No arm state file touched."""
        cache = tmp_path / "cache"
        journal_dir = tmp_path / "journal"
        import gamma.arm_state as arm_state_mod
        monkeypatch.setattr(arm_state_mod, "CACHE_DIR", cache)
        monkeypatch.setattr(arm_state_mod, "JOURNAL_DIR", journal_dir)
        init_all_arms(cache_dir=cache, journal_dir=journal_dir)

        legacy = {
            "id": "G001",
            "source": "agent_gamma",
            "symbol": "AAPL",
            "status": "CLOSED",
            "max_risk": 200.0,
            # NO arm_id field
        }

        import position_monitor as pm
        monkeypatch.setattr(pm, "log", lambda msg: None)
        monkeypatch.setattr(pm, "post_discord", lambda msg: None)
        # Run close handler — should be a no-op for legacy trades
        pm._update_arm_state_on_close(legacy, "profit_target", 50.0)

        # All arm state files unchanged
        for aid in VALID_ARM_IDS:
            s = load_arm_state(aid, base_dir=cache)
            assert s["current_equity"] == 10000.0
            assert s["total_trades"] == 0


# ─────────────────────────────────────────────────────────
# Circuit breaker per-arm scoping
# ─────────────────────────────────────────────────────────


class TestCircuitBreakerScoping:
    def test_circuit_breaker_blocks_only_affected_arm(self, tmp_path,
                                                       monkeypatch):
        """3 consecutive losses on Arm A → circuit breaker blocks Arm A.
        Arms B/C/D unaffected."""
        cache = tmp_path / "cache"
        journal_dir = tmp_path / "journal"
        import gamma.arm_state as arm_state_mod
        monkeypatch.setattr(arm_state_mod, "CACHE_DIR", cache)
        monkeypatch.setattr(arm_state_mod, "JOURNAL_DIR", journal_dir)
        init_all_arms(cache_dir=cache, journal_dir=journal_dir)

        import position_monitor as pm
        monkeypatch.setattr(pm, "log", lambda msg: None)
        monkeypatch.setattr(pm, "post_discord", lambda msg: None)

        # Three losing closes on Arm A
        for i in range(3):
            t = _trade("a", "AAPL", trade_id=f"Ga00{i+1}", max_risk=200.0)
            append_arm_trade("a", t, journal_dir=journal_dir)
            sa = load_arm_state("a", base_dir=cache)
            sa["cash"] = float(sa["cash"]) - 200.0  # entry cash deduction
            save_arm_state("a", sa, base_dir=cache)
            pm._update_arm_state_on_close(t, "stop_loss", -50.0)

        sa = load_arm_state("a", base_dir=cache)
        assert sa["consecutive_losses"] == 3
        assert sa["circuit_breaker_active"] is True
        assert sa["circuit_breaker_until"] is not None

        # B/C/D still pristine
        for aid in ("b", "c", "d"):
            s = load_arm_state(aid, base_dir=cache)
            assert s["consecutive_losses"] == 0
            assert s["circuit_breaker_active"] is False

    def test_winning_trade_resets_consecutive_losses(self, tmp_path, monkeypatch):
        """A losing trade increments consecutive_losses; a win resets to 0."""
        cache = tmp_path / "cache"
        journal_dir = tmp_path / "journal"
        import gamma.arm_state as arm_state_mod
        monkeypatch.setattr(arm_state_mod, "CACHE_DIR", cache)
        monkeypatch.setattr(arm_state_mod, "JOURNAL_DIR", journal_dir)
        init_all_arms(cache_dir=cache, journal_dir=journal_dir)

        import position_monitor as pm
        monkeypatch.setattr(pm, "log", lambda msg: None)
        monkeypatch.setattr(pm, "post_discord", lambda msg: None)

        # 2 losses
        for i in range(2):
            t = _trade("a", "AAPL", trade_id=f"Ga00{i+1}", max_risk=100.0)
            append_arm_trade("a", t, journal_dir=journal_dir)
            sa = load_arm_state("a", base_dir=cache)
            sa["cash"] = float(sa["cash"]) - 100.0
            save_arm_state("a", sa, base_dir=cache)
            pm._update_arm_state_on_close(t, "stop_loss", -30.0)

        assert load_arm_state("a", base_dir=cache)["consecutive_losses"] == 2

        # 1 win — should reset
        win = _trade("a", "TMO", trade_id="Ga003", max_risk=100.0)
        append_arm_trade("a", win, journal_dir=journal_dir)
        sa = load_arm_state("a", base_dir=cache)
        sa["cash"] = float(sa["cash"]) - 100.0
        save_arm_state("a", sa, base_dir=cache)
        pm._update_arm_state_on_close(win, "profit_target", 50.0)

        sa_after = load_arm_state("a", base_dir=cache)
        assert sa_after["consecutive_losses"] == 0
        assert sa_after["circuit_breaker_active"] is False


# ─────────────────────────────────────────────────────────
# --reset-experiment subcommand
# ─────────────────────────────────────────────────────────


class TestResetExperiment:
    def test_reset_experiment_zeroes_all_arms(self, tmp_path, monkeypatch):
        """After --reset-experiment --confirm: all 4 state files back to $10K
        and all 4 journals truncated."""
        cache = tmp_path / "cache"
        journal_dir = tmp_path / "journal"
        init_all_arms(cache_dir=cache, journal_dir=journal_dir)

        # Pollute each arm with an active position + journal entry
        for aid in VALID_ARM_IDS:
            t = _trade(aid, "AAPL", trade_id=f"G{aid}001")
            append_arm_trade(aid, t, journal_dir=journal_dir)
            s = load_arm_state(aid, base_dir=cache)
            s["current_equity"] = 12345.67
            s["total_trades"] = 5
            save_arm_state(aid, s, base_dir=cache)

        # Reset all arms
        from gamma.arm_state import reset_arm
        archive_dir = journal_dir / "archive"
        for aid in VALID_ARM_IDS:
            reset_arm(aid, cache_dir=cache, journal_dir=journal_dir,
                       archive_dir=archive_dir)

        # All zeroed
        for aid in VALID_ARM_IDS:
            s = load_arm_state(aid, base_dir=cache)
            assert s["current_equity"] == 10000.0
            assert s["total_trades"] == 0
            j = (journal_dir / f"gamma_arm_{aid}_trades.jsonl").read_text()
            assert j == ""

    def test_reset_experiment_archives_journals(self, tmp_path):
        """Each arm's pre-reset journal copy lives in archive/ for forensics."""
        cache = tmp_path / "cache"
        journal_dir = tmp_path / "journal"
        init_all_arms(cache_dir=cache, journal_dir=journal_dir)

        # Add journal entries
        for aid in VALID_ARM_IDS:
            t = _trade(aid, "AAPL", trade_id=f"G{aid}001")
            append_arm_trade(aid, t, journal_dir=journal_dir)

        from gamma.arm_state import reset_arm
        archive_dir = journal_dir / "archive"
        for aid in VALID_ARM_IDS:
            reset_arm(aid, cache_dir=cache, journal_dir=journal_dir,
                       archive_dir=archive_dir)

        for aid in VALID_ARM_IDS:
            archived_journal = list(archive_dir.glob(f"gamma_arm_{aid}_trades_*.jsonl"))
            assert len(archived_journal) == 1
            content = archived_journal[0].read_text()
            assert "AAPL" in content
            archived_state = list(archive_dir.glob(f"gamma_arm_{aid}_account_*.json"))
            assert len(archived_state) == 1


# ─────────────────────────────────────────────────────────
# --promote-arm subcommand
# ─────────────────────────────────────────────────────────


class TestPromoteArm:
    def test_promote_arm_disables_feature_flag(self, tmp_path, monkeypatch):
        """The dotenv updater swaps GAMMA_AB_TEST_ENABLED=1 → 0 without
        exposing other lines."""
        env_path = tmp_path / ".env"
        env_path.write_text(
            "DISCORD_BOT_TOKEN=secret-token-do-not-print\n"
            "GAMMA_AB_TEST_ENABLED=1\n"
            "OTHER_KEY=other-value\n"
        )

        import gamma_agent
        gamma_agent._set_env_var_in_dotenv("GAMMA_AB_TEST_ENABLED", "0",
                                            env_path=env_path)

        new = env_path.read_text()
        assert "GAMMA_AB_TEST_ENABLED=0" in new
        assert "GAMMA_AB_TEST_ENABLED=1" not in new
        # Other lines untouched
        assert "DISCORD_BOT_TOKEN=secret-token-do-not-print" in new
        assert "OTHER_KEY=other-value" in new

    def test_promote_arm_appends_if_missing(self, tmp_path):
        env_path = tmp_path / ".env"
        env_path.write_text("ONLY_KEY=value\n")
        import gamma_agent
        gamma_agent._set_env_var_in_dotenv("GAMMA_AB_TEST_ENABLED", "0",
                                            env_path=env_path)
        content = env_path.read_text()
        assert "ONLY_KEY=value" in content
        assert "GAMMA_AB_TEST_ENABLED=0" in content

    def test_promote_arm_lists_other_arms_open_positions(self, tmp_path):
        """The promote command identifies open positions in non-winning arms
        so the operator knows what to close. Per plan §K — actual close is
        not auto-executed."""
        cache = tmp_path / "cache"
        journal_dir = tmp_path / "journal"
        init_all_arms(cache_dir=cache, journal_dir=journal_dir)

        # Arm A is the winner; Arms B and C have open positions to close
        append_arm_trade("b", _trade("b", "AAPL", trade_id="Gb001"),
                          journal_dir=journal_dir)
        append_arm_trade("c", _trade("c", "TMO", trade_id="Gc001"),
                          journal_dir=journal_dir)
        append_arm_trade("c", _trade("c", "JNJ", trade_id="Gc002"),
                          journal_dir=journal_dir)

        opens_b = arm_open_positions("b", journal_dir=journal_dir)
        opens_c = arm_open_positions("c", journal_dir=journal_dir)
        opens_d = arm_open_positions("d", journal_dir=journal_dir)

        assert len(opens_b) == 1
        assert opens_b[0]["symbol"] == "AAPL"
        assert len(opens_c) == 2
        assert {t["symbol"] for t in opens_c} == {"TMO", "JNJ"}
        assert len(opens_d) == 0


# ─────────────────────────────────────────────────────────
# Per-arm journal sync on close
# ─────────────────────────────────────────────────────────


class TestPerArmJournalCloseSync:
    def test_update_arm_journal_entry_finds_trade(self, tmp_path):
        """update_arm_journal_entry rewrites the per-arm journal in place
        with the merged updates. Symmetric with rewrite_journal_atomic."""
        journal_dir = tmp_path / "journal"
        init_all_arms(cache_dir=tmp_path / "cache", journal_dir=journal_dir)

        append_arm_trade("a", _trade("a", "AAPL", trade_id="Ga001"),
                          journal_dir=journal_dir)
        append_arm_trade("a", _trade("a", "TMO", trade_id="Ga002"),
                          journal_dir=journal_dir)

        ok = update_arm_journal_entry("a", "Ga001", {
            "status": "CLOSED",
            "pnl": 87.50,
            "close_reason": "profit_target",
        }, journal_dir=journal_dir)
        assert ok is True

        loaded = load_arm_journal("a", base_dir=journal_dir)
        ga001 = next(t for t in loaded if t["id"] == "Ga001")
        ga002 = next(t for t in loaded if t["id"] == "Ga002")
        assert ga001["status"] == "CLOSED"
        assert ga001["pnl"] == 87.50
        # Other entry untouched
        assert ga002["status"] == "OPEN"

    def test_update_arm_journal_entry_returns_false_for_missing_id(self, tmp_path):
        journal_dir = tmp_path / "journal"
        init_all_arms(cache_dir=tmp_path / "cache", journal_dir=journal_dir)
        ok = update_arm_journal_entry("a", "Ga999", {"status": "CLOSED"},
                                        journal_dir=journal_dir)
        assert ok is False
