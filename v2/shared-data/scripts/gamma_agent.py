#!/usr/bin/env python3
"""Agent Gamma — Connors RSI(10) pullback agent.

Three-phase cron:
  --scan            30 20 * * 1-5  (4:30 PM ET, post-close)
                    Fetch daily data, compute Wilder RSI(10) + SMA(200),
                    filter for setups, write top-N to gamma_pending_entries.json.
  --execute         33 13 * * 1-5  (9:33 AM ET, market open)
                    Read pending file, re-validate, place bull-call debit
                    spreads via shared IBKR adapter. Journal as G###.
  --verify-spreads  30 13 * * 1    (9:30 AM ET Monday, added 2026-05-09)
                    Pull live ATM bid/ask for every UNIVERSE symbol, write
                    pass/blocked status to gamma_spread_status.json. Scanner
                    consults this file as filter F0. Block list refreshed
                    weekly. Gated by env var GAMMA_SPREAD_CHECK_ENABLED=1
                    (default on).

Optional flags:
  --dry-run        skip Discord posts and order submission; log proposals only
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

# Gamma owns clientId=22 to avoid collision with Alpha (1) and Beta (21)
os.environ.setdefault("IBKR_CLIENT_ID", "22")

from _logger import setup as _logger_setup
from _gate_logger import log_gate_block

_logger_setup("gamma_agent")

ET = ZoneInfo("America/New_York")
CACHE = Path("/root/quantai-v2/shared-data/cache")
PENDING_PATH = CACHE / "gamma_pending_entries.json"
INDICATOR_CACHE = CACHE / "gamma_indicator_cache.json"
JOURNAL = Path("/root/quantai-v2/shared-data/journal/paper/trades.jsonl")
DASHBOARD_STATE = Path("/var/dashboard/state/agent-gamma-state.json")

# Auto-load .env (mirrors beta_agent pattern)
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
SCAN = "--scan" in sys.argv
EXECUTE = "--execute" in sys.argv
VERIFY_SPREADS = "--verify-spreads" in sys.argv
RESET_EXPERIMENT = "--reset-experiment" in sys.argv
PROMOTE_ARM = "--promote-arm" in sys.argv
EVALUATE_PROMOTION = "--evaluate-promotion" in sys.argv
CONFIRM = "--confirm" in sys.argv

# Optional --reason "..." and --promote-arm <a|b|c|d> CLI arg parsing
def _parse_arg_value(name: str) -> str | None:
    """Extract the value following a ``--name`` token, e.g. --reason 'foo'."""
    try:
        idx = sys.argv.index(name)
        if idx + 1 < len(sys.argv) and not sys.argv[idx + 1].startswith("--"):
            return sys.argv[idx + 1]
    except ValueError:
        pass
    return None


RESET_REASON = _parse_arg_value("--reason")
PROMOTE_ARM_ID = _parse_arg_value("--promote-arm")

PENDING_MAX_AGE_HOURS = 18  # drop pending entries older than this on --execute

# Spread verifier feature flag (added 2026-05-09 with universe expansion).
# Default ON. To disable without git operations:
#   sudo sed -i 's/GAMMA_SPREAD_CHECK_ENABLED=1/GAMMA_SPREAD_CHECK_ENABLED=0/' .env
SPREAD_CHECK_ENABLED = os.environ.get("GAMMA_SPREAD_CHECK_ENABLED", "1") == "1"
SPREAD_STATUS_PATH = Path("/root/quantai-v2/shared-data/cache/gamma_spread_status.json")

# 4-arm A/B/C/D test feature flag (added 2026-05-10 with commit 3 of the
# implementation phasing per docs/gamma-four-arm-ab-test-plan.md §J).
# Default OFF — when 0, run_scan() and run_execute() behave EXACTLY as
# pre-experiment Gamma. When 1, dispatch routes to run_scan_4arm() and
# run_execute_4arm() which orchestrate four independent virtual portfolios.
GAMMA_AB_TEST_ENABLED = os.environ.get("GAMMA_AB_TEST_ENABLED", "0") == "1"

# Per-arm pending entries paths
def _arm_pending_path(arm_id: str) -> Path:
    return CACHE / f"gamma_arm_{arm_id}_pending_entries.json"

# Master ranking_decisions log
RANKING_DECISIONS_PATH = Path("/root/quantai-v2/shared-data/logs/gamma_ranking_decisions.jsonl")


# ── small helpers ─────────────────────────────────────────────────────────────

def _post_discord(msg: str) -> None:
    if DRY_RUN or not DISCORD_BOT_TOKEN or not DISCORD_ALERTS_CH:
        if DRY_RUN:
            print(f"[gamma_agent] DRY-RUN discord: {msg[:120]}")
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


def _write_dashboard_state(data: dict) -> None:
    try:
        DASHBOARD_STATE.parent.mkdir(parents=True, exist_ok=True)
        tmp = DASHBOARD_STATE.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "last_updated": datetime.now(ET).isoformat(),
            "status": "ok",
            "data": data,
        }, indent=2))
        os.replace(tmp, DASHBOARD_STATE)
    except Exception as e:
        logging.warning("dashboard state write failed: %s", e)


def _journal_write(entry: dict) -> None:
    JOURNAL.parent.mkdir(parents=True, exist_ok=True)
    with open(JOURNAL, "a") as f:
        f.write(json.dumps(entry) + "\n")


def _next_gamma_id(journal: list) -> str:
    """Max-based G### generation (count-based has a gap-collision bug — see
    the Alpha fix in commit 3a2b251)."""
    max_n = 0
    for t in journal:
        tid = (t.get("id") or "")
        if tid.startswith("G") and tid[1:].isdigit():
            try:
                max_n = max(max_n, int(tid[1:]))
            except ValueError:
                continue
    return f"G{max_n + 1:03d}"


# ── --scan mode (post-close) ──────────────────────────────────────────────────

def run_scan() -> int:
    print(f"[gamma_agent] SCAN start {datetime.now(ET).isoformat()}  dry_run={DRY_RUN}")

    from gamma import UNIVERSE, MAX_DAILY_ENTRIES
    from gamma.scanner import scan_with_indicators
    from gamma.risk_check import (
        check_portfolio_gates,
        filter_setups,
        load_journal,
        open_gamma_positions,
    )

    journal = load_journal()
    open_gamma = open_gamma_positions(journal)

    ok, why = check_portfolio_gates(journal)
    if not ok:
        print(f"[gamma_agent] portfolio gates blocked: {why}")
        _write_dashboard_state({
            "scan_results": {"total_scanned": 0, "above_200ma": 0,
                             "rsi_below_30": 0, "qualifying_setups": 0,
                             "instruments_triggering": []},
            "open_positions": len(open_gamma),
            "max_positions": 3,
            "next_action": f"blocked: {why}",
            "last_scan_time": datetime.now(ET).isoformat(),
        })
        # Still post Discord summary so Amit knows the scan ran
        _post_discord(f"📊 Agent Gamma | Scan paused — {why}")
        return 0

    open_symbols = {p.get("symbol") for p in open_gamma if p.get("symbol")}
    setups, indicator_cache = scan_with_indicators(UNIVERSE, open_symbols=open_symbols)
    print(f"[gamma_agent] {len(setups)} qualifying setups before risk filter "
          f"(indicators computed for {len(indicator_cache)})")

    # Persist indicator cache for position_monitor's RSI exit checks
    if not DRY_RUN:
        try:
            INDICATOR_CACHE.parent.mkdir(parents=True, exist_ok=True)
            INDICATOR_CACHE.write_text(json.dumps({
                "scan_timestamp": datetime.now(ET).isoformat(),
                "indicators": indicator_cache,
            }, indent=2))
        except Exception as e:
            logging.warning("indicator cache write failed: %s", e)

    eligible = filter_setups(setups, journal)
    print(f"[gamma_agent] {len(eligible)} after sector/limit filter")

    # Write pending file (consumed by --execute next morning)
    pending = {
        "scan_timestamp": datetime.now(ET).isoformat(),
        "scan_date": datetime.now(ET).date().isoformat(),
        "entries": eligible[:MAX_DAILY_ENTRIES],
    }
    if not DRY_RUN:
        try:
            PENDING_PATH.parent.mkdir(parents=True, exist_ok=True)
            PENDING_PATH.write_text(json.dumps(pending, indent=2))
            print(f"[gamma_agent] wrote {len(pending['entries'])} pending entries to {PENDING_PATH}")
        except Exception as e:
            logging.error("failed to write pending file: %s", e)
            return 5
    else:
        print(f"[gamma_agent] DRY-RUN pending: {json.dumps(pending, indent=2, default=str)}")

    # Discord summary
    scanned = len(indicator_cache)
    if eligible:
        lines = [
            f"  • {s['symbol']} — RSI(10) = {s['rsi_10']:.1f} | "
            f"{s['distance_above_200ma_pct']:.2f}% above 200 MA"
            for s in eligible
        ]
        msg = (
            "📊 Agent Gamma | Daily Scan Complete\n"
            f"🔍 Scanned {scanned}/{len(UNIVERSE)} | "
            f"qualifying: {len(setups)} | eligible after filters: {len(eligible)}\n"
            "📋 Pending entries:\n" + "\n".join(lines) + "\n"
            "⏰ Execution at next open (9:33 AM ET)"
        )
    else:
        msg = (
            "📊 Agent Gamma | Daily Scan Complete\n"
            f"🔍 Scanned {scanned}/{len(UNIVERSE)} instruments | "
            f"qualifying setups: {len(setups)} | eligible after filters: 0\n"
            "📋 No entries pending."
        )
    _post_discord(msg)

    _write_dashboard_state({
        "scan_results": {
            "total_scanned": 27,
            "qualifying_setups": len(setups),
            "eligible_after_filters": len(eligible),
            "instruments_triggering": [s["symbol"] for s in eligible],
        },
        "open_positions": len(open_gamma),
        "max_positions": 3,
        "next_action": (f"will execute {len(eligible)} at next open"
                        if eligible else "no entries pending"),
        "last_scan_time": datetime.now(ET).isoformat(),
    })
    return 0


# ── --execute mode (market open) ──────────────────────────────────────────────

def _load_pending() -> tuple[list[dict], dict | None]:
    """Returns (entries, raw_payload). Empty list if file missing or stale."""
    if not PENDING_PATH.exists():
        print("[gamma_agent] no pending file — nothing to execute")
        return [], None
    try:
        payload = json.loads(PENDING_PATH.read_text())
    except Exception as e:
        logging.error("pending file corrupt: %s", e)
        return [], None

    ts = payload.get("scan_timestamp", "")
    try:
        scan_dt = datetime.fromisoformat(ts)
        if scan_dt.tzinfo is None:
            scan_dt = scan_dt.replace(tzinfo=ET)
        age_h = (datetime.now(ET) - scan_dt).total_seconds() / 3600.0
        if age_h > PENDING_MAX_AGE_HOURS:
            print(f"[gamma_agent] pending file is {age_h:.1f}h old — discarding")
            return [], payload
    except Exception:
        pass

    return list(payload.get("entries") or []), payload


def _revalidate(entry: dict) -> tuple[bool, str]:
    """Re-check setup at execute time using fresh daily data.

    Soft bound: RSI may tick up overnight from the < 30 scan signal.
    Entry is still valid up to 35. Original Connors signal requires < 30
    at daily close (scan phase).
    """
    from gamma import RSI_REVALIDATE_SOFT
    from gamma.scanner import _fetch_history
    from gamma._indicators import sma, wilders_rsi

    symbol = entry["symbol"]
    fetched = _fetch_history(symbol)
    if fetched is None:
        return False, "yfinance fetch failed"
    closes, _ = fetched
    if len(closes) < 220:
        return False, "insufficient history"

    close = closes[-1]
    sma_200 = sma(closes, period=200)
    rsi_10 = wilders_rsi(closes, period=10)
    if sma_200 is None or rsi_10 is None:
        return False, "indicator calc failed"

    if close <= sma_200:
        return False, f"trend break: {close:.2f} <= 200 SMA {sma_200:.2f}"
    if rsi_10 >= RSI_REVALIDATE_SOFT:
        return False, f"RSI {rsi_10:.1f} >= {RSI_REVALIDATE_SOFT} (soft bound)"

    # Update entry with freshest values
    entry["close"] = round(close, 2)
    entry["rsi_10"] = round(rsi_10, 2)
    entry["sma_200"] = round(sma_200, 2)
    return True, f"re-validated: RSI {rsi_10:.1f}, close {close:.2f}"


def run_execute() -> int:
    print(f"[gamma_agent] EXECUTE start {datetime.now(ET).isoformat()}  dry_run={DRY_RUN}")

    # IBKR-only gate (mirrors beta_agent.py:151)
    from broker import get_broker
    broker = get_broker()
    if broker.name != "ibkr":
        logging.error("Gamma requires BROKER_TYPE=ibkr (got %s)", broker.name)
        print(f"[gamma_agent] refusing: broker is {broker.name}, not ibkr")
        return 2

    # Graceful connect-failure handling: leave pending file intact, exit 0
    try:
        connected = broker.connect()
    except Exception as e:
        logging.warning("IBKR connect raised: %s — preserving pending file for retry", e)
        print(f"[gamma_agent] IBKR connect failed: {e} — will retry next cycle")
        return 0
    if not connected:
        logging.warning("IBKR connect returned False — preserving pending file for retry")
        print("[gamma_agent] IBKR connect returned False — will retry next cycle")
        return 0

    pending, payload = _load_pending()
    if not pending:
        print("[gamma_agent] no pending entries to execute")
        return 0

    acct = broker.get_account() or {}
    real_equity = float(acct.get("equity") or 0)
    if real_equity <= 0:
        logging.error("get_account returned zero equity — refusing to execute")
        return 4
    # Cap effective equity for position sizing. Real broker equity (~$1M paper)
    # would oversize every spread; cap reflects Gamma's design intent.
    from _decision_helpers import effective_equity, AGENT_ACCOUNT_CAP
    equity = effective_equity(real_equity)
    if real_equity > AGENT_ACCOUNT_CAP:
        print(f"[gamma_agent] sizing-cap applied: real ${real_equity:,.0f} → ${equity:,.0f}")

    from gamma.risk_check import (
        check_portfolio_gates,
        filter_setups,
        load_journal,
    )
    from gamma.strike_selector import build_spread

    journal = load_journal()
    ok, why = check_portfolio_gates(journal)
    if not ok:
        print(f"[gamma_agent] portfolio gates blocked at execute: {why}")
        _post_discord(f"⚠️ Agent Gamma | execute blocked: {why}")
        # Don't delete pending; tomorrow's scan will overwrite
        return 0

    eligible = filter_setups(pending, journal)
    if not eligible:
        print("[gamma_agent] no entries pass risk filter at execute time")
        _consume_pending_file(pending, payload, [], [])
        return 0

    today_iso = datetime.now(ET).date().isoformat()
    placed: list[dict] = []
    failed: list[tuple[dict, str]] = []

    for setup in eligible:
        symbol = setup["symbol"]
        ok, reason = _revalidate(setup)
        if not ok:
            print(f"[gamma_agent] {symbol} re-validate FAIL: {reason}")
            failed.append((setup, f"revalidate_fail: {reason}"))
            continue
        print(f"[gamma_agent] {symbol} re-validated: {reason}")

        proposal = build_spread(setup, broker, equity, today_iso)
        if not proposal:
            print(f"[gamma_agent] {symbol} build_spread returned None")
            failed.append((setup, "build_spread_none"))
            continue

        from _decision_helpers import rsi_depth_score
        from _conviction_gate import check_conviction
        conv_score = rsi_depth_score(setup.get("rsi_10"))
        conv = check_conviction(conv_score, strategy="rsi_pullback_debit_spread")
        if not conv.allowed:
            print(f"[gamma_agent] conviction gate blocked {symbol}: {conv.reason}")
            log_gate_block("conviction", symbol, "gamma", conv.reason, "rsi_pullback_debit_spread")
            failed.append((setup, f"conviction_gate: {conv.reason}"))
            continue
        if conv.size_multiplier < 1.0:
            proposal["qty"] = max(1, int(proposal["qty"] * conv.size_multiplier))

        from _macro_blackout import check_macro_blackout
        _gamma_intel: dict = {}
        try:
            _intel_path = Path("/root/quantai-v2/shared-data/cache/market_intelligence.json")
            if _intel_path.exists():
                _gamma_intel = json.loads(_intel_path.read_text())
        except (OSError, json.JSONDecodeError):
            pass
        blk = check_macro_blackout(_gamma_intel, "rsi_pullback_debit_spread")
        if not blk.allowed:
            print(f"[gamma_agent] macro blackout blocked {symbol}: {blk.reason}")
            log_gate_block("macro_blackout", symbol, "gamma", blk.reason, "rsi_pullback_debit_spread")
            failed.append((setup, f"macro_blackout: {blk.reason}"))
            continue

        coid = f"gamma-{datetime.now(ET).strftime('%Y%m%d-%H%M%S')}-{symbol[:6]}"
        proposal["client_order_id"] = coid

        if DRY_RUN:
            print(f"[gamma_agent] DRY-RUN proposal {symbol}: "
                  f"{json.dumps(proposal, indent=2, default=str)}")
            placed.append(proposal)
            continue

        try:
            fill = broker.place_mleg_order(
                proposal["legs"], qty=proposal["qty"], tif="day", client_order_id=coid,
            )
        except Exception as e:
            logging.error("place_mleg_order raised for %s: %s", symbol, e)
            failed.append((setup, f"place_exception: {e}"))
            continue
        if not fill:
            # Partial-fill safeguard: the order packet may have reached the gateway
            # even if place_mleg_order returned None (connection drop after submit).
            recovered_orders = []
            try:
                recovered_orders = broker.get_open_orders(client_order_id=coid)
            except Exception as _rec_err:
                logging.warning("gamma_agent: get_open_orders reconciliation failed: %s", _rec_err)
            if recovered_orders:
                fill = recovered_orders[0]
                logging.warning(
                    "gamma_agent: order recovered from open orders after None result "
                    "(coid=%s orderId=%s symbol=%s)", coid, fill.get("order_id"), symbol,
                )
                print(f"[gamma_agent] ⚠️  order recovered from open orders (coid={coid} "
                      f"orderId={fill.get('order_id','?')}) — treating as submitted")
            else:
                logging.error("place_mleg_order returned None for %s", symbol)
                failed.append((setup, "place_returned_none"))
                continue

        # Build journal entry
        entry = dict(proposal)
        entry["id"] = _next_gamma_id(journal + placed)  # reflect this batch's appends
        entry["timestamp"] = datetime.now(ET).isoformat()
        entry["mode"] = "paper"
        entry["status"] = "OPEN"
        entry["order_id"] = fill.get("order_id", "") or fill.get("id", "")
        entry["fill_status"] = fill.get("status", "")
        entry["filled_qty"] = fill.get("filled_qty", 0)
        entry["avg_fill_price"] = fill.get("avg_fill_price", 0)
        entry["notes"] = (
            f"{symbol} RSI(10)={setup['rsi_10']:.1f}, "
            f"{setup['distance_above_200ma_pct']:.2f}% above 200 SMA. "
            "Pullback in confirmed uptrend."
        )

        from _decision_helpers import age_of, rsi_depth_score
        _thesis = (
            f"{symbol} RSI(10)={setup['rsi_10']:.1f}, above 200 SMA by "
            f"{setup['distance_above_200ma_pct']:.2f}%. Connors pullback signal — "
            f"oversold dip in confirmed uptrend (historical win rate ~89%)."
        )
        entry["decision"] = {
            "conviction_score": rsi_depth_score(setup.get("rsi_10")),
            "thesis": _thesis,
            "key_risk": (
                "Sector rotation extending the pullback past 200 SMA; "
                "broad market regime change overriding individual technicals."
            ),
            "invalidation": (
                f"{symbol} closes below 200 SMA ({setup['sma_200']:.2f}) "
                f"or RSI(10) > 40 hasn't fired by 10 trading days."
            ),
            "alternatives_considered": [],
            "skills_consulted": ["rsi-pullback-mechanics", "earnings-risk", "sector-correlation"],
            "regime_at_entry": "uptrend",
            "vix_at_entry": setup.get("vix"),
            "vix_data_age_seconds": age_of(setup.get("vix_timestamp")),
            "chain_data_age_seconds": age_of(setup.get("chain_timestamp")),
            "market_intel_age_seconds": age_of(
                setup.get("scan_timestamp")
                or (payload.get("generated_at") if payload else None)
            ),
            "pipeline_stage_durations": setup.get("stage_durations", {}),
            "rsi_at_entry": setup.get("rsi_10"),
            "sma_200_distance_pct": setup.get("distance_above_200ma_pct"),
            "sector": setup.get("sector"),
        }
        entry["full_trajectory"] = None

        _journal_write(entry)
        print(f"[gamma_agent] journaled as {entry['id']} (order_id={entry['order_id']})")
        placed.append(entry)

        msg = (
            f"🟢 Agent Gamma | ENTRY — RSI Pullback\n"
            f"📈 {symbol} Bull Call Spread "
            f"${entry['legs'][0].get('strike')}/${entry['legs'][1].get('strike')} | "
            f"{entry['expiry']} expiry\n"
            f"💰 Debit: ${entry['net_debit']:.2f} | Max risk: ${entry['total_risk']:.0f} "
            f"({(entry.get('total_risk_pct') or 0)*100:.2f}%)\n"
            f"📊 RSI(10): {setup['rsi_10']:.1f} | "
            f"Price: ${setup['close']:.2f} | 200 SMA: ${setup['sma_200']:.2f}\n"
            f"🆔 {entry['id']} | order={str(entry['order_id'])[:12]}"
        )
        _post_discord(msg)

    _consume_pending_file(pending, payload, placed, failed)
    _write_dashboard_state_after_execute(journal, placed)
    return 0


def _consume_pending_file(pending: list[dict], payload: dict | None,
                          placed: list[dict], failed: list[tuple[dict, str]]) -> None:
    """If everything placed cleanly, delete the pending file. If anything
    failed, rewrite the pending file with only the still-actionable entries
    so the next manual run can retry them. DRY-RUN never modifies the file.
    """
    if DRY_RUN:
        return
    if not failed:
        try:
            PENDING_PATH.unlink(missing_ok=True)
            print("[gamma_agent] consumed pending file")
        except Exception as e:
            logging.warning("could not unlink pending file: %s", e)
        return
    # Failures present — keep only the failed entries for retry, drop placed.
    placed_symbols = {p.get("symbol") for p in placed}
    leftovers = [s for s in pending
                 if s.get("symbol") not in placed_symbols]
    new_payload = {
        "scan_timestamp": (payload or {}).get("scan_timestamp"),
        "scan_date": (payload or {}).get("scan_date"),
        "entries": leftovers,
        "retry_failures": [{"symbol": s.get("symbol"), "reason": r} for s, r in failed],
    }
    try:
        PENDING_PATH.write_text(json.dumps(new_payload, indent=2))
        print(f"[gamma_agent] {len(leftovers)} entries left in pending for retry")
    except Exception as e:
        logging.warning("could not rewrite pending file: %s", e)


def _write_dashboard_state_after_execute(journal: list, placed: list[dict]) -> None:
    from gamma.risk_check import open_gamma_positions
    refreshed = open_gamma_positions(journal + placed)
    closed_gamma = [t for t in journal if t.get("source") == "agent_gamma"
                    and t.get("status") == "CLOSED"]
    wins = [t for t in closed_gamma if (t.get("pnl") or 0) > 0]
    win_rate = (len(wins) / len(closed_gamma)) if closed_gamma else 0.0
    total_pnl = sum((t.get("pnl") or 0) for t in closed_gamma)
    _write_dashboard_state({
        "open_positions": len(refreshed),
        "max_positions": 3,
        "today_entries": len(placed),
        "total_trades": len(closed_gamma) + len(refreshed),
        "win_rate": round(win_rate, 3),
        "total_pnl": round(total_pnl, 2),
        "current_positions": [
            {
                "id": p.get("id"),
                "symbol": p.get("symbol"),
                "entry_rsi": p.get("rsi_at_entry"),
                "expiry": p.get("expiry"),
                "net_debit": p.get("net_debit"),
            }
            for p in refreshed
        ],
        "next_action": (f"placed {len(placed)} this morning"
                        if placed else "awaiting next scan"),
        "last_execute_time": datetime.now(ET).isoformat(),
    })


# ── --verify-spreads mode (Monday market open) ────────────────────────────────

def run_verify_spreads() -> int:
    """Pull current ATM bid/ask for every UNIVERSE symbol; write
    gamma_spread_status.json. Scanner reads this as filter F0.

    Gated by GAMMA_SPREAD_CHECK_ENABLED env var (default 1). When disabled,
    exits early with a log line so the cron entry is harmless.
    """
    if not SPREAD_CHECK_ENABLED:
        print("[gamma_agent] verify-spreads: GAMMA_SPREAD_CHECK_ENABLED=0 — skipping")
        return 0

    from gamma import UNIVERSE
    from gamma import spread_verifier as sv

    print(f"[gamma_agent] VERIFY-SPREADS start {datetime.now(ET).isoformat()}  "
          f"dry_run={DRY_RUN}  universe_size={len(UNIVERSE)}")

    previous = sv.load_status(SPREAD_STATUS_PATH)
    payload = sv.verify_all(UNIVERSE, previous_state=previous)

    print(f"[gamma_agent] verify-spreads results: "
          f"passed={payload['n_passed']} "
          f"blocked={payload['n_blocked']} "
          f"fetch_failed={payload['n_fetch_failed']} "
          f"permanent={payload['n_permanent_blocks']}")

    if DRY_RUN:
        print("[gamma_agent] DRY-RUN — not writing state file or posting Discord")
        # Print the would-be-blocked symbols for review
        blocked = [r for r in payload["results"]
                    if not r.get("passed")
                    and r.get("blocked_reason") in ("spread_too_wide", "permanent_block_3_strikes")]
        if blocked:
            print(f"[gamma_agent] would-block: {[(b['symbol'], b.get('blocked_reason')) for b in blocked]}")
        return 0

    sv.write_status(payload, SPREAD_STATUS_PATH)
    print(f"[gamma_agent] wrote {SPREAD_STATUS_PATH}")

    # Discord alert (informational, weekly)
    blocked_syms = [r["symbol"] for r in payload["results"]
                     if not r.get("passed")
                     and r.get("blocked_reason") in ("spread_too_wide", "permanent_block_3_strikes")]
    fetch_failed_syms = [r["symbol"] for r in payload["results"]
                          if r.get("blocked_reason") == "fetch_failed"]

    if blocked_syms or fetch_failed_syms:
        msg_lines = [f"🚧 Gamma spread-verifier — {len(payload['results'])} symbols checked"]
        if blocked_syms:
            msg_lines.append(f"  Blocked ({len(blocked_syms)}): {blocked_syms[:10]}"
                             + ("..." if len(blocked_syms) > 10 else ""))
        if fetch_failed_syms:
            msg_lines.append(f"  Fetch-failed ({len(fetch_failed_syms)}): "
                             f"{fetch_failed_syms[:5]}"
                             + ("..." if len(fetch_failed_syms) > 5 else ""))
        if payload.get("n_permanent_blocks", 0) > 0:
            permanent = [r["symbol"] for r in payload["results"]
                          if r.get("blocked_reason") == "permanent_block_3_strikes"]
            msg_lines.append(f"  🔴 Permanent blocks (3-strike): {permanent}")
        _post_discord("\n".join(msg_lines))
    return 0


# ── 4-arm A/B/C/D dispatch (commit 3 of plan §J) ──────────────────────────────


def _log_ranking_decision(payload: dict) -> None:
    """Append one entry to gamma_ranking_decisions.jsonl. Used by run_scan_4arm
    to capture every per-arm rank decision for forensic + post-hoc analysis."""
    if DRY_RUN:
        return
    try:
        RANKING_DECISIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(RANKING_DECISIONS_PATH, "a") as f:
            f.write(json.dumps(payload, default=str) + "\n")
    except Exception as e:
        logging.warning("failed to write ranking_decisions.jsonl: %s", e)


def run_scan_4arm() -> int:
    """4-arm scan dispatch. Per docs/gamma-four-arm-ab-test-plan.md §A:

      shared scanner output
        → reward:risk estimate (commit 1)
        → for each arm: ranker.rank → filter_setups_for_arm → top-N → per-arm pending file
        → ranking_decisions.jsonl audit log
        → Discord summary
    """
    print(f"[gamma_agent] SCAN-4ARM start {datetime.now(ET).isoformat()}  dry_run={DRY_RUN}")

    from gamma import UNIVERSE, MAX_DAILY_ENTRIES
    from gamma.scanner import scan_with_indicators
    from gamma.risk_check import (
        check_portfolio_gates_for_arm,
        filter_setups_for_arm,
        load_journal,
    )
    from gamma.rankers import ARM_TO_RANKER, RANKERS, get_ranker
    from gamma.arm_state import (
        VALID_ARM_IDS, load_arm_state, save_arm_state,
    )
    from gamma.reward_risk_estimator import compute_reward_risk_estimates

    journal = load_journal()

    # Connect broker (needed for reward:risk estimator chain pulls)
    from broker import get_broker
    broker = get_broker()
    if not broker.connect():
        logging.warning("IBKR connect failed during 4-arm scan — skipping")
        return 0

    # Compute "open across all arms" set so the scanner skips them
    all_arm_open: set[str] = set()
    for aid in VALID_ARM_IDS:
        for t in journal:
            if (t.get("arm_id") == aid
                    and t.get("source") == f"agent_gamma_arm_{aid}"
                    and t.get("status") == "OPEN"):
                if t.get("symbol"):
                    all_arm_open.add(t["symbol"])

    # Shared scanner output (same scanner as single-arm; spread blocklist applied)
    setups, indicator_cache = scan_with_indicators(UNIVERSE)
    print(f"[gamma_agent] {len(setups)} qualifying setups (indicators for {len(indicator_cache)})")

    # Persist indicator cache for position_monitor's RSI exit checks
    if not DRY_RUN and indicator_cache:
        try:
            INDICATOR_CACHE.parent.mkdir(parents=True, exist_ok=True)
            INDICATOR_CACHE.write_text(json.dumps({
                "scan_timestamp": datetime.now(ET).isoformat(),
                "indicators": indicator_cache,
            }, indent=2))
        except Exception as e:
            logging.warning("indicator cache write failed: %s", e)

    # Reward:risk estimates (commit 1) — used by Arms B and D
    setups, estimator_duration = compute_reward_risk_estimates(setups, broker)
    print(f"[gamma_agent] reward_risk_estimator: {estimator_duration:.2f}s "
          f"({sum(1 for s in setups if s.get('reward_risk_estimate') is not None)}/{len(setups)} estimated)")

    # Load market intelligence for ranker context (VIX etc.)
    context: dict = {"today": datetime.now(ET).date(),
                     "scan_timestamp": datetime.now(ET).isoformat()}
    try:
        intel_path = Path("/root/quantai-v2/shared-data/cache/market_intelligence.json")
        if intel_path.exists():
            intel = json.loads(intel_path.read_text())
            context["vix"] = intel.get("vix") or intel.get("VIX")
    except Exception:
        pass

    # Per-arm dispatch
    decisions_record: dict = {
        "scan_timestamp": context["scan_timestamp"],
        "n_qualifying": len(setups),
        "qualifying_symbols": [s["symbol"] for s in setups],
        "vix_at_scan": context.get("vix"),
        "estimator_duration_sec": round(estimator_duration, 2),
        "ranks_per_arm": {},
        "picked_per_arm": {},
        "skipped_arms": {},
    }
    arm_summary: dict[str, list[str]] = {}

    for arm_id in VALID_ARM_IDS:
        # Per-arm portfolio gate — circuit breaker, daily cap, etc.
        ok, why = check_portfolio_gates_for_arm(journal, arm_id)
        if not ok:
            decisions_record["skipped_arms"][arm_id] = why
            arm_summary[arm_id] = []
            print(f"[gamma_agent] arm {arm_id.upper()} blocked: {why}")
            continue

        # Apply ranker (using fresh copy so fields don't bleed between arms)
        ranker = get_ranker(arm_id)
        ranked = ranker.rank([dict(s) for s in setups], context)
        decisions_record["ranks_per_arm"][arm_id] = [r["symbol"] for r in ranked]

        # Apply per-arm caps
        eligible = filter_setups_for_arm(ranked, journal, arm_id)
        picks = eligible[:MAX_DAILY_ENTRIES]
        decisions_record["picked_per_arm"][arm_id] = [p["symbol"] for p in picks]
        arm_summary[arm_id] = [p["symbol"] for p in picks]

        # Write per-arm pending file
        pending_payload = {
            "arm_id": arm_id,
            "ranker_used": ARM_TO_RANKER[arm_id],
            "scan_timestamp": context["scan_timestamp"],
            "scan_date": datetime.now(ET).date().isoformat(),
            "entries": picks,
        }
        if not DRY_RUN:
            try:
                path = _arm_pending_path(arm_id)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(pending_payload, indent=2, default=str))
                print(f"[gamma_agent] arm {arm_id.upper()}: wrote {len(picks)} pending → {path.name}")
            except Exception as e:
                logging.error("failed to write %s pending: %s", arm_id, e)
        else:
            print(f"[gamma_agent] DRY-RUN arm {arm_id.upper()} pending: "
                  f"{[p['symbol'] for p in picks]}")

    _log_ranking_decision(decisions_record)

    # Discord summary — 4 arms side-by-side
    msg_lines = [
        "📊 Agent Gamma 4-Arm | Daily Scan Complete",
        f"🔍 Qualifying setups: {len(setups)}",
    ]
    for aid in VALID_ARM_IDS:
        ranker_name = ARM_TO_RANKER[aid]
        picks = arm_summary.get(aid, [])
        skipped = decisions_record["skipped_arms"].get(aid)
        if skipped:
            msg_lines.append(f"  • Arm {aid.upper()} ({ranker_name}): SKIPPED — {skipped}")
        else:
            picks_str = ", ".join(picks) if picks else "—"
            msg_lines.append(f"  • Arm {aid.upper()} ({ranker_name}): {picks_str}")
    _post_discord("\n".join(msg_lines))
    return 0


def run_execute_4arm() -> int:
    """4-arm execute dispatch. Per docs/gamma-four-arm-ab-test-plan.md §C.

    For each symbol picked by ANY arm: submit each picking arm's order
    within ~5s. If any arm's order terminal-fails, cancel all still-working
    orders for that symbol (skip-all per plan §G). Each arm books its own
    fill (no averaging). Per-arm journal + state file updated on success.
    """
    print(f"[gamma_agent] EXECUTE-4ARM start {datetime.now(ET).isoformat()}  dry_run={DRY_RUN}")

    from broker import get_broker
    broker = get_broker()
    if broker.name != "ibkr":
        logging.error("Gamma requires BROKER_TYPE=ibkr (got %s)", broker.name)
        return 2
    try:
        if not broker.connect():
            print("[gamma_agent] IBKR connect failed — preserving pending files for retry")
            return 0
    except Exception as e:
        logging.warning("IBKR connect raised: %s", e)
        return 0

    from gamma import MAX_DAILY_ENTRIES
    from gamma.risk_check import (
        check_portfolio_gates_for_arm, filter_setups_for_arm, load_journal,
    )
    from gamma.strike_selector import build_spread
    from gamma.arm_state import (
        VALID_ARM_IDS, append_arm_trade, load_arm_state, save_arm_state,
        load_arm_journal, next_arm_trade_id,
    )

    # Equity sizing cap (mirror single-arm path)
    acct = broker.get_account() or {}
    real_equity = float(acct.get("equity") or 0)
    if real_equity <= 0:
        logging.error("get_account returned zero equity — refusing to execute")
        return 4

    journal = load_journal()
    today_iso = datetime.now(ET).date().isoformat()

    # 1. Load each arm's pending entries
    arm_pending: dict[str, list[dict]] = {}
    for aid in VALID_ARM_IDS:
        path = _arm_pending_path(aid)
        if not path.exists():
            arm_pending[aid] = []
            continue
        try:
            data = json.loads(path.read_text())
            arm_pending[aid] = list(data.get("entries") or [])
        except Exception as e:
            logging.warning("could not load %s pending: %s", aid, e)
            arm_pending[aid] = []

    total_pending = sum(len(v) for v in arm_pending.values())
    if total_pending == 0:
        print("[gamma_agent] no per-arm pending entries to execute")
        return 0

    # 2. Group by symbol — for each symbol, collect (arm_id, setup) tuples
    by_symbol: dict[str, list[tuple[str, dict]]] = {}
    for aid, entries in arm_pending.items():
        for e in entries:
            by_symbol.setdefault(e["symbol"], []).append((aid, e))

    placed_per_arm: dict[str, list[dict]] = {a: [] for a in VALID_ARM_IDS}
    failed_per_arm: dict[str, list[tuple[dict, str]]] = {a: [] for a in VALID_ARM_IDS}

    # 3. Per-symbol orchestration
    for symbol, arm_entries in by_symbol.items():
        proposals: list[tuple[str, dict, dict]] = []  # (arm_id, setup, proposal)

        # Build proposals per arm using arm-specific equity
        for aid, setup in arm_entries:
            arm_state = load_arm_state(aid)
            arm_equity = float(arm_state["current_equity"])
            proposal = build_spread(setup, broker, arm_equity, today_iso)
            if not proposal:
                failed_per_arm[aid].append((setup, "build_spread_none"))
                continue
            coid = (
                f"gamma-arm-{aid}-{datetime.now(ET).strftime('%Y%m%d-%H%M%S')}"
                f"-{symbol[:6]}"
            )
            proposal["client_order_id"] = coid
            proposals.append((aid, setup, proposal))

        if not proposals:
            continue

        # 4. Submit all arms' orders for this symbol
        submission_results: list[tuple[str, dict, dict, dict | None]] = []
        for aid, setup, proposal in proposals:
            if DRY_RUN:
                submission_results.append((aid, setup, proposal, {
                    "order_id": f"DRY-{aid}-{symbol}",
                    "status": "Filled",
                    "filled_qty": proposal["qty"],
                    "avg_fill_price": proposal["net_debit"],
                }))
                continue
            try:
                fill = broker.place_mleg_order(
                    proposal["legs"], qty=proposal["qty"], tif="day",
                    client_order_id=proposal["client_order_id"],
                )
            except Exception as e:
                logging.error("place_mleg_order raised arm %s %s: %s", aid, symbol, e)
                fill = None
            submission_results.append((aid, setup, proposal, fill))

        # 5. Skip-all on partial failure (plan §C)
        TERMINAL_FAIL_STATUSES = {"cancelled", "canceled", "rejected", "inactive",
                                   "apicancelled", "apicanceled"}
        any_failed = any(
            (fill is None) or
            (str(fill.get("status") or "").lower() in TERMINAL_FAIL_STATUSES)
            for _, _, _, fill in submission_results
        )
        if any_failed and not DRY_RUN:
            # Cancel any still-working orders for this symbol
            for aid, setup, proposal, fill in submission_results:
                if fill and (fill.get("order_id") or fill.get("id")):
                    status = str(fill.get("status") or "").lower()
                    if status not in TERMINAL_FAIL_STATUSES and status != "filled":
                        try:
                            broker.cancel_order(fill.get("order_id") or fill.get("id"))
                        except Exception:
                            pass
                failed_per_arm[aid].append(
                    (setup, f"skip_all_partial_failure: {fill}"),
                )
            failures_summary = ", ".join(
                f"{aid.upper()}={(fill or {}).get('status', 'None')}"
                for aid, _, _, fill in submission_results
            )
            _post_discord(
                f"🔴 Gamma 4-arm | {symbol} cancelled — partial broker failure: {failures_summary}"
            )
            continue

        # 6. Record fills per arm
        for aid, setup, proposal, fill in submission_results:
            entry = dict(proposal)
            arm_journal = load_arm_journal(aid)
            entry["id"] = next_arm_trade_id(aid, arm_journal + placed_per_arm[aid])
            entry["arm_id"] = aid
            entry["timestamp"] = datetime.now(ET).isoformat()
            entry["mode"] = "paper"
            entry["status"] = "OPEN"
            entry["source"] = f"agent_gamma_arm_{aid}"
            entry["ranker_used"] = (
                "rsi_only" if aid == "a" else
                "composite" if aid == "b" else
                "weighted_blend" if aid == "c" else
                "reward_risk_first"
            )
            entry["order_id"] = (fill or {}).get("order_id") or (fill or {}).get("id") or ""
            entry["fill_status"] = (fill or {}).get("status", "")
            entry["filled_qty"] = (fill or {}).get("filled_qty", 0)
            entry["avg_fill_price"] = (fill or {}).get("avg_fill_price", 0)
            entry["arm_equity_at_entry"] = load_arm_state(aid)["current_equity"]
            entry["rsi_at_entry"] = setup.get("rsi_10")
            entry["sma_200_distance_pct"] = setup.get("distance_above_200ma_pct")
            entry["sector"] = setup.get("sector")

            if not DRY_RUN:
                append_arm_trade(aid, entry)
                # Update arm state: cash decreases by max_risk
                state = load_arm_state(aid)
                state["cash"] = float(state["cash"]) - float(entry["max_risk"])
                save_arm_state(aid, state)
                print(f"[gamma_agent] arm {aid.upper()} {entry['id']} {symbol} "
                       f"max_risk=${entry['max_risk']:.0f}")
            placed_per_arm[aid].append(entry)
            _post_discord(
                f"🟢 Agent Gamma 4-arm | Arm {aid.upper()} ENTRY\n"
                f"📈 {symbol} ${entry['legs'][0].get('strike')}/${entry['legs'][1].get('strike')} | "
                f"{entry.get('expiry', '')}\n"
                f"💰 Debit ${entry['net_debit']:.2f} | r:r {entry.get('reward_risk', 0):.2f}\n"
                f"🆔 {entry['id']} | order={str(entry['order_id'])[:12]}"
            )

    # 7. Cleanup pending files
    if not DRY_RUN:
        for aid in VALID_ARM_IDS:
            path = _arm_pending_path(aid)
            try:
                if path.exists():
                    path.unlink()
            except Exception:
                pass

    total_placed = sum(len(v) for v in placed_per_arm.values())
    total_failed = sum(len(v) for v in failed_per_arm.values())
    print(f"[gamma_agent] EXECUTE-4ARM done: {total_placed} placed, {total_failed} failed")
    return 0


def run_reset_experiment() -> int:
    """Clean restart all 4 arms back to $10K. Archives current state +
    journals to gamma/journal/paper/archive/. Per plan §G.

    Two-phase: without --confirm, posts a Discord notice and prints what
    WOULD happen. With --confirm, performs the reset.
    """
    from gamma.arm_state import (
        VALID_ARM_IDS, reset_arm, _arm_state_path, _arm_journal_path,
        load_arm_state, JOURNAL_DIR,
    )

    reason = RESET_REASON or "operator-initiated"
    archive_dir = JOURNAL_DIR / "archive"

    if not CONFIRM:
        print(f"[gamma_agent] RESET-EXPERIMENT requested (reason: {reason})")
        print("  This will:")
        for aid in VALID_ARM_IDS:
            try:
                state = load_arm_state(aid)
                eq = state.get("current_equity", 0)
                trades = state.get("total_trades", 0)
            except Exception:
                eq, trades = "?", "?"
            print(f"    - Arm {aid.upper()}: reset ${eq} → $10K, archive {trades} trade(s)")
        print(f"  Archive directory: {archive_dir}")
        print("  Re-run with --confirm to proceed.")
        _post_discord(
            f"🔁 Gamma 4-arm RESET requested\nreason: {reason}\n"
            f"Re-run `gamma_agent.py --reset-experiment --reason \"{reason}\" --confirm` "
            f"to zero all 4 arms back to $10K."
        )
        return 0

    # Confirmed — perform the reset
    print(f"[gamma_agent] RESET-EXPERIMENT confirmed (reason: {reason})")
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    archive_dir.mkdir(parents=True, exist_ok=True)
    for aid in VALID_ARM_IDS:
        if DRY_RUN:
            print(f"  DRY-RUN would reset arm {aid.upper()}")
            continue
        reset_arm(aid, archive_dir=archive_dir)
        print(f"  arm {aid.upper()} reset → $10K, archived to {archive_dir}/")

    # Log reset event
    if not DRY_RUN:
        reset_log = archive_dir / "experiment_resets.jsonl"
        event = {
            "timestamp": datetime.now().isoformat(),
            "reason": reason,
            "archive_timestamp": timestamp,
        }
        try:
            with open(reset_log, "a") as f:
                f.write(json.dumps(event) + "\n")
        except Exception as e:
            logging.warning("could not log reset event: %s", e)

    _post_discord(
        f"🔁 Gamma 4-arm RESET complete\n"
        f"All arms back to $10K starting equity.\n"
        f"reason: {reason}"
    )
    return 0


def run_promote_arm() -> int:
    """Declare the winning arm. Closes other arms' open positions are NOT
    auto-closed (operator runs position_monitor or manual closes); this
    subcommand archives all 4 arms' final state and disables
    GAMMA_AB_TEST_ENABLED. Per plan §K."""
    if PROMOTE_ARM_ID not in ("a", "b", "c", "d"):
        print(f"[gamma_agent] invalid --promote-arm value: {PROMOTE_ARM_ID!r}")
        print("  Expected one of: a, b, c, d")
        return 1

    from gamma.arm_state import (
        VALID_ARM_IDS, _arm_state_path, _arm_journal_path,
        load_arm_state, arm_open_positions, JOURNAL_DIR,
    )
    from gamma.rankers import ARM_TO_RANKER

    arm_id = PROMOTE_ARM_ID
    ranker_name = ARM_TO_RANKER[arm_id]
    archive_dir = JOURNAL_DIR / "archive"
    reason = RESET_REASON or "promotion-evaluator-decision"

    if not CONFIRM:
        print(f"[gamma_agent] PROMOTE-ARM requested: arm {arm_id.upper()} ({ranker_name})")
        print("  This will:")
        print(f"    - Archive all 4 arms' state and journals to {archive_dir}/")
        print(f"    - Disable GAMMA_AB_TEST_ENABLED in .env (set to 0)")
        print(f"    - List other arms' open positions for operator to close")
        for aid in VALID_ARM_IDS:
            try:
                state = load_arm_state(aid)
                eq = state.get("current_equity", 0)
                pnl = state.get("total_realized_pnl", 0)
                trades = state.get("total_trades", 0)
            except Exception:
                eq, pnl, trades = "?", "?", "?"
            tag = " ← WINNER" if aid == arm_id else ""
            print(f"    - Arm {aid.upper()} ({ARM_TO_RANKER[aid]}): "
                   f"${eq} (P&L ${pnl}, {trades} trades){tag}")
        print(f"  Re-run with --confirm to proceed.")
        _post_discord(
            f"🏆 Gamma promote-arm requested: Arm {arm_id.upper()} ({ranker_name})\n"
            f"Re-run `gamma_agent.py --promote-arm {arm_id} --confirm` "
            f"to finalize."
        )
        return 0

    # Confirmed — perform the promotion
    print(f"[gamma_agent] PROMOTE-ARM confirmed: arm {arm_id.upper()} ({ranker_name})")
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    archive_dir.mkdir(parents=True, exist_ok=True)

    # Archive all 4 arms' state + journals
    for aid in VALID_ARM_IDS:
        src_state = _arm_state_path(aid)
        if src_state.exists():
            dst_state = archive_dir / f"gamma_arm_{aid}_account_promote_{timestamp}.json"
            if not DRY_RUN:
                dst_state.write_bytes(src_state.read_bytes())
            print(f"  archived state: {dst_state.name}")
        src_j = _arm_journal_path(aid)
        if src_j.exists() and src_j.stat().st_size > 0:
            dst_j = archive_dir / f"gamma_arm_{aid}_trades_promote_{timestamp}.jsonl"
            if not DRY_RUN:
                dst_j.write_bytes(src_j.read_bytes())
            print(f"  archived journal: {dst_j.name}")

    # List other arms' open positions
    others = [a for a in VALID_ARM_IDS if a != arm_id]
    print(f"\n  Open positions in non-winning arms (operator must close):")
    for aid in others:
        opens = arm_open_positions(aid)
        if not opens:
            print(f"    Arm {aid.upper()}: no open positions")
        else:
            print(f"    Arm {aid.upper()}: {len(opens)} position(s)")
            for t in opens:
                print(f"      - {t.get('id')} {t.get('symbol')} "
                       f"max_risk=${t.get('max_risk', 0):.0f}")

    # Disable feature flag in .env
    if not DRY_RUN:
        try:
            _set_env_var_in_dotenv("GAMMA_AB_TEST_ENABLED", "0")
            print(f"  GAMMA_AB_TEST_ENABLED set to 0 in .env")
        except Exception as e:
            logging.error("failed to update .env: %s", e)
            print(f"  WARNING: could not update .env automatically: {e}")
            print(f"  Manually: sudo sed -i 's/GAMMA_AB_TEST_ENABLED=1/GAMMA_AB_TEST_ENABLED=0/' /home/trader/QuantAI/.env")

    # Log promotion event
    if not DRY_RUN:
        event_log = archive_dir / "promotion_decisions.jsonl"
        event = {
            "timestamp": datetime.now().isoformat(),
            "winning_arm": arm_id,
            "winning_ranker": ranker_name,
            "reason": reason,
            "archive_timestamp": timestamp,
            "final_equity_per_arm": {
                aid: load_arm_state(aid).get("current_equity") for aid in VALID_ARM_IDS
            },
        }
        try:
            with open(event_log, "a") as f:
                f.write(json.dumps(event, default=str) + "\n")
        except Exception as e:
            logging.warning("could not log promotion: %s", e)

    final_equities = {aid: load_arm_state(aid).get("current_equity") for aid in VALID_ARM_IDS}
    msg_lines = [
        f"🏆 Gamma 4-arm test concluded",
        f"Winner: Arm {arm_id.upper()} ({ranker_name})",
        f"Final equity:",
    ]
    for aid in VALID_ARM_IDS:
        tag = " ← winner" if aid == arm_id else ""
        msg_lines.append(f"  Arm {aid.upper()}: ${final_equities[aid]:.2f}{tag}")
    msg_lines.append(f"GAMMA_AB_TEST_ENABLED=0 — single-arm production resumes.")
    _post_discord("\n".join(msg_lines))
    return 0


def run_evaluate_promotion() -> int:
    """Read all 4 arms' state + journal, run promotion_evaluator, print decision.

    Pure read-only. No state mutations, no Discord posts (unless caller
    explicitly enables). Used by the operator at days 60/90/120/150/180
    to decide whether to promote an arm. Per plan §K rollout flow."""
    from gamma.arm_state import (
        VALID_ARM_IDS, compute_experiment_day,
        load_arm_state, load_arm_journal,
    )
    from gamma.promotion_evaluator import (
        evaluate_promotion, format_decision_human_readable,
    )

    states = {aid: load_arm_state(aid) for aid in VALID_ARM_IDS}
    journals = {aid: load_arm_journal(aid) for aid in VALID_ARM_IDS}

    # Use the experiment_day from any arm's state (they should match —
    # same experiment_started_at across arms)
    exp_day = compute_experiment_day(states["a"])

    decision = evaluate_promotion(states, journals, exp_day)
    print(format_decision_human_readable(decision))

    if decision["decision"] == "promote":
        print()
        print(f"  Next step: gamma_agent.py --promote-arm {decision['winner']} --confirm")
    elif decision["decision"] == "extend":
        print()
        print(f"  Next step: continue running. Re-evaluate in 30 days.")
    elif decision["decision"] == "hard_cap_default":
        print()
        print(f"  Next step: gamma_agent.py --promote-arm a --confirm "
               f"(hard cap default → Arm A)")
    return 0


def _set_env_var_in_dotenv(key: str, value: str,
                             env_path: Path = Path("/home/trader/QuantAI/.env")) -> None:
    """Update one specific KEY=VALUE in .env without exposing other lines.

    Reads the file line-by-line, replaces the matching key (or appends if
    missing), writes atomically via temp+rename. Never logs file contents.
    Critical: .env contains Discord bot tokens and IBKR credentials.
    """
    if not env_path.exists():
        raise FileNotFoundError(f"{env_path} does not exist")
    lines = env_path.read_text().splitlines()
    found = False
    new_lines = []
    for line in lines:
        if line.startswith(f"{key}="):
            new_lines.append(f"{key}={value}")
            found = True
        else:
            new_lines.append(line)
    if not found:
        new_lines.append(f"{key}={value}")
    # Atomic replace
    import tempfile as _tf, os as _os
    fd, tmp = _tf.mkstemp(dir=str(env_path.parent), prefix=".env.", suffix=".tmp")
    try:
        with _os.fdopen(fd, "w") as f:
            f.write("\n".join(new_lines) + "\n")
        _os.replace(tmp, env_path)
    except Exception:
        try:
            _os.unlink(tmp)
        except OSError:
            pass
        raise


# ── entry point ───────────────────────────────────────────────────────────────

def main() -> int:
    # Operator subcommands take priority
    if RESET_EXPERIMENT:
        return run_reset_experiment()
    if PROMOTE_ARM:
        return run_promote_arm()
    if EVALUATE_PROMOTION:
        return run_evaluate_promotion()

    modes = sum([SCAN, EXECUTE, VERIFY_SPREADS])
    if modes > 1:
        print("[gamma_agent] choose exactly one of --scan, --execute, --verify-spreads",
              file=sys.stderr)
        return 1
    if SCAN:
        # 4-arm dispatch behind feature flag (commit 3). When flag is OFF,
        # behavior is byte-identical to pre-experiment Gamma.
        return run_scan_4arm() if GAMMA_AB_TEST_ENABLED else run_scan()
    if EXECUTE:
        return run_execute_4arm() if GAMMA_AB_TEST_ENABLED else run_execute()
    if VERIFY_SPREADS:
        return run_verify_spreads()
    print(
        "usage: gamma_agent.py --scan | --execute | --verify-spreads "
        "| --reset-experiment [--reason \"...\"] [--confirm] "
        "| --promote-arm <a|b|c|d> [--confirm] "
        "| --evaluate-promotion  [--dry-run]",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
