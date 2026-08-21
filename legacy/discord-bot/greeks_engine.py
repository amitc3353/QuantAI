"""
Greeks Engine — Local Options Pricing & Greeks
================================================
Wraps py_vollib for Black-Scholes-Merton option pricing.
Zero API cost. Sub-millisecond per computation.

Provides:
  - Option price (call/put)
  - All Greeks: delta, gamma, theta, vega, rho
  - Implied volatility from price
  - Spread analysis: max profit, max loss, breakeven, P(profit)
  - Trade card generation for strategies
"""

import math
import logging
from dataclasses import dataclass, asdict
from typing import Optional

from py_vollib.black_scholes import black_scholes as bs_price
from py_vollib.black_scholes.greeks.analytical import (
    delta as bs_delta,
    gamma as bs_gamma,
    theta as bs_theta,
    vega as bs_vega,
    rho as bs_rho,
)
from py_vollib.black_scholes.implied_volatility import implied_volatility as bs_iv

log = logging.getLogger("greeks-engine")

# Risk-free rate (approximate, updated periodically)
DEFAULT_RISK_FREE_RATE = 0.043  # ~4.3% as of March 2026


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class OptionGreeks:
    """Complete Greeks snapshot for a single option."""
    delta: float
    gamma: float
    theta: float  # per day
    vega: float
    rho: float


@dataclass
class OptionPricing:
    """Full pricing for a single option contract."""
    symbol: str
    strike: float
    expiry_dte: int
    option_type: str  # call | put
    underlying_price: float
    iv: float
    theoretical_price: float
    greeks: OptionGreeks
    intrinsic_value: float
    extrinsic_value: float
    moneyness: str  # ITM | ATM | OTM


@dataclass
class SpreadAnalysis:
    """Analysis for an options spread strategy."""
    strategy: str
    symbol: str
    legs: list[dict]
    net_credit: float  # positive = credit received
    net_debit: float  # positive = debit paid
    max_profit: float
    max_loss: float
    breakeven: float | list[float]
    pop: float  # probability of profit (estimate)
    risk_reward_ratio: float
    net_delta: float
    net_gamma: float
    net_theta: float  # per day
    net_vega: float
    annualized_return: float  # annualized % return on risk


# ---------------------------------------------------------------------------
# Core pricing functions
# ---------------------------------------------------------------------------
def price_option(
    flag: str,  # 'c' or 'p'
    S: float,   # underlying price
    K: float,   # strike price
    t: float,   # time to expiry in years
    r: float = DEFAULT_RISK_FREE_RATE,
    sigma: float = 0.20,  # implied volatility
) -> float:
    """Calculate Black-Scholes option price."""
    if t <= 0:
        # At expiry
        if flag == 'c':
            return max(S - K, 0)
        return max(K - S, 0)
    try:
        return bs_price(flag, S, K, t, r, sigma)
    except Exception as e:
        log.error(f"Price calc error: {e}")
        return 0.0


def compute_greeks(
    flag: str,
    S: float,
    K: float,
    t: float,
    r: float = DEFAULT_RISK_FREE_RATE,
    sigma: float = 0.20,
) -> OptionGreeks:
    """Compute all Greeks for an option."""
    if t <= 0.001:
        t = 0.001  # Avoid division by zero
    try:
        return OptionGreeks(
            delta=round(bs_delta(flag, S, K, t, r, sigma), 4),
            gamma=round(bs_gamma(flag, S, K, t, r, sigma), 6),
            theta=round(bs_theta(flag, S, K, t, r, sigma), 4),  # per day
            vega=round(bs_vega(flag, S, K, t, r, sigma), 4),
            rho=round(bs_rho(flag, S, K, t, r, sigma), 4),
        )
    except Exception as e:
        log.error(f"Greeks calc error: {e}")
        return OptionGreeks(0, 0, 0, 0, 0)


def compute_iv(
    price: float,
    flag: str,
    S: float,
    K: float,
    t: float,
    r: float = DEFAULT_RISK_FREE_RATE,
) -> float:
    """Compute implied volatility from option price."""
    if t <= 0.001 or price <= 0:
        return 0.0
    try:
        return round(bs_iv(price, S, K, t, r, flag), 4)
    except Exception as e:
        log.warning(f"IV calc failed (may be deep ITM/OTM): {e}")
        return 0.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def dte_to_years(dte: int) -> float:
    """Convert days-to-expiry to annualized time."""
    return max(dte / 365.0, 0.001)


def classify_moneyness(flag: str, S: float, K: float) -> str:
    """Classify option as ITM, ATM, or OTM."""
    pct_diff = abs(S - K) / S
    if pct_diff < 0.01:
        return "ATM"
    if flag == 'c':
        return "ITM" if S > K else "OTM"
    else:
        return "ITM" if S < K else "OTM"


