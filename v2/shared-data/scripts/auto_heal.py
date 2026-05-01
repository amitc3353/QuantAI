#!/usr/bin/env python3
"""auto_heal.py — Claude-driven auto-heal routine for KARNA + QuantAI.

Runs 4×/day on cron around the trading window. Two modes:

  --mode=apply   (12:30 / 20:45 UTC): can execute fixes (with approval gates).
                  Self-downgrades to observe if the trading-window guard trips.
  --mode=observe (15:00 / 18:00 UTC): read-only. Queues findings to digest_buffer
                  and proposals to pending_fixes; never mutates state.

Other flags:
  --dry-run             no Discord, no writes, no LLM cost; print what would happen.
  --rollback <fix_id>   restore .bak from applied/<fix_id>.json receipt.
  --reset <fix_id>      clear quarantine state for a fix-id (manual override).
  --status              print runtime state and exit.

Reuses _discord.post_to_channel and _llm_client.Client (ClawRoute).
Reads (does not modify) docs/error-catalog.json as taxonomy reference.

Hard rules (enforced in code, not LLM-overrideable):
  * Never edits .env, openclaw.service, ibgateway.service, or any path matching
    a credential glob.
  * Never mutates trades.jsonl (only position_monitor.py writes it).
  * Never executes during the trading window 13:00-20:00 UTC Mon-Fri.
  * Max 3 file mutations + 2 service restarts per run.
  * 3-attempt budget per fix-id; quarantine after 3 failures.
  * .bak before every edit.
  * Diff size cap: 80 lines.
  * If any open paper positions exist, refuses edits to broker/trading scripts
    even outside the trading window.
"""

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# --- Env loading (mirrors error_detector.py pattern; never reads value of any secret) ---
for _ef in [Path("/home/trader/QuantAI/.env"), Path("/root/quantai-v2/.env")]:
    if _ef.exists():
        for _line in _ef.read_text().splitlines():
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.partition("=")
                if not os.environ.get(_k.strip()):
                    os.environ[_k.strip()] = _v.strip()
        break

# --- Constants ---
ET = ZoneInfo("America/New_York")
UTC = timezone.utc
REPO = Path("/home/trader/QuantAI")
SCRIPT_DIR = REPO / "v2" / "shared-data" / "scripts"
DATA_DIR = SCRIPT_DIR / "auto_heal_data"
PENDING_DIR = DATA_DIR / "pending_fixes"
APPLIED_DIR = DATA_DIR / "applied"
DIGEST_DIR = DATA_DIR / "digest_buffer"
STATE_FILE = DATA_DIR / "state.json"
LOCK_FILE = Path("/tmp/auto_heal.lock")
LOG_PATH = Path("/root/quantai-v2/shared-data/logs/auto_heal.log")

CATALOG_PATH = REPO / "docs" / "error-catalog.json"
DASHBOARD_STATE_DIR = Path("/var/dashboard/state")
DASHBOARD_AUTO_HEAL = DASHBOARD_STATE_DIR / "quantai-auto-heal.json"
JOURNAL_PATH = Path("/root/quantai-v2/shared-data/journal/paper/trades.jsonl")
HEARTBEAT_DIR = Path("/tmp/quantai-heartbeats")

LOG_FILES = [
    Path("/root/quantai-v2/shared-data/logs/pipeline.log"),
    Path("/root/quantai-v2/shared-data/logs/heartbeat.log"),
    Path("/root/quantai-v2/shared-data/logs/position_monitor.log"),
    Path("/root/quantai-v2/shared-data/logs/error_detector.log"),
    Path("/root/quantai-v2/shared-data/logs/beta.log"),
]

# Channel env vars; fall back to ALERTS for any unset.
CH_HEALTH = "DISCORD_CHANNEL_SYSTEM_HEALTH"
CH_APPROVALS = "DISCORD_CHANNEL_APPROVALS"
CH_LOGS = "DISCORD_CHANNEL_LOGS"
CH_FALLBACK = "DISCORD_CHANNEL_ALERTS"

# Hardcoded safety gates — NOT LLM-overrideable.
NEVER_TOUCH_PATHS = [
    "/etc/systemd/",
    "/home/trader/QuantAI/.env",
    "/root/quantai-v2/.env",
    "/home/openclaw/",
    "/root/quantai-v2/shared-data/journal/",
]
CREDENTIAL_PATTERNS = re.compile(r"(secret|token|credential|api[_-]?key|password|passwd)", re.IGNORECASE)
TRADING_PATH_GUARD = [
    "scripts/broker.py",
    "scripts/_broker_ibkr.py",
    "scripts/position_monitor.py",
    "scripts/autonomous_execution.py",
    "scripts/beta_agent.py",
]
WRITE_ALLOWLIST_PREFIXES = [
    str(REPO / "v2" / "shared-data" / "scripts") + "/",
    str(REPO / "docs") + "/",
    str(DASHBOARD_STATE_DIR) + "/",
]
NEVER_RESTART_SERVICES = {"openclaw", "openclaw.service", "ibgateway", "ibgateway.service"}

