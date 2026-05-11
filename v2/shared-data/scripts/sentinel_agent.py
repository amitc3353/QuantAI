#!/usr/bin/env python3
"""sentinel_agent.py — Sentinel: autonomous infrastructure operations agent.

Sentinel is a peer of Alpha/Beta/Gamma but operations-focused. It runs on its
own schedule (wrapper-driven; reads ET clock to decide mode). It reads the
deterministic system-health-report.json + errors.db + journal + agent identity,
proposes fixes via Claude, and either auto-applies safe_auto actions or queues
propose_wait cards to #karna-approvals for ✅/❌ approval.

Modes:
  --observe   Haiku (cheap). Read-only diagnose. Silent unless critical.
  --apply     Sonnet. Diagnose + execute approved + safe_auto fixes.
  --auto      Wrapper: reads ET clock, dispatches to observe/apply/None.

Other:
  --dry-run             No Discord, no LLM cost, no writes.
  --rollback <fix_id>   Restore .bak from applied/<fix_id>.json receipt.
  --reset <fix_id>      Clear quarantine for a fix-id.
  --status              Print runtime state and exit.

Built-in safe_auto actions (no LLM needed, run every apply cycle):
  - Catalog reclassification: SQL UPDATEs on errors.db for known-noise patterns
    (fail2ban, UFW, SSH brute force, health-monitor stale-socket) that match
    catalog entries with severity=info.

Hard rules (enforced in Python BEFORE the LLM ever sees the proposal):
  * NEVER_MODIFY_PATHS — trading-path scripts cannot be edited by Sentinel
  * NEVER_TOUCH_PATHS — .env, openclaw, journal, /etc/systemd/
  * NEVER_RESTART_SERVICES_BLANKET — openclaw never
  * POSITION_GATED_SERVICES — ibgateway only off-hours and 0 positions
  * CREDENTIAL_PATTERNS — anything matching is rejected
  * Trading-window guard (apply mode self-downgrades to observe inside 13:00-20:00 UTC weekday)
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# --- Env loading (never reads value of any secret) -----------------
for _ef in [Path("/home/trader/QuantAI/.env"), Path("/root/quantai-v2/.env")]:
    if _ef.exists():
        for _line in _ef.read_text().splitlines():
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.partition("=")
                if not os.environ.get(_k.strip()):
                    os.environ[_k.strip()] = _v.strip()
        break


# --- Constants -----------------------------------------------------
ET = ZoneInfo("America/New_York")
UTC = timezone.utc
REPO = Path("/home/trader/QuantAI")
SCRIPT_DIR = REPO / "v2" / "shared-data" / "scripts"
AGENTS_DIR = REPO / "v2" / "shared-data" / "agents"
DATA_DIR = SCRIPT_DIR / "auto_heal_data"  # reused; Sentinel inherits the dir layout
PENDING_DIR = DATA_DIR / "pending_fixes"
APPLIED_DIR = DATA_DIR / "applied"
DIGEST_DIR = DATA_DIR / "digest_buffer"
FIX_HISTORY_DIR = DATA_DIR / "fix_history"
STATE_FILE = DATA_DIR / "state.json"
LOCK_FILE = Path("/tmp/sentinel_agent.lock")
LOG_PATH = Path("/root/quantai-v2/shared-data/logs/sentinel.log")

CATALOG_PATH = REPO / "docs" / "error-catalog.json"
DASHBOARD_STATE_DIR = Path("/var/dashboard/state")
HEALTH_REPORT_PATH = DASHBOARD_STATE_DIR / "system-health-report.json"
SENTINEL_TILE_PATH = DASHBOARD_STATE_DIR / "quantai-sentinel.json"
POSITIONS_PATH = DASHBOARD_STATE_DIR / "quantai-positions.json"
TEST_RESULTS_PATH = DASHBOARD_STATE_DIR / "quantai-test-results.json"
ERRORS_DB = Path("/var/dashboard/errors.db")

JOURNAL_PATH = Path("/root/quantai-v2/shared-data/journal/paper/trades.jsonl")
WEEKLY_REPORTS_DIR = Path("/root/quantai-v2/shared-data/weekly_reports")
SENTINEL_IDENTITY = AGENTS_DIR / "AGENT_SENTINEL_IDENTITY.md"

# Discord channels
CH_HEALTH = "DISCORD_CHANNEL_SYSTEM_HEALTH"
CH_APPROVALS = "DISCORD_CHANNEL_APPROVALS"
CH_LOGS = "DISCORD_CHANNEL_LOGS"
CH_FALLBACK = "DISCORD_CHANNEL_ALERTS"


# ── Hardcoded safety rails (NOT LLM-overrideable) ─────────────────────────────

NEVER_MODIFY_PATHS = {
    # Trading-path files: Sentinel never edits these
    "scripts/autonomous_execution.py",
    "scripts/beta_agent.py",
    "scripts/gamma_agent.py",
    "scripts/position_monitor.py",
    "scripts/_broker_ibkr.py",
    "scripts/broker.py",
    # Gamma 4-arm A/B/C/D test internals (frozen during experiment per plan §G)
    "scripts/gamma/rankers/",            # ranker abstraction + 4 implementations
    "scripts/gamma/arm_state.py",        # per-arm state tracking + atomic writes
    "scripts/gamma/reward_risk_estimator.py",  # pre-rank r:r estimator
    "scripts/gamma/promotion_evaluator.py",    # win-criteria evaluator (locked rules)
}

NEVER_TOUCH_PATHS = [
    "/etc/systemd/",
    "/home/trader/QuantAI/.env",
    "/root/quantai-v2/.env",
    "/home/openclaw/",
    "/root/quantai-v2/shared-data/journal/",
]

NEVER_RESTART_SERVICES_BLANKET = {"openclaw", "openclaw.service"}

# Services Sentinel CAN restart, but ONLY off-hours and 0 positions
POSITION_GATED_SERVICES = {"ibgateway", "ibgateway.service"}

CREDENTIAL_PATTERNS = re.compile(
    r"(secret|token|credential|api[_-]?key|password|passwd)", re.IGNORECASE
)

WRITE_ALLOWLIST_PREFIXES = [
    str(REPO / "v2" / "shared-data" / "scripts") + "/",
    str(REPO / "v2" / "shared-data" / "agents") + "/",
    str(REPO / "docs") + "/",
    str(DASHBOARD_STATE_DIR) + "/",
]

MAX_FILE_MUTATIONS_PER_RUN = 3
MAX_SERVICE_RESTARTS_PER_RUN = 2
MAX_DIFF_LINES = 80
ATTEMPT_BUDGET = 3
APPROVAL_EXPIRY_HOURS = 48


# ── Schedule table (ET-local; wrapper exits silently outside slots) ──────────

SCHEDULE_ET = {
    "weekday": {
        (8, 30):  "apply",
        (10, 0):  "observe",
        (11, 0):  "observe",
        (12, 0):  "observe",
        (13, 0):  "observe",
        (14, 0):  "observe",
        (15, 0):  "observe",
        (16, 15): "apply",
        (21, 0):  "observe",
    },
    "weekend": {(10, 0): "observe"},
}


def resolve_mode_from_clock() -> str | None:
    """Return 'apply' / 'observe' / None based on current ET clock."""
    et = datetime.now(ET)
    table = SCHEDULE_ET["weekend"] if et.weekday() >= 5 else SCHEDULE_ET["weekday"]
    return table.get((et.hour, et.minute))


def now_utc() -> datetime:
    return datetime.now(UTC)


def is_trading_window(t: datetime | None = None) -> bool:
    t = t or now_utc()
    return t.weekday() < 5 and 13 <= t.hour < 20


def is_market_hours() -> bool:
    now = datetime.now(ET)
    if now.weekday() >= 5:
        return False
    h, m = now.hour, now.minute
    return (h == 9 and m >= 30) or (10 <= h < 16)


def log_line(msg: str) -> None:
    """Write a timestamped log line.

    Cron's `>> sentinel.log 2>&1` already redirects stdout to LOG_PATH, so
    we just print(). Removed the direct LOG_PATH.write() call (2026-05-04)
    which was duplicating every line in the log file.
    Manual invocations without cron print to stdout normally.
    """
    stamp = now_utc().strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{stamp}] {msg}", flush=True)


# ── Lock ─────────────────────────────────────────────────────────────────

class RunLock:
    def __init__(self, path: Path):
        self.path = path
        self.fd = None

    def __enter__(self):
        try:
            self.fd = open(self.path, "w")
        except PermissionError:
            try:
                self.path.unlink()
                log_line(f"WARN: removed stale lock owned by another user: {self.path}")
            except Exception as e:
                log_line(f"ERROR: cannot acquire lock {self.path}: {e}")
                sys.exit(1)
            self.fd = open(self.path, "w")
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log_line("another sentinel run holds the lock; exiting")
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


# ── State ─────────────────────────────────────────────────────────────────

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
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2))
    os.replace(tmp, STATE_FILE)


# ── Discord ──────────────────────────────────────────────────────────────

def _channel(env_key: str) -> str:
    return os.environ.get(env_key) or os.environ.get(CH_FALLBACK, "")


def post(channel_env: str, msg: str, dry_run: bool) -> str | None:
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
            headers={"Authorization": f"Bot {token}",
                     "User-Agent": "DiscordBot (https://github.com/karna/quantai, 1.0)"},
            method="PUT",
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status in (200, 204)
    except Exception as e:
        log_line(f"WARN: react failed: {e}")
        return False


def has_human_reaction(channel_env: str, message_id: str, emoji: str) -> bool:
    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    ch = _channel(channel_env)
    if not token or not ch or not message_id:
        return False
    encoded = urllib.parse.quote(emoji)
    try:
        req = urllib.request.Request(
            f"https://discord.com/api/v10/channels/{ch}/messages/{message_id}/reactions/{encoded}",
            headers={"Authorization": f"Bot {token}",
                     "User-Agent": "DiscordBot (https://github.com/karna/quantai, 1.0)"},
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            users = json.loads(r.read())
        return any(not u.get("bot", False) for u in users)
    except Exception as e:
        log_line(f"WARN: reaction-poll failed: {e}")
        return False


# ── Position helpers (used by safety rails) ─────────────────────────────

def read_open_positions_count() -> int:
    """Read open position count from quantai-positions.json. Fail-safe to 0
    means: if we can't read the file, do NOT block ibgateway operations on
    that — but the calling code should ALSO check market hours and have
    operator approval where appropriate. This function is one signal of two.
    """
    if not POSITIONS_PATH.exists():
        return 0
    try:
        doc = json.loads(POSITIONS_PATH.read_text())
        return int(doc.get("data", {}).get("count", 0) or 0)
    except Exception:
        return 0


# ── Code-enforced safety: validate proposal & shell commands ─────────────

def is_path_allowed(path_str: str) -> tuple[bool, str]:
    """Returns (allowed, reason)."""
    if not path_str:
        return False, "empty path"
    abs_p = path_str if path_str.startswith("/") else str((REPO / path_str).resolve())
    # Block NEVER_TOUCH first
    for blocked in NEVER_TOUCH_PATHS:
        if abs_p.startswith(blocked):
            return False, f"blocked path: {blocked}"
    # Credential patterns
    if CREDENTIAL_PATTERNS.search(abs_p):
        return False, "credential-like path"
    # NEVER_MODIFY_PATHS — match by suffix (rel path tail)
    for never in NEVER_MODIFY_PATHS:
        if abs_p.endswith(never):
            return False, f"trading-path file: {never}"
    # Allowlist
    if not any(abs_p.startswith(allow) for allow in WRITE_ALLOWLIST_PREFIXES):
        return False, "outside write allowlist"
    return True, "ok"


def is_command_safe(cmd: str, *, open_positions: int, market_open: bool) -> tuple[bool, str]:
    """Position-aware shell command validation. Returns (safe, reason).

    Order matters: more-specific checks come before generic ones so error
    messages are precise (e.g. "never-restart service: openclaw" beats the
    generic "dangerous pattern: 'systemctl stop'").
    """
    cmd_l = cmd.lower()

    # 1) NEVER_MODIFY_PATHS — block any reference to trading-path files
    for path in NEVER_MODIFY_PATHS:
        if path in cmd:
            return False, f"command references trading-path file: {path}"

    # 2) Service-specific (most specific first)
    # Blanket-blocked services (openclaw never restarted by Sentinel)
    for svc in NEVER_RESTART_SERVICES_BLANKET:
        if re.search(rf"systemctl\s+(restart|stop|start)\s+{re.escape(svc)}\b", cmd_l):
            return False, f"never-restart service: {svc}"

    # Position-gated services (ibgateway: off-hours + 0 positions only)
    for svc in POSITION_GATED_SERVICES:
        if re.search(rf"systemctl\s+restart\s+{re.escape(svc)}\b", cmd_l):
            if open_positions > 0:
                return False, f"{svc} restart blocked: {open_positions} open positions"
            if market_open:
                return False, f"{svc} restart blocked: market hours"

    # 3) Generic dangerous patterns (substring + word-boundary)
    substr_bad = ["sudo rm", "rm -rf /", "rm -rf ~", "mkfs", "> /dev/",
                  "curl ", "wget ", "| sh", "| bash", "eval ",
                  "systemctl stop"]
    for b in substr_bad:
        if b in cmd_l:
            return False, f"dangerous pattern: {b!r}"
    # Word-boundary (e.g. "dd " at start vs "ddclient")
    word_bad = [r"\bdd\b\s"]
    for pat in word_bad:
        if re.search(pat, cmd_l):
            return False, f"dangerous pattern: {pat!r}"

    return True, "ok"


def fix_id_for(p: dict) -> str:
    raw = json.dumps({
        "id": p.get("id"),
        "files": sorted(p.get("target_files") or []),
        "diff": p.get("diff", ""),
        "cmds": p.get("shell_commands") or [],
    }, sort_keys=True)
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def validate_proposal(p: dict, *, open_positions: int, market_open: bool) -> tuple[bool, str]:
    """Run ALL safety checks. Even if LLM tagged fix_class=safe_auto, this gate
    still runs. Reject if any path is in NEVER_MODIFY_PATHS or any command is unsafe.
    """
    fc = p.get("fix_class", "")
    if fc not in ("safe_auto", "propose_wait", "never_touch"):
        return False, f"invalid fix_class={fc!r}"
    diff = p.get("diff", "") or ""
    if diff.count("\n") > MAX_DIFF_LINES:
        return False, f"diff exceeds {MAX_DIFF_LINES} lines"
    for tf in p.get("target_files") or []:
        ok, why = is_path_allowed(tf)
        if not ok:
            return False, f"path not allowed: {tf} ({why})"
    for cmd in p.get("shell_commands") or []:
        ok, why = is_command_safe(cmd, open_positions=open_positions, market_open=market_open)
        if not ok:
            return False, f"unsafe shell command: {why}"
    return True, "ok"


# ── Built-in safe_auto: catalog reclassification ─────────────────────────

# Patterns matching catalog entries with severity=info that frequently show up
# at higher severity in errors.db due to upstream classifier drift.
RECLASSIFY_PATTERNS = [
    # (signature_substring, target_severity, resolved_by_note)
    ("fail2ban.filter", "info", "catalog-reclassify: fail2ban sshd noise"),
    ("fail2ban.actions", "info",
     "catalog-reclassify: fail2ban action subsystem (NOTICE Ban/Unban) noise"),
    ("[UFW BLOCK]", "info", "catalog-reclassify: UFW firewall noise"),
    ("Invalid user", "info", "catalog-reclassify: SSH brute-force noise"),
    ("health-monitor: restarting (reason: stale-socket)", "info",
     "catalog-reclassify: health-monitor stale-socket transient"),
    ("Disconnected from invalid user", "info", "catalog-reclassify: SSH noise"),
    ("Connection closed by invalid user", "info", "catalog-reclassify: SSH noise"),
    ("Disconnected from authenticating user", "info", "catalog-reclassify: SSH noise"),
    ("Received disconnect from", "info",
     "catalog-reclassify: SSH preauth disconnect noise (added 2026-05-04)"),
    ("review: empty Haiku response", "info",
     "catalog-reclassify: trade_reviewer transient Haiku failure (added 2026-05-04)"),
    ("Haiku call failed: timed out", "info",
     "catalog-reclassify: transient Haiku timeout (added 2026-05-04)"),
    # IBKR routine operational events that shouldn't carry error severity (added 2026-05-04)
    ("Connectivity between IBKR and Trader Workstation", "info",
     "catalog-reclassify: IBKR Error 1100 connectivity loss "
     "(nightly window expected per runbook-ibkr-nightly-restart.md)"),
    ("No security definition has been found", "info",
     "catalog-reclassify: option contract not on IBKR; agent skipped (routine)"),
    ("IBKRBroker: failed to qualify leg", "info",
     "catalog-reclassify: leg qualification failure; agent skipped (routine)"),
    ("Trade rejected: Already", "info",
     "catalog-reclassify: defensive risk-guard rejection (positions cap hit) — system working as designed (added 2026-05-05)"),
    ("Disconnecting authenticating user", "info",
     "catalog-reclassify: SSH brute-force 'Too many auth' noise (added 2026-05-05)"),
    # KARNA / OpenClaw security-rail noise (added 2026-05-09)
    ("exec denied: allowlist miss", "info",
     "catalog-reclassify: openclaw tool allowlist deny — security rail working as designed"),
    ("tools.profile (coding) allowlist contains unknown entries", "info",
     "catalog-reclassify: openclaw startup config warning (apply_patch/image_generate "
     "shipped but unavailable in current runtime) — cosmetic only"),
]


def reclassify_catalog_noise(dry_run: bool) -> dict:
    """For each known-noise pattern: any unresolved event at warning/error/critical
    matching the pattern gets severity flipped to 'info' AND resolved_at set.
    Returns counts of what was touched.
    """
    if not ERRORS_DB.exists():
        return {"reclassified": 0, "skipped_db_missing": True}
    if dry_run:
        # Just count what WOULD change
        try:
            con = sqlite3.connect(f"file:{ERRORS_DB}?mode=ro", uri=True)
            cur = con.cursor()
            total = 0
            for sig, _sev, _note in RECLASSIFY_PATTERNS:
                cur.execute(
                    "SELECT COUNT(*) FROM events "
                    "WHERE resolved_at IS NULL "
                    "AND severity IN ('warning','error','critical') "
                    "AND (signature LIKE ? OR message LIKE ?)",
                    (f"%{sig}%", f"%{sig}%"),
                )
                total += cur.fetchone()[0] or 0
            con.close()
            return {"reclassified": 0, "would_reclassify": total, "dry_run": True}
        except Exception as e:
            return {"reclassified": 0, "error": str(e)}

    counts = {"reclassified": 0, "by_pattern": {}}
    try:
        con = sqlite3.connect(ERRORS_DB)
        cur = con.cursor()
        for sig, target_sev, note in RECLASSIFY_PATTERNS:
            cur.execute(
                "UPDATE events SET severity=?, resolved_at=datetime('now'), resolved_by=? "
                "WHERE resolved_at IS NULL "
                "AND severity IN ('warning','error','critical') "
                "AND (signature LIKE ? OR message LIKE ?)",
                (target_sev, note, f"%{sig}%", f"%{sig}%"),
            )
            n = cur.rowcount or 0
            if n:
                counts["by_pattern"][sig[:40]] = n
                counts["reclassified"] += n
        con.commit()
        con.close()
    except Exception as e:
        return {"reclassified": counts["reclassified"], "error": str(e)}
    return counts


# ── Context bundle ──────────────────────────────────────────────────────

def query_errors_db_summary() -> dict:
    """Compact summary of errors.db state for the LLM."""
    if not ERRORS_DB.exists():
        return {"error": "errors.db missing"}
    try:
        con = sqlite3.connect(f"file:{ERRORS_DB}?mode=ro", uri=True)
        cur = con.cursor()
        # Counts by severity for unresolved
        cur.execute(
            "SELECT severity, COUNT(*) FROM events "
            "WHERE resolved_at IS NULL GROUP BY severity"
        )
        by_severity = dict(cur.fetchall())
        # Top 8 unresolved by count
        cur.execute(
            "SELECT id, severity, count, substr(signature,1,80), substr(message,1,150) "
            "FROM events WHERE resolved_at IS NULL "
            "ORDER BY count DESC LIMIT 8"
        )
        top_unresolved = [
            {"id": r[0], "severity": r[1], "count": r[2],
             "signature": r[3], "sample": r[4]}
            for r in cur.fetchall()
        ]
        con.close()
        return {"by_severity": by_severity, "top_unresolved": top_unresolved}
    except Exception as e:
        return {"error": str(e)}


def gather_context() -> dict:
    """Compact bundle for the LLM. Read-only."""
    bundle = {
        "now_utc": now_utc().isoformat(),
        "now_et": datetime.now(ET).isoformat(),
        "trading_window": is_trading_window(),
        "market_hours": is_market_hours(),
        "open_positions": read_open_positions_count(),
        "system_health": {},
        "errors_db": {},
        "test_results": {},
        "catalog_taxonomy": [],
        "weekly_report_age_days": None,
    }
    # System health report
    if HEALTH_REPORT_PATH.exists():
        try:
            bundle["system_health"] = json.loads(HEALTH_REPORT_PATH.read_text())
        except Exception as e:
            bundle["system_health"] = {"error": f"parse failed: {e}"}
    else:
        bundle["system_health"] = {"error": "report missing"}

    # errors.db summary
    bundle["errors_db"] = query_errors_db_summary()

    # Test results
    if TEST_RESULTS_PATH.exists():
        try:
            bundle["test_results"] = json.loads(TEST_RESULTS_PATH.read_text())
        except Exception:
            pass

    # Weekly report age
    if WEEKLY_REPORTS_DIR.exists():
        reports = sorted(WEEKLY_REPORTS_DIR.glob("*.md"),
                         key=lambda p: p.stat().st_mtime, reverse=True)
        if reports:
            age_days = (now_utc().timestamp() - reports[0].stat().st_mtime) / 86400
            bundle["weekly_report_age_days"] = round(age_days, 1)

    # Catalog taxonomy (compact)
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


# ── LLM call ────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are Sentinel, the autonomous infrastructure operations agent for KARNA + QuantAI (an autonomous options trading system on a VPS). You are a peer of trading agents Alpha, Beta, and Gamma but operations-focused — you do NOT trade. Your job is to keep the system healthy.

Inputs you receive:
- system-health-report.json (deterministic; 13 health checks)
- errors.db summary (by severity + top unresolved)
- catalog taxonomy (id/severity/auto_action/runbook)
- positions count (CRITICAL safety input)
- test results, weekly report age

Output STRICT JSON, no prose, no markdown fences:
{
  "summary": "one-line health summary",
  "findings": [
    {"id": "short-slug", "severity": "critical|high|medium|low|info",
     "what": "...", "evidence": "log file or check name"}
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

Classification rules (strict):
- fix_class="never_touch" — anything in {.env, openclaw, ibgateway, journal,
  /etc/systemd, autonomous_execution.py, beta_agent.py, gamma_agent.py,
  position_monitor.py, _broker_ibkr.py, broker.py}. ALWAYS classify these as
  observe-only.
- fix_class="propose_wait" — code edits to ANY file, novel/unknown errors,
  anything where you're <90% confident, anything matching catalog auto_action="none".
- fix_class="safe_auto" — ONLY for:
  * Stale lock/tmp file cleanup
  * Restart of NON-trading collector crons (collect_karna, collect_system,
    collect_quantai, collect_alpaca, etc.) — NEVER ibgateway or openclaw
  * Catalog reclassification of known-noise patterns (Sentinel runs this
    automatically as a built-in; do NOT propose it again)
  * IBKR gateway restart ONLY when ALL THREE: market_hours=false,
    open_positions=0, AND the report's ibkr_port check is in 'error' state

Conservatism rules:
- Default to propose_wait. Never_touch beats safe_auto beats propose_wait.
- diff must be <80 lines.
- shell_commands must NOT include sudo rm, rm -rf /, dd, mkfs, > /dev/,
  curl|sh, eval, or any pipe to a shell.
- If health is fine, return empty findings and proposals — silence is correct.
- Be silent unless acting. No "all clear" findings.
"""


