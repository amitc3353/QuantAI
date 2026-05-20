"""Unit tests for derive_max_loss() and the P&L clamp integration.

Bug context (D1, D3, D4): position_monitor.compute_trade_pnl() returns raw
IBKR per-leg unrealizedPNL sums with no structural cap. For multi-leg
spreads with stale or inconsistent per-leg pricing, this can exceed the
position's structural max loss by 4× or more (-248% observed on Gc001).

Fix (Commit 3): derive_max_loss(trade) returns a per-trade UPPER BOUND on
structural max loss. The main loop clamps raw P&L to that bound before any
exit decision or journal update sees it.

These tests pin:
- Tier 1: max_risk in journal (Beta, Gamma) is canonical
- Tier 2: leg-geometry derives wing × 100 (Alpha iron condors, verticals)
- Tier 3: no bound available — no clamping, None returned

EXPLICIT TRADE-OFF documented in tests 3, 11, 15: the wing-based bound is
COARSER than true max loss because it ignores credit. A $1-wide iron
condor with $0.75 credit has true max loss $25, but the bound is $100.
This is deliberate — sidesteps the D4 majority case (56% of Alpha iron
condors have credit > wing, which would otherwise produce nonsense
geometry). The clamp's job is to stop catastrophic mis-booking, not to
enforce exact max_risk. See test 15.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

# Bootstrap: position_monitor imports broker which has heavy deps.
# Install a fake broker module BEFORE importing position_monitor.
_BROKER_MOD = MagicMock()
_BROKER_MOD.BrokerBase = type("FakeBrokerBase", (), {})
_BROKER_MOD.DRY_RUN_SENTINEL = {"DRY_RUN": True}
sys.modules.setdefault("broker", _BROKER_MOD)

import position_monitor as pm  # noqa: E402


# ── Fixtures ──────────────────────────────────────────────────────────────


def _iron_condor(put_short=100, put_long=99, call_short=110, call_long=111,
                 **overrides) -> dict:
    """Build an iron condor trade with parameterized wing strikes."""
    trade = {
        "id": "ICtest",
        "source": "agent_alpha",
        "symbol": "INTC",
        "strategy": "iron_condor",
        "legs": [
            {"action": "sell", "type": "put", "strike": float(put_short),
             "expiry": "2026-06-15"},
            {"action": "buy", "type": "put", "strike": float(put_long),
             "expiry": "2026-06-15"},
            {"action": "sell", "type": "call", "strike": float(call_short),
             "expiry": "2026-06-15"},
            {"action": "buy", "type": "call", "strike": float(call_long),
             "expiry": "2026-06-15"},
        ],
    }
    trade.update(overrides)
    return trade


def _vertical_spread(short_strike=100, long_strike=95, **overrides) -> dict:
    """Build a 2-leg vertical spread (debit or credit)."""
    trade = {
        "id": "Vtest",
        "source": "agent_alpha",
        "symbol": "INTC",
        "strategy": "bull_put_spread",
        "legs": [
            {"action": "sell", "type": "put", "strike": float(short_strike),
             "expiry": "2026-06-15"},
            {"action": "buy", "type": "put", "strike": float(long_strike),
             "expiry": "2026-06-15"},
        ],
    }
    trade.update(overrides)
    return trade


def _same_strike_diagonal(strike=55, **overrides) -> dict:
    """Build an A026-class same-strike diagonal (no wing width)."""
    trade = {
        "id": "Dtest",
        "source": "agent_alpha",
        "symbol": "OXY",
        "strategy": "diagonal_spread",
        "legs": [
            {"action": "sell", "type": "call", "strike": float(strike),
             "expiry": "2026-05-22"},
            {"action": "buy", "type": "call", "strike": float(strike),
             "expiry": "2026-07-17"},
        ],
    }
    trade.update(overrides)
    return trade


# ── Tier 1: max_risk in journal (Beta, Gamma) ─────────────────────────────


class TestTier1MaxRiskFromJournal:
    """When max_risk is present in the journal, derive_max_loss returns it
    verbatim — the entry agent's risk calculation is canonical."""

    def test_case_4_iron_condor_with_max_risk_clamps_to_max_risk(self):
        """Test 4: Iron condor with max_risk (Beta), P&L exceeds max_risk.
        Expected: Clamp at max_risk regardless of geometry."""
        trade = _iron_condor(max_risk=500.0)
        bound = pm.derive_max_loss(trade)
        assert bound == 500.0
        # Apply clamp: raw -$800 → clamped to -$500
        clamped = max(-800.0, -bound)
        assert clamped == -500.0

    def test_case_5_debit_spread_with_max_risk_clamps_to_max_risk(self):
        """Test 5: Debit spread with max_risk (Gamma Gc001-class), P&L exceeds
        max_risk. Expected: Clamp at max_risk."""
        trade = {
            "id": "Gc001",
            "source": "agent_gamma_arm_c",
            "strategy": "rsi_pullback_debit_spread",
            "max_risk": 55.0,
            "net_debit": 0.55,
            "legs": [
                {"side": "buy", "type": "C", "strike": 53.0, "expiry": "2026-05-29"},
                {"side": "sell", "type": "C", "strike": 54.0, "expiry": "2026-05-29"},
            ],
        }
        bound = pm.derive_max_loss(trade)
        assert bound == 55.0
        # The historic -248% bug: raw -$136.64 → clamped to -$55
        clamped = max(-136.64, -bound)
        assert clamped == -55.0

    def test_case_7_crosscheck_max_risk_exceeds_wing_uses_max_risk(self, capsys):
        """Test 7: max_risk > wing × 100. Expected: uses max_risk, logs discrepancy.

        Tier 1 trusts the journal over geometry — DIRECTION: journal wins
        even when journal value is LARGER than geometry-derived bound. The
        discrepancy is logged via print() (position_monitor's log()) but
        doesn't change the returned value."""
        trade = _iron_condor(put_short=100, put_long=98, call_short=110, call_long=112,
                              max_risk=500.0)  # wing=$2, wing×100=$200 < max_risk=$500
        bound = pm.derive_max_loss(trade)
        assert bound == 500.0  # max_risk wins despite mismatch (journal > geometry)
        # Verify the crosscheck warning was actually emitted
        captured = capsys.readouterr()
        assert "crosscheck" in captured.out
        assert "max_risk=$500.00" in captured.out
        assert "wing×100=$200.00" in captured.out

    def test_case_8_crosscheck_max_risk_within_wing_no_discrepancy(self):
        """Test 8: max_risk ≤ wing × 100. Expected: uses max_risk, no discrepancy."""
        trade = _iron_condor(put_short=100, put_long=98, call_short=110, call_long=112,
                              max_risk=150.0)  # wing=$2, wing×100=$200 > max_risk=$150
        bound = pm.derive_max_loss(trade)
        assert bound == 150.0

    def test_max_risk_zero_falls_to_tier_2(self):
        """max_risk=0 is treated as absent. Falls through to geometry tier."""
        trade = _iron_condor(max_risk=0)
        bound = pm.derive_max_loss(trade)
        # Default wing in _iron_condor: |100-99|=1 put, |110-111|=1 call → wing=1
        assert bound == 100.0  # Tier 2: wing × 100

    def test_max_risk_negative_falls_to_tier_2(self):
        """Negative max_risk treated as invalid. Falls through to geometry."""
        trade = _iron_condor(max_risk=-50.0)
        bound = pm.derive_max_loss(trade)
        assert bound == 100.0  # Tier 2

    def test_max_risk_string_coerces(self):
        """max_risk as string (legacy data) coerces to float."""
        trade = _iron_condor(max_risk="250")
        bound = pm.derive_max_loss(trade)
        assert bound == 250.0


