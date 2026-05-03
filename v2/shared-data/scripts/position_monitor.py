
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

import os, sys, json, subprocess, time
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

# Unique IBKR clientId so concurrent cron jobs don't collide on clientId=1.
os.environ.setdefault("IBKR_CLIENT_ID", "31")
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


def _leg_occ(trade, leg):
    """Resolve a leg's OCC symbol. Beta journal entries store the broker-
    correct OCC (with SPXW/VIXW tradingClass prefix) directly on the leg.
    Alpha legacy entries don't have it, so fall back to building from
    trade.symbol — which is wrong for weeklies but correct for equity legs."""
    sym = leg.get("symbol")
    if sym:
        return sym
    return build_occ(trade["symbol"], leg["expiry"], leg["type"], leg["strike"])


def compute_trade_pnl(trade, alpaca_pos):
    """Sum unrealized_pl across all legs present in Alpaca.
    Returns (total_pnl, legs_found). Missing legs contribute 0 — not an error.
    """
    total, found = 0.0, 0
    for leg in trade.get("legs", []):
        try:
            occ = _leg_occ(trade, leg)
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
            occ = _leg_occ(trade, leg)
            if occ not in alpaca_pos:
                continue
            # Beta journal stores side; Alpha legacy stores action. Reverse it.
            entry_dir = leg.get("action") or leg.get("side") or ""
            close_side = "sell" if entry_dir == "buy" else "buy"
            closing.append({"ratio_qty": "1", "side": close_side, "symbol": occ})
        except Exception as e:
            log(f"  build_closing_legs error: {e}")
    return closing


def place_close_order(trade, legs, close_qty=1):
    """Place a close order through the active broker. 1 leg → plain order;
    2-4 legs → mleg combo. Returns order dict on success, None on failure.
    """
    if DRY_RUN:
        log(f"  [DRY RUN] Would close {trade['id']} qty={close_qty} with {len(legs)} legs:")
        for leg in legs:
            log(f"    {leg['side'].upper()} {leg['symbol']}")
        return {"id": "dry-run", "status": "simulated"}

    if not (1 <= len(legs) <= 4):
        log(f"  Close order skipped: unexpected leg count {len(legs)} for {trade.get('id','?')}")
        return None

    import time as _t
    coid = f"close-{trade.get('id','?')}-{int(_t.time())}"
    try:
        result = get_broker().close_position(legs, qty=close_qty, client_order_id=coid)
    except Exception as e:
        log(f"  Close order exception: {e}")
        return None
    if result is None:
        log(f"  Close order FAILED ({len(legs)}-leg qty={close_qty}): broker returned None")
        logging.error("Close order FAILED %d-leg qty=%d: broker returned None", len(legs), close_qty)
        return None
    order_id = (result.get("order_id") or "")[:8]
    log(f"  Close order placed ({len(legs)} leg{'s' if len(legs)!=1 else ''} qty={close_qty}): {order_id}")
    return {"id": result.get("order_id", ""), "status": result.get("status", "submitted")}


# ── Exit logic ─────────────────────────────────────────────────────────────────

def _min_leg_dte(trade, today):
    """Days to the soonest-expiring leg."""
    dtes = []
    for leg in trade.get("legs", []):
        try:
            d = datetime.strptime(leg["expiry"], "%Y-%m-%d").date()
            dtes.append((d - today).days)
        except Exception:
            continue
    return min(dtes) if dtes else 999


_INTEL_CACHE = {"loaded_at": None, "data": {}}
_GAP_CACHE = {"date": None, "gap_pct": None}
_REGIME_CACHE = {"loaded_at": None, "regime": "UNKNOWN"}
_GAMMA_INDICATOR_CACHE = {"loaded_at": None, "data": {}}


def _load_intel_macro():
    """Load cached market_intelligence.macro. Refreshes once per minute."""
    import time
    now_ts = time.time()
    if _INTEL_CACHE["loaded_at"] and now_ts - _INTEL_CACHE["loaded_at"] < 60:
        return _INTEL_CACHE["data"]
    try:
        with open("/root/quantai-v2/shared-data/cache/market_intelligence.json") as f:
            data = json.load(f).get("macro", {})
    except Exception:
        data = {}
    _INTEL_CACHE.update({"loaded_at": now_ts, "data": data})
    return data


