
import logging
import sys
sys.path.insert(0, '/home/trader/QuantAI/v2/shared-data/scripts')
from _logger import setup as _logger_setup
_logger_setup('position_monitor')

#!/usr/bin/env python3
"""
QuantAI Position Threshold Monitor (Slice D)

Runs every 2 minutes during market hours via cron. For each OPEN agent trade:
  - Fetches live P&L from Alpaca /v2/positions
  - Checks: stop loss (2x credit), profit target (50% credit),
    expiry proximity (today/tomorrow), hard close (3:30 PM ET)
  - On trigger: places market close order, updates journal atomically,
    syncs Google Sheets, posts Discord alert
  - Always writes /var/dashboard/state/quantai-positions.json with real P&L

Usage:
  python3 position_monitor.py            # normal run
  python3 position_monitor.py --dry-run  # read-only: no orders, no journal writes
"""

import os, sys, json, subprocess
from datetime import datetime, date, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# ── Auto-load .env ─────────────────────────────────────────────────────────────
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

try:
    import requests
except ImportError:
    print("ERROR: requests library not installed. Run: pip install requests")
    sys.exit(1)

from broker import get_broker

ET = ZoneInfo("America/New_York")
DRY_RUN = "--dry-run" in sys.argv

DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
DISCORD_ALERTS_CH = os.environ.get("DISCORD_CHANNEL_ALERTS", "")

JOURNAL   = "/root/quantai-v2/shared-data/journal/paper/trades.jsonl"
SCRIPTS   = "/home/trader/QuantAI/v2/shared-data/scripts"
DASH_FILE = Path("/var/dashboard/state/quantai-positions.json")


# ── Helpers ────────────────────────────────────────────────────────────────────

def log(msg):
    ts = datetime.now(ET).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


def build_occ(underlying, expiry_str, opt_type, strike):
    """Build OCC option symbol from journal leg fields.
    e.g. XOM 2026-06-18 call 150.0 → XOM260618C00150000
    """
    ymd = expiry_str.replace("-", "")[2:]  # "2026-06-18" → "260618"
    cp  = "C" if opt_type.lower().startswith("c") else "P"
    return f"{underlying}{ymd}{cp}{int(round(float(strike) * 1000)):08d}"


# Close-attempt tracking — bounded retries per trade so a stuck close (e.g. partial
# leg state on Alpaca) doesn't fire every 2 minutes forever.
CLOSE_ATTEMPTS_FILE = "/root/quantai-v2/shared-data/cache/close_attempts.json"
MAX_CLOSE_ATTEMPTS = 5


