#!/usr/bin/env python3
"""Phase 0 diagnostic — classify historical PHANTOM_NEVER_FILLED records by origin.

READ-ONLY. Scans the trade journal and bins every phantom entry into one of five
origin classes so we know where in the lifecycle to harden first:

    O1_NEVER_SUBMITTED       order_id empty/missing            (agent crashed pre-broker)
    O2_REJECTED_AT_BROKER    fill_status in terminal-failure   (margin/contract/IBKR err)
    O3_STUCK_INDETERMINATE   fill_status indeterminate, aged   (poll exited early)
    O4_OPEN_THEN_VANISHED    had a fill, then escalated        (post-fill drift)
    O5_CLOSE_FAILURE         close-side failure resurrected it (exit, not entry)

Usage:
    python3 classify_phantoms.py
    python3 classify_phantoms.py --journal /path/to/trades.jsonl
    QUANTAI_JOURNAL=/path/to/trades.jsonl python3 classify_phantoms.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Optional

DEFAULT_JOURNAL = "/root/quantai-v2/shared-data/journal/paper/trades.jsonl"

TERMINAL_FAILURE_STATUSES = {
    "cancelled", "canceled", "apicancelled", "apicanceled",
    "rejected", "inactive",
}
INDETERMINATE_STATUSES = {
    "submitted", "presubmitted", "pendingsubmit", "pendingcancel",
    "apipending",
}

PHANTOM_STATUS = "PHANTOM_NEVER_FILLED"

CLASSES = [
    "O1_NEVER_SUBMITTED",
    "O2_REJECTED_AT_BROKER",
    "O3_STUCK_INDETERMINATE",
    "O4_OPEN_THEN_VANISHED",
    "O5_CLOSE_FAILURE",
    "O0_UNCLASSIFIED",
]


def load_journal(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    out = []
    with open(path) as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                out.append(json.loads(raw))
            except json.JSONDecodeError:
                continue
    return out


def _parse_ts(value) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def classify(record: dict) -> str:
    """Bin a single phantom record into one of the origin classes."""
    close_reason = (record.get("close_reason") or "").lower()
    if record.get("working_close_order_id") or "close" in close_reason:
        return "O5_CLOSE_FAILURE"

    if record.get("fill_confirmed_at") or record.get("filled_qty", 0) > 0:
        return "O4_OPEN_THEN_VANISHED"

    if not record.get("order_id"):
        return "O1_NEVER_SUBMITTED"

    fill_status = (record.get("fill_status") or "").lower()
    if fill_status in TERMINAL_FAILURE_STATUSES:
        return "O2_REJECTED_AT_BROKER"
    if fill_status in INDETERMINATE_STATUSES:
        return "O3_STUCK_INDETERMINATE"
    # order_id present + no recognised fill_status = ack landed but final status
    # was lost or never reported. Treat as a stuck-indeterminate variant: the
    # FSM's typed SubmitResult.UNKNOWN bucket will replace this hole.
    if fill_status in ("", "unknown", "none"):
        return "O3_STUCK_INDETERMINATE"

    return "O0_UNCLASSIFIED"


def _age_hours(record: dict) -> Optional[float]:
    entry = _parse_ts(record.get("timestamp"))
    exit_ = (
        _parse_ts(record.get("close_timestamp"))
        or _parse_ts(record.get("phantom_escalated_at"))
        or datetime.now(timezone.utc)
    )
    if entry is None:
        return None
    delta = exit_ - entry
    return round(delta.total_seconds() / 3600.0, 1)


def summarize(records: list[dict]) -> dict:
    phantoms = [r for r in records if r.get("status") == PHANTOM_STATUS]
    bins: dict[str, list[dict]] = defaultdict(list)
    for r in phantoms:
        bins[classify(r)].append(r)
    return {"total_records": len(records), "phantoms": phantoms, "bins": bins}


def _example_row(r: dict) -> str:
    tid = r.get("id", "?")
    source = r.get("source") or r.get("strategy") or "?"
    age = _age_hours(r)
    age_s = f"{age}h" if age is not None else "?"
    fs = r.get("fill_status") or ""
    has_oid = "Y" if r.get("order_id") else "N"
    has_wco = "Y" if r.get("working_close_order_id") else "N"
    return f"  {tid:<8} src={source:<22} age={age_s:<7} fs={fs:<14} oid={has_oid} wco={has_wco}"


def render(summary: dict, max_examples: int = 5) -> str:
    total = summary["total_records"]
    phantoms = summary["phantoms"]
    bins = summary["bins"]
    n_ph = len(phantoms)

    lines: list[str] = []
    lines.append("=" * 70)
    lines.append("PHANTOM ORIGIN CLASSIFICATION")
    lines.append("=" * 70)
    lines.append(f"Journal records scanned: {total}")
    lines.append(f"PHANTOM_NEVER_FILLED records: {n_ph}")
    lines.append("")

    if n_ph == 0:
        lines.append("No phantoms found. Nothing to classify.")
        return "\n".join(lines)

    lines.append(f"{'CLASS':<24} {'COUNT':>6} {'PCT':>7}")
    lines.append("-" * 40)
    counts = Counter({cls: len(bins.get(cls, [])) for cls in CLASSES})
    for cls in CLASSES:
        c = counts[cls]
        pct = (100.0 * c / n_ph) if n_ph else 0.0
        lines.append(f"{cls:<24} {c:>6} {pct:>6.1f}%")

    lines.append("")
    lines.append("REPRESENTATIVE EXAMPLES (up to %d per class):" % max_examples)
    lines.append("")
    for cls in CLASSES:
        records = bins.get(cls, [])
        if not records:
            continue
        lines.append(f"[{cls}]")
        for r in records[:max_examples]:
            lines.append(_example_row(r))
        lines.append("")

    lines.append("DECISION RECOMMENDATION")
    lines.append("-" * 40)
    lines.append(_recommend(counts))
    lines.append("")
    return "\n".join(lines)


def _recommend(counts: Counter) -> str:
    if sum(counts.values()) == 0:
        return "No phantoms — no action needed."
    top = counts.most_common(1)[0]
    cls, n = top
    msg = {
        "O1_NEVER_SUBMITTED": (
            "Strongest guard belongs at PROPOSED → SUBMIT_PENDING: the agent "
            "crashed between coid mint and broker dispatch. Persist coid + "
            "intent BEFORE calling place_mleg_order so a crash leaves a "
            "recoverable record."
        ),
        "O2_REJECTED_AT_BROKER": (
            "Strongest guard belongs at SUBMIT_PENDING → REJECTED: the broker "
            "rejected orders. Pre-flight validation (margin, contract sanity, "
            "duplicate-symbol checks) should fire before submission, and "
            "rejections must terminate cleanly without arm-cash drift."
        ),
        "O3_STUCK_INDETERMINATE": (
            "Strongest guard belongs at ACKED → FILLED polling: orders ACKed "
            "but never reached terminal. Tighten ENTRY_POLL_SECONDS and add a "
            "per-state 15min deadline that auto-cancels via broker.cancel_order "
            "instead of letting the 3h global timer fire."
        ),
        "O4_OPEN_THEN_VANISHED": (
            "Strongest guard belongs at FILLED → OPEN reconciliation: fills "
            "landed but positions vanished. Add post-fill positions() check "
            "within 5min, separate PHANTOM_VANISHED terminal state, alert "
            "operator (could be early-close or broker callback race)."
        ),
        "O5_CLOSE_FAILURE": (
            "Strongest guard belongs at EXIT_SUBMITTED → CLOSED path: closes "
            "are failing. Match auto_heal suppressed_patterns/recurring-3c6683b1; "
            "treat close as first-class state with retry budget and EXIT_ACKED "
            "30min escalation."
        ),
    }.get(cls, "Mixed distribution — guards needed on multiple transitions.")
    return f"Dominant class: {cls} ({n} of {sum(counts.values())}). {msg}"


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--journal",
        default=os.environ.get("QUANTAI_JOURNAL", DEFAULT_JOURNAL),
        help="Path to trades.jsonl (default: %(default)s)",
    )
    ap.add_argument(
        "--max-examples",
        type=int,
        default=5,
        help="Examples per class to print (default: 5)",
    )
    args = ap.parse_args(argv)

    records = load_journal(args.journal)
    summary = summarize(records)
    sys.stdout.write(render(summary, max_examples=args.max_examples))
    return 0


if __name__ == "__main__":
    sys.exit(main())