def _current_beta_regime():
    """Read current Beta regime from dashboard state. Cached per minute."""
    import time
    now_ts = time.time()
    if _REGIME_CACHE["loaded_at"] and now_ts - _REGIME_CACHE["loaded_at"] < 60:
        return _REGIME_CACHE["regime"]
    try:
        from pathlib import Path as _P
        state = json.loads(_P("/var/dashboard/state/agent-beta-state.json").read_text())
        r = state.get("data", {}).get("current_regime", "UNKNOWN")
    except Exception:
        r = "UNKNOWN"
    _REGIME_CACHE.update({"loaded_at": now_ts, "regime": r})
    return r


def _spx_gap_pct(now):
    """Today's SPX open vs yesterday's close, %. Cached per day. Returns None on failure."""
    today = now.date()
    if _GAP_CACHE["date"] == today and _GAP_CACHE["gap_pct"] is not None:
        return _GAP_CACHE["gap_pct"]
    try:
        import yfinance as yf
        hist = yf.Ticker("^GSPC").history(period="3d")
        if len(hist) < 2:
            return None
        today_open = float(hist["Open"].iloc[-1])
        prev_close = float(hist["Close"].iloc[-2])
        if prev_close <= 0:
            return None
        gap_pct = (today_open - prev_close) / prev_close * 100
        _GAP_CACHE.update({"date": today, "gap_pct": gap_pct})
        return gap_pct
    except Exception:
        return None


def _live_net_delta(trade, broker):
    """Sum live signed deltas across legs. Returns None if any leg can't be quoted."""
    if broker is None:
        return None
    total = 0.0
    legs = trade.get("legs") or []
    for leg in legs:
        sym = leg.get("symbol")
        if not sym:
            return None
        q = broker.get_option_quote(sym)
        if not q or q.get("delta") is None:
            return None
        ratio = int(leg.get("ratio_qty", 1))
        sign = 1 if str(leg.get("side", "")).lower() == "buy" else -1
        total += sign * ratio * float(q["delta"])
    return total


def _gamma_indicators(symbol):
    """Return the latest cached daily indicators for `symbol` from
    gamma_indicator_cache.json (written by gamma_agent.py --scan after market
    close). Cached for 5 minutes between reads. Returns {} if the file is
    missing or stale.
    """
    import time
    now_ts = time.time()
    if (_GAMMA_INDICATOR_CACHE["loaded_at"] and
            now_ts - _GAMMA_INDICATOR_CACHE["loaded_at"] < 300):
        return _GAMMA_INDICATOR_CACHE["data"].get(symbol, {})
    try:
        with open("/root/quantai-v2/shared-data/cache/gamma_indicator_cache.json") as f:
            payload = json.load(f)
        data = payload.get("indicators") or {}
    except Exception:
        data = {}
    _GAMMA_INDICATOR_CACHE.update({"loaded_at": now_ts, "data": data})
    return data.get(symbol, {})


def check_gamma_exit(trade, pnl, now, broker=None):
    """Agent Gamma exit checks (Connors RSI pullback).

    Triggers (in order):
      1. RSI(10) > 40            — primary Connors exit
      2. Time stop (10 trading days from entry_date)
      3. Trend break (close < 200 SMA)
      4. Stop loss (-50% on debit)
      5. Take profit (+150% on debit)

    Returns (should_close, reason, close_fraction, partial_flag) or None
    if the trade is not Gamma.
    """
    if trade.get("source") != "agent_gamma":
        return None
    rules = trade.get("exit_rules") or {}
    if not rules:
        return None
    today = now.date()

    basis = abs(trade.get("net_debit") or 0)
    pnl_pct = (pnl / (basis * 100)) * 100 if basis > 0 else 0.0

    symbol = trade.get("symbol") or trade.get("instrument")
    indicators = _gamma_indicators(symbol) if symbol else {}
    rsi_now = indicators.get("rsi_10")
    close_now = indicators.get("close")
    sma_200 = indicators.get("sma_200")

    # 1. Primary: RSI(10) recovery
    rsi_thr = rules.get("rsi_exit_threshold", 40)
    if rsi_now is not None and rsi_now > float(rsi_thr):
        return True, f"rsi_recovery (RSI={rsi_now:.1f}>{rsi_thr})", 1.0, None

    # 2. Time stop
    time_stop = rules.get("time_stop_days")
    entry_date = rules.get("entry_date") or trade.get("timestamp", "")[:10]
    if time_stop is not None and entry_date:
        try:
            from datetime import date as _date
            ed = _date.fromisoformat(entry_date[:10])
            held_days = 0
            cur = ed
            from datetime import timedelta as _td
            while cur < today:
                if cur.weekday() < 5:
                    held_days += 1
                cur += _td(days=1)
            if held_days >= int(time_stop):
                return True, f"time_stop ({held_days}>={time_stop} trading days)", 1.0, None
        except Exception:
            pass

    # 3. Trend break: closed below 200 SMA
    if close_now is not None and sma_200 is not None and close_now < sma_200:
        return True, f"trend_break (close {close_now:.2f} < SMA200 {sma_200:.2f})", 1.0, None

    # 4. Stop loss
    if basis > 0:
        sl = rules.get("stop_loss_pct", -50)
        if pnl_pct <= float(sl):
            return True, f"stop_loss ({pnl_pct:.0f}%<={sl}%)", 1.0, None

    # 5. Take profit
    if basis > 0:
        tp = rules.get("take_profit_pct", 150)
        if pnl_pct >= float(tp):
            return True, f"take_profit ({pnl_pct:.0f}%>={tp}%)", 1.0, None

    return False, "hold", 1.0, None


