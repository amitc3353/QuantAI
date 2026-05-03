#!/usr/bin/env python3
"""Self-learning system dashboard collector.

Aggregates capability_requests/{agent}/*.json + trade_reviews/{agent}/*.md
into a single dashboard tile state. Reads learning_tracker.json to know
which items have been marked resolved and moves them out of `open_items`.

JSON contract (matches the rest of /var/dashboard/state/*):
  {"last_updated": ISO, "status": "ok|warning|stale|idle", "data": {...}}

Cron: */5 * * * *  cd /var/dashboard && python3 collect_learning.py
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, "/home/trader/QuantAI/v2/shared-data/scripts")

from _paths import (
    CAPABILITY_REQUESTS_DIR as REQUESTS_DIR,
    TRADE_REVIEWS_DIR as REVIEWS_DIR,
    WEEKLY_REPORTS_DIR as REPORTS_DIR,
    LEARNING_TRACKER as TRACKER,
    LEARNING_STATE as STATE,
)
from _decision_helpers import week_start_for as _week_start_for

ET = ZoneInfo("America/New_York")

AGENTS = ["agent_alpha", "agent_beta", "agent_gamma"]

# Priority ranking for sort order (lower = higher priority)
PRIORITY_RANK = {"critical": 0, "would_help": 1, "nice_to_have": 2}


def _slug(s: str) -> str:
    """Lowercase, replace non-alphanum runs with single hyphen."""
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-") or "item"


def _week_start(date_iso: str) -> str:
    """Monday of the week containing date_iso. Delegates to _decision_helpers."""
    return _week_start_for(date_iso)


def _read_json(p: Path) -> dict | None:
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def _load_capability_requests() -> list[dict]:
    """Return all gap items across all agents, one per gap (not yet grouped)."""
    items = []
    for agent in AGENTS:
        d = REQUESTS_DIR / agent
        if not d.exists():
            continue
        for fp in sorted(d.glob("*.json")):
            data = _read_json(fp)
            if not data:
                continue
            diagnosis = data.get("diagnosis") or {}
            gaps = diagnosis.get("gaps_identified") or []
            trade_id = data.get("trade_id") or fp.stem
            timestamp = data.get("timestamp") or ""
            for gap in gaps:
                items.append({
                    "_kind": "capability_request",
                    "agent": agent,
                    "dimension": gap.get("dimension"),
                    "request": gap.get("request") or "",
                    "evidence": gap.get("evidence") or "",
                    "priority": gap.get("priority") or "would_help",
                    "estimated_impact": gap.get("estimated_impact_dollars"),
                    "trade_id": trade_id,
                    "timestamp": timestamp,
                })
    return items


_REVIEW_PARAM_RE = re.compile(
    r"^- \*\*(?P<param>[^:*]+):\*\*\s*(?P<current>[^→]+?)\s*→\s*(?P<suggested>[^—]+?)\s*—\s*(?P<reason>.+)$",
    re.MULTILINE,
)


def _load_parameter_suggestions() -> list[dict]:
    """Extract parameter_suggestions from trade review markdowns.

    Format in the review.md is:
      ## Parameter Suggestions
      - **param_name:** current_value → suggested_value — reasoning
    """
    items = []
    for agent in AGENTS:
        d = REVIEWS_DIR / agent
        if not d.exists():
            continue
        for fp in sorted(d.glob("*.md")):
            try:
                text = fp.read_text()
            except Exception:
                continue
            trade_id = fp.stem
            # Use mtime as a proxy for when the review was written
            try:
                ts = datetime.fromtimestamp(fp.stat().st_mtime, tz=ET).isoformat()
            except Exception:
                ts = ""
            for m in _REVIEW_PARAM_RE.finditer(text):
                items.append({
                    "_kind": "parameter_suggestion",
                    "agent": agent,
                    "parameter": m.group("param").strip(),
                    "current": m.group("current").strip(),
                    "suggested": m.group("suggested").strip(),
                    "reason": m.group("reason").strip(),
                    "trade_id": trade_id,
                    "timestamp": ts,
                })
    return items


def _load_tracker() -> dict:
    if not TRACKER.exists():
        return {"resolved": {}}
    data = _read_json(TRACKER) or {}
    if "resolved" not in data:
        data["resolved"] = {}
    return data


def _stable_id(prefix: str, week_start: str, agent: str, key: str) -> str:
    """Generate a deterministic ID so resolutions persist across collector runs."""
    digest = hashlib.sha256(f"{prefix}|{week_start}|{agent}|{key}".encode()).hexdigest()[:6]
    return f"{prefix}-{week_start}-{agent}-{_slug(key)}-{digest}"


def _group_capability_items(raw: list[dict]) -> list[dict]:
    """Group raw gaps by (week, agent, dimension)."""
    groups = defaultdict(list)
    for it in raw:
        ws = _week_start(it.get("timestamp", ""))
        key = (ws, it["agent"], it.get("dimension") or "unknown")
        groups[key].append(it)

    out = []
    for (week_start, agent, dimension), gaps in groups.items():
        priorities = [g.get("priority") or "would_help" for g in gaps]
        # Pick worst priority (lowest rank number)
        worst = min(priorities, key=lambda p: PRIORITY_RANK.get(p, 1))
        impacts = [g.get("estimated_impact") for g in gaps if g.get("estimated_impact") is not None]
        total_impact = round(sum(impacts), 2) if impacts else None
        sample_request = gaps[0]["request"]
        # Title: first 60 chars of representative request
        title = (sample_request[:60] + "…") if len(sample_request) > 60 else sample_request
        # Summary: combine evidence
        summaries = list({g["evidence"] for g in gaps if g["evidence"]})[:3]
        if not summaries:
            summaries = list({g["request"] for g in gaps if g["request"]})[:3]
        summary = " | ".join(summaries)[:300]
        source_trades = sorted({g["trade_id"] for g in gaps if g.get("trade_id")})
        item = {
            "id": _stable_id("cap", week_start, agent, dimension),
            "date": week_start,
            "agent": agent,
            "type": "capability_request",
            "dimension": dimension,
            "title": title,
            "summary": summary,
            "frequency": len(gaps),
            "estimated_impact": total_impact,
            "priority": worst,
            "source_trades": source_trades,
            "status": "open",
        }
        out.append(item)
    return out


def _group_parameter_items(raw: list[dict]) -> list[dict]:
    """Group raw parameter suggestions by (week, agent, parameter)."""
    groups = defaultdict(list)
    for it in raw:
        ws = _week_start(it.get("timestamp", ""))
        key = (ws, it["agent"], it["parameter"])
        groups[key].append(it)

    out = []
    for (week_start, agent, parameter), sugs in groups.items():
        first = sugs[0]
        title = f"{parameter}: {first['current']} → {first['suggested']}"
        summary = " | ".join({s["reason"] for s in sugs})[:300]
        source_trades = sorted({s["trade_id"] for s in sugs if s.get("trade_id")})
        item = {
            "id": _stable_id("param", week_start, agent, parameter),
            "date": week_start,
            "agent": agent,
            "type": "parameter_suggestion",
            "dimension": None,
            "title": title[:100],
            "summary": summary,
            "frequency": len(sugs),
            "estimated_impact": None,
            "priority": "would_help",
            "source_trades": source_trades,
            "status": "open",
        }
        out.append(item)
    return out


def _sort_open(items: list[dict]) -> list[dict]:
    """Sort by priority asc, then frequency desc, then date desc."""
    return sorted(
        items,
        key=lambda i: (
            PRIORITY_RANK.get(i.get("priority") or "would_help", 1),
            -(i.get("frequency") or 0),
            -(i.get("estimated_impact") or 0),
            i.get("date") or "",
        ),
    )


def _stats(open_items: list[dict], resolved_items: list[dict]) -> dict:
    by_dim: dict[str, int] = defaultdict(int)
    by_agent: dict[str, int] = defaultdict(int)
    for it in open_items:
        if it.get("dimension"):
            by_dim[it["dimension"]] += 1
        if it.get("agent"):
            by_agent[it["agent"]] += 1
    return {
        "total_open": len(open_items),
        "total_resolved": len(resolved_items),
        "by_dimension": dict(by_dim),
        "by_agent": dict(by_agent),
    }


def _latest_weekly_report() -> str | None:
    if not REPORTS_DIR.exists():
        return None
    reports = sorted(REPORTS_DIR.glob("*_synthesis.md"))
    return reports[-1].name if reports else None


def main() -> int:
    cap_raw = _load_capability_requests()
    param_raw = _load_parameter_suggestions()
    tracker = _load_tracker()
    resolved_map = tracker.get("resolved", {})

    cap_items = _group_capability_items(cap_raw)
    param_items = _group_parameter_items(param_raw)
    all_items = cap_items + param_items

    open_items = []
    resolved_items = []
    for it in all_items:
        if it["id"] in resolved_map:
            entry = resolved_map[it["id"]]
            resolved_items.append({
                "id": it["id"],
                "agent": it["agent"],
                "type": it["type"],
                "title": it["title"],
                "resolved_date": entry.get("resolved_date") or "",
                "resolution_note": entry.get("resolution_note") or "",
            })
        else:
            open_items.append(it)

    # Also list resolutions whose original items have aged out (kept for history)
    for rid, entry in resolved_map.items():
        if any(r["id"] == rid for r in resolved_items):
            continue
        resolved_items.append({
            "id": rid,
            "agent": entry.get("agent", "?"),
            "type": entry.get("type", "?"),
            "title": entry.get("title", rid),
            "resolved_date": entry.get("resolved_date") or "",
            "resolution_note": entry.get("resolution_note") or "",
        })

    open_items = _sort_open(open_items)
    resolved_items.sort(key=lambda r: r.get("resolved_date") or "", reverse=True)

    # Status logic: idle if nothing, warning if many open, ok otherwise
    if not open_items and not resolved_items:
        status = "idle"
    elif len(open_items) > 10:
        status = "warning"
    else:
        status = "ok"

    payload = {
        "last_updated": datetime.now(ET).isoformat(),
        "status": status,
        "data": {
            "open_items": open_items,
            "resolved_items": resolved_items,
            "stats": _stats(open_items, resolved_items),
            "latest_weekly_report": _latest_weekly_report(),
        },
    }

    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    os.replace(tmp, STATE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
