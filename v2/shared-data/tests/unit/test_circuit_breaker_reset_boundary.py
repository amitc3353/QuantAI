"""Regression test for the 2026-06-03 circuit-breaker-vs-reset bug.

Bug: consecutive_arm_losses counted ALL CLOSED losing trades for an arm from
the union journal, ignoring experiment resets. After the Gamma incident
recovery reset (fresh $10K baseline), the 8 reconciled incident-loss entries
(closed the same day) tripped the per-arm circuit breaker, blocking all 4 arms
for 48h on artifacts of the duplicate-close cascade — defeating the reset.

Fix: consecutive_arm_losses(journal, arm_id, since_iso=...) and
check_portfolio_gates_for_arm(..., since_iso=...) exclude trades that closed
at/before the experiment-reset boundary (the arm's experiment_started_at).
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SCRIPTS = str(Path(__file__).resolve().parents[2] / "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from gamma.risk_check import consecutive_arm_losses, check_portfolio_gates_for_arm


def _iso_hours_ago(h: float) -> str:
    """ISO-8601 UTC timestamp h hours before now. Keeps breaker close-times
    inside the 48h window regardless of when the test runs (no time-bomb)."""
    return (datetime.now(timezone.utc) - timedelta(hours=h)).isoformat()


def _arm_closed(arm_id, tid, pnl, close_ts, entry_ts="2026-05-20T09:00:00+00:00"):
    # entry_ts defaults to a non-today past date so the per-day entry cap
    # (MAX_DAILY_ENTRIES, keyed on timestamp[:10]) does not fire before the
    # circuit-breaker check we are exercising. close_ts drives the breaker.
    return {
        "id": tid,
        "arm_id": arm_id,
        "source": f"agent_gamma_arm_{arm_id}",
        "status": "CLOSED",
        "pnl": pnl,
        "close_timestamp": close_ts,
        "timestamp": entry_ts,
    }


# Now-relative so the 48h breaker window always holds. RESET is the boundary;
# PRE closed before it, POST after it. All within the last few hours.
RESET = _iso_hours_ago(2)
PRE = _iso_hours_ago(3)    # before reset boundary
POST = _iso_hours_ago(1)   # after reset boundary


# ── consecutive_arm_losses ────────────────────────────────────────────────────

def test_without_since_counts_all_losses_backward_compat():
    """No since_iso → counts every CLOSED loser (unchanged legacy behavior)."""
    journal = [
        _arm_closed("a", "Ga101", -470, PRE),
        _arm_closed("a", "Ga102", -470, PRE),
    ]
    consec, _ = consecutive_arm_losses(journal, "a")
    assert consec == 2


def test_since_excludes_pre_reset_losses():
    """THE INCIDENT CASE: all losses closed before the reset boundary → 0."""
    journal = [
        _arm_closed("a", "Ga101", -470, PRE),
        _arm_closed("a", "Ga102", -470, PRE),
    ]
    consec, _ = consecutive_arm_losses(journal, "a", since_iso=RESET)
    assert consec == 0, "pre-reset losses must not count toward the streak"


def test_since_counts_only_post_reset_losses():
    journal = [
        _arm_closed("a", "Ga101", -470, PRE),   # excluded
        _arm_closed("a", "Ga102", -470, PRE),   # excluded
        _arm_closed("a", "Ga103", -50, POST),   # counts
    ]
    consec, _ = consecutive_arm_losses(journal, "a", since_iso=RESET)
    assert consec == 1


def test_since_boundary_is_exclusive_of_equal_timestamp():
    """A trade closed exactly at the reset boundary belongs to the prior
    experiment and is excluded (strictly-after semantics)."""
    journal = [_arm_closed("a", "Ga101", -470, RESET)]
    consec, _ = consecutive_arm_losses(journal, "a", since_iso=RESET)
    assert consec == 0


def test_since_post_reset_win_breaks_streak():
    journal = [
        _arm_closed("a", "Ga104", -50, _iso_hours_ago(0.3)),   # newest, loss
        _arm_closed("a", "Ga103", +30, _iso_hours_ago(0.6)),   # win breaks streak
        _arm_closed("a", "Ga102", -470, POST),                 # older loss (before win)
    ]
    consec, _ = consecutive_arm_losses(journal, "a", since_iso=RESET)
    assert consec == 1  # only the newest loss before the win


# ── check_portfolio_gates_for_arm ─────────────────────────────────────────────

def test_gate_blocks_without_since_on_pre_reset_losses():
    """Reproduces the bug: 3 pre-reset losses, no since → circuit breaker fires."""
    journal = [
        _arm_closed("a", "Ga101", -470, PRE),
        _arm_closed("a", "Ga102", -470, PRE),
        _arm_closed("a", "Ga103", -470, PRE),
    ]
    ok, why = check_portfolio_gates_for_arm(journal, "a")
    assert ok is False
    assert "circuit breaker" in why


def test_gate_allows_with_since_on_pre_reset_losses():
    """The fix: same 3 pre-reset losses, but since_iso scopes them out → OK."""
    journal = [
        _arm_closed("a", "Ga101", -470, PRE),
        _arm_closed("a", "Ga102", -470, PRE),
        _arm_closed("a", "Ga103", -470, PRE),
    ]
    ok, why = check_portfolio_gates_for_arm(journal, "a", since_iso=RESET)
    assert ok is True, f"fresh reset must not be circuit-broken by pre-reset losses (got: {why})"


def test_gate_still_fires_on_post_reset_loss_streak():
    """The breaker MUST still work for real post-reset losses."""
    journal = [
        _arm_closed("a", "Ga201", -50, _iso_hours_ago(0.9)),
        _arm_closed("a", "Ga202", -50, _iso_hours_ago(0.6)),
        _arm_closed("a", "Ga203", -50, _iso_hours_ago(0.3)),
    ]
    ok, why = check_portfolio_gates_for_arm(journal, "a", since_iso=RESET)
    assert ok is False
    assert "circuit breaker" in why


def test_other_arms_unaffected():
    """since_iso scoping is per-arm; arm b's pre-reset losses don't gate arm a."""
    journal = [
        _arm_closed("b", "Gb101", -470, PRE),
        _arm_closed("b", "Gb102", -470, PRE),
        _arm_closed("b", "Gb103", -470, PRE),
    ]
    ok, why = check_portfolio_gates_for_arm(journal, "a", since_iso=RESET)
    assert ok is True