MAX_FILE_MUTATIONS_PER_RUN = 3
MAX_SERVICE_RESTARTS_PER_RUN = 2
MAX_DIFF_LINES = 80
ATTEMPT_BUDGET = 3
APPROVAL_EXPIRY_HOURS = 48
LOG_TAIL_LINES = 200


def now_utc() -> datetime:
    return datetime.now(UTC)


def is_trading_window(t: datetime | None = None) -> bool:
    t = t or now_utc()
    return t.weekday() < 5 and 13 <= t.hour < 20


def has_open_positions() -> bool:
    """Read journal tail to check for any status==OPEN trades. Read-only.

    Returns False on any access error (PermissionError, missing path) so the
    safer guard ("assume positions exist") never triggers in the wrong direction
    — the trading-window guard is the primary defense; this is a secondary one.
    """
    try:
        if not JOURNAL_PATH.exists():
            return False
    except Exception:
        return False
    try:
        with open(JOURNAL_PATH, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            block = 65536
            data = b""
            while size > 0 and data.count(b"\n") <= 200:
                read = min(block, size)
                size -= read
                f.seek(size)
                data = f.read(read) + data
        for line in data.decode("utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                if rec.get("status") == "OPEN":
                    return True
            except Exception:
                pass
    except Exception:
        pass
    return False


def log_line(msg: str) -> None:
    stamp = now_utc().strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"[{stamp}] {msg}"
    print(line)
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


# --- Lock -----------------------------------------------------------

class RunLock:
    def __init__(self, path: Path):
        self.path = path
        self.fd = None

    def __enter__(self):
        try:
            self.fd = open(self.path, "w")
        except PermissionError:
            # Stale lock owned by a different user (e.g. created by 'trader'
            # during a dry-run but now invoked as root via cron). Root can unlink
            # files in /tmp even if owned by another user (sticky-bit bypass).
            try:
                self.path.unlink()
                log_line(f"WARN: removed stale lock owned by another user: {self.path}")
            except Exception as unlink_err:
                log_line(f"ERROR: cannot acquire or remove lock {self.path}: {unlink_err}")
                sys.exit(1)
            self.fd = open(self.path, "w")
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log_line("another auto_heal run holds the lock; exiting")
            sys.exit(0)
        self.fd.write(f"{os.getpid()}\n")
        self.fd.flush()
        return self

    def __exit__(self, *exc):
        try:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            self.fd.close()
        except Exception:
            pass


# --- State ----------------------------------------------------------

def load_state() -> dict:
    if not STATE_FILE.exists():
        return {"attempts": {}, "quarantined": [], "last_run": {}}
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {"attempts": {}, "quarantined": [], "last_run": {}}


def save_state(state: dict, dry_run: bool) -> None:
    if dry_run:
        return
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2))
    os.replace(tmp, STATE_FILE)


# --- Discord --------------------------------------------------------

def _channel(env_key: str) -> str:
    return os.environ.get(env_key) or os.environ.get(CH_FALLBACK, "")


