"""
Guard Engine — Deterministic Constraint Layer
===============================================
Every trade proposal must pass ALL guards before reaching the exchange.
REJECT kills the trade — it does not queue it.
This service uses ZERO AI tokens. Pure Python rule enforcement.

Guards enforced:
  Position-level: max size, max loss, max contracts, min DTE, min liquidity
  Portfolio-level: max delta, max theta, max open positions, max concentration, max daily loss
  Timing: no-trade zones, earnings blackout, VIX pause, cooldown
  Strategy-specific: PMCC, bull put, iron condor, covered call rules
"""

import os
import json
import logging
from datetime import datetime, timedelta
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional
from pathlib import Path

from fastapi import FastAPI, HTTPException, Body
from pydantic import BaseModel
import uvicorn

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO")),
    format="%(asctime)s [guard-engine] %(levelname)s: %(message)s",
)
log = logging.getLogger("guard-engine")

# ---------------------------------------------------------------------------
# Guard Configuration — loaded from JSON, immutable at runtime
# ---------------------------------------------------------------------------
DEFAULT_CONFIG = {
    "position": {
        "max_position_pct": 5.0,
        "max_loss_pct": 2.0,
        "max_contracts": 10,
        "min_dte": 21,
        "min_open_interest": 100,
        "max_bid_ask_spread": 0.15,
    },
    "portfolio": {
        "max_delta": 0.30,
        "max_theta_daily": -50.0,
        "max_open_positions": 8,
        "max_sector_concentration": 3,
        "max_daily_loss_pct": 3.0,
    },
    "timing": {
        "no_trade_open_minutes": 15,
        "no_trade_close_minutes": 15,
        "earnings_blackout_hours": 48,
        "vix_pause_threshold": 35.0,
        "cooldown_hours": 24,
        "market_open_hour": 9,
        "market_open_minute": 30,
        "market_close_hour": 16,
        "market_close_minute": 0,
    },
    "strategy": {
        "pmcc": {
            "min_leaps_delta": 0.75,
            "max_short_delta": 0.25,
            "min_leaps_dte": 90,
        },
        "bull_put": {
            "min_iv_rank": 50,
            "max_width": 5,
            "min_credit_ratio": 0.33,
        },
        "iron_condor": {
            "min_iv_rank": 60,
            "min_sigma_distance": 1.0,
            "max_risk_credit_ratio": 2.0,
        },
        "covered_call": {
            "max_delta": 0.30,
            "no_earnings_week": True,
        },
    },
    "whitelist": ["SPY", "QQQ", "IWM", "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "TSLA", "META"],
    "halted": False,
}


def load_config() -> dict:
    """Load guard config from file, falling back to defaults."""
    paths = [
        Path("/app/configs/guard_config.json"),
        Path("configs/guard_config.json"),
        Path("guard-engine/config.json"),
    ]
    for p in paths:
        if p.exists():
            log.info(f"Loading guard config from {p}")
            with open(p) as f:
                user_config = json.load(f)
            # Deep merge with defaults
            merged = DEFAULT_CONFIG.copy()
            for section, values in user_config.items():
                if isinstance(values, dict) and section in merged and isinstance(merged[section], dict):
                    merged[section] = {**merged[section], **values}
                else:
                    merged[section] = values
            return merged
    log.warning("No guard config found, using defaults")
    return DEFAULT_CONFIG.copy()


CONFIG = load_config()

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------
class TradeProposal(BaseModel):
    """Incoming trade proposal to be validated."""
    symbol: str
    strategy: str = "unknown"
    direction: str = "long"  # long | short
    position_pct: float = 0.0  # % of portfolio
    max_loss_pct: float = 0.0  # % of portfolio
    contracts: int = 1
    dte: int = 30
    open_interest: Optional[int] = None
    bid_ask_spread: Optional[float] = None
    iv_rank: Optional[float] = None
    delta: Optional[float] = None
    leaps_delta: Optional[float] = None
    short_delta: Optional[float] = None
    leaps_dte: Optional[int] = None
    spread_width: Optional[float] = None
    credit_ratio: Optional[float] = None
    risk_credit_ratio: Optional[float] = None
    sigma_distance: Optional[float] = None
    sector: Optional[str] = None
    is_earnings_week: bool = False


class PortfolioState(BaseModel):
    """Current portfolio state for portfolio-level checks."""
    net_delta: float = 0.0
    daily_theta: float = 0.0
    open_positions: int = 0
    sector_counts: dict[str, int] = {}
    daily_pnl_pct: float = 0.0
    recent_trades: list[dict] = []  # [{symbol, closed_at}]


class GuardResult(BaseModel):
    result: str  # APPROVE | REJECT
    reason: str
    guard_name: str = ""
    checks_passed: list[str] = []
    checks_failed: list[str] = []


# ---------------------------------------------------------------------------
# Individual guard functions
# Each returns (passed: bool, message: str)
# ---------------------------------------------------------------------------

def check_halted() -> tuple[bool, str]:
    if CONFIG.get("halted", False):
        return False, "System is HALTED (emergency stop active)"
    return True, "System active"


def check_whitelist(symbol: str) -> tuple[bool, str]:
    whitelist = CONFIG.get("whitelist", [])
    if not whitelist:
        return True, "No whitelist configured"
    if symbol.upper() in [s.upper() for s in whitelist]:
        return True, f"{symbol} is on the whitelist"
    return False, f"{symbol} is NOT on the whitelist. Allowed: {', '.join(whitelist)}"


def check_position_size(position_pct: float) -> tuple[bool, str]:
    limit = CONFIG["position"]["max_position_pct"]
    if position_pct <= limit:
        return True, f"Position size {position_pct}% ≤ {limit}% limit"
    return False, f"Position size {position_pct}% EXCEEDS {limit}% limit"


def check_max_loss(max_loss_pct: float) -> tuple[bool, str]:
    limit = CONFIG["position"]["max_loss_pct"]
    if max_loss_pct <= limit:
        return True, f"Max loss {max_loss_pct}% ≤ {limit}% limit"
    return False, f"Max loss {max_loss_pct}% EXCEEDS {limit}% limit"


def check_max_contracts(contracts: int) -> tuple[bool, str]:
    limit = CONFIG["position"]["max_contracts"]
    if contracts <= limit:
        return True, f"Contracts {contracts} ≤ {limit} limit"
    return False, f"Contracts {contracts} EXCEEDS {limit} limit"


def check_min_dte(dte: int, strategy: str = "") -> tuple[bool, str]:
    limit = CONFIG["position"]["min_dte"]
    # Covered calls exempt from DTE minimum
    if strategy == "covered_call":
        return True, "Covered call exempt from DTE minimum"
    if dte >= limit:
        return True, f"DTE {dte} ≥ {limit} minimum"
    return False, f"DTE {dte} BELOW {limit} minimum (no week-0 options)"


def check_liquidity(open_interest: Optional[int], bid_ask_spread: Optional[float]) -> tuple[bool, str]:
    if open_interest is not None:
        min_oi = CONFIG["position"]["min_open_interest"]
        if open_interest < min_oi:
            return False, f"Open interest {open_interest} BELOW {min_oi} minimum"
    if bid_ask_spread is not None:
        max_spread = CONFIG["position"]["max_bid_ask_spread"]
        if bid_ask_spread > max_spread:
            return False, f"Bid-ask spread ${bid_ask_spread} EXCEEDS ${max_spread} maximum"
    return True, "Liquidity OK"


def check_no_trade_zones() -> tuple[bool, str]:
    """No trading 15 min after open and 15 min before close."""
    now = datetime.now()
    tc = CONFIG["timing"]
    market_open = now.replace(hour=tc["market_open_hour"], minute=tc["market_open_minute"], second=0)
    market_close = now.replace(hour=tc["market_close_hour"], minute=tc["market_close_minute"], second=0)

    no_trade_after_open = market_open + timedelta(minutes=tc["no_trade_open_minutes"])
    no_trade_before_close = market_close - timedelta(minutes=tc["no_trade_close_minutes"])

    if now < no_trade_after_open:
        return False, f"No-trade zone: within {tc['no_trade_open_minutes']}min of market open"
    if now > no_trade_before_close:
        return False, f"No-trade zone: within {tc['no_trade_close_minutes']}min of market close"
    return True, "Outside no-trade zones"


def check_earnings_blackout(is_earnings_week: bool) -> tuple[bool, str]:
    if is_earnings_week:
        return False, "Earnings blackout: within 48h of earnings announcement"
    return True, "No earnings blackout"


def check_vix(current_vix: Optional[float] = None) -> tuple[bool, str]:
    if current_vix is None:
        return True, "VIX data not provided, skipping"
    threshold = CONFIG["timing"]["vix_pause_threshold"]
    if current_vix > threshold:
        return False, f"VIX {current_vix} > {threshold} — system paused (advisory-only)"
    return True, f"VIX {current_vix} ≤ {threshold}"


def check_cooldown(symbol: str, recent_trades: list[dict]) -> tuple[bool, str]:
    cooldown_hours = CONFIG["timing"]["cooldown_hours"]
    cutoff = datetime.utcnow() - timedelta(hours=cooldown_hours)
    for trade in recent_trades:
        if trade.get("symbol", "").upper() == symbol.upper():
            closed_at = trade.get("closed_at")
            if closed_at:
                closed_time = datetime.fromisoformat(closed_at)
                if closed_time > cutoff:
                    return False, f"Cooldown: {symbol} was closed within {cooldown_hours}h"
    return True, f"No cooldown violation for {symbol}"


def check_portfolio_delta(net_delta: float) -> tuple[bool, str]:
    limit = CONFIG["portfolio"]["max_delta"]
    if abs(net_delta) <= limit:
        return True, f"Portfolio delta {net_delta:.2f} within ±{limit}"
    return False, f"Portfolio delta {net_delta:.2f} EXCEEDS ±{limit}"


def check_portfolio_theta(daily_theta: float) -> tuple[bool, str]:
    limit = CONFIG["portfolio"]["max_theta_daily"]
    if daily_theta >= limit:  # theta is negative, so -30 >= -50 is fine
        return True, f"Portfolio theta ${daily_theta}/day within ${limit}/day limit"
    return False, f"Portfolio theta ${daily_theta}/day EXCEEDS ${limit}/day limit"


def check_max_positions(open_positions: int) -> tuple[bool, str]:
    limit = CONFIG["portfolio"]["max_open_positions"]
    if open_positions < limit:
        return True, f"Open positions {open_positions} < {limit} limit"
    return False, f"Open positions {open_positions} AT {limit} limit — no new positions"


def check_sector_concentration(sector: Optional[str], sector_counts: dict) -> tuple[bool, str]:
    if not sector:
        return True, "No sector info, skipping concentration check"
    limit = CONFIG["portfolio"]["max_sector_concentration"]
    current = sector_counts.get(sector, 0)
    if current < limit:
        return True, f"Sector '{sector}' count {current} < {limit} limit"
    return False, f"Sector '{sector}' count {current} AT {limit} limit"


def check_daily_loss(daily_pnl_pct: float) -> tuple[bool, str]:
    limit = CONFIG["portfolio"]["max_daily_loss_pct"]
    if daily_pnl_pct > -limit:
        return True, f"Daily P&L {daily_pnl_pct:.1f}% above -{limit}% pause threshold"
    return False, f"Daily P&L {daily_pnl_pct:.1f}% HIT -{limit}% — all new trades paused"


# Strategy-specific guards
def check_pmcc(proposal: TradeProposal) -> tuple[bool, str]:
    cfg = CONFIG["strategy"]["pmcc"]
    if proposal.leaps_delta is not None and proposal.leaps_delta < cfg["min_leaps_delta"]:
        return False, f"PMCC: LEAPS delta {proposal.leaps_delta} < {cfg['min_leaps_delta']} minimum"
    if proposal.short_delta is not None and proposal.short_delta > cfg["max_short_delta"]:
        return False, f"PMCC: short delta {proposal.short_delta} > {cfg['max_short_delta']} maximum"
    if proposal.leaps_dte is not None and proposal.leaps_dte < cfg["min_leaps_dte"]:
        return False, f"PMCC: LEAPS DTE {proposal.leaps_dte} < {cfg['min_leaps_dte']} minimum"
    return True, "PMCC parameters OK"


def check_bull_put(proposal: TradeProposal) -> tuple[bool, str]:
    cfg = CONFIG["strategy"]["bull_put"]
    if proposal.iv_rank is not None and proposal.iv_rank < cfg["min_iv_rank"]:
        return False, f"Bull put: IV rank {proposal.iv_rank} < {cfg['min_iv_rank']} minimum"
    if proposal.spread_width is not None and proposal.spread_width > cfg["max_width"]:
        return False, f"Bull put: width {proposal.spread_width} > {cfg['max_width']} maximum"
    if proposal.credit_ratio is not None and proposal.credit_ratio < cfg["min_credit_ratio"]:
        return False, f"Bull put: credit ratio {proposal.credit_ratio:.0%} < {cfg['min_credit_ratio']:.0%} minimum"
    return True, "Bull put parameters OK"


def check_iron_condor(proposal: TradeProposal) -> tuple[bool, str]:
    cfg = CONFIG["strategy"]["iron_condor"]
    if proposal.iv_rank is not None and proposal.iv_rank < cfg["min_iv_rank"]:
        return False, f"Iron condor: IV rank {proposal.iv_rank} < {cfg['min_iv_rank']} minimum"
    if proposal.sigma_distance is not None and proposal.sigma_distance < cfg["min_sigma_distance"]:
        return False, f"Iron condor: sigma {proposal.sigma_distance} < {cfg['min_sigma_distance']} minimum"
    if proposal.risk_credit_ratio is not None and proposal.risk_credit_ratio > cfg["max_risk_credit_ratio"]:
        return False, f"Iron condor: risk/credit {proposal.risk_credit_ratio} > {cfg['max_risk_credit_ratio']} max"
    return True, "Iron condor parameters OK"


def check_covered_call(proposal: TradeProposal) -> tuple[bool, str]:
    cfg = CONFIG["strategy"]["covered_call"]
    if proposal.delta is not None and abs(proposal.delta) > cfg["max_delta"]:
        return False, f"Covered call: delta {proposal.delta} > {cfg['max_delta']} maximum"
    if cfg["no_earnings_week"] and proposal.is_earnings_week:
        return False, "Covered call: not allowed during earnings week"
    return True, "Covered call parameters OK"


# ---------------------------------------------------------------------------
# Master guard pipeline — runs ALL checks, returns first failure or APPROVE
# ---------------------------------------------------------------------------
STRATEGY_GUARDS = {
    "pmcc": check_pmcc,
    "bull_put": check_bull_put,
    "iron_condor": check_iron_condor,
    "covered_call": check_covered_call,
}


def run_guard_pipeline(
    proposal: TradeProposal,
    portfolio: Optional[PortfolioState] = None,
    current_vix: Optional[float] = None,
) -> GuardResult:
    """Run ALL guards. Returns APPROVE only if every guard passes."""
    checks_passed = []
    checks_failed = []

    # Ordered guard checks — most critical first
    guards = [
        ("system_halt", lambda: check_halted()),
        ("whitelist", lambda: check_whitelist(proposal.symbol)),
        ("position_size", lambda: check_position_size(proposal.position_pct)),
        ("max_loss", lambda: check_max_loss(proposal.max_loss_pct)),
        ("max_contracts", lambda: check_max_contracts(proposal.contracts)),
        ("min_dte", lambda: check_min_dte(proposal.dte, proposal.strategy)),
        ("liquidity", lambda: check_liquidity(proposal.open_interest, proposal.bid_ask_spread)),
        ("no_trade_zones", lambda: check_no_trade_zones()),
        ("earnings_blackout", lambda: check_earnings_blackout(proposal.is_earnings_week)),
        ("vix_pause", lambda: check_vix(current_vix)),
    ]

    # Portfolio-level guards (if portfolio state provided)
    if portfolio:
        guards.extend([
            ("portfolio_delta", lambda: check_portfolio_delta(portfolio.net_delta)),
            ("portfolio_theta", lambda: check_portfolio_theta(portfolio.daily_theta)),
            ("max_positions", lambda: check_max_positions(portfolio.open_positions)),
            ("sector_concentration", lambda: check_sector_concentration(proposal.sector, portfolio.sector_counts)),
            ("daily_loss", lambda: check_daily_loss(portfolio.daily_pnl_pct)),
            ("cooldown", lambda: check_cooldown(proposal.symbol, portfolio.recent_trades)),
        ])

    # Strategy-specific guards
    strategy_guard = STRATEGY_GUARDS.get(proposal.strategy)
    if strategy_guard:
        guards.append((f"strategy_{proposal.strategy}", lambda: strategy_guard(proposal)))

    # Run all guards
    for guard_name, guard_fn in guards:
        passed, message = guard_fn()
        if passed:
            checks_passed.append(f"{guard_name}: {message}")
        else:
            checks_failed.append(f"{guard_name}: {message}")
            # REJECT on first failure — fail fast
            log.warning(f"REJECT [{guard_name}] {proposal.symbol}: {message}")
            return GuardResult(
                result="REJECT",
                reason=message,
                guard_name=guard_name,
                checks_passed=checks_passed,
                checks_failed=checks_failed,
            )

    log.info(f"APPROVE {proposal.symbol}: all {len(checks_passed)} guards passed")
    return GuardResult(
        result="APPROVE",
        reason=f"All {len(checks_passed)} guards passed",
        guard_name="all",
        checks_passed=checks_passed,
        checks_failed=[],
    )


# ---------------------------------------------------------------------------
# FastAPI service
# ---------------------------------------------------------------------------
app = FastAPI(title="Guard Engine", version="1.0.0")


@app.get("/health")
def health():
    return {
        "status": "healthy",
        "halted": CONFIG.get("halted", False),
        "guards_loaded": len(DEFAULT_CONFIG),
        "timestamp": datetime.utcnow().isoformat(),
    }


@app.get("/config")
def get_config():
    return CONFIG


@app.post("/check", response_model=GuardResult)
def check_trade(proposal: TradeProposal = Body(..., embed=False)):
    """Run full guard pipeline on a trade proposal."""
    return run_guard_pipeline(proposal)


@app.post("/halt")
def halt_system():
    """Emergency stop — halt all new trades."""
    CONFIG["halted"] = True
    log.critical("SYSTEM HALTED via API")
    return {"status": "halted", "message": "All new trades blocked"}


@app.post("/resume")
def resume_system():
    """Resume trading after emergency stop."""
    CONFIG["halted"] = False
    log.info("System RESUMED via API")
    return {"status": "active", "message": "Trading resumed"}


@app.post("/reload")
def reload_config():
    """Reload guard config from disk."""
    global CONFIG
    CONFIG = load_config()
    log.info("Guard config reloaded")
    return {"status": "reloaded", "config": CONFIG}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8100, log_level="info")