def call_llm(bundle: dict, mode: str, dry_run: bool) -> dict:
    """Send bundle to Claude. Mode 'observe' uses Haiku, 'apply' uses Sonnet."""
    if dry_run:
        log_line("[DRY] skipping LLM call; returning empty plan")
        return {"summary": "dry-run, no LLM call", "findings": [], "proposals": []}

    sys.path.insert(0, str(SCRIPT_DIR))
    try:
        from _llm_call import call_llm_json
    except Exception as e:
        log_line(f"ERROR: cannot import _llm_call: {e}")
        return {"summary": "LLM import failed", "findings": [], "proposals": [], "_error": str(e)}

    if mode == "observe":
        model = "claude-haiku-4-5"
        tier = None
    else:
        model = "claude-sonnet-4-6"
        tier = "COMPLEX"

    user_msg = (
        "Review this snapshot and propose fixes per the schema. "
        f"Mode: {mode}. Be silent if nothing's wrong.\n\n"
        f"```json\n{json.dumps(bundle, indent=2)[:60000]}\n```"
    )
    result = call_llm_json(
        model=model, system=SYSTEM_PROMPT, user=user_msg,
        max_tokens=4000, caller="sentinel",
        tier=tier,
    )
    if not result:
        return {"summary": "LLM call failed after retries", "findings": [], "proposals": []}
    return result


