"""
cto_listener.py — Dockerized CTO Task Runner
=============================================
Replaces scripts/cto_listener.sh (fragile host-side bash script).

HOW IT WORKS:
  1. Polls data/cto_queue.json every 2 seconds for status=pending
  2. Runs Claude Code CLI (claude --print) for each task
  3. Posts results back to Discord via the webhook stored in the task
  4. Marks task as done/failed in the queue file
  5. Logs everything to data/logs/cto_*.log

QUEUE FORMAT (matches chat_agent.py handle_cto_task):
  {
    "task": "check orchestrator logs for errors",
    "webhook": "https://discord.com/api/webhooks/...",
    "timestamp": "2026-03-24T10:00:00",
    "status": "pending"   // pending → running → done/failed
  }

WHY DOCKER OVER BASH:
  - restart: unless-stopped → survives reboots, no crontab needed
  - No PID file management
  - Proper logging via docker compose logs cto-listener
  - Error handling that doesn't die silently
  - Same lifecycle as all other QuantAI containers
"""

import json
import os
import subprocess
import time
import logging
import traceback
from datetime import datetime, timezone
from pathlib import Path

import requests

# ─── Configuration ───────────────────────────────────────────────────────

QUEUE_FILE = Path(os.getenv("CTO_QUEUE_FILE", "/app/data/cto_queue.json"))
LOG_DIR = Path(os.getenv("CTO_LOG_DIR", "/app/data/logs"))
PROJECT_DIR = Path(os.getenv("CTO_PROJECT_DIR", "/app/project"))
CLAUDE_MD = PROJECT_DIR / "CLAUDE.md"

WEBHOOK_SYSTEM = os.getenv("DISCORD_WEBHOOK_SYSTEM", "")
POLL_INTERVAL = int(os.getenv("CTO_POLL_INTERVAL", "2"))
MAX_TASK_TIMEOUT = int(os.getenv("CTO_MAX_TIMEOUT", "300"))  # 5 min
MAX_OUTPUT_LENGTH = 1800  # Discord embed limit

# ─── Logging ─────────────────────────────────────────────────────────────

LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [CTO] %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_DIR / "cto_listener.log"),
    ],
)
log = logging.getLogger("cto_listener")

# ─── Discord Posting ─────────────────────────────────────────────────────

def post_discord(webhook_url: str, title: str, message: str,
                 color: int = 3447003):
    """Post an embed to Discord. Matches the bash script's format."""
    if not webhook_url:
        log.warning("No webhook URL, skipping Discord post")
        return False

    # Escape message for JSON embedding
    embed = {
        "title": title[:256],
        "description": message[:4096],
        "color": color,
    }

    try:
        resp = requests.post(
            webhook_url,
            json={"embeds": [embed]},
            timeout=10,
        )
        if resp.status_code == 204:
            return True
        elif resp.status_code == 429:
            retry_after = resp.json().get("retry_after", 5)
            log.warning(f"Discord rate limited, waiting {retry_after}s")
            time.sleep(retry_after)
            resp2 = requests.post(
                webhook_url, json={"embeds": [embed]}, timeout=10
            )
            return resp2.status_code == 204
        else:
            log.error(f"Discord webhook {resp.status_code}: {resp.text[:200]}")
            return False
    except Exception as e:
        log.error(f"Discord post failed: {e}")
        return False


# ─── Queue Management ────────────────────────────────────────────────────

def read_queue() -> dict | None:
    """
    Read the queue file. Returns the task dict if status=pending,
    else None. Matches the single-object format from chat_agent.py.
    """
    if not QUEUE_FILE.exists():
        return None
    try:
        with open(QUEUE_FILE) as f:
            data = json.load(f)

        if isinstance(data, dict) and data.get("status") == "pending":
            return data

        return None
    except (json.JSONDecodeError, KeyError):
        return None
    except Exception as e:
        log.error(f"Queue read error: {e}")
        return None


def update_queue_status(status: str):
    """Update the queue file's status field."""
    if not QUEUE_FILE.exists():
        return
    try:
        with open(QUEUE_FILE) as f:
            data = json.load(f)
        data["status"] = status
        data["completed_at"] = datetime.now(timezone.utc).isoformat()
        with open(QUEUE_FILE, "w") as f:
            json.dump(data, f)
    except Exception as e:
        log.error(f"Failed to update queue status: {e}")


# ─── Task Execution ──────────────────────────────────────────────────────

def build_prompt(task_text: str) -> str:
    """Build the Claude Code prompt, matching the bash script's format."""
    claude_context = ""
    if CLAUDE_MD.exists():
        try:
            claude_context = CLAUDE_MD.read_text()[:4000]
        except Exception:
            pass

    return f"""Read CLAUDE.md first. Then: {task_text}

After completing:
1. Summarize what you found and what you did (max 3 sentences)
2. List any files changed
3. If you need approval before deploying, say so explicitly"""