# ── Tier 2: leg geometry (Alpha iron condors, verticals) ──────────────────


class TestTier2WingGeometry:
    """When max_risk is absent, derive_max_loss falls back to wing × 100."""

    def test_case_1_valid_iron_condor_within_wing(self):
        """Test 1: Valid iron condor, P&L within wing_width.
        Expected: bound is wing×100, raw pnl passes through (no clamping needed)."""
        trade = _iron_condor()  # $1 wings, max_risk absent
        bound = pm.derive_max_loss(trade)
        assert bound == 100.0
        # Raw -$80 within bound — clamp doesn't engage
        clamped = max(-80.0, -bound)
        assert clamped == -80.0

    def test_case_2_iron_condor_pnl_exceeds_wing(self):
        """Test 2: Iron condor, P&L exceeds wing × 100. Expected: Clamp."""
        trade = _iron_condor()  # $1 wings
        bound = pm.derive_max_loss(trade)
        assert bound == 100.0
        clamped = max(-250.0, -bound)
        assert clamped == -100.0

    def test_case_3_d4_iron_condor_credit_exceeds_wing(self):
        """Test 3: D4 case — iron condor where credit > wing width.
        Expected: clamp uses wing×100 regardless of credit.

        This is the 56%-majority case for Alpha iron condors. The
        wing-based bound sidesteps D4 entirely because it never inspects
        credit. Whether credit is $1.65 (impossible fill) or $0.65
        (realistic), the bound is identical: wing × 100."""
        # A020-class: $1 wings, credit=$1.65 (impossible)
        trade = _iron_condor(estimated_credit=1.65)
        bound = pm.derive_max_loss(trade)
        assert bound == 100.0  # Wing-based, credit ignored
        clamped = max(-250.0, -bound)
        assert clamped == -100.0

    def test_case_11_asymmetric_wings_uses_max(self):
        """Test 11: Asymmetric iron condor. Put wing $1, call wing $2.50.
        Expected: max(wings) × 100 = $250."""
        trade = _iron_condor(put_short=100, put_long=99,
                              call_short=110, call_long=112.5)
        bound = pm.derive_max_loss(trade)
        assert bound == 250.0  # max(1, 2.5) × 100

    def test_case_12_vertical_spread(self):
        """Test 12: 2-leg vertical spread, different strikes, same expiry.
        Expected: |strike diff| × 100."""
        trade = _vertical_spread(short_strike=100, long_strike=95)  # $5 wide
        bound = pm.derive_max_loss(trade)
        assert bound == 500.0
        clamped = max(-800.0, -bound)
        assert clamped == -500.0

    def test_iron_condor_strike_order_invariant(self):
        """Wing width is symmetric — swapping long/short doesn't change it."""
        trade_a = _iron_condor(put_short=100, put_long=99)
        trade_b = _iron_condor(put_short=99, put_long=100)
        assert pm.derive_max_loss(trade_a) == pm.derive_max_loss(trade_b)