# ── Proposal writing ────────────────────────────────────────────────────

def write_proposal(p: dict, channel_id_used: str | None,
                   message_id: str | None, dry_run: bool) -> Path | None:
    fid = fix_id_for(p)
    out = PENDING_DIR / f"{fid}.json"
    if out.exists():
        return out
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
    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record, indent=2))
    os.replace(tmp, out)
    return out


def post_proposal_card(rec_path: Path, rec: dict, dry_run: bool) -> tuple[str | None, str | None]:
    sev = rec.get("severity", "info")
    emoji = {"critical": "🚨", "high": "⚠️", "medium": "🟡",
             "low": "🟢", "info": "📋"}.get(sev, "📋")
    diff_lines = rec.get("diff", "").count("\n")
    lines_str = f", diff {diff_lines} lines" if diff_lines else ""
    cmds = len(rec.get("shell_commands") or [])
    cmds_str = f", {cmds} cmd(s)" if cmds else ""
    msg = (
        f"{emoji} Sentinel proposal `{rec['fix_id']}` (sev={sev}{lines_str}{cmds_str})\n"
        f"{rec['description'][:600]}\n"
        f"Runbook: `{rec.get('runbook', 'none')}`\n"
        f"React ✅ to apply at next post-close run, ❌ to dismiss. "
        f"Expires {rec['expires_at'][:16]}Z."
    )
    mid = post(CH_APPROVALS, msg, dry_run)
    if mid:
        react(CH_APPROVALS, mid, "✅", dry_run)
        react(CH_APPROVALS, mid, "❌", dry_run)
        rec["discord_message_id"] = mid
        rec["channel_id"] = _channel(CH_APPROVALS)
        if not dry_run:
            rec_path.write_text(json.dumps(rec, indent=2))
    return CH_APPROVALS, mid