def estimate_pop_from_delta(delta: float) -> float:
    """
    Rough probability of profit estimate from delta.
    For credit spreads, POP ≈ 1 - |delta of short strike|.
    This is an approximation.
    """
    return round(1 - abs(delta), 2)


# ---------------------------------------------------------------------------
# Full option analysis
# ---------------------------------------------------------------------------
def analyze_option(
    symbol: str,
    strike: float,
    dte: int,
    option_type: str,  # "call" or "put"
    underlying_price: float,
    iv: float,
    r: float = DEFAULT_RISK_FREE_RATE,
) -> OptionPricing:
    """Full analysis of a single option contract."""
    flag = 'c' if option_type.lower() == 'call' else 'p'
    t = dte_to_years(dte)

    theo_price = price_option(flag, underlying_price, strike, t, r, iv)
    greeks = compute_greeks(flag, underlying_price, strike, t, r, iv)

    if flag == 'c':
        intrinsic = max(underlying_price - strike, 0)
    else:
        intrinsic = max(strike - underlying_price, 0)
    extrinsic = max(theo_price - intrinsic, 0)

    return OptionPricing(
        symbol=symbol,
        strike=strike,
        expiry_dte=dte,
        option_type=option_type,
        underlying_price=underlying_price,
        iv=round(iv, 4),
        theoretical_price=round(theo_price, 2),
        greeks=greeks,
        intrinsic_value=round(intrinsic, 2),
        extrinsic_value=round(extrinsic, 2),
        moneyness=classify_moneyness(flag, underlying_price, strike),
    )


# ---------------------------------------------------------------------------
# Spread strategies
# ---------------------------------------------------------------------------
def analyze_bull_put_spread(
    symbol: str,
    underlying_price: float,
    short_strike: float,
    long_strike: float,
    dte: int,
    short_iv: float,
    long_iv: float,
    r: float = DEFAULT_RISK_FREE_RATE,
) -> SpreadAnalysis:
    """Analyze a bull put spread (sell higher put, buy lower put)."""
    t = dte_to_years(dte)

    short_price = price_option('p', underlying_price, short_strike, t, r, short_iv)
    long_price = price_option('p', underlying_price, long_strike, t, r, long_iv)
    short_greeks = compute_greeks('p', underlying_price, short_strike, t, r, short_iv)
    long_greeks = compute_greeks('p', underlying_price, long_strike, t, r, long_iv)

    net_credit = round(short_price - long_price, 2)
    width = short_strike - long_strike
    max_loss = round(width - net_credit, 2)
    max_profit = net_credit
    breakeven = round(short_strike - net_credit, 2)
    pop = estimate_pop_from_delta(short_greeks.delta)
    rr_ratio = round(max_loss / max_profit, 2) if max_profit > 0 else 999
    ann_return = round((max_profit / max_loss) * (365 / dte) * 100, 1) if max_loss > 0 else 0

    return SpreadAnalysis(
        strategy="bull_put_spread",
        symbol=symbol,
        legs=[
            {"action": "sell", "type": "put", "strike": short_strike, "price": round(short_price, 2),
             "delta": short_greeks.delta, "iv": round(short_iv, 4)},
            {"action": "buy", "type": "put", "strike": long_strike, "price": round(long_price, 2),
             "delta": long_greeks.delta, "iv": round(long_iv, 4)},
        ],
        net_credit=net_credit,
        net_debit=0,
        max_profit=max_profit,
        max_loss=max_loss,
        breakeven=breakeven,
        pop=pop,
        risk_reward_ratio=rr_ratio,
        net_delta=round(short_greeks.delta + long_greeks.delta, 4),
        net_gamma=round(short_greeks.gamma + long_greeks.gamma, 6),
        net_theta=round(short_greeks.theta + long_greeks.theta, 4),
        net_vega=round(short_greeks.vega + long_greeks.vega, 4),
        annualized_return=ann_return,
    )