# ── Tier 3: no bound derivable ────────────────────────────────────────────


class TestTier3NoBound:
    """When neither max_risk nor wing geometry is available, derive_max_loss
    returns None. Caller skips clamping and logs the unbounded condition."""

    def test_case_6_same_strike_diagonal_no_bound(self):
        """Test 6: A026-class same-strike diagonal. Expected: None."""
        trade = _same_strike_diagonal()
        bound = pm.derive_max_loss(trade)
        assert bound is None

    def test_case_9_single_leg_no_bound(self):
        """Test 9: Single leg (naked) — no wing concept. Expected: None."""
        trade = {
            "id": "naked",
            "source": "agent_alpha",
            "legs": [{"action": "sell", "type": "call", "strike": 100.0,
                      "expiry": "2026-06-15"}],
        }
        bound = pm.derive_max_loss(trade)
        assert bound is None

    def test_case_10_empty_legs_no_bound(self):
        """Test 10: Missing/empty legs array. Expected: None."""
        trade = {"id": "noleg", "source": "agent_alpha", "legs": []}
        bound = pm.derive_max_loss(trade)
        assert bound is None

    def test_missing_legs_key_no_bound(self):
        """legs key absent entirely → None."""
        trade = {"id": "nolegkey", "source": "agent_alpha"}
        bound = pm.derive_max_loss(trade)
        assert bound is None

    def test_malformed_legs_no_bound(self):
        """Legs missing 'strike' field → None."""
        trade = {
            "id": "malformed",
            "source": "agent_alpha",
            "legs": [{"action": "sell"}, {"action": "buy"}],
        }
        bound = pm.derive_max_loss(trade)
        assert bound is None

    def test_legs_with_string_strikes_uncoercible(self):
        """Non-numeric strike strings → None (graceful, not crash)."""
        trade = {
            "id": "badstrike",
            "source": "agent_alpha",
            "legs": [{"strike": "not-a-number"}, {"strike": "also-bad"}],
        }
        bound = pm.derive_max_loss(trade)
        assert bound is None

    def test_same_strike_diagonal_with_max_risk_uses_tier_1(self):
        """Same-strike diagonal that HAS max_risk in journal still gets a
        clamp via Tier 1. Tier 3 only applies when both max_risk and
        geometry are unavailable."""
        trade = _same_strike_diagonal(max_risk=425.0)
        bound = pm.derive_max_loss(trade)
        assert bound == 425.0


