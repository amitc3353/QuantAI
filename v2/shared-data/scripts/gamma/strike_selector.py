"""Bull-call debit-spread builder for Agent Gamma.

For a qualifying setup (price > 200 SMA, RSI(10) < 30), constructs a
14-21 DTE bull call spread with:
  - Long leg: ATM call (delta ~0.50)
  - Short leg: OTM call (delta ~0.27)
  - Position-sized so total risk ≤ 1% of equity

Reuses beta/_chain_helpers for chain queries — same shapes, same
broker contract, no need to reinvent.
"""
from __future__ import annotations

import logging
from typing import Optional

from beta._chain_helpers import (
    fill_quote,
    find_by_delta,
    find_nearest_expiry,
    leg,
    mid,
    nearest_strike,
)

from . import (
    DTE_MAX,
    DTE_MAX_FALLBACK,
    DTE_MIN,
    DTE_MIN_FALLBACK,
    HOLD_PERIOD_MAX_DAYS,
    INSTRUMENT_CONFIG,
    LONG_DELTA_TARGET,
    LONG_DELTA_TOL,
    MAX_RISK_PER_TRADE,
    MIN_REWARD_RISK,
    RSI_EXIT_THRESHOLD,
    SHORT_DELTA_TARGET,
    SHORT_DELTA_TOL,
    TARGET_DTE,
    ibkr_symbol,
)


def _delta_within(entry: dict | None, target: float, tol: float) -> bool:
    if not entry or entry.get("delta") is None:
        return False
    return abs(float(entry["delta"]) - target) <= tol


def build_spread(setup: dict, broker, account_equity: float,
                 today_iso: str) -> Optional[dict]:
    """Construct a bull call debit spread proposal for `setup`.

    Returns a trade dict ready for `broker.place_mleg_order` / journal,
    or None if no clean fit (chain unavailable, debit > 55% of width,
    reward:risk too poor, etc.). Caller is responsible for applying
    risk gates BEFORE calling this function.
    """
    symbol = setup["symbol"]
    cfg = INSTRUMENT_CONFIG[symbol]
    price = float(setup["close"])
    # Translate to IBKR's expected symbol form (e.g. "BRK.B" → "BRK B").
    # Same value for most symbols; only a few need the swap.
    broker_symbol = ibkr_symbol(symbol)

    # 1. Fetch chain — strikes ±5% of spot, target window 14-21 DTE
    try:
        chain = broker.fetch_option_chain(
            broker_symbol,
            dte_range=(DTE_MIN, DTE_MAX),
            strike_range=(price * 0.95, price * 1.05),
            include_quotes=True,
        )
    except Exception as e:
        logging.warning("gamma chain fetch failed for %s: %s", symbol, e)
        return None
    if not chain:
        # Widen window once before giving up
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
        logging.info("gamma %s: empty chain — skip", symbol)
        return None

    # 2. Pick expiry as close to TARGET_DTE as we can
    expiry = (
        find_nearest_expiry(chain, target_dte=TARGET_DTE, min_dte=DTE_MIN, max_dte=DTE_MAX)
        or find_nearest_expiry(chain, target_dte=TARGET_DTE, min_dte=DTE_MIN_FALLBACK, max_dte=DTE_MAX_FALLBACK)
    )
    if not expiry:
        logging.info("gamma %s: no suitable expiry in 14-21 DTE", symbol)
        return None

    # 3. Long leg — ATM call. Try delta-targeted first, then nearest-strike fallback.
    long_entry = find_by_delta(chain, LONG_DELTA_TARGET, "C", expiry)
    if not _delta_within(long_entry, LONG_DELTA_TARGET, LONG_DELTA_TOL):
        long_entry = nearest_strike(chain, price, "C", expiry)

    # 4. Short leg — OTM call (~0.27 delta or ~2.5% above spot)
    short_entry = find_by_delta(chain, SHORT_DELTA_TARGET, "C", expiry)
    if not _delta_within(short_entry, SHORT_DELTA_TARGET, SHORT_DELTA_TOL):
        short_entry = nearest_strike(chain, price * 1.025, "C", expiry)

    if not (long_entry and short_entry):
        return None
    if long_entry["strike"] >= short_entry["strike"]:
        return None  # invalid — long must be lower strike than short

    # Make sure both legs have populated quotes
    fill_quote(long_entry, broker)
    fill_quote(short_entry, broker)

    long_mid = mid(long_entry)
    short_mid = mid(short_entry)
    if long_mid is None or short_mid is None:
        logging.info("gamma %s: missing mid for one leg", symbol)
        return None

    debit = round(long_mid - short_mid, 2)
    if debit <= 0:
        return None  # would be a credit spread — wrong structure

    width = float(short_entry["strike"]) - float(long_entry["strike"])
    if width <= 0:
        return None
    if debit > 0.55 * width:
        logging.info("gamma %s: debit %.2f > 0.55 * width %.2f", symbol, debit, width)
        return None  # spread too expensive

    multiplier = cfg["multiplier"]
    risk_per_contract = debit * multiplier  # $ per contract
    max_profit_per_contract = (width - debit) * multiplier
    reward_risk = max_profit_per_contract / risk_per_contract if risk_per_contract > 0 else 0

    if reward_risk < MIN_REWARD_RISK:
        logging.info("gamma %s: reward:risk %.2f < %.2f", symbol, reward_risk, MIN_REWARD_RISK)
        return None

    # 5. Position sizing — risk ≤ 1% of equity
    risk_budget = account_equity * MAX_RISK_PER_TRADE
    qty = max(1, int(risk_budget // risk_per_contract))
    total_risk = qty * risk_per_contract

    long_delta = float(long_entry.get("delta") or LONG_DELTA_TARGET)
    short_delta = float(short_entry.get("delta") or SHORT_DELTA_TARGET)
    long_vega = float(long_entry.get("vega") or 0.10)
    short_vega = float(short_entry.get("vega") or 0.07)

    proposal = {
        "source": "agent_gamma",
        "strategy": "rsi_pullback_debit_spread",
        "instrument": symbol,
        "symbol": symbol,
        "instrument_type": cfg["type"],
        "tax_treatment": cfg["tax"],
        "sector": cfg.get("sector", "unknown"),
        "direction": "long",
        "legs": [
            leg("buy", long_entry, 1),
            leg("sell", short_entry, 1),
        ],
        "underlying_price": price,
        "expiry": expiry,
        "net_debit": debit,
        "spread_width": round(width, 2),
        "max_risk": round(risk_per_contract, 2),
        "max_profit": round(max_profit_per_contract, 2),
        "reward_risk": round(reward_risk, 2),
        "qty": qty,
        "total_risk": round(total_risk, 2),
        "total_risk_pct": round(total_risk / account_equity, 4) if account_equity > 0 else None,
        "net_delta": round(long_delta - short_delta, 3),
        "net_vega": round(long_vega - short_vega, 3),
        "rsi_at_entry": setup["rsi_10"],
        "price_at_entry": setup["close"],
        "sma_200_at_entry": setup["sma_200"],
        "distance_above_200ma_pct": setup["distance_above_200ma_pct"],
        "exit_rules": {
            "rsi_exit_threshold": RSI_EXIT_THRESHOLD,
            "rsi_period": 10,
            "time_stop_days": HOLD_PERIOD_MAX_DAYS,
            "trend_break_ma": 200,
            "stop_loss_pct": -50,
            "take_profit_pct": 150,
            "entry_date": today_iso,
        },
    }
    return proposal