# ── Apply / rollback ───────────────────────────────────────────────────

def backup_path(target: Path) -> Path:
    stamp = now_utc().strftime("%Y-%m-%d-%H%M%S")
    return target.with_suffix(target.suffix + f".bak.{stamp}-sentinel")


def apply_diff(diff_text: str, dry_run: bool) -> tuple[bool, str, list[str]]:
    if not diff_text.strip():
        return True, "no diff", []
    baks: list[str] = []
    files: list[str] = []
    for line in diff_text.splitlines():
        if line.startswith("--- ") or line.startswith("+++ "):
            parts = line.split(None, 1)
            if len(parts) == 2:
                f = parts[1].split("\t")[0]
                if f and f != "/dev/null":
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


def _is_advisory(rec: dict) -> bool:
    """A proposal is 'advisory' when it has no diff AND no shell commands —
    just a description suggesting the operator investigate something. These
    shouldn't show as 'applied' (they accomplish nothing) — they're
    acknowledged when ✅'d.

    Added 2026-05-04 after audit found 4 propose_wait advisories were
    marked 'applied' on day-one with empty receipts.
    """
    diff = (rec.get("diff") or "").strip()
    cmds = rec.get("shell_commands") or []
    return not diff and not cmds


def _validate_command_targets(cmd: str) -> tuple[bool, str]:
    """Reject commands referencing non-existent paths or systemd units.
    Catches LLM hallucinations like `cd /opt/karna` or
    `systemctl restart collect_clawroute.service` (when the cron job
    isn't actually a systemd unit).

    Returns (ok, reason). Soft-fails (returns ok=True) on systemctl
    list-unit-files probe failure so we don't block on infra glitches.
    """
    # 1. cd <absolute path>
    for m in re.finditer(r"\bcd\s+(\S+)", cmd):
        path = m.group(1).strip("\"'").rstrip(";|&")
        if path.startswith("/") and not Path(path).exists():
            return False, f"hallucinated path: {path}"

    # 2. systemctl <verb> <unit>
    pattern = r"systemctl\s+(?:--user\s+)?(?:restart|start|stop|reload|status)\s+(\S+)"
    for m in re.finditer(pattern, cmd):
        unit = m.group(1).strip("\"'").rstrip(";|&")
        unit_full = unit if (unit.endswith(".service") or unit.endswith(".timer")) else unit + ".service"
        try:
            result = subprocess.run(
                ["systemctl", "list-unit-files", unit_full],
                capture_output=True, text=True, timeout=5,
            )
        except Exception:
            # Soft-fail: probe error → don't block on infra glitch
            continue
        combined = (result.stdout or "") + (result.stderr or "")
        if "0 unit files" in combined or unit_full not in combined:
            return False, f"hallucinated systemd unit: {unit_full}"

    return True, "ok"