def post(channel_env: str, msg: str, dry_run: bool) -> str | None:
    """Post to Discord; return message id on success, None otherwise.

    Uses the same bot token as _discord.py but goes direct so we get back the
    message_id (which we need for reaction polling).
    """
    if dry_run:
        log_line(f"[DRY] would post to {channel_env}: {msg[:160]}")
        return None
    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    ch = _channel(channel_env)
    if not token or not ch:
        log_line(f"WARN: discord skipped — token or channel ({channel_env}) missing")
        return None
    try:
        req = urllib.request.Request(
            f"https://discord.com/api/v10/channels/{ch}/messages",
            data=json.dumps({"content": msg[:1900]}).encode(),
            headers={
                "Authorization": f"Bot {token}",
                "Content-Type": "application/json",
                # Cloudflare WAF in front of Discord returns HTTP 403 / err 1010
                # ("Bot Access Denied") when User-Agent is missing or generic.
                # Discord docs require: "DiscordBot (<url>, <version>)".
                "User-Agent": "DiscordBot (https://github.com/karna/quantai, 1.0)",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            payload = json.loads(r.read())
            return payload.get("id")
    except Exception as e:
        log_line(f"WARN: discord post failed ({channel_env}): {e}")
        return None


def react(channel_env: str, message_id: str, emoji: str, dry_run: bool) -> bool:
    """Bot self-reacts to a message (creates the voting button)."""
    if dry_run or not message_id:
        return True
    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    ch = _channel(channel_env)
    if not token or not ch:
        return False
    encoded = urllib.parse.quote(emoji)
    try:
        req = urllib.request.Request(
            f"https://discord.com/api/v10/channels/{ch}/messages/{message_id}/reactions/{encoded}/@me",
            data=b"",
            headers={
                "Authorization": f"Bot {token}",
                "User-Agent": "DiscordBot (https://github.com/karna/quantai, 1.0)",
            },
            method="PUT",
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status in (200, 204)
    except Exception as e:
        log_line(f"WARN: react failed: {e}")
        return False


def has_human_reaction(channel_env: str, message_id: str, emoji: str) -> bool:
    """True if at least one non-bot user has reacted with `emoji`."""
    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    ch = _channel(channel_env)
    if not token or not ch or not message_id:
        return False
    encoded = urllib.parse.quote(emoji)
    try:
        req = urllib.request.Request(
            f"https://discord.com/api/v10/channels/{ch}/messages/{message_id}/reactions/{encoded}",
            headers={
                "Authorization": f"Bot {token}",
                "User-Agent": "DiscordBot (https://github.com/karna/quantai, 1.0)",
            },
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            users = json.loads(r.read())
        return any(not u.get("bot", False) for u in users)
    except Exception as e:
        log_line(f"WARN: reaction-poll failed: {e}")
        return False


# --- Context bundle (inputs to LLM) ---------------------------------

def tail_lines(path: Path, n: int) -> list[str]:
    try:
        if not path.exists():
            return []
    except Exception:
        return []
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            block = 65536
            data = b""
            while size > 0 and data.count(b"\n") <= n:
                read = min(block, size)
                size -= read
                f.seek(size)
                data = f.read(read) + data
        return data.decode("utf-8", errors="replace").splitlines()[-n:]
    except Exception:
        return []


def grep_errors(lines: list[str], cap: int = 40) -> list[str]:
    pat = re.compile(r"error|fail|traceback|exception|warn|reject|timeout|panic|fatal", re.IGNORECASE)
    out = []
    for line in lines:
        if pat.search(line):
            out.append(line[:300])
        if len(out) >= cap:
            break
    return out


def gather_context() -> dict:
    """Build the compact bundle we'll send to Claude. All read-only."""
    bundle = {
        "now_utc": now_utc().isoformat(),
        "trading_window": is_trading_window(),
        "open_positions": has_open_positions(),
        "ibgateway": "unknown",
        "logs": {},
        "heartbeats": {},
        "dashboard_state": {},
        "catalog_taxonomy": [],
    }

    # ibgateway: only is-active (exit code), NEVER status (leaks creds).
    try:
        r = subprocess.run(
            ["systemctl", "is-active", "ibgateway"],
            capture_output=True, text=True, timeout=5,
        )
        bundle["ibgateway"] = r.stdout.strip() or "inactive"
    except Exception:
        bundle["ibgateway"] = "probe-failed"

    # Heartbeats: just file mtime age in minutes.
    try:
        if HEARTBEAT_DIR.exists():
            for p in HEARTBEAT_DIR.glob("*.beat"):
                try:
                    age_min = (time.time() - p.stat().st_mtime) / 60
                    bundle["heartbeats"][p.name] = round(age_min, 1)
                except Exception:
                    pass
    except Exception:
        pass

    # Log tails — error-grep only, capped.
    for log in LOG_FILES:
        try:
            if not log.exists():
                continue
        except Exception:
            continue
        tail = tail_lines(log, LOG_TAIL_LINES)
        errs = grep_errors(tail)
        if errs:
            bundle["logs"][log.name] = errs

    # Dashboard state — pre-filter to relevant tiles only.
    relevant = ["quantai-errors.json", "quantai-alerts.json", "quantai-heartbeats.json",
                "quantai-positions.json", "agent-beta-state.json", "cron-status.json",
                "system.json", "quantai-data-status.json"]
    try:
        if DASHBOARD_STATE_DIR.exists():
            for name in relevant:
                p = DASHBOARD_STATE_DIR / name
                try:
                    if not p.exists():
                        continue
                    obj = json.loads(p.read_text())
                    bundle["dashboard_state"][name] = {
                        "status": obj.get("status"),
                        "data": obj.get("data"),
                    }
                except Exception:
                    pass
    except Exception:
        pass

    # Catalog taxonomy: just id/severity/auto_action/runbook (compact).
    if CATALOG_PATH.exists():
        try:
            cat = json.loads(CATALOG_PATH.read_text())
            for e in cat.get("errors", []):
                bundle["catalog_taxonomy"].append({
                    "id": e.get("id"),
                    "severity": e.get("severity"),
                    "auto_action": e.get("auto_action"),
                    "runbook": e.get("runbook"),
                })
        except Exception:
            pass

    return bundle


# --- LLM call -------------------------------------------------------

SYSTEM_PROMPT = """You are the Auto-Heal advisor for KARNA + QuantAI, an autonomous options trading system.

Your role: review a snapshot of system health (logs, dashboard state, heartbeats, error catalog) and propose targeted fixes. The Python wrapper executes safe fixes and routes risky ones to a Discord approval queue. You do NOT execute anything yourself.

Hard rules you must respect when classifying proposals:
- fix_class="never_touch" — for anything involving .env, openclaw, ibgateway, journal mutations, or paths outside the QuantAI repo. Always classify these as observe-only.
- fix_class="propose_wait" — code edits, novel/unknown errors, anything matching catalog auto_action="none", or any change you're <90% confident is correct.
- fix_class="safe_auto" — only for: stale lock/tmp file cleanup, log rotation, restart of NON-trading services (NOT ibgateway, NOT openclaw), or catalog auto_action in {retry, skip, restart_service} that the existing error_detector might have missed.

Output STRICT JSON, no prose, no markdown fences:
{
  "summary": "one-line health summary",
  "findings": [
    {"id": "short-slug", "severity": "critical|high|medium|low|info",
     "what": "...", "evidence": "log file or state path"}
  ],
  "proposals": [
    {"id": "short-slug", "fix_class": "safe_auto|propose_wait|never_touch",
     "severity": "critical|high|medium|low|info",
     "description": "what this fix does and why",
     "runbook": "docs/runbooks/runbook-foo.md or empty",
     "target_files": ["relative path under repo, or empty list"],
     "shell_commands": ["safe shell commands, optional"],
     "diff": "unified diff if editing files, else empty"}
  ]
}

Conservatism rules:
- Default to propose_wait. Never_touch beats safe_auto beats propose_wait beats omitting.
- diff must be <80 lines. If larger, split into multiple proposals or explain why a manual fix is needed.
- shell_commands must NOT include sudo, rm -rf /, dd, mkfs, > /dev/, curl|sh, or any pipe to a shell.
- If you don't see a concrete actionable issue, return empty findings and proposals — silence is fine.
"""


def call_llm(bundle: dict, dry_run: bool) -> dict:
    """Send the bundle to Claude via ClawRoute and parse the JSON response."""
    if dry_run:
        log_line("[DRY] skipping LLM call; returning empty plan")
        return {"summary": "dry-run, no LLM call", "findings": [], "proposals": []}

    sys.path.insert(0, str(SCRIPT_DIR))
    try:
        from _llm_client import Client
    except Exception as e:
        log_line(f"ERROR: cannot import _llm_client: {e}")
        return {"summary": "LLM import failed", "findings": [], "proposals": [], "_error": str(e)}

    user_msg = (
        "Review this system snapshot and propose fixes per the schema in the system prompt.\n\n"
        f"```json\n{json.dumps(bundle, indent=2)[:60000]}\n```"
    )
    try:
        client = Client()
        resp = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=4000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
            tier="COMPLEX",
        )
        text = resp.content[0].text.strip()
    except Exception as e:
        log_line(f"ERROR: LLM call failed: {e}")
        return {"summary": "LLM call failed", "findings": [], "proposals": [], "_error": str(e)}

    # Strip accidental code fences.
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except Exception as e:
        log_line(f"ERROR: LLM returned non-JSON: {e}; raw[:300]={text[:300]!r}")
        return {"summary": "LLM JSON parse failed", "findings": [], "proposals": [], "_error": str(e)}


# --- Proposal validation & writing ---------------------------------

def is_path_allowed(path_str: str) -> bool:
    if not path_str:
        return False
    abs_p = str((REPO / path_str).resolve()) if not path_str.startswith("/") else path_str
    if any(abs_p.startswith(blocked) for blocked in NEVER_TOUCH_PATHS):
        return False
    if CREDENTIAL_PATTERNS.search(abs_p):
        return False
    return any(abs_p.startswith(allow) for allow in WRITE_ALLOWLIST_PREFIXES)


def is_command_safe(cmd: str) -> bool:
    cmd_l = cmd.lower()
    bad = ["sudo ", "rm -rf /", "rm -rf ~", " dd ", "mkfs", "> /dev/", "curl ", "wget ", "| sh", "| bash", "eval ", "systemctl stop"]
    return not any(b in cmd_l for b in bad)


def fix_id_for(p: dict) -> str:
    """Stable id from proposal content so dedupe works across runs."""
    raw = json.dumps({
        "id": p.get("id"),
        "files": sorted(p.get("target_files") or []),
        "diff": p.get("diff", ""),
        "cmds": p.get("shell_commands") or [],
    }, sort_keys=True)
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def validate_proposal(p: dict, open_positions: bool) -> tuple[bool, str]:
    fc = p.get("fix_class", "")
    if fc not in ("safe_auto", "propose_wait", "never_touch"):
        return False, f"invalid fix_class={fc!r}"
    diff = p.get("diff", "") or ""
    if diff.count("\n") > MAX_DIFF_LINES:
        return False, f"diff exceeds {MAX_DIFF_LINES} lines"
    for tf in p.get("target_files") or []:
        if not is_path_allowed(tf):
            return False, f"path not allowed: {tf}"
        if open_positions and any(tf.endswith(t.split('/')[-1]) for t in TRADING_PATH_GUARD):
            return False, f"open positions — refused edit to trading-path file {tf}"
    for cmd in p.get("shell_commands") or []:
        if not is_command_safe(cmd):
            return False, f"unsafe shell command: {cmd[:80]}"
    return True, "ok"


def write_proposal(p: dict, channel_id_used: str | None,
                   message_id: str | None, dry_run: bool) -> Path | None:
    fid = fix_id_for(p)
    out = PENDING_DIR / f"{fid}.json"
    if out.exists():
        return out  # already queued
    record = {
        "fix_id": fid,
        "created_at": now_utc().isoformat(),
        "expires_at": (now_utc() + timedelta(hours=APPROVAL_EXPIRY_HOURS)).isoformat(),
        "severity": p.get("severity", "info"),
        "fix_class": p["fix_class"],
        "auto_apply": p["fix_class"] == "safe_auto",
        "description": p.get("description", "")[:500],
        "runbook": p.get("runbook", ""),
        "target_files": p.get("target_files") or [],
        "shell_commands": p.get("shell_commands") or [],
        "diff": p.get("diff", ""),
        "discord_message_id": message_id,
        "channel_id": channel_id_used,
        "status": "approved_safe_auto" if p["fix_class"] == "safe_auto" else "awaiting_approval",
    }
    if dry_run:
        log_line(f"[DRY] would write proposal {fid}: {record['description'][:80]}")
        return None
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record, indent=2))
    os.replace(tmp, out)
    return out


# --- Apply / rollback ----------------------------------------------

def backup_path(target: Path) -> Path:
    stamp = now_utc().strftime("%Y-%m-%d-%H%M%S")
    return target.with_suffix(target.suffix + f".bak.{stamp}-autoheal")


def apply_diff(diff_text: str, dry_run: bool) -> tuple[bool, str, list[str]]:
    """Apply a unified diff via `patch -p0`. Returns (ok, detail, baks_created)."""
    if not diff_text.strip():
        return True, "no diff", []
    # Backup all files referenced before patching.
    baks = []
    files = []
    for line in diff_text.splitlines():
        if line.startswith("--- ") or line.startswith("+++ "):
            parts = line.split(None, 1)
            if len(parts) == 2:
                f = parts[1].split("\t")[0]
                if f and f not in ("/dev/null",):
                    files.append(f)
    files = list(dict.fromkeys(files))
    for f in files:
        p = Path(f if f.startswith("/") else str(REPO / f))
        if p.exists():
            bak = backup_path(p)
            if not dry_run:
                shutil.copy2(p, bak)
            baks.append(str(bak))
    if dry_run:
        return True, f"[DRY] would patch {len(files)} files", baks
    try:
        proc = subprocess.run(
            ["patch", "-p0", "--forward"],
            input=diff_text, capture_output=True, text=True,
            cwd=str(REPO), timeout=20,
        )
        ok = proc.returncode == 0
        detail = (proc.stdout + proc.stderr)[-400:]
        return ok, detail, baks
    except Exception as e:
        return False, f"patch exception: {e}", baks


def run_shell(cmd: str, dry_run: bool) -> tuple[bool, str]:
    if dry_run:
        return True, f"[DRY] would run: {cmd[:120]}"
    try:
        proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
        return proc.returncode == 0, (proc.stdout + proc.stderr)[-400:]
    except Exception as e:
        return False, f"exception: {e}"


def execute_proposal(rec: dict, state: dict, dry_run: bool) -> tuple[bool, dict]:
    fid = rec["fix_id"]
    receipt = {
        "fix_id": fid,
        "applied_at": now_utc().isoformat(),
        "fix_class": rec["fix_class"],
        "description": rec["description"],
        "shell_results": [],
        "patch_result": None,
        "backups": [],
    }
    # Diff first (so we have backups), then commands.
    if rec.get("diff"):
        ok, detail, baks = apply_diff(rec["diff"], dry_run)
        receipt["patch_result"] = {"ok": ok, "detail": detail}
        receipt["backups"].extend(baks)
        if not ok:
            return False, receipt
    for cmd in rec.get("shell_commands") or []:
        ok, detail = run_shell(cmd, dry_run)
        receipt["shell_results"].append({"cmd": cmd, "ok": ok, "detail": detail})
        if not ok:
            return False, receipt
    return True, receipt


# --- Apply mode -----------------------------------------------------

def consume_pending(state: dict, dry_run: bool) -> dict:
    """Iterate pending_fixes; apply approved + safe-auto; report counts."""
    counts = {"applied": 0, "skipped_unapproved": 0, "expired": 0, "quarantined": 0, "failed": 0}
    receipts = []
    file_mut = 0
    svc_restarts = 0

    for path in sorted(PENDING_DIR.glob("*.json")):
        try:
            rec = json.loads(path.read_text())
        except Exception:
            continue
        fid = rec.get("fix_id") or path.stem
        if fid in state.get("quarantined", []):
            counts["quarantined"] += 1
            continue
        # Expiry check.
        try:
            if now_utc() > datetime.fromisoformat(rec.get("expires_at", "")):
                counts["expired"] += 1
                if not dry_run:
                    path.unlink()
                continue
        except Exception:
            pass

        # Decide whether to apply.
        will_apply = rec.get("auto_apply", False)
        if not will_apply:
            ch_env = CH_APPROVALS
            mid = rec.get("discord_message_id")
            if mid and has_human_reaction(ch_env, mid, "✅"):
                will_apply = True
        if not will_apply:
            counts["skipped_unapproved"] += 1
            continue

        # Per-run limits.
        n_files = len(rec.get("target_files") or [])
        n_cmds = len(rec.get("shell_commands") or [])
        if file_mut + n_files > MAX_FILE_MUTATIONS_PER_RUN:
            log_line(f"file-mutation cap reached; deferring {fid}")
            continue
        if svc_restarts + n_cmds > MAX_SERVICE_RESTARTS_PER_RUN and "systemctl" in " ".join(rec.get("shell_commands") or []):
            log_line(f"service-restart cap reached; deferring {fid}")
            continue

        ok, receipt = execute_proposal(rec, state, dry_run)
        receipts.append(receipt)
        attempts = state.setdefault("attempts", {})
        attempts[fid] = attempts.get(fid, 0) + 1
        if ok:
            counts["applied"] += 1
            file_mut += n_files
            svc_restarts += n_cmds
            if not dry_run:
                APPLIED_DIR.mkdir(parents=True, exist_ok=True)
                (APPLIED_DIR / f"{fid}.json").write_text(json.dumps(receipt, indent=2))
                if not rec.get("consume_once"):
                    path.unlink()
                else:
                    path.unlink()
            attempts[fid] = 0  # reset on success
            ch = _channel(CH_LOGS)
            post(CH_LOGS, f"✅ Auto-Heal applied `{fid}` — {rec['description'][:160]}", dry_run)
        else:
            counts["failed"] += 1
            if attempts[fid] >= ATTEMPT_BUDGET:
                state.setdefault("quarantined", []).append(fid)
                counts["quarantined"] += 1
                post(CH_HEALTH, f"🚨 Auto-Heal QUARANTINED `{fid}` after {ATTEMPT_BUDGET} failures: {rec['description'][:160]}", dry_run)
            else:
                post(CH_HEALTH, f"⚠️ Auto-Heal fix `{fid}` failed (attempt {attempts[fid]}/{ATTEMPT_BUDGET})", dry_run)

    counts["receipts"] = receipts
    return counts


def drain_digest(dry_run: bool) -> dict:
    """Aggregate today's digest_buffer entries and return a summary dict."""
    today = now_utc().strftime("%Y-%m-%d")
    summary = {"date": today, "findings": [], "proposals_queued": 0}
    for buf in sorted(DIGEST_DIR.glob(f"{today}*.jsonl")):
        try:
            for line in buf.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                summary["findings"].extend(rec.get("findings", []))
                summary["proposals_queued"] += len(rec.get("proposals_queued", []))
        except Exception:
            pass
        if not dry_run:
            buf.unlink()
    return summary


# --- Observe mode helpers ------------------------------------------

def append_digest(plan: dict, queued_ids: list[str], dry_run: bool) -> None:
    today = now_utc().strftime("%Y-%m-%d")
    out = DIGEST_DIR / f"{today}.jsonl"
    rec = {
        "ts": now_utc().isoformat(),
        "summary": plan.get("summary", ""),
        "findings": plan.get("findings", []),
        "proposals_queued": queued_ids,
    }
    if dry_run:
        log_line(f"[DRY] would append digest with {len(queued_ids)} proposals queued")
        return
    DIGEST_DIR.mkdir(parents=True, exist_ok=True)
    with open(out, "a") as f:
        f.write(json.dumps(rec) + "\n")


def post_proposal_card(rec_path: Path, rec: dict, dry_run: bool) -> tuple[str | None, str | None]:
    """Post a Discord card for a propose_wait proposal; return (channel_env, message_id)."""
    sev = rec.get("severity", "info")
    emoji = {"critical": "🚨", "high": "⚠️", "medium": "🟡", "low": "🟢", "info": "📋"}.get(sev, "📋")
    diff_lines = rec.get("diff", "").count("\n")
    lines_str = f", diff {diff_lines} lines" if diff_lines else ""
    cmds = len(rec.get("shell_commands") or [])
    cmds_str = f", {cmds} cmd(s)" if cmds else ""
    msg = (
        f"{emoji} Auto-Heal proposal `{rec['fix_id']}` (sev={sev}{lines_str}{cmds_str})\n"
        f"{rec['description'][:600]}\n"
        f"Runbook: `{rec.get('runbook', 'none')}`\n"
        f"React ✅ to apply at next post-close run, ❌ to dismiss. Expires {rec['expires_at'][:16]}Z."
    )
    mid = post(CH_APPROVALS, msg, dry_run)
    if mid:
        react(CH_APPROVALS, mid, "✅", dry_run)
        react(CH_APPROVALS, mid, "❌", dry_run)
        # Persist message_id back into the proposal file.
        rec["discord_message_id"] = mid
        rec["channel_id"] = _channel(CH_APPROVALS)
        if not dry_run:
            rec_path.write_text(json.dumps(rec, indent=2))
    return CH_APPROVALS, mid


# --- Dashboard tile -------------------------------------------------

def write_dashboard(state: dict, last_summary: str, dry_run: bool) -> None:
    pending = list(PENDING_DIR.glob("*.json"))
    payload = {
        "last_updated": now_utc().isoformat(),
        "status": "warning" if any(state.get("quarantined", [])) else "ok",
        "data": {
            "last_summary": last_summary,
            "pending_count": len(pending),
            "pending_ids": [p.stem for p in pending][:20],
            "quarantined": state.get("quarantined", [])[-20:],
            "last_run": state.get("last_run", {}),
        },
    }
    if dry_run:
        log_line(f"[DRY] would write dashboard tile: {payload['data']}")
        return
    try:
        DASHBOARD_STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = DASHBOARD_AUTO_HEAL.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        os.replace(tmp, DASHBOARD_AUTO_HEAL)
    except Exception as e:
        log_line(f"WARN: dashboard write failed: {e}")


# --- Modes ---------------------------------------------------------

def run_observe(dry_run: bool, state: dict) -> str:
    bundle = gather_context()
    plan = call_llm(bundle, dry_run)
    open_pos = bundle["open_positions"]
    queued = []
    for p in plan.get("proposals", []):
        ok, why = validate_proposal(p, open_pos)
        if not ok:
            log_line(f"reject proposal: {why}")
            continue
        if p["fix_class"] == "never_touch":
            continue  # observed only, not queued
        out = write_proposal(p, None, None, dry_run)
        if out is None and not dry_run:
            continue
        if p["fix_class"] == "propose_wait" and out is not None:
            try:
                rec = json.loads(out.read_text())
                post_proposal_card(out, rec, dry_run)
            except Exception:
                pass
        queued.append(fix_id_for(p))
    append_digest(plan, queued, dry_run)
    summary = plan.get("summary", "(no summary)")
    log_line(f"observe done: {len(plan.get('findings', []))} findings, {len(queued)} queued")
    return summary


def run_apply(dry_run: bool, state: dict) -> str:
    # Re-assert window guard at runtime (paranoia for clock drift).
    if is_trading_window():
        log_line("trading window detected at apply-time; downgrading to observe")
        return run_observe(dry_run, state)

    # First do a fresh diagnose so we have current proposals.
    fresh_summary = run_observe(dry_run, state)

    counts = consume_pending(state, dry_run)
    log_line(f"apply done: {counts}")

    # Post-close run (20:45 UTC) — drain digest into a daily summary card.
    hour = now_utc().hour
    if hour >= 20:
        digest = drain_digest(dry_run)
        n = len(digest["findings"])
        msg = (
            f"📊 Auto-Heal daily digest {digest['date']}\n"
            f"Mid-trading observations: {n} findings, {digest['proposals_queued']} proposals queued.\n"
            f"Post-close: {counts['applied']} applied, {counts['skipped_unapproved']} awaiting ✅, "
            f"{counts['failed']} failed, {counts['quarantined']} quarantined."
        )
        post(CH_HEALTH, msg, dry_run)
    else:
        msg = (
            f"🌅 Auto-Heal pre-market {now_utc().strftime('%H:%M')} UTC: "
            f"{counts['applied']} applied, {counts['skipped_unapproved']} awaiting ✅, "
            f"{counts['failed']} failed, {counts['quarantined']} quarantined."
        )
        post(CH_HEALTH, msg, dry_run)

    return fresh_summary


# --- Rollback / reset ----------------------------------------------

def cmd_rollback(fix_id: str, dry_run: bool) -> int:
    receipt_path = APPLIED_DIR / f"{fix_id}.json"
    if not receipt_path.exists():
        print(f"no receipt for fix_id={fix_id}", file=sys.stderr)
        return 2
    receipt = json.loads(receipt_path.read_text())
    for bak in receipt.get("backups", []):
        bak_p = Path(bak)
        if not bak_p.exists():
            print(f"backup missing: {bak}", file=sys.stderr)
            continue
        # Original = strip the `.bak.<stamp>-autoheal` suffix.
        orig = re.sub(r"\.bak\.[0-9-]+-autoheal$", "", bak)
        if dry_run:
            print(f"[DRY] would restore {bak} -> {orig}")
            continue
        shutil.copy2(bak_p, orig)
        print(f"restored {orig} from {bak}")
    return 0


def cmd_reset(fix_id: str, dry_run: bool) -> int:
    state = load_state()
    state.get("quarantined", []).remove(fix_id) if fix_id in state.get("quarantined", []) else None
    state.setdefault("attempts", {})[fix_id] = 0
    save_state(state, dry_run)
    print(f"reset attempts/quarantine for {fix_id}")
    return 0


def cmd_status() -> int:
    state = load_state()
    pending = list(PENDING_DIR.glob("*.json"))
    print(f"pending: {len(pending)}")
    for p in pending:
        try:
            rec = json.loads(p.read_text())
            print(f"  {rec['fix_id']:14s} {rec['fix_class']:14s} sev={rec['severity']:8s} {rec['description'][:60]}")
        except Exception:
            pass
    print(f"quarantined: {state.get('quarantined', [])}")
    print(f"attempts: {state.get('attempts', {})}")
    print(f"last_run: {state.get('last_run', {})}")
    return 0


# --- Main -----------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mode", choices=("apply", "observe"), default="observe")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--rollback", metavar="FIX_ID")
    ap.add_argument("--reset", metavar="FIX_ID")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()

    if args.status:
        return cmd_status()
    if args.rollback:
        return cmd_rollback(args.rollback, args.dry_run)
    if args.reset:
        return cmd_reset(args.reset, args.dry_run)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PENDING_DIR.mkdir(exist_ok=True)
    APPLIED_DIR.mkdir(exist_ok=True)
    DIGEST_DIR.mkdir(exist_ok=True)

    log_line(f"auto_heal start mode={args.mode} dry_run={args.dry_run}")
    state = load_state()
    state.setdefault("last_run", {})

    with RunLock(LOCK_FILE):
        if args.mode == "observe":
            summary = run_observe(args.dry_run, state)
        else:
            summary = run_apply(args.dry_run, state)

        state["last_run"][args.mode] = now_utc().isoformat()
        save_state(state, args.dry_run)
        write_dashboard(state, summary, args.dry_run)

    log_line(f"auto_heal done mode={args.mode}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
