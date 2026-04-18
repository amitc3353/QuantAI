#!/usr/bin/env python3
"""
QuantAI Error Catalog — manual add CLI.

Append a new entry to docs/error-catalog.json. Atomic write with .bak backup.

Usage:
  sudo python3 add_error.py \\
      --id alpaca-rate-limit \\
      --pattern "429 Too Many Requests" \\
      --category recurring \\
      --severity high \\
      --auto-action retry \\
      --runbook runbooks/runbook-alpaca-rate-limit.md \\
      --description "Alpaca API returned 429 — back off and retry."
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
CATALOG_PATH = Path("/home/trader/QuantAI/docs/error-catalog.json")

VALID_CATEGORY = {"recurring", "novel", "transient"}
VALID_SEVERITY = {"critical", "high", "medium", "low", "info", "unknown"}
VALID_AUTO = {"none", "retry", "skip", "restart_service"}


def parse_args():
    p = argparse.ArgumentParser(description="Add an entry to error-catalog.json")
    p.add_argument("--id", required=True, help="Short unique id, e.g. alpaca-rate-limit")
    p.add_argument("--pattern", required=True, help="Substring (or regex with --regex) to match in log lines")
    p.add_argument("--regex", action="store_true", help="Treat --pattern as a regex")
    p.add_argument("--category", required=True, choices=sorted(VALID_CATEGORY))
    p.add_argument("--severity", default="unknown", choices=sorted(VALID_SEVERITY))
    p.add_argument("--auto-action", dest="auto_action", default="none", choices=sorted(VALID_AUTO))
    p.add_argument("--retry-command", dest="retry_command", default="",
                   help="Shell command to run when auto-action=retry")
    p.add_argument("--restart-target", dest="restart_target", default="",
                   help="systemd unit name when auto-action=restart_service")
    p.add_argument("--runbook", default="", help="Relative path under docs/ to the runbook markdown")
    p.add_argument("--description", required=True, help="One-line human description of the error")
    p.add_argument("--source", default="manual", help="Provenance tag (default: manual)")
    p.add_argument("--force", action="store_true", help="Overwrite existing id (default: fail)")
    return p.parse_args()


def main():
    args = parse_args()

    if not CATALOG_PATH.exists():
        print(f"catalog not found: {CATALOG_PATH}", file=sys.stderr)
        sys.exit(1)

    cat = json.loads(CATALOG_PATH.read_text())
    entries = cat.get("errors", [])
    by_id = {e["id"]: e for e in entries}

    if args.id in by_id and not args.force:
        print(f"id '{args.id}' already exists. Use --force to overwrite.", file=sys.stderr)
        sys.exit(2)

    if args.auto_action == "retry" and not args.retry_command:
        print("WARN: auto-action=retry without --retry-command — detector will defer to next cron cycle.", file=sys.stderr)
    if args.auto_action == "restart_service" and not args.restart_target:
        print("ERROR: auto-action=restart_service requires --restart-target.", file=sys.stderr)
        sys.exit(3)

    today = datetime.now(ET).strftime("%Y-%m-%d")
    entry = {
        "id": args.id,
        "pattern": args.pattern,
        "is_regex": bool(args.regex),
        "category": args.category,
        "severity": args.severity,
        "auto_action": args.auto_action,
        "description": args.description,
        "runbook": args.runbook,
        "first_seen": today,
        "last_seen": today,
        "occurrence_count": 0,
        "source": args.source,
    }
    if args.retry_command:
        entry["retry_command"] = args.retry_command
    if args.restart_target:
        entry["restart_target"] = args.restart_target

    if args.id in by_id:
        # Preserve historic counters when overwriting.
        existing = by_id[args.id]
        entry["first_seen"] = existing.get("first_seen", today)
        entry["occurrence_count"] = existing.get("occurrence_count", 0)
        entries = [e for e in entries if e["id"] != args.id]

    entries.append(entry)
    cat["errors"] = entries
    cat["last_updated"] = datetime.now(ET).isoformat(timespec="seconds")

    # Atomic write with .bak
    bak = CATALOG_PATH.with_suffix(".json.bak")
    bak.write_bytes(CATALOG_PATH.read_bytes())
    tmp = CATALOG_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cat, indent=2))
    os.replace(tmp, CATALOG_PATH)

    print(f"OK — catalog now has {len(entries)} entries. Backup: {bak}")


if __name__ == "__main__":
    main()