def execute_proposal(rec: dict, dry_run: bool) -> tuple[str, dict | None]:
    """Execute a fix proposal.

    Returns (outcome, receipt) where outcome is one of:
      - "applied"      : diff/commands ran successfully; receipt populated
      - "failed"       : diff/command failed; receipt has the failure detail
      - "acknowledged" : advisory card with no diff/commands — receipt None

    Outcome was previously bool; expanded 2026-05-04 to distinguish
    advisory acknowledgments from actual fixes.
    """
    fid = rec["fix_id"]

    # Advisory short-circuit: nothing to do, just acknowledge
    if _is_advisory(rec):
        log_line(f"acknowledged advisory {fid}: {rec['description'][:120]}")
        return "acknowledged", None

    receipt = {
        "fix_id": fid,
        "applied_at": now_utc().isoformat(),
        "fix_class": rec["fix_class"],
        "description": rec["description"],
        "shell_results": [],
        "patch_result": None,
        "backups": [],
    }
    if rec.get("diff"):
        ok, detail, baks = apply_diff(rec["diff"], dry_run)
        receipt["patch_result"] = {"ok": ok, "detail": detail}
        receipt["backups"].extend(baks)
        if not ok:
            return "failed", receipt
    for cmd in rec.get("shell_commands") or []:
        ok, detail = run_shell(cmd, dry_run)
        receipt["shell_results"].append({"cmd": cmd, "ok": ok, "detail": detail})
        if not ok:
            return "failed", receipt
    return "applied", receipt


