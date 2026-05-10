"""Per-arm state tracking for the Gamma 4-arm A/B/C/D ranking test.

Each arm runs as an independent virtual portfolio with $10K starting capital.
Per-arm state files at ``/root/quantai-v2/shared-data/cache/gamma_arm_<id>_account.json``
hold the running equity, cash, P&L, circuit breaker status, and experiment
progress for each arm.

Per-arm trade journals at
``/root/quantai-v2/shared-data/journal/paper/gamma_arm_<id>_trades.jsonl`` are
authoritative for analytics; the existing union ``trades.jsonl`` continues to
receive the same entries (with the additive ``arm_id`` field) for backwards
compatibility with sentinel, error_learner, weekly_synthesis, and the
dashboard collectors.

This module is commit 2 of the 5-commit phasing in
``docs/gamma-four-arm-ab-test-plan.md``. **No behavior change in production**
until commit 3 wires the per-arm dispatch and commit 5 flips the feature flag.

Public API
----------

* :func:`load_arm_state(arm_id)` → dict (creates default if missing)
* :func:`save_arm_state(arm_id, state)` — atomic temp+rename write
* :func:`init_arm_state(arm_id, starting_equity, ...)` — fresh $10K state
* :func:`init_all_arms(starting_equity)` — initialize all 4 arms + journal files
* :func:`next_arm_trade_id(arm_id, journal)` — Ga###/Gb###/Gc###/Gd### counter
* :func:`load_arm_journal(arm_id)` → list[dict]
* :func:`append_arm_trade(arm_id, trade)` — write to per-arm journal AND union
* :func:`arm_open_positions(arm_id)` → list[dict] (arm's OPEN trades)
* :func:`compute_experiment_day(state)` → int (days since experiment_started_at)
* :func:`reconcile(state, open_trades, threshold)` → (ok, details)
* :func:`reconcile_and_alert(arm_id, state, open_trades, post_discord=...)`
* :func:`reset_arm(arm_id, starting_equity, archive_dir)` — clean restart
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional


# ── Constants ──────────────────────────────────────────────────────────


VALID_ARM_IDS: tuple[str, ...] = ("a", "b", "c", "d")

ARM_TO_RANKER_NAME: dict[str, str] = {
    "a": "rsi_only",
    "b": "composite",
    "c": "weighted_blend",
    "d": "reward_risk_first",
}

DEFAULT_STARTING_EQUITY = 10_000.00

CACHE_DIR = Path("/root/quantai-v2/shared-data/cache")
JOURNAL_DIR = Path("/root/quantai-v2/shared-data/journal/paper")
UNION_JOURNAL_PATH = JOURNAL_DIR / "trades.jsonl"

# Reconciliation thresholds
RECONCILE_DOLLAR_THRESHOLD = 1.00  # alert if invariants drift by > $1


# ── Path helpers ──────────────────────────────────────────────────────


def _arm_state_path(arm_id: str, base_dir: Path = CACHE_DIR) -> Path:
    _validate_arm_id(arm_id)
    return base_dir / f"gamma_arm_{arm_id}_account.json"


def _arm_journal_path(arm_id: str, base_dir: Path = JOURNAL_DIR) -> Path:
    _validate_arm_id(arm_id)
    return base_dir / f"gamma_arm_{arm_id}_trades.jsonl"


def _validate_arm_id(arm_id: str) -> None:
    if arm_id not in VALID_ARM_IDS:
        raise ValueError(
            f"Invalid arm_id {arm_id!r}; expected one of {VALID_ARM_IDS}"
        )


# ── Time helpers ──────────────────────────────────────────────────────


def _now_iso() -> str:
    """ISO-8601 timestamp in local timezone (matches existing journal writes)."""
    return datetime.now().astimezone().isoformat()


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None


# ── State schema ──────────────────────────────────────────────────────


def _default_state(arm_id: str, starting_equity: float = DEFAULT_STARTING_EQUITY,
                    experiment_started_at: Optional[str] = None) -> dict:
    """Build a fresh state dict with all required fields populated.

    Used by init_arm_state() AND as the fallback when load_arm_state()
    finds a missing or malformed file. ``experiment_started_at`` is
    None until commit 5's feature flag flips and the experiment begins.
    """
    return {
        "arm_id": arm_id,
        "ranker_used": ARM_TO_RANKER_NAME[arm_id],
        "starting_equity": float(starting_equity),
        "current_equity": float(starting_equity),
        "cash": float(starting_equity),  # all cash on init; positions consume it
        "peak_equity": float(starting_equity),
        "drawdown_pct": 0.00,
        "total_realized_pnl": 0.00,
        "total_trades": 0,
        "winning_trades": 0,
        "losing_trades": 0,
        "consecutive_losses": 0,
        "circuit_breaker_active": False,
        "circuit_breaker_until": None,
        "last_trade_close_ts": None,
        "last_updated": _now_iso(),
        "experiment_started_at": experiment_started_at,
        "experiment_day": 0,
    }


# ── State load / save (atomic) ────────────────────────────────────────


def load_arm_state(arm_id: str, base_dir: Path = CACHE_DIR) -> dict:
    """Read state file. Returns a fresh default dict if the file is missing
    or corrupt — the caller shouldn't crash just because the experiment
    hasn't been initialized yet. Logs a warning on parse failure."""
    _validate_arm_id(arm_id)
    path = _arm_state_path(arm_id, base_dir)
    try:
        if not path.exists():
            return _default_state(arm_id)
        with open(path) as f:
            data = json.load(f)
        # Compute experiment_day on read (don't trust stored value)
        data["experiment_day"] = compute_experiment_day(data)
        return data
    except (json.JSONDecodeError, OSError, PermissionError) as e:
        logging.warning(
            "arm_state: cannot load %s (%s); falling back to default",
            path, e,
        )
        return _default_state(arm_id)


