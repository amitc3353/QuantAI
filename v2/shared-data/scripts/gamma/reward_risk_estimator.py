"""Pre-rank reward:risk estimator for the Gamma 4-arm A/B/C/D test (added 2026-05-10).

Computes a fast approximation of bull-call-debit-spread reward:risk for each
qualifying setup at scan time. The approximation lets Arm B (composite) and
Arm D (reward_risk_first) use r:r as a pre-rank factor without running the
full ``build_spread()`` per symbol.

**Strike selection** mirrors ``build_spread()`` exactly so the estimator and
the eventual order construction use the same legs:

- Long leg: ``find_by_delta(0.50, tol 0.10)`` → fallback ``nearest_strike(price)``
- Short leg: ``find_by_delta(0.27, tol 0.08)`` → fallback ``nearest_strike(price * 1.025)``
- Expiry: ``find_nearest_expiry`` over the same DTE windows as build_spread

**R:R formula** (matches ``build_spread()`` line 141):

::

    debit = long_mid - short_mid
    width = short_strike - long_strike
    reward_risk = (width - debit) / debit

**Parity check** (pre-flight item 8b): the estimator's r:r must agree with
``build_spread()``'s r:r on a 20-symbol fixture set within a 15% median
absolute difference. The
``test_estimator_vs_build_spread_median_delta`` unit test enforces this; any
regression fails CI before commit 2 lands.

**Why a separate function** (since strike selection mirrors build_spread): the
estimator skips proposal-dict construction, position sizing, journal field
population, and exit-rules wiring. Cleaner separation, easier to reason about
in isolation, and a single number (r:r float or None) is what the rankers
actually need at scan time.

Usage::

    from gamma.reward_risk_estimator import compute_reward_risk_estimates
    setups, duration_sec = compute_reward_risk_estimates(setups, broker)
    # Each setup now has 'reward_risk_estimate' (float) or None on failure.
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from beta._chain_helpers import (
    fill_quote,
    find_by_delta,
    find_nearest_expiry,
    mid,
    nearest_strike,
)

from . import (
    DTE_MAX,
    DTE_MAX_FALLBACK,
    DTE_MIN,
    DTE_MIN_FALLBACK,
    LONG_DELTA_TARGET,
    LONG_DELTA_TOL,
    SHORT_DELTA_TARGET,
    SHORT_DELTA_TOL,
    TARGET_DTE,
    ibkr_symbol,
)

# Parallel workers for the per-symbol estimator pass. yfinance/IBKR option
# chain pulls dominate; 8 workers handle 5–10 setups in ~2s.
ESTIMATOR_WORKERS = 8

# Same structural quality gate as build_spread (line 134) — debit > 55% of
# width is a degenerate spread, return None to mirror build_spread's behavior.
DEBIT_OVER_WIDTH_THRESHOLD = 0.55


def _delta_within(entry: Optional[dict], target: float, tol: float) -> bool:
    """Mirror of strike_selector._delta_within (lines 45-48)."""
    if not entry or entry.get("delta") is None:
        return False
    return abs(float(entry["delta"]) - target) <= tol


def estimate_one(setup: dict, broker) -> Optional[float]:
    """Compute reward:risk estimate for one setup. Returns float or None on
    any failure (chain unavailable, no suitable expiry, missing legs, debit
    too large vs width, etc.).

    Strike selection is intentionally identical to ``build_spread()`` so the
    estimate predicts the eventual proposal's r:r within ~5% in production
    (validated by the parity test, threshold 15%)."""
    try:
        symbol = setup["symbol"]
        price = float(setup["close"])
        broker_symbol = ibkr_symbol(symbol)

        # 1. Fetch chain — same call signature and fallback as build_spread
        try:
            chain = broker.fetch_option_chain(
                broker_symbol,
                dte_range=(DTE_MIN, DTE_MAX),
                strike_range=(price * 0.95, price * 1.05),
                include_quotes=True,
            )
        except Exception as e:
            logging.warning(
                "reward_risk_estimator: chain fetch failed for %s: %s", symbol, e
            )
            return None
        if not chain:
            try:
                chain = broker.fetch_option_chain(
                    broker_symbol,
                    dte_range=(DTE_MIN_FALLBACK, DTE_MAX_FALLBACK),
                    strike_range=(price * 0.95, price * 1.05),
                    include_quotes=True,
                )
            except Exception:
                chain = None
        if not chain:
            return None

        # 2. Pick expiry (same as build_spread)
        expiry = (
            find_nearest_expiry(chain, target_dte=TARGET_DTE,
                                 min_dte=DTE_MIN, max_dte=DTE_MAX)
            or find_nearest_expiry(chain, target_dte=TARGET_DTE,
                                    min_dte=DTE_MIN_FALLBACK, max_dte=DTE_MAX_FALLBACK)
        )
        if not expiry:
            return None

        # 3. Long leg: ATM call (delta-targeted, fallback to nearest-strike)
        long_entry = find_by_delta(chain, LONG_DELTA_TARGET, "C", expiry)
        if not _delta_within(long_entry, LONG_DELTA_TARGET, LONG_DELTA_TOL):
            long_entry = nearest_strike(chain, price, "C", expiry)

        # 4. Short leg: OTM call (delta-targeted, fallback to nearest-strike at +2.5%)
        short_entry = find_by_delta(chain, SHORT_DELTA_TARGET, "C", expiry)
        if not _delta_within(short_entry, SHORT_DELTA_TARGET, SHORT_DELTA_TOL):
            short_entry = nearest_strike(chain, price * 1.025, "C", expiry)

        if not (long_entry and short_entry):
            return None
        if long_entry["strike"] >= short_entry["strike"]:
            return None

        # 5. Populate quotes if needed and compute mids
        fill_quote(long_entry, broker)
        fill_quote(short_entry, broker)
        long_mid = mid(long_entry)
        short_mid = mid(short_entry)
        if long_mid is None or short_mid is None:
            return None

        # 6. Compute reward:risk (same math as build_spread, multiplier cancels out)
        debit = long_mid - short_mid
        if debit <= 0:
            return None
        width = float(short_entry["strike"]) - float(long_entry["strike"])
        if width <= 0:
            return None
        if debit > DEBIT_OVER_WIDTH_THRESHOLD * width:
            return None  # degenerate spread; matches build_spread filter
        max_profit = width - debit
        if max_profit <= 0:
            return None
        return round(max_profit / debit, 2)
    except Exception as e:
        logging.warning(
            "reward_risk_estimator: %s failed: %s", setup.get("symbol"), e
        )
        return None


def compute_reward_risk_estimates(
    setups: list[dict],
    broker,
    n_workers: int = ESTIMATOR_WORKERS,
) -> tuple[list[dict], float]:
    """Parallel pass: attach ``reward_risk_estimate`` to each setup.

    Returns ``(setups, duration_sec)`` where ``duration_sec`` is wall-time of
    the parallel pass. The dashboard tile surfaces this duration alongside
    ``scan_duration_sec`` so the operator can spot parallelization headroom
    issues at high-setup-day load.

    Setups whose estimator fails get ``reward_risk_estimate = None``. Arms B
    and D handle this (they treat None as 0.0 in their score functions, so
    failed estimates rank last).

    Mutates input setups in-place (adds the field) and returns the same list
    for caller convenience.
    """
    if not setups:
        return setups, 0.0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=n_workers) as ex:
        futures = {ex.submit(estimate_one, s, broker): s for s in setups}
        for fut in as_completed(futures):
            setup = futures[fut]
            try:
                setup["reward_risk_estimate"] = fut.result()
            except Exception as e:
                logging.warning(
                    "reward_risk_estimator executor: %s: %s",
                    setup.get("symbol"), e
                )
                setup["reward_risk_estimate"] = None
    duration = time.time() - t0
    return setups, duration