def consume_pending(state: dict, dry_run: bool, *,
                    open_positions: int, market_open: bool) -> dict:
    """Iterate pending_fixes; apply approved + safe-auto. Re-validates each
    proposal at consume time (positions / market state may have changed).

    Outcomes tracked: applied (real work done), acknowledged (advisory ✅'d
    with nothing to execute), failed, quarantined, expired, hallucinated
    (paths/units that don't exist), skipped_unapproved, blocked_by_safety.
    """
    counts = {"applied": 0, "acknowledged": 0, "skipped_unapproved": 0,
              "expired": 0, "quarantined": 0, "failed": 0,
              "blocked_by_safety": 0, "hallucinated": 0}
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
        try:
            if now_utc() > datetime.fromisoformat(rec.get("expires_at", "")):
                counts["expired"] += 1
                if not dry_run:
                    path.unlink()
                continue
        except Exception:
            pass

        # Re-validate at consume time (positions might have changed since proposal)
        ok, why = validate_proposal(rec, open_positions=open_positions,
                                    market_open=market_open)
        if not ok:
            log_line(f"REVALIDATION blocked {fid}: {why}")
            counts["blocked_by_safety"] += 1
            continue

        will_apply = rec.get("auto_apply", False)
        if not will_apply:
            mid = rec.get("discord_message_id")
            if mid and has_human_reaction(CH_APPROVALS, mid, "✅"):
                will_apply = True
        if not will_apply:
            counts["skipped_unapproved"] += 1
            continue

        # Pre-execution check: catch hallucinated paths/services BEFORE running
        # (added 2026-05-04 — Sentinel proposed `cd /opt/karna` and
        # `systemctl restart collect_clawroute.service`, neither existed)
        hallucination_found = False
        for cmd in rec.get("shell_commands") or []:
            valid, vreason = _validate_command_targets(cmd)
            if not valid:
                log_line(f"hallucinated — skipped {fid}: {vreason} :: {cmd[:120]}")
                counts["hallucinated"] += 1
                if not dry_run:
                    path.unlink()  # don't keep retrying hallucinated commands
                post(CH_HEALTH,
                     f"⚠️ Sentinel skipped `{fid}` — {vreason}\n"
                     f"   Command was: `{cmd[:140]}`", dry_run)
                hallucination_found = True
                break
        if hallucination_found:
            continue

        n_files = len(rec.get("target_files") or [])
        n_cmds = len(rec.get("shell_commands") or [])
        if file_mut + n_files > MAX_FILE_MUTATIONS_PER_RUN:
            log_line(f"file-mutation cap reached; deferring {fid}")
            continue
        if (svc_restarts + n_cmds > MAX_SERVICE_RESTARTS_PER_RUN and
                "systemctl" in " ".join(rec.get("shell_commands") or [])):
            log_line(f"service-restart cap reached; deferring {fid}")
            continue

        outcome, receipt = execute_proposal(rec, dry_run)
        attempts = state.setdefault("attempts", {})
        attempts[fid] = attempts.get(fid, 0) + 1

        if outcome == "acknowledged":
            # Advisory card with no diff/commands — log + remove pending, no
            # receipt, no count toward 'applied'
            counts["acknowledged"] += 1
            if not dry_run:
                path.unlink()
            attempts[fid] = 0
            post(CH_LOGS,
                 f"📋 Sentinel acknowledged advisory `{fid}` — "
                 f"{rec['description'][:160]}", dry_run)
            continue

        if outcome == "applied":
            counts["applied"] += 1
            receipts.append(receipt)
            file_mut += n_files
            svc_restarts += n_cmds
            if not dry_run:
                APPLIED_DIR.mkdir(parents=True, exist_ok=True)
                FIX_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
                receipt_path = APPLIED_DIR / f"{fid}.json"
                receipt_path.write_text(json.dumps(receipt, indent=2))
                hist_name = f"{now_utc().strftime('%Y-%m-%d-%H%M%S')}-{fid}.json"
                shutil.copy2(receipt_path, FIX_HISTORY_DIR / hist_name)
                path.unlink()
            attempts[fid] = 0
            post(CH_LOGS, f"✅ Sentinel applied `{fid}` — "
                          f"{rec['description'][:160]}", dry_run)
        else:  # failed
            counts["failed"] += 1
            receipts.append(receipt)
            if attempts[fid] >= ATTEMPT_BUDGET:
                state.setdefault("quarantined", []).append(fid)
                counts["quarantined"] += 1
                post(CH_HEALTH,
                     f"🚨 Sentinel QUARANTINED `{fid}` after {ATTEMPT_BUDGET} "
                     f"failures: {rec['description'][:160]}", dry_run)
            else:
                post(CH_HEALTH,
                     f"⚠️ Sentinel fix `{fid}` failed "
                     f"(attempt {attempts[fid]}/{ATTEMPT_BUDGET})", dry_run)

    counts["receipts"] = receipts
    return counts