def check_beta_exit(trade, pnl, now, broker=None):
    """Beta-specific exit rules from trade['exit_rules'].

    Returns (should_close, reason, close_fraction, partial_flag) or None for non-Beta.
    close_fraction=1.0 for full close; <1.0 for partial (scale-out).
    partial_flag is the journal key to set True after a partial close, or None.
    """
    rules = trade.get("exit_rules") or {}
    if not rules:
        return None
    today = now.date()
    weekday = now.weekday()  # 0=Mon, 4=Fri
    macro = _load_intel_macro()

    basis = abs(trade.get("net_debit") or trade.get("net_credit") or 0)
    pnl_pct = (pnl / (basis * 100)) * 100 if basis > 0 else 0.0

    # -- Scale-out triggers (event strangle) — checked before time/pnl exits --
    for so in (rules.get("scale_out_at") or []):
        gp = so.get("gain_pct")
        frac = so.get("sell_fraction")
        if gp is None or frac is None:
            continue
        flag_key = f"_scaled_at_{int(gp)}"
        if trade.get(flag_key):
            continue
        if basis > 0 and pnl_pct >= float(gp):
            return True, f"scale_out_at ({pnl_pct:.0f}%>={gp}%)", float(frac), flag_key

    # -- Trailing stop (active only after first scale-out) --
    trailing_stop = rules.get("trailing_stop_pct")
    if trailing_stop is not None:
        first_scale_done = any(
            trade.get(f"_scaled_at_{int((so.get('gain_pct') or 0))}", False)
            for so in (rules.get("scale_out_at") or [])
        )
        if first_scale_done and basis > 0:
            peak = float(trade.get("_peak_pnl_pct") or 0)
            if peak > 0 and pnl_pct < peak - float(trailing_stop):
                return True, f"trailing_stop ({pnl_pct:.0f}%<peak {peak:.0f}%-{trailing_stop}%)", 1.0, None

    # -- Hard time exit (universal) --
    time_exit_dte = rules.get("time_exit_dte")
    if time_exit_dte is not None:
        dte = _min_leg_dte(trade, today)
        if dte <= int(time_exit_dte):
            return True, f"time_exit_dte ({dte}<={time_exit_dte})", 1.0, None

    # -- Hard time exit for ratios --
    hard_dte = rules.get("hard_time_exit_dte")
    if hard_dte is not None:
        dte = _min_leg_dte(trade, today)
        if dte <= int(hard_dte):
            return True, f"hard_time_exit ({dte}<={hard_dte})", 1.0, None

    # -- PnL thresholds --
    if basis > 0:
        tp = rules.get("take_profit_pct")
        if tp is not None and pnl_pct >= float(tp):
            return True, f"take_profit ({pnl_pct:.0f}%>={tp}%)", 1.0, None
        sl = rules.get("stop_loss_pct")
        if sl is not None and pnl_pct <= float(sl):
            return True, f"stop_loss ({pnl_pct:.0f}%<={sl}%)", 1.0, None
        # 2x-credit stop (credit spreads): stored as stop_loss_2x_credit=True + credit_at_entry
        if rules.get("stop_loss_2x_credit") and pnl_pct <= -200.0:
            return True, f"stop_loss_2x_credit ({pnl_pct:.0f}%<=-200%)", 1.0, None

    # -- Post-event exit (event strangle, 1h after event day starts) --
    post_event_hours = rules.get("post_event_exit_hours")
    event_type = trade.get("event_type")
    if post_event_hours is not None and event_type:
        days_field_map = {
            "CPI": "cpi_days_away", "NFP": "jobs_days_away",
            "FOMC": "fomc_days_away", "GDP": "gdp_days_away",
        }
        days_field = days_field_map.get(event_type)
        if days_field and macro.get("is_event_day") and macro.get(days_field, 999) == 0:
            try:
                entry_ts = datetime.fromisoformat(trade.get("timestamp", ""))
                if entry_ts.tzinfo is None:
                    entry_ts = entry_ts.replace(tzinfo=ET)
                elapsed_h = (now - entry_ts).total_seconds() / 3600
                if elapsed_h >= float(post_event_hours):
                    return True, f"post_event_exit ({event_type} day +{elapsed_h:.1f}h)", 1.0, None
            except Exception:
                pass

    # -- Big-move scale-out (event strangle, >=X% SPX move before event) --
    big_move_pct = rules.get("big_move_scale_out_pct")
    if big_move_pct is not None and not trade.get("_big_move_scaled"):
        spx_now = macro.get("spx_price") or 0
        entry_spx = float(trade.get("underlying_price") or 0)
        if spx_now > 0 and entry_spx > 0:
            move = abs(spx_now - entry_spx) / entry_spx * 100
            if move >= float(big_move_pct):
                return True, f"big_move_scale_out ({move:.1f}%>={big_move_pct}%)", 0.5, "_big_move_scaled"

    # -- VIX spike-capture (VIX Calls hedge) --
    vix_strike = rules.get("vix_strike")
    if vix_strike is not None:
        current_vix = macro.get("vix") or 0
        if current_vix > 0:
            vix_2x = float(vix_strike) * 2
            if not trade.get("_vix_2x_scaled") and current_vix >= vix_2x:
                frac = float(rules.get("vix_2x_strike_sell_fraction", 0.75))
                return True, f"vix_2x_strike (VIX {current_vix:.1f}>={vix_2x:.1f})", frac, "_vix_2x_scaled"
            if not trade.get("_vix_crossed_scaled") and current_vix >= float(vix_strike):
                frac = float(rules.get("vix_cross_strike_sell_fraction", 0.5))
                return True, f"vix_cross_strike (VIX {current_vix:.1f}>={vix_strike})", frac, "_vix_crossed_scaled"

    # -- Gamma scalp (ratio backspreads, >=3% SPX move within 5 days of entry) --
    gamma_pct = rules.get("gamma_scalp_pct")
    gamma_frac = rules.get("gamma_scalp_sell_fraction")
    if gamma_pct is not None and gamma_frac is not None and not trade.get("_gamma_scalp_done"):
        spx_now = macro.get("spx_price") or 0
        entry_spx = float(trade.get("underlying_price") or 0)
        if spx_now > 0 and entry_spx > 0:
            move = abs(spx_now - entry_spx) / entry_spx * 100
            if move >= float(gamma_pct):
                try:
                    entry_ts = datetime.fromisoformat(trade.get("timestamp", ""))
                    if entry_ts.tzinfo is None:
                        entry_ts = entry_ts.replace(tzinfo=ET)
                    if (now - entry_ts).days <= 5:
                        return True, f"gamma_scalp ({move:.1f}%>={gamma_pct}% d≤5)", float(gamma_frac), "_gamma_scalp_done"
                except Exception:
                    pass

    # -- Calendar short-leg roll (close when short leg approaches 3 DTE) --
    short_leg_min_dte = rules.get("short_leg_min_dte")
    if short_leg_min_dte is not None:
        expiry_short = trade.get("expiry_short")
        if expiry_short:
            try:
                short_dte = (datetime.strptime(expiry_short, "%Y-%m-%d").date() - today).days
                if short_dte <= int(short_leg_min_dte):
                    return True, f"short_leg_min_dte ({short_dte}<={short_leg_min_dte})", 1.0, None
            except Exception:
                pass

    # -- Calendar underlying breach (close if underlying moves >=X% from strike) --
    breach_pct = rules.get("underlying_breach_pct")
    breach_strike = rules.get("underlying_breach_strike")
    if breach_pct is not None and breach_strike is not None:
        spx_now = macro.get("spx_price") or 0
        if spx_now > 0 and float(breach_strike) > 0:
            dist = abs(spx_now - float(breach_strike)) / float(breach_strike) * 100
            if dist >= float(breach_pct):
                return True, f"underlying_breach ({dist:.1f}%>={breach_pct}%)", 1.0, None

    # -- BWB breakout (close if SPX moves >=X% from entry price) --
    breakout_pct = rules.get("breakout_pct")
    if breakout_pct is not None:
        spx_now = macro.get("spx_price") or 0
        entry_spx = float(trade.get("underlying_price") or 0)
        if spx_now > 0 and entry_spx > 0:
            move = abs(spx_now - entry_spx) / entry_spx * 100
            if move >= float(breakout_pct):
                return True, f"breakout ({move:.1f}%>={breakout_pct}%)", 1.0, None

    # -- Weekend close (Friday >=15:00 ET) --
    if rules.get("weekend_close") and weekday == 4 and now.hour >= 15:
        return True, "weekend_close", 1.0, None

    # -- Gap-open close (9:30-10:00 ET only, SPX gap >=1%) --
    if rules.get("gap_open_close") and weekday < 5 and now.hour == 9 and now.minute >= 30:
        gap = _spx_gap_pct(now)
        if gap is not None and abs(gap) >= 1.0:
            return True, f"gap_open_close (SPX gap {gap:+.1f}%)", 1.0, None

    # -- Regime exit (credit spread — exit when leaving HIGH_VOL) --
    regime_exits = rules.get("regime_exit_on_change")
    if regime_exits:
        current = _current_beta_regime()
        if current not in regime_exits and current != "UNKNOWN":
            return True, f"regime_exit ({current} not in {regime_exits})", 1.0, None

    # -- Event close buffer (credit spreads and debit spreads) --
    for buf_key in ("event_close_buffer_days", "event_buffer_days"):
        event_buf = rules.get(buf_key)
        if event_buf is not None:
            for d in ("fomc_days_away", "cpi_days_away", "jobs_days_away"):
                if macro.get(d, 999) <= int(event_buf):
                    return True, f"{buf_key} ({d}={macro.get(d)})", 1.0, None
            break

    # -- Valley danger (ratio backspreads) --
    valley = rules.get("valley_strike")
    if valley:
        dte = _min_leg_dte(trade, today)
        valley_exit_dte = int(rules.get("valley_exit_dte", 14))
        prox_pct = float(rules.get("valley_proximity_pct", 5.0))
        try:
            udl = float(trade.get("underlying_price") or 0)
            if udl > 0 and dte <= valley_exit_dte:
                dist = abs(udl - float(valley)) / float(valley) * 100
                if dist < prox_pct:
                    return True, f"valley_danger ({dist:.1f}% from {valley} @ {dte}d)", 1.0, None
        except Exception:
            pass

    # -- Trend reversal (ratio backspreads) --
    adx_min = rules.get("trend_reversal_adx_min")
    if adx_min is not None:
        adx = macro.get("spx_adx_14")
        if adx is not None and float(adx) < float(adx_min):
            return True, f"trend_reversal (ADX {adx:.0f} < {adx_min})", 1.0, None
    if rules.get("trend_reversal_ema"):
        price = macro.get("spx_price")
        ema = macro.get("spx_ema_20")
        slope = macro.get("spx_ema_20_slope")
        if price is not None and ema is not None:
            entry_dir = float(trade.get("net_delta") or 0)
            if entry_dir > 0 and price < ema and slope != "positive":
                return True, f"trend_reversal (price {price:.0f} < EMA20 {ema:.0f})", 1.0, None
            if entry_dir < 0 and price > ema and slope != "negative":
                return True, f"trend_reversal (price {price:.0f} > EMA20 {ema:.0f})", 1.0, None

    # -- Delta exit (ratio backspreads, live broker snapshot) --
    delta_thr = rules.get("delta_exit_threshold")
    if delta_thr is not None and broker is not None:
        live_delta = _live_net_delta(trade, broker)
        if live_delta is not None and abs(live_delta) > float(delta_thr):
            return True, f"delta_exit (|{live_delta:.2f}| > {delta_thr})", 1.0, None

    return False, "hold", 1.0, None


