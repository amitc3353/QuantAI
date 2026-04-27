#!/usr/bin/env python3
"""Agent Beta — main entry point.

Cron: */15 13-20 * * 1-5 (every 15 min during market hours).

Flow per cycle:
  1. Load market_intelligence.json + event_moves.json.
  2. Verify broker is IBKR (Beta's strategies require native index options).
  3. Classify regime via beta/regime_detector.
  4. Pick primary strategy (and fallback) per spec § 4.
  5. Risk check via beta/risk_engine.
  6. Strategy.can_enter → strategy.select_strikes → strategy.position_size.
  7. Submit via broker.place_mleg_order, journal, alert, dashboard.

Flags:
  --dry-run       emit the trade proposal but don't submit or journal
  --force-regime=NAME   override regime detector (testing)
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, "/home/trader/QuantAI/v2/shared-data/scripts")
from _logger import setup as _logger_setup

_logger_setup("beta_agent")

ET = ZoneInfo("America/New_York")
CACHE = Path("/root/quantai-v2/shared-data/cache")
JOURNAL = Path("/root/quantai-v2/shared-data/journal/paper/trades.jsonl")
LOGS = Path("/root/quantai-v2/shared-data/logs")
DASHBOARD_STATE = Path("/var/dashboard/state/agent-beta-state.json")

INTEL_PATH = CACHE / "market_intelligence.json"
EVENT_MOVES_PATH = CACHE / "event_moves.json"

# Auto-load .env
import pathlib as _pl
for _ef in [_pl.Path("/home/trader/QuantAI/.env"), _pl.Path("/root/quantai-v2/.env")]:
    if _ef.exists():
        for _line in _ef.read_text().splitlines():
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.partition("=")
                if not os.environ.get(_k.strip()):
                    os.environ[_k.strip()] = _v.strip()
        break

DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
DISCORD_ALERTS_CH = os.environ.get("DISCORD_CHANNEL_ALERTS", "")

DRY_RUN = "--dry-run" in sys.argv
FORCE_REGIME = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--force-regime=")), None)


# Regime → ordered list of strategy module names (primary first, then fallbacks).
REGIME_STRATEGY_MAP: dict[str, list[str]] = {
    "HALT": [],
    "CRISIS": ["put_ratio_backspread", "vix_calls"],
    "MEAN_REVERSION_OVERBOUGHT": ["broken_wing_butterfly", "debit_spread"],
    "MEAN_REVERSION_OVERSOLD": ["broken_wing_butterfly", "debit_spread"],
    "HIGH_VOL": ["credit_spread_offset", "broken_wing_butterfly"],
    "SQUEEZE": ["event_strangle"],
    "PRE_EVENT": ["event_strangle"],
    "TREND_UP": ["call_ratio_backspread", "debit_spread"],
    "TREND_DOWN": ["put_ratio_backspread", "debit_spread"],
    "LOW_VOL": ["event_strangle", "vix_calls"],
    "RANGE": ["broken_wing_butterfly", "calendar_spread"],
    "NORMAL": ["debit_spread"],
}


def _load_strategy(name: str):
    mod = __import__(f"beta.strategies.{name}", fromlist=[name])
    return mod


def _next_beta_id(journal: list) -> str:
    n = sum(1 for t in journal if (t.get("id") or "").startswith("B"))
    return f"B{n + 1:03d}"


def _simulate_slippage(num_legs: int, regime: str, avg_spread: float = 0.0) -> float:
    s = 0.10 * num_legs
    if avg_spread > 0.30:
        s += 0.05 * num_legs
    if regime == "CRISIS":
        s *= 3.0
    return round(s, 2)


def _post_discord(msg: str) -> None:
    if not DISCORD_BOT_TOKEN or not DISCORD_ALERTS_CH or DRY_RUN:
        return
    try:
        import requests
        requests.post(
            f"https://discord.com/api/v10/channels/{DISCORD_ALERTS_CH}/messages",
            headers={"Authorization": f"Bot {DISCORD_BOT_TOKEN}", "Content-Type": "application/json"},
            json={"content": msg[:1900]}, timeout=10,
        )
    except Exception as e:
        logging.warning("discord post failed: %s", e)


def _write_dashboard_state(state: dict) -> None:
    try:
        DASHBOARD_STATE.parent.mkdir(parents=True, exist_ok=True)
        tmp = DASHBOARD_STATE.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "last_updated": datetime.now(ET).isoformat(),
            "status": "ok",
            "data": state,
        }, indent=2))
        os.replace(tmp, DASHBOARD_STATE)
    except Exception as e:
        logging.warning("dashboard state write failed: %s", e)


def _journal_write(entry: dict) -> None:
    JOURNAL.parent.mkdir(parents=True, exist_ok=True)
    with open(JOURNAL, "a") as f:
        f.write(json.dumps(entry) + "\n")


def main() -> int:
    print(f"[beta_agent] start {datetime.now(ET).isoformat()}  dry_run={DRY_RUN}")

    if not INTEL_PATH.exists():
        logging.error("market_intelligence.json not found at %s", INTEL_PATH)
        return 1
    intel = json.loads(INTEL_PATH.read_text())
    event_moves = {}
    if EVENT_MOVES_PATH.exists():
        try:
            event_moves = json.loads(EVENT_MOVES_PATH.read_text())
        except Exception:
            event_moves = {}

    from broker import get_broker
    broker = get_broker()
    if broker.name != "ibkr":
        logging.error("Beta requires BROKER_TYPE=ibkr (got %s)", broker.name)
        print(f"[beta_agent] refusing: broker is {broker.name}, not ibkr")
        return 2
    if not broker.connect():
        logging.error("IBKR connect failed")
        return 3

    from beta.regime_detector import classify_regime, load_state, save_state, write_dashboard_state
    state = load_state()
    if FORCE_REGIME:
        regime, reason = FORCE_REGIME, "forced via --force-regime"
    else:
        regime, reason = classify_regime(intel, state)
    print(f"[beta_agent] regime={regime}  reason={reason}")
    write_dashboard_state(regime, reason, intel)

    if regime == "HALT":
        print("[beta_agent] HALT — no entries")
        return 0

    from beta.risk_engine import check_risk, load_journal, open_beta_positions
    journal = load_journal()
    open_beta = open_beta_positions(journal)

    acct = broker.get_account()
    if not acct:
        logging.error("get_account returned None")
        return 4
    equity = float(acct.get("equity") or 0)

    candidate_strategies = REGIME_STRATEGY_MAP.get(regime, [])
    if not candidate_strategies:
        print(f"[beta_agent] no strategies mapped to {regime}")
        return 0

    chosen = None
    for sname in candidate_strategies:
        try:
            smod = _load_strategy(sname)
        except Exception as e:
            logging.warning("strategy %s import failed: %s", sname, e)
            continue
        ok, why = smod.can_enter(intel, regime, journal)
        if not ok:
            print(f"[beta_agent] {sname}: SKIP — {why}")
            continue
        # for BWB the regime is needed by select_strikes via _direction
        intel.setdefault("macro", {})["_regime_override"] = regime
        try:
            strikes = smod.select_strikes(intel, broker, equity)
        except Exception as e:
            logging.warning("strategy %s select_strikes failed: %s", sname, e)
            strikes = None
        if not strikes:
            print(f"[beta_agent] {sname}: SKIP — strike selection returned None")
            continue
        chosen = (smod, strikes)
        break

    if not chosen:
        print("[beta_agent] no strategy fired this cycle")
        _write_dashboard_state({
            "current_regime": regime, "regime_reason": reason,
            "open_positions": len(open_beta), "max_positions": 3,
            "strategies_active": [], "next_action": "no fit this cycle",
        })
        return 0

    smod, strikes = chosen
    risk_pct_default = {"CRISIS": 0.005, "PRE_EVENT": 0.005,
                        "MEAN_REVERSION_OVERBOUGHT": 0.0075,
                        "MEAN_REVERSION_OVERSOLD": 0.0075,
                        "RANGE": 0.0075}.get(regime, 0.01)
    proposal = {
        "source": "agent_beta",
        "strategy": smod.NAME,
        "instrument": smod.INSTRUMENT,
        "regime_at_entry": regime,
        "regime_reason": reason,
        "legs": strikes["legs"],
        "net_debit": strikes.get("net_debit"),
        "net_credit": strikes.get("net_credit"),
        "max_risk": strikes["max_risk"],
        "expiry": strikes.get("expiry"),
        "net_delta": strikes.get("net_delta"),
        "net_vega": strikes.get("net_vega"),
        "underlying_price": (intel.get("macro") or {}).get("spx_price"),
        "risk_pct": risk_pct_default,
        "exit_rules": smod.build_exit_rules(strikes, intel),
    }

    ok, why, proposal = check_risk(proposal, intel, acct, journal)
    if not ok:
        print(f"[beta_agent] risk block: {why}")
        return 0

    qty = smod.position_size(equity, proposal["max_risk"], proposal["risk_pct"])
    if qty <= 0:
        print("[beta_agent] position_size=0 — skipping")
        return 0
    proposal["qty"] = qty

    coid = f"beta-{datetime.now(ET).strftime('%Y%m%d-%H%M%S')}-{smod.NAME[:6]}"
    proposal["client_order_id"] = coid

    # Slippage simulation
    avg_spread = (intel.get("macro") or {}).get("spx_atm_bid_ask_spread") or 0
    proposal["simulated_slippage"] = _simulate_slippage(len(proposal["legs"]), regime, avg_spread)

    print(f"[beta_agent] proposal: {smod.NAME} {smod.INSTRUMENT} qty={qty} "
          f"max_risk=${proposal['max_risk']:.0f} debit=${proposal.get('net_debit')}  coid={coid}")

    if DRY_RUN:
        print("[beta_agent] DRY-RUN — proposal:", json.dumps(proposal, indent=2, default=str))
        return 0

    fill = broker.place_mleg_order(proposal["legs"], qty=qty, tif="day", client_order_id=coid)
    if not fill:
        logging.error("place_mleg_order returned None")
        return 5

    # Journal write
    entry = dict(proposal)
    entry["id"] = _next_beta_id(journal)
    entry["timestamp"] = datetime.now(ET).isoformat()
    entry["mode"] = "paper"
    entry["status"] = "OPEN"
    entry["order_id"] = fill.get("order_id", "")
    entry["fill_status"] = fill.get("status", "")
    entry["filled_qty"] = fill.get("filled_qty", 0)
    entry["avg_fill_price"] = fill.get("avg_fill_price", 0)
    entry["regime_data"] = {
        "vix": (intel.get("macro") or {}).get("vix"),
        "iv_rank": (intel.get("macro") or {}).get("spx_iv_rank"),
        "adx": (intel.get("macro") or {}).get("spx_adx_14"),
        "rsi": (intel.get("macro") or {}).get("spx_rsi_14"),
        "implied_move_pct": (intel.get("macro") or {}).get("spx_implied_move_pct"),
        "skew": (intel.get("macro") or {}).get("spx_put_call_skew"),
    }
    _journal_write(entry)
    print(f"[beta_agent] journaled as {entry['id']}")

    # Discord
    msg = (f"🤖 Agent Beta | {regime} → {smod.NAME}\n"
           f"📊 {smod.INSTRUMENT} | {reason}\n"
           f"📈 qty={qty}  debit=${entry.get('net_debit', 0):.2f}\n"
           f"💰 Max risk: ${entry['max_risk']:.0f} ({entry['risk_pct']*100:.2f}%)\n"
           f"📋 trade={entry['id']}  order={entry['order_id'][:12]}")
    _post_discord(msg)

    _write_dashboard_state({
        "current_regime": regime, "regime_reason": reason,
        "open_positions": len(open_beta) + 1, "max_positions": 3,
        "strategies_active": [smod.NAME], "next_action": f"placed {entry['id']}",
    })

    return 0


if __name__ == "__main__":
    sys.exit(main())
