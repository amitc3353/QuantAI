"""Gamma spread verifier — pulls live ATM bid/ask for each universe symbol,
computes spread%, writes pass/blocked status to gamma_spread_status.json.

Runs Monday 9:30 AM ET via cron (added 2026-05-09 with universe expansion).
Scanner reads the state file before qualifying setups (filter F0).

Result schema:
  - passed: True   → clean, scanner allows
  - passed: False, blocked_reason: "spread_too_wide"          → scanner BLOCKS
  - passed: False, blocked_reason: "fetch_failed"             → scanner ALLOWS (fail-open)
  - passed: False, blocked_reason: "permanent_block_3_strikes" → scanner BLOCKS

Failure semantics:
  - spread > 5%       → blocked for the week
  - yfinance error    → fail-OPEN (allow), warn to Discord
  - 3 consecutive Mondays of fetch_failed → escalate to permanent block
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

try:
    import yfinance as yf
except ImportError as e:
    raise ImportError("yfinance not available; required by spread verifier") from e

# Lazy import to avoid circular: gamma/__init__.py imports nothing from
# this module, but this module needs yf_symbol().
def _yf_symbol(sym: str) -> str:
    from gamma import yf_symbol
    return yf_symbol(sym)


# Threshold for the "spread quality" filter. ATM spread ≤ 5% of mid passes.
SPREAD_PCT_THRESHOLD = 5.0

# Number of consecutive Mondays of fetch_failed before escalating to a
# permanent block. Resets on any successful verification.
PERMANENT_BLOCK_THRESHOLD = 3

# How many symbols to verify in parallel. Conservative — yfinance options
# chain pulls are heavier than history pulls.
VERIFY_WORKERS = 10


# ── single-symbol verification ───────────────────────────────────────────


def _atm_spread_pct(strikes_df, last_close: float) -> Optional[float]:
    """Find the strike closest to last_close, return (ask-bid)/mid as a percent.
    Returns None if the row has zero/negative bid or ask."""
    if strikes_df is None or len(strikes_df) == 0:
        return None
    # Find ATM strike (closest to last_close)
    diffs = (strikes_df["strike"] - last_close).abs()
    idx = diffs.idxmin()
    row = strikes_df.loc[idx]
    bid = float(row.get("bid") or 0.0)
    ask = float(row.get("ask") or 0.0)
    if bid <= 0 or ask <= 0 or ask < bid:
        return None
    mid = (bid + ask) / 2.0
    if mid <= 0:
        return None
    return ((ask - bid) / mid) * 100.0


def verify_one(symbol: str, dte_target_min: int = 14, dte_target_max: int = 28) -> dict:
    """Verify one symbol's ATM bid-ask spread. Returns a dict matching the
    documented schema. Fail-open on any yfinance error.

    Translates QA symbol → yfinance ticker via gamma.yf_symbol() so XSP, SPX,
    BRK.B etc. resolve correctly. Note that yfinance has options chains for
    ETFs (SPY, QQQ, IWM, XLK ...) but not raw indices (^GSPC, ^NDX, ^RUT) —
    indices will naturally come back as fetch_failed (fail-open). IBKR
    validates index option spreads at execute time via strike_selector.
    """
    try:
        ticker = yf.Ticker(_yf_symbol(symbol))
        expiries = list(ticker.options or ())
        if not expiries:
            return {
                "symbol": symbol,
                "passed": False,
                "blocked_reason": "fetch_failed",
                "error": "yfinance returned no expiries",
            }

        # Pick the first expiry in the [dte_target_min, dte_target_max] window;
        # fall back to the nearest if none are in range.
        from datetime import date as _date
        today = _date.today()
        in_window = []
        for e in expiries:
            try:
                ed = _date.fromisoformat(e)
                dte = (ed - today).days
                if dte_target_min <= dte <= dte_target_max:
                    in_window.append((e, dte))
            except ValueError:
                continue
        if in_window:
            expiry_used = in_window[0][0]
        else:
            expiry_used = expiries[0]

        # Pull last close (for ATM strike detection)
        hist = ticker.history(period="5d", auto_adjust=False)
        if hist is None or hist.empty or "Close" not in hist:
            return {
                "symbol": symbol,
                "passed": False,
                "blocked_reason": "fetch_failed",
                "error": "no recent close price",
            }
        last_close = float(hist["Close"].dropna().iloc[-1])

        chain = ticker.option_chain(expiry_used)
        spread_call = _atm_spread_pct(chain.calls, last_close)
        spread_put = _atm_spread_pct(chain.puts, last_close)

        # Pass if EITHER side is clean (matches Part B §3 spec)
        candidates = [s for s in (spread_call, spread_put) if s is not None]
        if not candidates:
            return {
                "symbol": symbol,
                "passed": False,
                "blocked_reason": "fetch_failed",
                "error": "no usable bid/ask on ATM call or put",
                "expiry_used": expiry_used,
            }
        best_spread = min(candidates)
        if best_spread <= SPREAD_PCT_THRESHOLD:
            return {
                "symbol": symbol,
                "passed": True,
                "spread_pct": round(best_spread, 2),
                "expiry_used": expiry_used,
            }
        return {
            "symbol": symbol,
            "passed": False,
            "blocked_reason": "spread_too_wide",
            "spread_pct": round(best_spread, 2),
            "expiry_used": expiry_used,
            "reason": f"ATM spread {best_spread:.2f}% > {SPREAD_PCT_THRESHOLD}% threshold",
        }
    except Exception as e:
        return {
            "symbol": symbol,
            "passed": False,
            "blocked_reason": "fetch_failed",
            "error": f"{type(e).__name__}: {e}",
        }


# ── universe-wide verification + state-file aggregation ─────────────────


def _previous_counts(previous_state: Optional[dict]) -> dict:
    """Pull consecutive_fail_counts from prior state file. Returns {} if missing."""
    if not previous_state:
        return {}
    return dict(previous_state.get("consecutive_fail_counts", {}))


def verify_all(
    universe: Iterable[str],
    previous_state: Optional[dict] = None,
    n_workers: int = VERIFY_WORKERS,
) -> dict:
    """Verify every symbol in universe. Aggregates into the state-file payload.

    Tracks consecutive fetch_failed counters across runs (read from
    previous_state). Symbols hitting 3 consecutive fetch_failed get their
    blocked_reason rewritten to 'permanent_block_3_strikes'.
    """
    universe = list(universe)
    prev_counts = _previous_counts(previous_state)
    new_counts: dict[str, int] = {}
    raw_results: dict[str, dict] = {}

    with ThreadPoolExecutor(max_workers=n_workers) as ex:
        futures = {ex.submit(verify_one, s): s for s in universe}
        for fut in as_completed(futures):
            sym = futures[fut]
            try:
                raw_results[sym] = fut.result()
            except Exception as e:
                raw_results[sym] = {
                    "symbol": sym,
                    "passed": False,
                    "blocked_reason": "fetch_failed",
                    "error": f"executor: {type(e).__name__}: {e}",
                }

    # Update consecutive fail counts and apply 3-strike escalation
    results: list[dict] = []
    n_passed = 0
    n_blocked = 0
    n_fetch_failed = 0
    n_permanent = 0
    for sym in universe:  # preserve input order
        r = raw_results.get(sym, {
            "symbol": sym,
            "passed": False,
            "blocked_reason": "fetch_failed",
            "error": "missing result",
        })
        if r.get("passed"):
            new_counts[sym] = 0
            n_passed += 1
        elif r.get("blocked_reason") == "fetch_failed":
            new_counts[sym] = prev_counts.get(sym, 0) + 1
            if new_counts[sym] >= PERMANENT_BLOCK_THRESHOLD:
                # Rewrite blocked_reason — escalate to permanent block
                r = dict(r)
                r["blocked_reason"] = "permanent_block_3_strikes"
                r["consecutive_fail_count"] = new_counts[sym]
                n_permanent += 1
            else:
                r = dict(r)
                r["consecutive_fail_count"] = new_counts[sym]
                n_fetch_failed += 1
        elif r.get("blocked_reason") == "spread_too_wide":
            new_counts[sym] = 0   # spread-failure resets the fetch counter
            n_blocked += 1
        else:
            # Any other blocked_reason (forward compatibility)
            new_counts[sym] = 0
            n_blocked += 1
        results.append(r)

    return {
        "verified_at": datetime.now().astimezone().isoformat(),
        "universe_size": len(universe),
        "n_passed": n_passed,
        "n_blocked": n_blocked,
        "n_fetch_failed": n_fetch_failed,
        "n_permanent_blocks": n_permanent,
        "results": results,
        "consecutive_fail_counts": new_counts,
    }


# ── state file I/O ───────────────────────────────────────────────────────


def write_status(payload: dict, path: Path) -> None:
    """Atomic write via temp file + rename."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=2, default=str)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise


def load_status(path: Path) -> Optional[dict]:
    """Return parsed state file, or None if missing/invalid/unreadable.
    Treats PermissionError the same as missing — caller falls back to
    fail-open (no prior state). Production cron runs as root and has access."""
    path = Path(path)
    try:
        if not path.exists():
            return None
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError, PermissionError) as e:
        logging.warning("spread_verifier: cannot load %s: %s", path, e)
        return None


# ── helper for scanner integration ───────────────────────────────────────


def blocked_symbols(state: Optional[dict]) -> set[str]:
    """Return the set of symbols the scanner should reject under F0.

    fetch_failed is EXCLUDED (fail-open semantics — a transient yfinance
    hiccup must not kill the universe). Only spread_too_wide and
    permanent_block_3_strikes block.
    """
    if not state or not state.get("results"):
        return set()
    return {
        r["symbol"] for r in state["results"]
        if not r.get("passed")
        and r.get("blocked_reason") in ("spread_too_wide", "permanent_block_3_strikes")
    }