def check_exit_threshold(trade, pnl, now, broker=None):
    """Returns (should_close, exit_reason, close_fraction, partial_flag).

    close_fraction=1.0 for full close; <1.0 for partial scale-out.
    partial_flag is the journal key to set True after a partial close (or None).

    For Beta trades (exit_rules present): consult Beta logic. The 3:30 PM
    blanket close was REMOVED for Beta — its time_exit_dte / hard_time_exit_dte
    rules cover the same ground without prematurely closing multi-week trades.

    For Alpha trades (no exit_rules): use the original 4-rule logic, BUT the
    3:30 PM hard close now fires only when min leg DTE <= 1.
    """
    today = now.date()
    min_dte = _min_leg_dte(trade, today)

    gamma = check_gamma_exit(trade, pnl, now, broker=broker)
    if gamma is not None:
        should, reason, fraction, flag = gamma
        return (True, reason, fraction, flag) if should else (False, "", 1.0, None)

    beta = check_beta_exit(trade, pnl, now, broker=broker)
    if beta is not None:
        should, reason, fraction, flag = beta
        return (True, reason, fraction, flag) if should else (False, "", 1.0, None)

    # Alpha path. 1. Hard close at 3:30 PM ET — ONLY for 0/1 DTE legs.
    if min_dte <= 1 and (now.hour > 15 or (now.hour == 15 and now.minute >= 30)):
        return True, "hard_close_15_30", 1.0, None

    # 2. Expiry proximity — today or tomorrow
    tomorrow = date.fromordinal(today.toordinal() + 1)
    for leg in trade.get("legs", []):
        try:
            exp = datetime.strptime(leg["expiry"], "%Y-%m-%d").date()
            if exp <= tomorrow:
                return True, "expiry_proximity", 1.0, None
        except Exception:
            pass

    # 3 & 4. Credit-based thresholds — skip if estimated_credit is zero/missing
    credit = abs(trade.get("estimated_credit") or 0)
    if credit > 0:
        if pnl < -(2 * credit):
            return True, "stop_loss", 1.0, None
        if pnl >= 0.5 * credit:
            return True, "profit_target", 1.0, None

    # 5 & 6. Debit-based thresholds (diagonal spreads: estimated_credit=0, net_debit set)
    debit = abs(trade.get("net_debit") or 0)
    if credit == 0 and debit > 0:
        debit_basis = debit * 100
        if pnl < -debit_basis:
            return True, "stop_loss_debit (100%)", 1.0, None
        if pnl >= 0.5 * debit_basis:
            return True, "profit_target_debit (50%)", 1.0, None

    return False, "", 1.0, None


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