# ── Clamp semantics: floor only, positive P&L untouched ───────────────────


class TestClampSemantics:
    """The clamp is a floor on losses (raw_pnl, -bound). It must NOT cap
    profits or alter zero/positive P&L."""

    def test_case_13_positive_pnl_not_clamped(self):
        """Test 13: Profitable trade. Expected: clamp inert, raw pnl passes."""
        trade = _iron_condor()  # bound = $100
        bound = pm.derive_max_loss(trade)
        # Raw +$50 (profitable). max(+50, -100) = +50 — unchanged.
        clamped = max(50.0, -bound)
        assert clamped == 50.0

    def test_case_14_zero_pnl_not_clamped(self):
        """Test 14: P&L exactly zero. Expected: clamp inert."""
        trade = _iron_condor()
        bound = pm.derive_max_loss(trade)
        clamped = max(0.0, -bound)
        assert clamped == 0.0

    def test_clamp_at_exact_bound_no_change(self):
        """raw_pnl = -bound: clamp returns -bound (no change, same value)."""
        trade = _iron_condor()  # bound = $100
        bound = pm.derive_max_loss(trade)
        clamped = max(-100.0, -bound)
        assert clamped == -100.0


# ── Explicit overstatement trade-off (per operator note 2) ────────────────


class TestExplicitOverstatementTradeOff:
    """These tests document — in executable form — that the wing-based
    bound deliberately overstates max loss for Alpha trades. The clamp
    allows raw P&L losses LARGER than the trade's true max risk when only
    geometry is available. This is a known trade-off, not a bug."""

    def test_case_15_alpha_style_loss_exceeds_true_max_risk_passes_through(self):
        """Test 15: A debit-spread structure WITHOUT max_risk in journal
        (Alpha-style trade). Wing $1, so bound = $100. True max risk for a
        debit spread with $0.55 net_debit would be $55, but we have no
        net_debit field — only legs. Raw P&L of -$80 exceeds the TRUE
        max risk ($55) but is within the wing-based bound ($100) and
        therefore PASSES THROUGH UNCLAMPED.

        This documents that derive_max_loss is an UPPER BOUND, not the
        exact max loss. The clamp prevents catastrophic mis-booking
        (-$300 on a $25-max trade) but does NOT enforce exact max_risk.
        """
        # Gc001-shaped trade but with max_risk field absent (Alpha-style)
        alpha_style_debit_spread = {
            "id": "Atest",
            "source": "agent_alpha",
            "strategy": "debit_spread",
            "legs": [
                {"action": "buy", "type": "call", "strike": 53.0,
                 "expiry": "2026-05-29"},
                {"action": "sell", "type": "call", "strike": 54.0,
                 "expiry": "2026-05-29"},
            ],
            # NOTE: no max_risk, no net_debit, no estimated_credit
        }
        bound = pm.derive_max_loss(alpha_style_debit_spread)
        assert bound == 100.0  # Wing × 100 = $1 × 100

        # True max risk for a $0.55 net_debit on $1 wing would be $55
        # but we don't know net_debit. Raw -$80 is GREATER than true max
        # ($55) but is within the bound ($100) — passes through unclamped.
        raw_pnl = -80.0
        clamped = max(raw_pnl, -bound)
        assert clamped == -80.0, (
            "Clamp must ALLOW -$80 through even though true max risk is "
            "~$55. The wing-based bound overstates max loss by the credit "
            "amount when net_debit/net_credit are not available. This is "
            "intentional — fixes D1 catastrophic booking, does not enforce "
            "exact max_risk."
        )

    def test_iron_condor_with_credit_bound_overstates_by_credit(self):
        """A19-class trade: $1 wing, $0.75 credit, true max loss = $25.
        Bound = $100. Overstatement = 4×. Documents the trade-off."""
        trade = _iron_condor()  # $1 wing
        bound = pm.derive_max_loss(trade)
        true_max_loss_with_075_credit = 25.0  # ($1 - $0.75) × 100
        # Bound overstates true max loss by ~4×
        assert bound > true_max_loss_with_075_credit * 3.9
        assert bound == 100.0