# ── Digest helpers ──────────────────────────────────────────────────

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


def drain_digest(dry_run: bool) -> dict:
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
            try:
                buf.unlink()
            except Exception:
                pass
    return summary


# ── Mode runners ──────────────────────────────────────────────────────

def next_scheduled_et() -> str:
    """Compute the next scheduled slot (for dashboard display)."""
    et = datetime.now(ET)
    candidates = []
    for offset in range(0, 8):
        day = et + timedelta(days=offset)
        table = SCHEDULE_ET["weekend"] if day.weekday() >= 5 else SCHEDULE_ET["weekday"]
        for (h, m), mode in table.items():
            slot = day.replace(hour=h, minute=m, second=0, microsecond=0)
            if slot > et:
                candidates.append((slot, mode))
    if not candidates:
        return "?"
    candidates.sort()
    slot, mode = candidates[0]
    return f"{slot.strftime('%a %H:%M')} ET ({mode})"


def run_observe(dry_run: bool, state: dict) -> tuple[str, dict]:
    bundle = gather_context()
    plan = call_llm(bundle, "observe", dry_run)
    open_pos = bundle["open_positions"]
    market_open = bundle["market_hours"]
    queued = []
    blocked = 0
    for p in plan.get("proposals", []):
        ok, why = validate_proposal(p, open_positions=open_pos, market_open=market_open)
        if not ok:
            log_line(f"reject proposal: {why}")
            blocked += 1
            continue
        if p["fix_class"] == "never_touch":
            continue
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
    log_line(f"observe done: {len(plan.get('findings', []))} findings, "
             f"{len(queued)} queued, {blocked} safety-blocked")
    counts = {"findings": len(plan.get("findings", [])),
              "queued": len(queued), "safety_blocked": blocked}
    return summary, counts