def post_close_alert(trade, exit_reason, pnl, is_partial=False, close_qty=None):
    strategy = (trade.get("strategy") or "").replace("_", " ").upper()
    instrument = trade.get("instrument") or trade.get("symbol") or "?"
    basis = abs(trade.get("net_debit") or trade.get("net_credit") or 0)
    pnl_pct = round(pnl / (basis * 100) * 100, 1) if basis > 0 else 0.0

    if trade.get("source") == "agent_gamma":
        entry_rsi = trade.get("rsi_at_entry")
        # Pull live RSI from indicator cache for context
        sym = trade.get("symbol") or trade.get("instrument") or ""
        try:
            ind = _gamma_indicators(sym)
            exit_rsi = ind.get("rsi_10")
        except Exception:
            exit_rsi = None
        rsi_note = ""
        if entry_rsi is not None and exit_rsi is not None:
            rsi_note = f"📊 Entry RSI: {entry_rsi} → Exit RSI: {exit_rsi}\n"
        msg = (
            f"✅ Agent Gamma | EXIT — {exit_reason}\n"
            f"📈 {instrument} {strategy}\n"
            f"💰 P&L: ${pnl:+.2f} ({pnl_pct:+.1f}%)\n"
            f"{rsi_note}"
            f"📋 {trade.get('id','?')} | {datetime.now(ET).strftime('%H:%M ET')}"
        )
    elif trade.get("source") == "agent_beta":
        action = "SCALED" if is_partial else "CLOSED"
        qty_note = f" (×{close_qty})" if is_partial and close_qty else ""
        msg = (
            f"🤖 Agent Beta | {action} — {strategy}{qty_note}\n"
            f"📊 {instrument} | {exit_reason}\n"
            f"💰 P&L: ${pnl:+.2f} ({pnl_pct:+.1f}%)\n"
            f"📋 {trade.get('id','?')} | {datetime.now(ET).strftime('%H:%M ET')}"
        )
    else:
        labels = {
            "stop_loss":        "🛑 STOP LOSS",
            "profit_target":    "✅ PROFIT TARGET",
            "expiry_proximity": "⏳ EXPIRY PROXIMITY",
            "hard_close_15_30": "⏰ HARD CLOSE 3:30 PM",
        }
        label = labels.get(exit_reason, exit_reason.upper())
        credit = trade.get("estimated_credit", 0)
        msg = (
            f"{label} | {trade.get('id','?')} {trade.get('symbol','?')} {strategy} | "
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

    # Update _peak_pnl_pct for Beta trades with trailing-stop rules (non-close journal update).
    peak_updates = {}
    for t in open_trades:
        if not t.get("exit_rules", {}).get("trailing_stop_pct"):
            continue
        tid = t["id"]
        basis = abs(t.get("net_debit") or t.get("net_credit") or 0)
        if basis <= 0:
            continue
        pnl_pct = (pnl_map.get(tid, 0) / (basis * 100)) * 100
        old_peak = float(t.get("_peak_pnl_pct") or 0)
        if pnl_pct > old_peak:
            peak_updates[tid] = {"_peak_pnl_pct": round(pnl_pct, 1)}
    if peak_updates and not DRY_RUN:
        rewrite_journal_atomic(peak_updates)
        log(f"Peak PnL updated for {len(peak_updates)} trade(s)")

    # Evaluate exits — but skip closes outside market hours.
    journal_updates = {}
    closed_trades   = []
    partial_trades  = []
    market_open = is_market_open(now)
    if not market_open:
        log(f"Market closed at {now.strftime('%H:%M ET')} — monitoring only, no close attempts")

    attempts = _load_close_attempts() if market_open else {}

    broker = get_broker()
    for t in open_trades:
        pnl = pnl_map[t["id"]]
        should_close, reason, close_fraction, partial_flag = check_exit_threshold(
            t, pnl, now, broker=broker
        )
        if not should_close:
            continue
        if not market_open:
            continue

        tid = t["id"]
        prior = attempts.get(tid, 0)
        if prior >= MAX_CLOSE_ATTEMPTS:
            continue

        trade_qty = t.get("qty") or 1
        close_qty = max(1, round(close_fraction * trade_qty))
        is_partial = close_fraction < 1.0 and close_qty < trade_qty

        log(f"EXIT triggered: {tid} ({reason}) close_qty={close_qty}/{trade_qty} — P&L ${pnl:+.2f}")

        legs = build_closing_legs(t, alpaca_pos)
        if not legs:
            log(f"  No active Alpaca legs for {tid} — already closed on broker; marking journal CLOSED")
            logging.warning("No active Alpaca legs for %s — broker already closed (journal repaired)", tid)
            basis = abs(t.get("net_debit") or t.get("net_credit") or t.get("estimated_credit") or 0)
            pnl_pct = round(pnl / (basis * 100), 4) if basis else 0.0
            journal_updates[tid] = {
                "status":         "CLOSED",
                "exit_timestamp": now.isoformat(),
                "close_timestamp": now.isoformat(),
                "exit_reason":    "closed_outside_pipeline",
                "close_reason":   "closed_outside_pipeline",
                "exit_pnl":       round(pnl, 2),
                "pnl":            round(pnl, 2),
                "pnl_pct":        pnl_pct,
                "close_order_id": "",
            }
            attempts.pop(tid, None)
            continue

        order = place_close_order(t, legs, close_qty)
        if order is None:
            attempts[tid] = prior + 1
            log(f"  Close order failed for {tid} (attempt {attempts[tid]}/{MAX_CLOSE_ATTEMPTS})")
            logging.warning("Close order failed for %s (attempt %d/%d)", tid, attempts[tid], MAX_CLOSE_ATTEMPTS)
            if attempts[tid] >= MAX_CLOSE_ATTEMPTS:
                log(f"  GIVING UP on {tid} after {MAX_CLOSE_ATTEMPTS} attempts — manual review needed")
                logging.error("Position close gave up after %d attempts on %s — manual review", MAX_CLOSE_ATTEMPTS, tid)
                post_discord(
                    f"⚠️ Position close gave up after {MAX_CLOSE_ATTEMPTS} attempts: "
                    f"`{tid}` {t.get('symbol','?')} {(t.get('strategy') or '').upper()} — manual review"
                )
            continue

        basis = abs(t.get("net_debit") or t.get("net_credit") or t.get("estimated_credit") or 0)
        pnl_pct = round(pnl / (basis * 100), 4) if basis else 0.0

        if is_partial:
            remaining_qty = trade_qty - close_qty
            log_entry = {
                "reason": reason, "qty_closed": close_qty,
                "qty_remaining": remaining_qty,
                "timestamp": now.isoformat(), "pnl_at_scale": round(pnl, 2),
            }
            upd = {
                "qty": remaining_qty,
                "_partial_close_log": (t.get("_partial_close_log") or []) + [log_entry],
            }
            if partial_flag:
                upd[partial_flag] = True
            journal_updates[tid] = upd
            partial_trades.append((t, reason, pnl, close_qty))
            attempts.pop(tid, None)
        else:
            # Full close
            try:
                entry_ts = datetime.fromisoformat(t.get("timestamp", ""))
                if entry_ts.tzinfo is None:
                    entry_ts = entry_ts.replace(tzinfo=ET)
                holding_days = (now - entry_ts).days
            except Exception:
                holding_days = None

            upd = {
                "status":          "CLOSED",
                "exit_timestamp":  now.isoformat(),
                "close_timestamp": now.isoformat(),
                "exit_reason":     reason,
                "close_reason":    reason,
                "exit_pnl":        round(pnl, 2),
                "pnl":             round(pnl, 2),
                "pnl_pct":         pnl_pct,
                "close_order_id":  order.get("id", ""),
            }
            if holding_days is not None:
                upd["holding_days"] = holding_days
            if partial_flag:
                upd[partial_flag] = True
            journal_updates[tid] = upd
            closed_trades.append((t, reason, pnl))
            attempts.pop(tid, None)
            # Code-resolve centralized-logger entries for close-failure patterns.
            try:
                sys.path.insert(0, '/var/dashboard')
                from lib_errors import resolve_catalog
                resolve_catalog("recurring-3c6683b1", by="code")
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

    all_updates = len(journal_updates)
    if journal_updates:
        if DRY_RUN:
            log(f"[DRY RUN] Would update {all_updates} trade(s): {list(journal_updates)}")
        else:
            ok = rewrite_journal_atomic(journal_updates)
            if ok:
                log(f"Journal updated — {len(closed_trades)} CLOSED, {len(partial_trades)} partial")
                if closed_trades:
                    sync_sheets()
                for (t, reason, pnl) in closed_trades:
                    post_close_alert(t, reason, pnl)
                    # Self-learning hooks — inline + try/except so a hang or LLM
                    # failure can never affect the next monitor cycle. Bounded by
                    # 20s LLM timeout in each script (worst-case ~40s per close).
                    _t0_diag = time.monotonic()
                    try:
                        from agent_self_diagnosis import diagnose as _self_diagnose
                        _self_diagnose(t["id"])
                    except Exception as _e:
                        log(f"  WARN: diagnosis failed for {t['id']}: {_e}")
                        logging.exception("Diagnosis failed for %s", t["id"])
                    finally:
                        logging.info("hook:diagnosis trade=%s elapsed=%.1fs",
                                     t["id"], time.monotonic() - _t0_diag)
                    _t0_rev = time.monotonic()
                    try:
                        from trade_reviewer import review as _trade_review
                        _trade_review(t["id"])
                    except Exception as _e:
                        log(f"  WARN: review failed for {t['id']}: {_e}")
                        logging.exception("Review failed for %s", t["id"])
                    finally:
                        logging.info("hook:review trade=%s elapsed=%.1fs",
                                     t["id"], time.monotonic() - _t0_rev)
                for (t, reason, pnl, cq) in partial_trades:
                    post_close_alert(t, reason, pnl, is_partial=True, close_qty=cq)
            else:
                msg = ("CRITICAL: position_monitor journal rewrite failed. "
                       "Close order(s) placed but journal not updated. Manual intervention needed.")
                log(msg)
                post_discord(f"⚠️ {msg}")

    log(f"Done — {len(closed_trades)} closed, {len(partial_trades)} partial, "
        f"{len(open_trades) - len(closed_trades) - len(partial_trades)} still open")


if __name__ == "__main__":
    main()
