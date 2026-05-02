#!/usr/bin/env python3
"""monthly_graph_refresh — invoke Claude Code headless to refresh graphify doc-side.

The post-commit graphify hook only does AST extraction. Markdown identity
files, skill docs, and runbooks need a full LLM-driven `/graphify --update`
to refresh their semantic edges. This script runs that monthly via cron.

On success: commits any graphify-out/ updates to local main.
On failure: posts a Discord #alerts message AND records an event in the
dashboard error catalog so it surfaces on the dashboard as an actionable
warning. The user can then run the update manually.

Cron: first Monday of each month at 6 AM UTC (1 AM ET — no trading conflicts).
  0 6 1-7 * 1  python3 .../monthly_graph_refresh.py

CLI: python3 monthly_graph_refresh.py [--dry-run]
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path("/home/trader/QuantAI")
LOG = Path("/root/quantai-v2/shared-data/logs/graph_refresh.log")
CLAUDE = "/home/trader/.npm-global/bin/claude"
TIMEOUT = 1800  # 30 min hard wall

# Auto-load .env so DISCORD_BOT_TOKEN, DISCORD_CHANNEL_ALERTS, ANTHROPIC_API_KEY are present
for _ef in [Path("/home/trader/QuantAI/.env"), Path("/root/quantai-v2/.env")]:
    if _ef.exists():
        for line in _ef.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                if not os.environ.get(k.strip()):
                    os.environ[k.strip()] = v.strip()
        break

DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
DISCORD_ALERTS_CH = os.environ.get("DISCORD_CHANNEL_ALERTS", "")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [graph_refresh] %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("graph_refresh")


def post_discord(msg: str) -> bool:
    if not DISCORD_BOT_TOKEN or not DISCORD_ALERTS_CH:
        log.warning("Discord token or channel not set; skipping post")
        return False
    try:
        import requests
        r = requests.post(
            f"https://discord.com/api/v10/channels/{DISCORD_ALERTS_CH}/messages",
            headers={
                "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
                "Content-Type": "application/json",
                "User-Agent": "DiscordBot (QuantAI graph_refresh, 1.0)",
            },
            json={"content": msg[:1900]},
            timeout=10,
        )
        return r.status_code in (200, 201, 204)
    except Exception as e:
        log.warning("Discord post failed: %s", e)
        return False


def record_dashboard_error(severity: str, summary: str, details: str) -> None:
    """Write to centralized error catalog so it surfaces on the dashboard."""
    try:
        sys.path.insert(0, "/var/dashboard")
        import lib_errors as L
        sig = f"graph_refresh|{summary}"
        sig_hash = hashlib.sha256(sig.encode()).hexdigest()[:16]
        message = f"{summary}\n\nLast log:\n{details[-1500:]}"
        with L.open_db() as conn:
            L.record_event(
                conn,
                source="graph_refresh",
                severity=severity,
                message=message,
                signature=sig[:240],
                signature_hash=sig_hash,
                catalog_id=None,
                runbook=None,
            )
    except Exception as e:
        log.warning("dashboard error catalog write failed: %s", e)


def commit_graphify_changes(stamp: datetime) -> tuple[bool, str]:
    """Stage and commit graphify-out updates if any. Returns (committed, summary)."""
    try:
        diff = subprocess.run(
            ["git", "status", "--short", "graphify-out/"],
            cwd=REPO, capture_output=True, text=True, timeout=30, check=False,
        ).stdout.strip()
        if not diff:
            return False, "no graphify-out changes"

        # Stage only the tracked + memory dir; skip cache/manifest/cost (gitignored)
        subprocess.run(
            ["git", "add",
             "graphify-out/GRAPH_REPORT.md",
             "graphify-out/graph.html",
             "graphify-out/graph.json",
             "graphify-out/memory/"],
            cwd=REPO, capture_output=True, text=True, timeout=30, check=False,
        )
        msg = f"chore(graphify): monthly --update refresh ({stamp.strftime('%Y-%m')})"
        commit = subprocess.run(
            ["git", "commit", "-m", msg],
            cwd=REPO, capture_output=True, text=True, timeout=60, check=False,
        )
        if commit.returncode != 0:
            return False, f"commit failed: {commit.stderr.strip()[:200]}"
        return True, msg
    except subprocess.TimeoutExpired:
        return False, "git operation timed out"
    except Exception as e:
        return False, f"commit error: {e}"


def run_graphify_update(dry_run: bool) -> tuple[int, str]:
    """Invoke claude -p with /graphify --update. Returns (exit_code, output)."""
    cmd = [
        CLAUDE,
        "-p",
        "Run `/graphify . --update` and report a one-line summary of what changed (nodes/edges/communities delta).",
        "--permission-mode", "bypassPermissions",
        "--max-budget-usd", "2.00",
        "--model", "claude-sonnet-4-6",
        "--output-format", "text",
        "--no-session-persistence",
    ]
    if dry_run:
        log.info("DRY RUN — would execute: %s", " ".join(cmd))
        return 0, "(dry-run — no actual call)"

    # Run as user trader's HOME so claude finds its config
    env = dict(os.environ)
    env.setdefault("HOME", "/home/trader")

    try:
        r = subprocess.run(
            cmd,
            cwd=REPO,
            env=env,
            timeout=TIMEOUT,
            capture_output=True,
            text=True,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return 124, f"claude -p timed out after {TIMEOUT}s"

    out = (r.stdout or "") + ("\n--- stderr ---\n" + r.stderr if r.stderr else "")
    return r.returncode, out


def append_log(stamp: datetime, exit_code: int, output: str) -> None:
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a") as f:
            f.write(f"\n=== run {stamp.isoformat()} (exit={exit_code}) ===\n")
            f.write(output)
            f.write("\n")
    except Exception as e:
        log.warning("log append failed: %s", e)


def main() -> int:
    parser = argparse.ArgumentParser(description="Monthly graphify --update via Claude Code headless")
    parser.add_argument("--dry-run", action="store_true",
                        help="Skip the actual claude invocation; show what would run")
    args = parser.parse_args()

    started = datetime.now(timezone.utc)
    log.info("start %s (dry_run=%s)", started.isoformat(), args.dry_run)

    exit_code, output = run_graphify_update(args.dry_run)
    append_log(started, exit_code, output)
    last_lines = "\n".join(output.splitlines()[-30:])

    if args.dry_run:
        log.info("dry-run complete")
        return 0

    if exit_code == 124:
        msg = (
            f"⏰ Monthly graphify --update **timed out** after {TIMEOUT}s. "
            f"Manual run needed: `cd {REPO} && claude -p '/graphify . --update'`"
        )
        post_discord(msg)
        record_dashboard_error("warning", "graphify --update timed out", last_lines)
        return 1

    if exit_code != 0:
        msg = (
            f"❌ Monthly graphify --update **FAILED** (exit {exit_code}). "
            f"Manual run needed: `cd {REPO} && claude -p '/graphify . --update'`\n"
            f"```{last_lines[-500:]}```"
        )
        post_discord(msg)
        record_dashboard_error("warning", f"graphify --update failed (exit {exit_code})", last_lines)
        return exit_code

    # Success — commit any graphify-out updates
    committed, summary = commit_graphify_changes(started)
    if committed:
        post_discord(
            f"✅ Monthly graphify refresh complete — committed: `{summary}`. "
            f"See graphify-out/GRAPH_REPORT.md for the new audit trail."
        )
    else:
        # Run succeeded but no commit — could mean graph already current, OR commit failed
        if "no graphify-out changes" in summary:
            post_discord("✅ Monthly graphify refresh ran. No graph changes (already current).")
        else:
            post_discord(
                f"⚠️ Monthly graphify refresh succeeded but **commit failed**: {summary}. "
                f"Inspect manually: `cd {REPO} && git status graphify-out/`"
            )
            record_dashboard_error("warning",
                                   f"graphify --update commit failed: {summary[:80]}",
                                   last_lines)
    log.info("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