def analyze_iron_condor(
    symbol: str,
    underlying_price: float,
    put_short: float,
    put_long: float,
    call_short: float,
    call_long: float,
    dte: int,
    put_short_iv: float,
    put_long_iv: float,
    call_short_iv: float,
    call_long_iv: float,
    r: float = DEFAULT_RISK_FREE_RATE,
) -> SpreadAnalysis:
    """Analyze an iron condor (bull put spread + bear call spread)."""
    t = dte_to_years(dte)

    ps_price = price_option('p', underlying_price, put_short, t, r, put_short_iv)
    pl_price = price_option('p', underlying_price, put_long, t, r, put_long_iv)
    cs_price = price_option('c', underlying_price, call_short, t, r, call_short_iv)
    cl_price = price_option('c', underlying_price, call_long, t, r, call_long_iv)

    ps_g = compute_greeks('p', underlying_price, put_short, t, r, put_short_iv)
    pl_g = compute_greeks('p', underlying_price, put_long, t, r, put_long_iv)
    cs_g = compute_greeks('c', underlying_price, call_short, t, r, call_short_iv)
    cl_g = compute_greeks('c', underlying_price, call_long, t, r, call_long_iv)

    put_credit = ps_price - pl_price
    call_credit = cs_price - cl_price
    net_credit = round(put_credit + call_credit, 2)

    put_width = put_short - put_long
    call_width = call_long - call_short
    max_width = max(put_width, call_width)
    max_loss = round(max_width - net_credit, 2)
    max_profit = net_credit

    be_lower = round(put_short - net_credit, 2)
    be_upper = round(call_short + net_credit, 2)

    pop = round(1 - abs(ps_g.delta) - abs(cs_g.delta), 2)
    rr_ratio = round(max_loss / max_profit, 2) if max_profit > 0 else 999
    ann_return = round((max_profit / max_loss) * (365 / dte) * 100, 1) if max_loss > 0 else 0

    return SpreadAnalysis(
        strategy="iron_condor",
        symbol=symbol,
        legs=[
            {"action": "sell", "type": "put", "strike": put_short, "price": round(ps_price, 2), "delta": ps_g.delta},
            {"action": "buy", "type": "put", "strike": put_long, "price": round(pl_price, 2), "delta": pl_g.delta},
            {"action": "sell", "type": "call", "strike": call_short, "price": round(cs_price, 2), "delta": cs_g.delta},
            {"action": "buy", "type": "call", "strike": call_long, "price": round(cl_price, 2), "delta": cl_g.delta},
        ],
        net_credit=net_credit,
        net_debit=0,
        max_profit=max_profit,
        max_loss=max_loss,
        breakeven=[be_lower, be_upper],
        pop=pop,
        risk_reward_ratio=rr_ratio,
        net_delta=round(ps_g.delta + pl_g.delta + cs_g.delta + cl_g.delta, 4),
        net_gamma=round(ps_g.gamma + pl_g.gamma + cs_g.gamma + cl_g.gamma, 6),
        net_theta=round(ps_g.theta + pl_g.theta + cs_g.theta + cl_g.theta, 4),
        net_vega=round(ps_g.vega + pl_g.vega + cs_g.vega + cl_g.vega, 4),
        annualized_return=ann_return,
    )


def analyze_covered_call(
    symbol: str,
    underlying_price: float,
    strike: float,
    dte: int,
    iv: float,
    cost_basis: float = None,
    r: float = DEFAULT_RISK_FREE_RATE,
) -> SpreadAnalysis:
    """Analyze a covered call (own shares + sell call)."""
    t = dte_to_years(dte)
    cost = cost_basis or underlying_price

    call_price = price_option('c', underlying_price, strike, t, r, iv)
    call_greeks = compute_greeks('c', underlying_price, strike, t, r, iv)

    premium = round(call_price, 2)
    max_profit = round((strike - cost) + premium, 2)
    max_loss = round(cost - premium, 2)  # If stock goes to 0
    breakeven = round(cost - premium, 2)
    ann_return = round((premium / cost) * (365 / dte) * 100, 1) if cost > 0 else 0

    return SpreadAnalysis(
        strategy="covered_call",
        symbol=symbol,
        legs=[
            {"action": "hold", "type": "shares", "qty": 100, "cost_basis": cost},
            {"action": "sell", "type": "call", "strike": strike, "price": premium,
             "delta": call_greeks.delta, "iv": round(iv, 4)},
        ],
        net_credit=premium,
        net_debit=0,
        max_profit=max_profit,
        max_loss=max_loss,
        breakeven=breakeven,
        pop=estimate_pop_from_delta(call_greeks.delta),
        risk_reward_ratio=round(max_loss / max_profit, 2) if max_profit > 0 else 999,
        net_delta=round(1 - call_greeks.delta, 4),  # 1.0 from shares minus call delta
        net_gamma=round(-call_greeks.gamma, 6),
        net_theta=round(-call_greeks.theta, 4),  # Negative because we sold
        net_vega=round(-call_greeks.vega, 4),
        annualized_return=ann_return,
    )


# ---------------------------------------------------------------------------
# Utility: convert SpreadAnalysis to dict for JSON/Discord
# ---------------------------------------------------------------------------
def spread_to_dict(spread: SpreadAnalysis) -> dict:
    """Convert SpreadAnalysis to a clean dict for serialization."""
    d = asdict(spread)
    return d