def run_apply(dry_run: bool, state: dict) -> tuple[str, dict]:
    if is_trading_window():
        log_line("trading window detected at apply-time; downgrading to observe")
        return run_observe(dry_run, state)

    # Built-in safe_auto: catalog reclassification (deterministic, no LLM)
    reclassify = reclassify_catalog_noise(dry_run)
    if reclassify.get("reclassified", 0) > 0 and not dry_run:
        post(CH_LOGS, f"🧹 Sentinel reclassified {reclassify['reclassified']} "
                      f"errors.db events as info: {list(reclassify.get('by_pattern', {}).keys())}",
             dry_run)

    # Fresh observe to generate proposals
    fresh_summary, observe_counts = run_observe(dry_run, state)

    # Re-read positions/market at consume time
    open_pos = read_open_positions_count()
    market_open = is_market_hours()
    counts = consume_pending(state, dry_run, open_positions=open_pos, market_open=market_open)
    log_line(f"apply done: {counts}")

    # Drain digest at the post-close apply (16:15 ET = 20:15 UTC during DST)
    et_hour = datetime.now(ET).hour
    if et_hour >= 16:
        digest = drain_digest(dry_run)
        msg = (
            f"📊 Sentinel daily digest {digest['date']}\n"
            f"Mid-trading observations: {len(digest['findings'])} findings, "
            f"{digest['proposals_queued']} proposals queued.\n"
            f"Post-close: {counts['applied']} applied, "
            f"{counts['skipped_unapproved']} awaiting ✅, "
            f"{counts['failed']} failed, {counts['quarantined']} quarantined, "
            f"{counts['blocked_by_safety']} safety-blocked. "
            f"Reclassified {reclassify.get('reclassified', 0)} catalog noise events."
        )
        # Only post if anything actually happened
        if (counts["applied"] or counts["failed"] or counts["quarantined"]
                or reclassify.get("reclassified", 0)):
            post(CH_HEALTH, msg, dry_run)
    else:
        # Pre-market apply (8:30 AM ET = 12:30 UTC during DST)
        if (counts["applied"] or counts["failed"] or counts["quarantined"]
                or reclassify.get("reclassified", 0)):
            msg = (f"🌅 Sentinel pre-market: {counts['applied']} applied, "
                   f"{counts['skipped_unapproved']} awaiting ✅, "
                   f"{counts['failed']} failed. "
                   f"Reclassified {reclassify.get('reclassified', 0)} noise events.")
            post(CH_HEALTH, msg, dry_run)

    counts["reclassify"] = reclassify
    return fresh_summary, counts


# ── Dashboard tile ──────────────────────────────────────────────────

def write_dashboard(state: dict, last_summary: str, last_mode: str,
                    last_actions: int, dry_run: bool) -> None:
    pending = list(PENDING_DIR.glob("*.json"))
    quarantined = state.get("quarantined", [])
    overall = "warning" if quarantined else "ok"
    payload = {
        "last_updated": now_utc().isoformat(),
        "status": overall,
        "data": {
            "last_run_et": datetime.now(ET).strftime("%a %H:%M ET"),
            "mode": last_mode,
            "actions_taken": last_actions,
            "summary": last_summary[:200],
            "pending_count": len(pending),
            "pending_ids": [p.stem for p in pending][:20],
            "quarantined": quarantined[-20:],
            "next_scheduled_run_et": next_scheduled_et(),
            "last_run": state.get("last_run", {}),
        },
    }
    if dry_run:
        log_line(f"[DRY] would write dashboard tile: pending={len(pending)} quarantined={len(quarantined)}")
        return
    try:
        DASHBOARD_STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = SENTINEL_TILE_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        os.replace(tmp, SENTINEL_TILE_PATH)
    except Exception as e:
        log_line(f"WARN: dashboard write failed: {e}")


# ── Operator commands ──────────────────────────────────────────────

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
        orig = re.sub(r"\.bak\.[0-9-]+-(?:sentinel|autoheal)$", "", bak)
        if dry_run:
            print(f"[DRY] would restore {bak} -> {orig}")
            continue
        shutil.copy2(bak_p, orig)
        print(f"restored {orig} from {bak}")
    return 0


def cmd_reset(fix_id: str, dry_run: bool) -> int:
    state = load_state()
    q = state.get("quarantined", [])
    if fix_id in q:
        q.remove(fix_id)
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
            print(f"  {rec['fix_id']:14s} {rec['fix_class']:14s} "
                  f"sev={rec['severity']:8s} {rec['description'][:60]}")
        except Exception:
            pass
    print(f"quarantined: {state.get('quarantined', [])}")
    print(f"last_run: {state.get('last_run', {})}")
    et_now = datetime.now(ET).strftime("%a %H:%M ET")
    print(f"now: {et_now} | mode-from-clock: {resolve_mode_from_clock()}")
    print(f"next scheduled: {next_scheduled_et()}")
    return 0


# ── Main ──────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mode", choices=("apply", "observe"))
    ap.add_argument("--auto", action="store_true",
                    help="Read ET clock; dispatch mode or exit silently")
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

    # --auto wrapper: resolve mode from ET clock, exit if no slot matches
    if args.auto:
        mode = resolve_mode_from_clock()
        if mode is None:
            return 0  # silent exit; cron fires every 15 min
        args.mode = mode

    if args.mode is None:
        ap.error("must specify --mode or --auto")
        return 2

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PENDING_DIR.mkdir(exist_ok=True)
    APPLIED_DIR.mkdir(exist_ok=True)
    DIGEST_DIR.mkdir(exist_ok=True)
    FIX_HISTORY_DIR.mkdir(exist_ok=True)

    log_line(f"sentinel start mode={args.mode} dry_run={args.dry_run}")
    state = load_state()
    state.setdefault("last_run", {})

    with RunLock(LOCK_FILE):
        if args.mode == "observe":
            summary, counts = run_observe(args.dry_run, state)
            actions = counts.get("queued", 0)
        else:
            summary, counts = run_apply(args.dry_run, state)
            actions = counts.get("applied", 0)

        state["last_run"][args.mode] = now_utc().isoformat()
        save_state(state, args.dry_run)
        write_dashboard(state, summary, args.mode, actions, args.dry_run)

    log_line(f"sentinel done mode={args.mode} actions={actions}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