# ── Integration: compute_trade_pnl + derive_max_loss composition ──────────


class TestClampIntegration:
    """Integration test: compose compute_trade_pnl + derive_max_loss + max()
    at the same call-site pattern used in the main loop (line ~1337)."""

    def test_gc001_scenario_integration_tier_1(self):
        """Real-world repro via TIER 1 (max_risk from journal).
        Gc001 trade with max_risk=$55 in journal, raw IBKR-reported P&L of
        -$136.64 (the historic -248% bug). After Commit 3, the clamp uses
        the journal's max_risk=$55 (Tier 1) and bounds booked P&L to -$55.

        Exercises: compute_trade_pnl + derive_max_loss Tier 1 + clamp."""
        gc001 = {
            "id": "Gc001",
            "source": "agent_gamma_arm_c",
            "strategy": "rsi_pullback_debit_spread",
            "symbol": "USB",
            "max_risk": 55.0,
            "net_debit": 0.55,
            "legs": [
                {"side": "buy", "type": "C", "strike": 53.0,
                 "expiry": "2026-05-29", "symbol": "USB260529C00053000"},
                {"side": "sell", "type": "C", "strike": 54.0,
                 "expiry": "2026-05-29", "symbol": "USB260529C00054000"},
            ],
        }
        # Simulate IBKR returning impossible per-leg unrealizedPNL
        alpaca_pos = {
            "USB260529C00053000": {"unrealized_pl": -310.0},  # long leg far below cost
            "USB260529C00054000": {"unrealized_pl": 173.36},  # short leg stale
        }
        # Sum: -310 + 173.36 = -136.64 (the historic value)
        raw_pnl, found = pm.compute_trade_pnl(gc001, alpaca_pos)
        assert found == 2
        assert raw_pnl == pytest.approx(-136.64)

        # Apply Tier 1 clamp
        bound = pm.derive_max_loss(gc001)
        assert bound == 55.0  # Tier 1: max_risk from journal
        clamped = max(raw_pnl, -bound)
        assert clamped == -55.0, (
            "Tier 1 clamp must bound the historic -$136.64 (-248%) to -$55 "
            "using max_risk from journal."
        )

    def test_gc001_scenario_integration_tier_2(self):
        """Same Gc001-shaped trade but with max_risk REMOVED from journal,
        forcing TIER 2 (wing-width geometry). The bound becomes $100
        (wing × 100), overstating the true max loss of $55 by ~1.8×.

        This is the case Alpha trades hit today (no max_risk in journal).
        Raw P&L of -$136.64 still gets clamped — to -$100 instead of -$55.
        Documents that Tier 2 protects against catastrophic mis-booking
        but doesn't enforce true max_risk when net_debit is unavailable."""
        gc001_no_max_risk = {
            "id": "Gc001-T2",
            "source": "agent_gamma_arm_c",  # source unchanged; only max_risk absent
            "strategy": "rsi_pullback_debit_spread",
            "symbol": "USB",
            # NO max_risk, NO net_debit — Alpha-style journal shape
            "legs": [
                {"side": "buy", "type": "C", "strike": 53.0,
                 "expiry": "2026-05-29", "symbol": "USB260529C00053000"},
                {"side": "sell", "type": "C", "strike": 54.0,
                 "expiry": "2026-05-29", "symbol": "USB260529C00054000"},
            ],
        }
        alpaca_pos = {
            "USB260529C00053000": {"unrealized_pl": -310.0},
            "USB260529C00054000": {"unrealized_pl": 173.36},
        }
        raw_pnl, found = pm.compute_trade_pnl(gc001_no_max_risk, alpaca_pos)
        assert found == 2
        assert raw_pnl == pytest.approx(-136.64)

        # Apply Tier 2 clamp — wing width is |54 - 53| = $1, so bound = $100
        bound = pm.derive_max_loss(gc001_no_max_risk)
        assert bound == 100.0, "Tier 2 falls back to wing × 100"
        clamped = max(raw_pnl, -bound)
        assert clamped == -100.0, (
            "Tier 2 clamp bounds -$136.64 to -$100 (wing-based), "
            "overstating true max risk ($55) by ~1.8× — this is the "
            "intentional overstatement for trades without max_risk."
        )
        # Confirm the overstatement is documented in the assertion
        true_max_risk = 55.0  # would be max_risk if it were in journal
        assert abs(clamped) > true_max_risk, (
            "Tier 2 must allow losses > true max_risk when journal lacks "
            "max_risk/net_debit. Documents the wing-vs-true-max gap."
        )

    def test_a026_scenario_integration_no_clamp(self):
        """A026 (same-strike OXY diagonal, no max_risk, no wing) gets no
        clamp. Raw P&L flows through. This is the documented Tier 3 case."""
        a026 = _same_strike_diagonal(id="A026", symbol="OXY")
        alpaca_pos = {
            "OXY260522C00055000": {"unrealized_pl": -200.0},
            "OXY260717C00055000": {"unrealized_pl": 143.20},
        }
        raw_pnl, found = pm.compute_trade_pnl(a026, alpaca_pos)
        # Without proper OCC symbols on legs (fixture doesn't include them),
        # legs may not match — but the clamp test still applies regardless
        bound = pm.derive_max_loss(a026)
        assert bound is None  # Tier 3
        # Per the integration pattern: if bound is None, pnl passes unchanged
        pnl = raw_pnl if bound is None else max(raw_pnl, -bound)
        assert pnl == raw_pnl

    def test_alpha_iron_condor_d4_integration(self):
        """A020-style: Alpha iron condor, $1 wings, no max_risk in journal,
        estimated_credit=$1.65 (D4 impossible fill). Raw P&L of -$250
        clamps to -$100 via Tier 2."""
        a020 = _iron_condor(id="A020", estimated_credit=1.65)
        # Build OCC symbols matching the leg structure
        # (real test would need real symbols; we simulate the raw_pnl directly)
        bound = pm.derive_max_loss(a020)
        assert bound == 100.0  # Tier 2, wing-based
        clamped = max(-250.0, -bound)
        assert clamped == -100.0