def run_claude_code(task_text: str) -> tuple[int, str]:
    """
    Run Claude Code CLI. Returns (exit_code, output).
    Tries 'claude' first, falls back to 'npx @anthropic-ai/claude-code'.
    """
    prompt = build_prompt(task_text)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = LOG_DIR / f"cto_{timestamp}.log"

    cmds_to_try = [
        ["claude", "--print", "--dangerously-skip-permissions", prompt],
        ["npx", "-y", "@anthropic-ai/claude-code", "--print",
         "--dangerously-skip-permissions", prompt],
    ]

    for cmd in cmds_to_try:
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=MAX_TASK_TIMEOUT,
                cwd=str(PROJECT_DIR),
                env={**os.environ},
            )

            output = result.stdout.strip()
            if result.returncode != 0 and result.stderr:
                stderr_clean = result.stderr.strip()
                # Filter out npm noise
                if stderr_clean and "npm warn" not in stderr_clean.lower():
                    output += f"\n\nSTDERR: {stderr_clean}"

            # Save full log
            with open(log_file, "w") as f:
                f.write(f"Timestamp: {timestamp}\n")
                f.write(f"Task: {task_text}\n")
                f.write(f"Command: {' '.join(cmd[:3])}...\n")
                f.write(f"Exit code: {result.returncode}\n")
                f.write(f"Output:\n{output}\n")

            return result.returncode, output

        except FileNotFoundError:
            continue  # Try next command
        except subprocess.TimeoutExpired:
            msg = f"Task timed out after {MAX_TASK_TIMEOUT}s"
            log.error(msg)
            with open(log_file, "w") as f:
                f.write(f"Task: {task_text}\n{msg}\n")
            return 124, msg

    # Neither command found
    msg = "Neither 'claude' nor 'npx' available. Is Claude Code installed?"
    log.error(msg)
    with open(log_file, "w") as f:
        f.write(msg)
    return 1, msg


def truncate_output(output: str) -> str:
    """Truncate for Discord, keeping start and end."""
    if len(output) <= MAX_OUTPUT_LENGTH:
        return output
    half = (MAX_OUTPUT_LENGTH - 30) // 2
    return output[:half] + "\n\n...(truncated)...\n\n" + output[-half:]


# ─── Main Loop ───────────────────────────────────────────────────────────

def process_task(task: dict):
    """Process a single CTO task from the queue."""
    task_text = task.get("task", "")
    webhook = task.get("webhook", WEBHOOK_SYSTEM)

    if not task_text:
        log.warning("Empty task, skipping")
        update_queue_status("done")
        return

    log.info(f"Running CTO task: {task_text[:80]}...")

    # Mark as running
    update_queue_status("running")

    # Execute Claude Code
    exit_code, output = run_claude_code(task_text)

    # Mark as done FIRST to prevent retry loop (matches bash script behavior)
    update_queue_status("done" if exit_code == 0 else "failed")

    # Post result to Discord
    display = truncate_output(output)

    if exit_code == 124:
        post_discord(webhook, "⏰ CTO Timed Out",
                     f"Task timed out after {MAX_TASK_TIMEOUT // 60} minutes.",
                     color=16711680)  # red
    elif exit_code == 0:
        post_discord(webhook, f"✅ CTO Done: {task_text[:80]}",
                     f"```\n{display}\n```",
                     color=2664261)  # green
    else:
        post_discord(webhook, f"⚠️ CTO Completed (exit {exit_code})",
                     f"```\n{display}\n```",
                     color=16776960)  # yellow

    log.info(f"Task complete (exit {exit_code})")


def main():
    """Main polling loop — runs forever."""
    log.info("=" * 60)
    log.info("CTO Listener started (Docker container)")
    log.info(f"  Queue file:    {QUEUE_FILE}")
    log.info(f"  Project dir:   {PROJECT_DIR}")
    log.info(f"  Poll interval: {POLL_INTERVAL}s")
    log.info(f"  Max timeout:   {MAX_TASK_TIMEOUT}s")
    log.info(f"  Webhook:       {'set' if WEBHOOK_SYSTEM else 'MISSING'}")
    log.info("=" * 60)

    # Ensure queue file exists
    if not QUEUE_FILE.exists():
        QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(QUEUE_FILE, "w") as f:
            json.dump({"status": "idle"}, f)
        log.info("Created empty queue file")

    # Post startup to #system-health
    post_discord(WEBHOOK_SYSTEM,
                 "🟢 CTO Listener — Online",
                 "CTO container started. Watching for tasks.",
                 color=5763719)  # green

    consecutive_errors = 0

    while True:
        try:
            task = read_queue()
            if task:
                process_task(task)
                consecutive_errors = 0

        except KeyboardInterrupt:
            log.info("Shutting down (SIGINT)")
            post_discord(WEBHOOK_SYSTEM,
                         "🔴 CTO Listener — Offline",
                         "CTO container stopped.",
                         color=15548997)  # red
            break

        except Exception as e:
            consecutive_errors += 1
            log.error(f"Main loop error ({consecutive_errors}): {e}")
            log.error(traceback.format_exc())

            # Back off on repeated errors, max 60s
            backoff = min(10 * consecutive_errors, 60)
            time.sleep(backoff)
            continue

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