def _load_close_attempts():
    try:
        with open(CLOSE_ATTEMPTS_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


def _save_close_attempts(data):
    try:
        os.makedirs(os.path.dirname(CLOSE_ATTEMPTS_FILE), exist_ok=True)
        with open(CLOSE_ATTEMPTS_FILE, "w") as f:
            json.dump(data, f)
    except Exception as e:
        log(f"  WARN: failed to persist close-attempt counters: {e}")


def is_market_open(now=None):
    """Equity options trade 09:30–16:00 ET on weekdays."""
    n = now or datetime.now(ET)
    if n.weekday() >= 5:
        return False
    h, m = n.hour, n.minute
    if h < 9 or (h == 9 and m < 30):
        return False
    if h >= 16:
        return False
    return True


# ── Journal ────────────────────────────────────────────────────────────────────

def load_journal():
    if not os.path.exists(JOURNAL):
        return []
    trades = []
    for line in open(JOURNAL):
        line = line.strip()
        if not line:
            continue
        try:
            trades.append(json.loads(line))
        except Exception:
            pass
    return trades


def rewrite_journal_atomic(updates):
    """Merge updates into matching journal entries, rewrite atomically.
    updates: {trade_id: {field: value, ...}}
    Returns True on success, False on any error (original untouched on failure).
    """
    tmp_path = JOURNAL + ".tmp"
    try:
        lines = []
        if os.path.exists(JOURNAL):
            for raw in open(JOURNAL):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    t = json.loads(raw)
                    if t.get("id") in updates:
                        t.update(updates[t["id"]])
                    lines.append(json.dumps(t))
                except Exception:
                    lines.append(raw)  # preserve malformed lines verbatim
        with open(tmp_path, "w") as f:
            f.write("\n".join(lines) + "\n")
        os.replace(tmp_path, JOURNAL)
        return True
    except Exception as e:
        log(f"Journal rewrite FAILED: {e}")
        return False


# ── Alpaca ─────────────────────────────────────────────────────────────────────

def fetch_alpaca_positions():
    """Fetch open positions through the active broker.

    Returns {occ_symbol: position_dict} or None on error.
    None means skip this cycle entirely — do not write zero-P&L dashboard.

    Position dicts use the broker's normalized shape, with `unrealized_pl`
    aliased onto `unrealized_pnl` for compatibility with compute_trade_pnl().
    """
    try:
        broker = get_broker()
        # Connect failure → None (skip cycle). The broker logs the underlying error.
        if not broker.connect():
            log("Broker connect failed; skipping cycle")
            return None
        positions = broker.get_positions()
        if positions is None:
            return None
        out = {}
        for p in positions:
            sym = p.get("symbol", "")
            if not sym:
                continue
            entry = dict(p)
            # Legacy alias used by compute_trade_pnl().
            entry["unrealized_pl"] = p.get("unrealized_pnl", 0)
            out[sym] = entry
        return out
    except Exception as e:
        log(f"fetch_alpaca_positions failed: {e}")
        return None


def compute_trade_pnl(trade, alpaca_pos):
    """Sum unrealized_pl across all legs present in Alpaca.
    Returns (total_pnl, legs_found). Missing legs contribute 0 — not an error.
    """
    total, found = 0.0, 0
    for leg in trade.get("legs", []):
        try:
            occ = build_occ(trade["symbol"], leg["expiry"], leg["type"], leg["strike"])
            if occ in alpaca_pos:
                total += float(alpaca_pos[occ].get("unrealized_pl", 0))
                found += 1
        except Exception as e:
            log(f"  OCC build error for {trade.get('id','?')} leg: {e}")
    return total, found


def build_closing_legs(trade, alpaca_pos):
    """Build reversed legs for a closing mleg order.
    Skips legs with no active Alpaca position. Returns [] if none found.
    No position_intent field — Alpaca rejects it (Bug 2).
    """
    closing = []
    for leg in trade.get("legs", []):
        try:
            occ = build_occ(trade["symbol"], leg["expiry"], leg["type"], leg["strike"])
            if occ not in alpaca_pos:
                continue
            close_side = "sell" if leg["action"] == "buy" else "buy"
            closing.append({"ratio_qty": "1", "side": close_side, "symbol": occ})
        except Exception as e:
            log(f"  build_closing_legs error: {e}")
    return closing


def place_close_order(trade, legs):
    """Place a close order through the active broker. 1 leg → plain order;
    2-4 legs → mleg combo. Returns order dict on success, None on failure.
    """
    if DRY_RUN:
        log(f"  [DRY RUN] Would close {trade['id']} with {len(legs)} legs:")
        for leg in legs:
            log(f"    {leg['side'].upper()} {leg['symbol']}")
        return {"id": "dry-run", "status": "simulated"}

    if not (1 <= len(legs) <= 4):
        log(f"  Close order skipped: unexpected leg count {len(legs)} for {trade.get('id','?')}")
        return None

    import time as _t
    coid = f"close-{trade.get('id','?')}-{int(_t.time())}"
    try:
        result = get_broker().close_position(legs, qty=1, client_order_id=coid)
    except Exception as e:
        log(f"  Close order exception: {e}")
        return None
    if result is None:
        log(f"  Close order FAILED ({len(legs)}-leg): broker returned None")
        logging.error("Close order FAILED %d-leg: broker returned None", len(legs))
        return None
    order_id = (result.get("order_id") or "")[:8]
    log(f"  Close order placed ({len(legs)} leg{'s' if len(legs)!=1 else ''}): {order_id}")
    return {"id": result.get("order_id", ""), "status": result.get("status", "submitted")}


# ── Exit logic ─────────────────────────────────────────────────────────────────

def check_exit_threshold(trade, pnl, now):
    """Check all four exit rules in priority order.
    Returns (should_close: bool, exit_reason: str).
    """
    # 1. Hard close — 3:30 PM ET
    if now.hour > 15 or (now.hour == 15 and now.minute >= 30):
        return True, "hard_close_15_30"

    # 2. Expiry proximity — today or tomorrow
    today = now.date()
    tomorrow = date.fromordinal(today.toordinal() + 1)
    for leg in trade.get("legs", []):
        try:
            exp = datetime.strptime(leg["expiry"], "%Y-%m-%d").date()
            if exp <= tomorrow:
                return True, "expiry_proximity"
        except Exception:
            pass

    # 3 & 4. Credit-based thresholds — skip if estimated_credit is zero/missing
    credit = abs(trade.get("estimated_credit") or 0)
    if credit > 0:
        if pnl < -(2 * credit):
            return True, "stop_loss"
        if pnl >= 0.5 * credit:
            return True, "profit_target"

    return False, ""


# ── Discord ────────────────────────────────────────────────────────────────────

def post_discord(msg):
    if DRY_RUN:
        log(f"[DRY RUN] Discord: {msg[:120]}")
        return
    if DISCORD_BOT_TOKEN and DISCORD_ALERTS_CH:
        try:
            requests.post(
                f"https://discord.com/api/v10/channels/{DISCORD_ALERTS_CH}/messages",
                headers={"Authorization": f"Bot {DISCORD_BOT_TOKEN}",
                         "Content-Type": "application/json"},
                json={"content": msg[:1900]}, timeout=8
            )
            return
        except Exception:
            pass


def post_close_alert(trade, exit_reason, pnl):
    labels = {
        "stop_loss":        "🛑 STOP LOSS",
        "profit_target":    "✅ PROFIT TARGET",
        "expiry_proximity": "⏳ EXPIRY PROXIMITY",
        "hard_close_15_30": "⏰ HARD CLOSE 3:30 PM",
    }
    label  = labels.get(exit_reason, exit_reason.upper())
    credit = trade.get("estimated_credit", 0)
    msg = (
        f"{label} | {trade.get('id','?')} {trade.get('symbol','?')} "
        f"{(trade.get('strategy') or '').replace('_',' ').upper()} | "
        f"P&L: ${pnl:+.2f} | Entry credit: ${credit:.2f} | "
        f"{datetime.now(ET).strftime('%H:%M ET')}"
    )
    post_discord(msg)


# ── Sheets sync ────────────────────────────────────────────────────────────────

def sync_sheets():
    try:
        r = subprocess.run(
            ["python3", f"{SCRIPTS}/sheets_sync.py"],
            capture_output=True, text=True, timeout=30
        )
        log("Sheets synced" if r.returncode == 0 else f"Sheets sync failed: {r.stderr[:80]}")
    except Exception as e:
        log(f"Sheets error: {e}")


# ── Dashboard ──────────────────────────────────────────────────────────────────

def write_dashboard(open_trades, pnl_map):
    positions = []
    any_critical = False
    for t in open_trades:
        pnl    = pnl_map.get(t["id"], 0.0)
        credit = abs(t.get("estimated_credit") or 0)
        pnl_pct = round(pnl / credit, 4) if credit else 0.0
        if credit and pnl < -(2 * credit):
            pos_status = "critical"
            any_critical = True
        elif credit and pnl >= 0.5 * credit:
            pos_status = "warning"  # at profit target — worth watching
        else:
            pos_status = "ok"
        positions.append({
            "id":         t.get("id"),
            "symbol":     t.get("symbol"),
            "strategy":   (t.get("strategy") or "").replace("_", " "),
            "source":     t.get("source", ""),
            "entry_time": t.get("timestamp", ""),
            "pnl":        round(pnl, 2),
            "pnl_pct":    pnl_pct,
            "status":     pos_status,
            "exit_reason": None,
        })
    overall = "warning" if any_critical else ("ok" if positions else "idle")
    state = {
        "last_updated": datetime.now(ET).isoformat(),
        "status": overall,
        "data": {"count": len(positions), "positions": positions},
    }
    try:
        DASH_FILE.parent.mkdir(parents=True, exist_ok=True)
        DASH_FILE.write_text(json.dumps(state, indent=2))
    except Exception as e:
        log(f"Dashboard write failed: {e}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    now = datetime.now(ET)
    log(f"Position monitor starting {'[DRY RUN] ' if DRY_RUN else ''}— {now.strftime('%H:%M ET %a')}")

    all_trades  = load_journal()
    open_trades = [t for t in all_trades
                   if t.get("status") == "OPEN"
                   and t.get("source", "").startswith("agent")]

    if not open_trades:
        write_dashboard([], {})
        log("No open agent positions — idle")
        return

    log(f"{len(open_trades)} open agent trade(s)")

    alpaca_pos = fetch_alpaca_positions()
    if alpaca_pos is None:
        log("Broker API unavailable — skipping cycle (dashboard not updated)")
        return

    log(f"Broker: {len(alpaca_pos)} option position(s) found")

    # Build P&L map
    pnl_map = {}
    for t in open_trades:
        pnl, found = compute_trade_pnl(t, alpaca_pos)
        pnl_map[t["id"]] = pnl
        log(f"  {t['id']} {t.get('symbol','?')} {(t.get('strategy') or '').replace('_',' ')} "
            f"| P&L: ${pnl:+.2f} ({found}/{len(t.get('legs',[]))} legs matched)")

    # Always write dashboard with fresh P&L
    write_dashboard(open_trades, pnl_map)

    # Evaluate exits — but skip closes outside market hours (options aren't tradable).
    journal_updates = {}
    closed_trades   = []
    market_open = is_market_open(now)
    if not market_open:
        log(f"Market closed at {now.strftime('%H:%M ET')} — monitoring only, no close attempts")

    attempts = _load_close_attempts() if market_open else {}

    for t in open_trades:
        pnl = pnl_map[t["id"]]
        should_close, reason = check_exit_threshold(t, pnl, now)
        if not should_close:
            continue
        if not market_open:
            continue  # P&L recorded above; close attempt deferred to next market session

        tid = t["id"]
        prior = attempts.get(tid, 0)
        if prior >= MAX_CLOSE_ATTEMPTS:
            # Quiet — already logged once when the limit was hit. Manual review needed.
            continue

        log(f"EXIT triggered: {tid} ({reason}) — P&L ${pnl:+.2f}")

        legs = build_closing_legs(t, alpaca_pos)
        if not legs:
            log(f"  No active Alpaca legs for {tid} — already closed on broker; marking journal CLOSED")
            logging.warning("No active Alpaca legs for %s — broker already closed (journal repaired)", tid)
            credit = abs(t.get("estimated_credit") or 0)
            pnl_pct = round(pnl / credit, 4) if credit else 0.0
            journal_updates[tid] = {
                "status":         "CLOSED",
                "exit_timestamp": now.isoformat(),
                "exit_reason":    "closed_outside_pipeline",
                "exit_pnl":       round(pnl, 2),
                "pnl":            round(pnl, 2),
                "pnl_pct":        pnl_pct,
                "close_order_id": "",
            }
            attempts.pop(tid, None)
            continue

        order = place_close_order(t, legs)
        if order is None:
            attempts[tid] = prior + 1
            log(f"  Close order failed for {tid} (attempt {attempts[tid]}/{MAX_CLOSE_ATTEMPTS}) — will retry next cycle")
            logging.warning("Close order failed for %s (attempt %d/%d)", tid, attempts[tid], MAX_CLOSE_ATTEMPTS)
            if attempts[tid] >= MAX_CLOSE_ATTEMPTS:
                log(f"  GIVING UP on {tid} after {MAX_CLOSE_ATTEMPTS} attempts — manual review needed")
                logging.error("Position close gave up after %d attempts on %s — manual review", MAX_CLOSE_ATTEMPTS, tid)
                post_discord(
                    f"⚠️ Position close gave up after {MAX_CLOSE_ATTEMPTS} attempts: "
                    f"`{tid}` {t.get('symbol','?')} {(t.get('strategy') or '').upper()} — manual review"
                )
            continue

        credit = abs(t.get("estimated_credit") or 0)
        pnl_pct = round(pnl / credit, 4) if credit else 0.0
        journal_updates[tid] = {
            "status":         "CLOSED",
            "exit_timestamp": now.isoformat(),
            "exit_reason":    reason,
            "exit_pnl":       round(pnl, 2),
            "pnl":            round(pnl, 2),
            "pnl_pct":        pnl_pct,
            "close_order_id": order.get("id", ""),
        }
        closed_trades.append((t, reason, pnl))
        attempts.pop(tid, None)
        # Code-resolve the centralized-logger entries for the close-failure pattern.
        # If the close succeeded, those errors are stale by definition. If they
        # recur, the resurfacing rule creates fresh rows.
        try:
            sys.path.insert(0, '/var/dashboard')
            from lib_errors import resolve_catalog
            resolve_catalog("recurring-3c6683b1", by="code")  # underlying mleg-legs error
            # Per-symbol echoes (A008/A009/A010 mapped 1:1 from earlier session)
            tid_to_catalog = {
                "A008": "recurring-74290d3d",
                "A009": "recurring-09cfb5d0",
                "A010": "recurring-2cf36ff6",
            }
            cat_id = tid_to_catalog.get(tid)
            if cat_id:
                resolve_catalog(cat_id, by="code")
        except Exception as _resolve_err:
            log(f"  WARN: resolve_catalog after close failed: {_resolve_err}")

    if market_open:
        _save_close_attempts(attempts)

    if journal_updates:
        if DRY_RUN:
            log(f"[DRY RUN] Would close {len(journal_updates)} trade(s): {list(journal_updates)}")
        else:
            ok = rewrite_journal_atomic(journal_updates)
            if ok:
                log(f"Journal updated — {len(journal_updates)} trade(s) marked CLOSED")
                sync_sheets()
                for (t, reason, pnl) in closed_trades:
                    post_close_alert(t, reason, pnl)
            else:
                msg = ("CRITICAL: position_monitor journal rewrite failed. "
                       "Close order(s) placed but journal not updated. Manual intervention needed.")
                log(msg)
                post_discord(f"⚠️ {msg}")

    log(f"Done — {len(closed_trades)} closed, "
        f"{len(open_trades) - len(closed_trades)} still open")


if __name__ == "__main__":
    main()
