"""
cto_listener.py — CTO Task Runner (Dockerized)
================================================
Replaces the fragile bash script with a proper Python service.
Runs as a Docker container with restart: unless-stopped.

Watches /app/data/cto_queue.json for pending tasks.
When a task appears, runs Claude Code and posts result to Discord.

Advantages over bash script:
- Docker handles restart on crash automatically
- Python handles file permissions correctly
- Proper logging to stdout (visible via docker logs)
- Clean error handling — never loops on failure
- ANTHROPIC_API_KEY from .env file, not host environment
"""

import os
import json
import time
import logging
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

import urllib.request
import urllib.error

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [cto-listener] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("cto-listener")

QUEUE_FILE = Path("/app/data/cto_queue.json")
PROJECT_DIR = Path("/app")
LOG_DIR = Path("/app/data/logs")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
POLL_INTERVAL = 2  # seconds


def post_discord(webhook: str, title: str, message: str, color: int = 3447003):
    """Post an embed to Discord webhook."""
    if not webhook:
        return
    # Escape for JSON safety
    message = message[:1900].replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    payload = json.dumps({
        "embeds": [{
            "title": title,
            "description": message,
            "color": color,
        }]
    }).encode("utf-8")
    try:
        req = urllib.request.Request(
            webhook,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        log.error(f"Discord post failed: {e}")


def read_queue() -> dict:
    """Read the queue file safely."""
    try:
        if not QUEUE_FILE.exists():
            return {}
        with open(QUEUE_FILE) as f:
            return json.load(f)
    except Exception as e:
        log.warning(f"Queue read error: {e}")
        return {}


def write_queue(data: dict):
    """Write to queue file safely."""
    try:
        QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = QUEUE_FILE.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(data, f)
        tmp.replace(QUEUE_FILE)
    except Exception as e:
        log.error(f"Queue write error: {e}")


def run_claude_code(task: str) -> tuple[str, int]:
    """Run Claude Code with the given task. Returns (output, exit_code)."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = LOG_DIR / f"cto_{timestamp}.log"

    prompt = (
        f"Read CLAUDE.md first. Then: {task}\n\n"
        "After completing:\n"
        "1. Summarize what you found and what you did (max 3 sentences)\n"
        "2. List any files changed\n"
        "3. If you need approval before deploying, say so explicitly"
    )

    env = os.environ.copy()
    env["ANTHROPIC_API_KEY"] = ANTHROPIC_API_KEY

    try:
        result = subprocess.run(
            ["claude", "--print", prompt],
            capture_output=True,
            text=True,
            timeout=300,
            cwd=str(PROJECT_DIR),
            env=env,
        )
        output = (result.stdout + result.stderr).strip()

        # Save log
        with open(log_file, "w") as f:
            f.write(f"Task: {task}\n")
            f.write(f"Exit: {result.returncode}\n\n")
            f.write(output)

        log.info(f"Claude Code exit={result.returncode}, log={log_file.name}")
        return output[:1800], result.returncode

    except subprocess.TimeoutExpired:
        log.error("Claude Code timed out after 5 minutes")
        return "Task timed out after 5 minutes.", 124
    except FileNotFoundError:
        log.error("claude command not found — is Claude Code installed?")
        return "Claude Code not installed in this container.", 1
    except Exception as e:
        log.error(f"Claude Code error: {e}")
        return str(e), 1


def process_task(task_data: dict):
    """Process a single queued task."""
    task = task_data.get("task", "")
    webhook = task_data.get("webhook", "")

    if not task:
        log.warning("Empty task — skipping")
        return

    log.info(f"Processing task: {task[:80]}")

    # Mark as running immediately to prevent re-processing
    write_queue({**task_data, "status": "running"})

    # Run Claude Code
    output, exit_code = run_claude_code(task)

    # Mark as done BEFORE posting (prevents retry loop if Discord fails)
    write_queue({**task_data, "status": "done", "completed_at": datetime.now().isoformat()})

    # Post result to Discord
    if exit_code == 124:
        post_discord(webhook, "⏰ CTO Timed Out", f"Task timed out: {task[:100]}", 16711680)
    elif exit_code == 0:
        post_discord(webhook, f"✅ CTO Done", f"**Task:** {task[:100]}\n\n```\n{output}\n```", 2664261)
    else:
        post_discord(webhook, f"⚠️ CTO Completed", f"**Task:** {task[:100]}\n\n```\n{output}\n```", 16776960)

    log.info(f"Task complete (exit={exit_code})")


def main():
    log.info("CTO Listener started")
    log.info(f"Queue file: {QUEUE_FILE}")
    log.info(f"Project dir: {PROJECT_DIR}")
    log.info(f"API key: {'set' if ANTHROPIC_API_KEY else 'MISSING'}")

    if not ANTHROPIC_API_KEY:
        log.error("ANTHROPIC_API_KEY not set — Claude Code will fail")

    # Post startup message to system health if webhook available
    webhook = os.getenv("DISCORD_WEBHOOK_SYSTEM", "")
    if webhook:
        post_discord(webhook, "🤖 CTO Listener Started", "Ready to receive tasks via `cto: [task]` in #chat", 2664261)

    while True:
        try:
            queue = read_queue()

            if queue.get("status") == "pending":
                process_task(queue)
            elif queue.get("status") == "running":
                # Stale running state — mark done to unblock
                age = time.time() - Path(QUEUE_FILE).stat().st_mtime
                if age > 350:  # 5 min + buffer
                    log.warning("Stale 'running' task detected — clearing")
                    write_queue({**queue, "status": "done"})

        except Exception as e:
            log.error(f"Main loop error: {e}")
            # Never crash the loop — just wait and retry

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
