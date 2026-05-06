#!/usr/bin/env python3
"""scheduled_cron_reenable.py — one-shot cron re-enable + skip-list installer.

Scheduled via `at` to fire 2026-05-07 10:00 ET (14:00 UTC), 30 minutes after
the morning close attempt. Two responsibilities:

  1. Strip the "# HALTED 2026-05-05 INTC-mismatch-investigation: " prefix from
     the 9 trading-cron lines that were halted on 2026-05-05.
  2. Create the position_monitor skip-list config so position_monitor leaves
     A018 and A020 alone while we wait for the May 15 expiry to clear them.

The skip list auto-expires after 2026-05-15 20:30 UTC (post-expiration close).
After that date, position_monitor returns to normal behavior even if the
config file is still present.

A backup copy of the crontab is written to
    /root/quantai-v2/shared-data/cache/crontab-pre-reenable-2026-05-07.txt
before any modification, so the rollback is `sudo crontab <file>`.

Posts a Discord summary to #system-health on completion.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

LOG_PATH = Path("/root/quantai-v2/shared-data/logs/scheduled_cron_reenable.log")
SCRIPT_DIR = Path("/home/trader/QuantAI/v2/shared-data/scripts")
CACHE_DIR = Path("/root/quantai-v2/shared-data/cache")
SKIP_LIST_PATH = CACHE_DIR / "position_monitor_skip_trades.json"
CRONTAB_BACKUP = CACHE_DIR / "crontab-pre-reenable-2026-05-07.txt"

HALT_PREFIX = "# HALTED 2026-05-05 INTC-mismatch-investigation: "

# Trades to add to the skip list; expires after May 15 expiration settles
SKIP_TRADE_IDS = ["A018", "A020"]
SKIP_EXPIRES_AFTER = "2026-05-15T20:30:00Z"
SKIP_REASON = (
    "Holding to May 15 INTC expiration per Option C — IBKR paper combo orders "
    "did not fill at any reasonable limit on 2026-05-06 or 2026-05-07. "
    "position_monitor must not attempt to close these trades."
)


def setup_logging():
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(message)s",
        handlers=[logging.FileHandler(LOG_PATH, mode="a"), logging.StreamHandler()],
    )


def load_env():
    for ef in [Path("/home/trader/QuantAI/.env"), Path("/root/quantai-v2/.env")]:
        if not ef.exists():
            continue
        for line in ef.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                if not os.environ.get(k.strip()):
                    os.environ[k.strip()] = v.strip()
        return


def post_discord(message: str):
    try:
        sys.path.insert(0, str(SCRIPT_DIR))
        from _discord import post_to_channel
        channel = os.environ.get("DISCORD_CHANNEL_SYSTEM_HEALTH") \
            or os.environ.get("DISCORD_CHANNEL_ALERTS")
        if channel:
            post_to_channel(channel, message)
    except Exception as e:
        logging.warning("Discord post failed: %s", e)


def write_skip_list():
    """Create the position_monitor skip-list config (atomic write)."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "skip_trade_ids": SKIP_TRADE_IDS,
        "skip_reason": SKIP_REASON,
        "expires_after": SKIP_EXPIRES_AFTER,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "created_by": "scheduled_cron_reenable.py",
    }
    tmp = SKIP_LIST_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    os.replace(tmp, SKIP_LIST_PATH)
    logging.info("  wrote skip list: %s (%d trades)", SKIP_LIST_PATH, len(SKIP_TRADE_IDS))


def backup_crontab() -> str:
    """Save the current root crontab to a file. Returns the content as a string."""
    rc = subprocess.run(["sudo", "crontab", "-l"], capture_output=True, text=True)
    if rc.returncode != 0:
        raise RuntimeError(f"crontab -l failed (rc={rc.returncode}): {rc.stderr}")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    CRONTAB_BACKUP.write_text(rc.stdout)
    logging.info("  backed up crontab to %s (%d lines)",
                 CRONTAB_BACKUP, rc.stdout.count("\n"))
    return rc.stdout


def reenable_lines(crontab: str) -> tuple[str, list[str]]:
    """Strip the HALT prefix from any line starting with it. Returns (new_text, changed_lines)."""
    lines = crontab.splitlines(keepends=True)
    changed = []
    out_lines = []
    for ln in lines:
        if ln.startswith(HALT_PREFIX):
            new_ln = ln[len(HALT_PREFIX):]
            out_lines.append(new_ln)
            changed.append(new_ln.rstrip("\n"))
        else:
            out_lines.append(ln)
    return "".join(out_lines), changed


def install_crontab(new_text: str) -> None:
    """Pipe new_text through `sudo crontab -`."""
    rc = subprocess.run(
        ["sudo", "crontab", "-"],
        input=new_text, text=True, capture_output=True,
    )
    if rc.returncode != 0:
        raise RuntimeError(f"crontab install failed (rc={rc.returncode}): {rc.stderr}")
    logging.info("  installed new crontab")


def main() -> int:
    setup_logging()
    load_env()
    logging.info("scheduled_cron_reenable.py start")

    try:
        # Step 1: write the skip list FIRST so position_monitor sees it
        # by the time the */2 trading-window cron fires
        write_skip_list()

        # Step 2: back up + edit + install crontab
        old_crontab = backup_crontab()
        new_crontab, changed = reenable_lines(old_crontab)

        if not changed:
            msg = ("🟡 scheduled_cron_reenable: no halted lines found in crontab "
                   "— maybe already re-enabled? skip list still installed.")
            logging.info(msg)
            post_discord(msg)
            return 0

        install_crontab(new_crontab)

        # Step 3: verify by reading back
        rc = subprocess.run(["sudo", "crontab", "-l"], capture_output=True, text=True)
        still_halted = sum(1 for ln in rc.stdout.splitlines() if ln.startswith(HALT_PREFIX))
        if still_halted:
            msg = (f"🔴 scheduled_cron_reenable: ran but {still_halted} HALTED lines "
                   f"remain in crontab. Backup at {CRONTAB_BACKUP}. Manual review needed.")
            logging.error(msg)
            post_discord(msg)
            return 1

        msg = (
            f"🟢 scheduled_cron_reenable: {len(changed)} trading crons re-enabled at "
            f"{datetime.now().strftime('%H:%M %Z')}. "
            f"position_monitor skip list installed (A018, A020). "
            f"Backup: {CRONTAB_BACKUP}"
        )
        logging.info(msg)
        for c in changed:
            logging.info("    re-enabled: %s", c[:120])
        post_discord(msg)
        return 0

    except Exception as e:
        logging.exception("scheduled_cron_reenable failed")
        post_discord(f"🔴 scheduled_cron_reenable: failed — {type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