def save_arm_state(arm_id: str, state: dict, base_dir: Path = CACHE_DIR) -> None:
    """Atomic write via tempfile + os.replace. Same pattern as
    ``gamma_spread_status.json``. Updates ``last_updated`` automatically."""
    _validate_arm_id(arm_id)
    path = _arm_state_path(arm_id, base_dir)
    state["last_updated"] = _now_iso()
    state["experiment_day"] = compute_experiment_day(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=path.parent, prefix=path.name + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(state, f, indent=2, default=str)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ── Initialization ────────────────────────────────────────────────────


def init_arm_state(arm_id: str,
                   starting_equity: float = DEFAULT_STARTING_EQUITY,
                   experiment_started_at: Optional[str] = None,
                   base_dir: Path = CACHE_DIR) -> dict:
    """Create a fresh state file at $10K (or specified) starting equity.
    Idempotent: overwrites any existing file. Raises ``ValueError`` for
    invalid ``arm_id`` (must be one of a/b/c/d)."""
    _validate_arm_id(arm_id)
    state = _default_state(arm_id, starting_equity, experiment_started_at)
    save_arm_state(arm_id, state, base_dir)
    return state


def init_all_arms(starting_equity: float = DEFAULT_STARTING_EQUITY,
                   experiment_started_at: Optional[str] = None,
                   cache_dir: Path = CACHE_DIR,
                   journal_dir: Path = JOURNAL_DIR) -> dict:
    """Initialize all 4 arms' state files + create empty journal files.
    Idempotent. Returns a dict {arm_id: state}.

    Used as part of pre-flight checklist (item 5 in plan §K) before flipping
    the GAMMA_AB_TEST_ENABLED feature flag in commit 5."""
    journal_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    states = {}
    for arm_id in VALID_ARM_IDS:
        states[arm_id] = init_arm_state(
            arm_id, starting_equity, experiment_started_at, base_dir=cache_dir
        )
        # Create empty journal file (touch — preserves any existing content)
        journal_path = _arm_journal_path(arm_id, journal_dir)
        if not journal_path.exists():
            journal_path.touch()
    return states


# ── Experiment day computation ────────────────────────────────────────


def compute_experiment_day(state: dict) -> int:
    """Days since experiment_started_at. Returns 0 if not yet started."""
    started = _parse_iso(state.get("experiment_started_at"))
    if started is None:
        return 0
    now = _now_utc()
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    delta = now - started
    return max(0, delta.days)


# ── Per-arm journal access ────────────────────────────────────────────


def load_arm_journal(arm_id: str,
                      base_dir: Path = JOURNAL_DIR) -> list[dict]:
    """Read per-arm journal as list of dicts. Returns empty list on missing
    file. Lines that fail to parse are skipped with a warning."""
    _validate_arm_id(arm_id)
    path = _arm_journal_path(arm_id, base_dir)
    if not path.exists():
        return []
    out: list[dict] = []
    try:
        for i, line in enumerate(path.read_text().splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as e:
                logging.warning(
                    "arm_state: skipping malformed line %d in %s: %s",
                    i + 1, path, e,
                )
    except (OSError, PermissionError) as e:
        logging.warning("arm_state: cannot read %s: %s", path, e)
    return out


def append_arm_trade(arm_id: str, trade: dict,
                      journal_dir: Path = JOURNAL_DIR) -> None:
    """Append a trade to BOTH the per-arm journal AND the union trades.jsonl.
    The trade dict must include ``arm_id`` (we set it if not present).

    Atomicity: each write is a single ``f.write(line + "\\n")`` call. JSONL
    line-level atomicity is what existing tools (sentinel, dashboard
    collectors) already rely on, so we match that contract.
    """
    _validate_arm_id(arm_id)
    if trade.get("arm_id") != arm_id:
        trade["arm_id"] = arm_id
    journal_dir.mkdir(parents=True, exist_ok=True)
    line = json.dumps(trade, default=str) + "\n"
    arm_path = _arm_journal_path(arm_id, journal_dir)
    with open(arm_path, "a") as f:
        f.write(line)
    # Union journal — same line, kept additive for backwards compat
    union_path = journal_dir / "trades.jsonl"
    with open(union_path, "a") as f:
        f.write(line)


def arm_open_positions(arm_id: str,
                        journal_dir: Path = JOURNAL_DIR) -> list[dict]:
    """All trades in this arm's journal with status=='OPEN'."""
    journal = load_arm_journal(arm_id, journal_dir)
    return [t for t in journal if (t.get("status") or "").upper() == "OPEN"]


# ── Trade ID generation (per-arm counter) ─────────────────────────────


def next_arm_trade_id(arm_id: str, journal: list[dict]) -> str:
    """Generate next sequential per-arm trade ID: Ga001, Gb001, Gc001, Gd001.

    Each arm's counter is independent. Reads the journal (typically loaded
    by ``load_arm_journal()``) to find the max existing counter and
    increments. Counter is 0-padded to 3 digits — a single arm hitting 999
    trades is well beyond the 80-trade-floor / 180-day-cap of the test, so
    the format is sufficient.

    Format: ``G{arm_letter}{NNN}``. Pre-experiment trades with id ``G001``,
    ``G002``, ``G003`` (no arm letter) are NOT counted toward the per-arm
    counter; they exist in the legacy single-Gamma series and are handled
    by ``test_legacy_gamma_trades_still_parseable``.
    """
    _validate_arm_id(arm_id)
    prefix = f"G{arm_id}"
    max_n = 0
    for t in journal:
        tid = (t.get("id") or "")
        if tid.startswith(prefix):
            suffix = tid[len(prefix):]
            if suffix.isdigit():
                try:
                    max_n = max(max_n, int(suffix))
                except ValueError:
                    continue
    return f"{prefix}{max_n + 1:03d}"


# ── Reconciliation ────────────────────────────────────────────────────


def reconcile(state: dict, open_trades: list[dict],
              threshold: float = RECONCILE_DOLLAR_THRESHOLD) -> tuple[bool, dict]:
    """Verify state-file invariants. Returns ``(ok, details)``.

    Two invariants checked:

    1. **Equity-vs-realized**: ``current_equity ≈ starting_equity +
       total_realized_pnl`` (within ``threshold``). Catches state drift
       where realized P&L was logged but equity not updated, or vice versa.

    2. **Cash-vs-positions**: ``cash + sum(open_trade max_risk) ≈
       current_equity`` (within ``threshold``). Catches the scenario where
       cash was decremented at entry but the trade entry never fully wrote
       to the journal (or the journal entry was missed).

    ``details`` includes the computed values + the deltas for either
    invariant that's broken; an empty dict if both pass.
    """
    starting = float(state.get("starting_equity") or 0)
    current = float(state.get("current_equity") or 0)
    realized = float(state.get("total_realized_pnl") or 0)
    cash = float(state.get("cash") or 0)
    open_max_risk = sum(float(t.get("max_risk") or 0) for t in open_trades)

    invariant_1_expected = starting + realized
    invariant_1_delta = current - invariant_1_expected

    invariant_2_expected = current
    invariant_2_actual = cash + open_max_risk
    invariant_2_delta = invariant_2_actual - invariant_2_expected

    ok_1 = abs(invariant_1_delta) <= threshold
    ok_2 = abs(invariant_2_delta) <= threshold

    if ok_1 and ok_2:
        return True, {}

    return False, {
        "arm_id": state.get("arm_id"),
        "starting_equity": starting,
        "current_equity": current,
        "total_realized_pnl": realized,
        "cash": cash,
        "open_trades_count": len(open_trades),
        "open_max_risk_sum": round(open_max_risk, 2),
        "invariant_1_equity_vs_realized": {
            "expected": round(invariant_1_expected, 2),
            "actual": round(current, 2),
            "delta": round(invariant_1_delta, 2),
            "ok": ok_1,
        },
        "invariant_2_cash_vs_positions": {
            "expected": round(invariant_2_expected, 2),
            "actual": round(invariant_2_actual, 2),
            "delta": round(invariant_2_delta, 2),
            "ok": ok_2,
        },
        "threshold_dollars": threshold,
    }


def reconcile_and_alert(arm_id: str, state: dict, open_trades: list[dict],
                         post_discord: Optional[Callable[[str], None]] = None,
                         threshold: float = RECONCILE_DOLLAR_THRESHOLD,
                         ) -> tuple[bool, dict]:
    """Reconcile + log + Discord alert on failure.

    ``post_discord`` is a callable accepting a single ``str`` message.
    Production code injects ``gamma_agent._post_discord`` (or equivalent);
    tests inject a MagicMock to verify the alert fires.

    The alert message is the operator-facing string; the full ``details``
    dict is logged at ERROR level for forensic review.
    """
    _validate_arm_id(arm_id)
    ok, details = reconcile(state, open_trades, threshold)
    if ok:
        return True, details

    msg_lines = [
        f"⚠️ Gamma Arm {arm_id.upper()} reconciliation drift detected",
    ]
    inv1 = details.get("invariant_1_equity_vs_realized", {})
    inv2 = details.get("invariant_2_cash_vs_positions", {})
    if not inv1.get("ok", True):
        msg_lines.append(
            f"  • equity vs realized: expected ${inv1['expected']:.2f}, "
            f"got ${inv1['actual']:.2f} (Δ ${inv1['delta']:+.2f})"
        )
    if not inv2.get("ok", True):
        msg_lines.append(
            f"  • cash + open_max_risk vs equity: expected "
            f"${inv2['expected']:.2f}, got ${inv2['actual']:.2f} "
            f"(Δ ${inv2['delta']:+.2f}, {details['open_trades_count']} open trades)"
        )
    msg_lines.append(
        f"  threshold ${threshold:.2f} — investigate before next trade entry"
    )
    msg = "\n".join(msg_lines)

    logging.error("arm_state reconciliation failed: %s", details)
    if post_discord:
        try:
            post_discord(msg)
        except Exception as e:
            logging.warning(
                "arm_state: post_discord callback raised: %s", e
            )
    return False, details


# ── Reset (clean restart, archive old) ────────────────────────────────


def reset_arm(arm_id: str,
              starting_equity: float = DEFAULT_STARTING_EQUITY,
              archive_dir: Optional[Path] = None,
              cache_dir: Path = CACHE_DIR,
              journal_dir: Path = JOURNAL_DIR) -> dict:
    """Clean restart for one arm: zero state, archive old journal+state.

    Used by the ``--reset-experiment`` operator command (added in commit 3)
    when a ranker bug requires the test to restart at day 0. Archives
    preserve forensic data; never delete the journal.

    Returns the fresh state dict.
    """
    _validate_arm_id(arm_id)
    if archive_dir is None:
        archive_dir = journal_dir / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")

    # Archive existing state
    state_path = _arm_state_path(arm_id, cache_dir)
    if state_path.exists():
        archived = archive_dir / f"gamma_arm_{arm_id}_account_{timestamp}.json"
        archived.write_bytes(state_path.read_bytes())

    # Archive existing journal (move, not copy — fresh journal will be empty)
    journal_path = _arm_journal_path(arm_id, journal_dir)
    if journal_path.exists() and journal_path.stat().st_size > 0:
        archived_journal = archive_dir / f"gamma_arm_{arm_id}_trades_{timestamp}.jsonl"
        archived_journal.write_bytes(journal_path.read_bytes())
        journal_path.write_text("")  # truncate

    return init_arm_state(arm_id, starting_equity, base_dir=cache_dir)
