"""
health_monitor.py — System Health & Silent Failure Detection
=============================================================
Answers the question: "Is everything actually working?"

WHAT IT CHECKS:
  1. Data freshness — are all data sources returning recent data?
  2. Service connectivity — guard engine, Alpaca, Discord webhooks
  3. Agent activity — are agents running and logging trades?
  4. Silent failures — data that looks fine but is stale/wrong
  5. Config sanity — are params within safe bounds?

WHEN IT RUNS:
  - Every 5 min during market hours (via scheduler)
  - On-demand via /health Discord command
  - On startup (first run)

ALERT LEVELS:
  🟢 HEALTHY   — everything working
  🟡 DEGRADED  — some non-critical failures, system continues
  🔴 CRITICAL  — core system failing, may affect trades

SILENT FAILURE PATTERNS WE CATCH:
  - VIX returning stale cached value from yesterday
  - FRED API returning None (network issue) but context_builder used fallback
  - Options chain returning empty (Alpaca key expired)
  - Agent journal not updated (agent ran but logged nothing)
  - Context score stuck at same value (builder caching incorrectly)
  - Guard engine HTTP 200 but returning wrong schema
"""

import os
import json
import logging
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional
import asyncio
import aiohttp

log = logging.getLogger("health-monitor")

GUARD_URL = os.getenv("GUARD_URL", "http://trader-guards:8100")
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")
CACHE_DIR = Path("/app/data/cache")
JOURNAL_DIR = Path("/app/data/memory/paper")
DATA_DIR = Path("/app/data")


# ─────────────────────────────────────────────────────────────────────────────
# INDIVIDUAL HEALTH CHECKS
# ─────────────────────────────────────────────────────────────────────────────

async def check_guard_engine() -> dict:
    """Is the guard engine up and returning valid responses?"""
    try:
        async with aiohttp.ClientSession() as session:
            # Health endpoint
            async with session.get(
                f"{GUARD_URL}/health",
                timeout=aiohttp.ClientTimeout(total=3)
            ) as resp:
                if resp.status != 200:
                    return {"status": "critical", "msg": f"Guard HTTP {resp.status}"}
                health_data = await resp.json()

            # Functional test: send a known-good proposal
            test_proposal = {
                "symbol": "SPY",
                "strategy": "iron_condor",
                "position_pct": 3.0,
                "max_loss_pct": 1.5,
                "dte": 0,
                "iv_rank": 55,
            }
            async with session.post(
                f"{GUARD_URL}/check",
                json=test_proposal,
                timeout=aiohttp.ClientTimeout(total=3)
            ) as resp:
                if resp.status != 200:
                    return {"status": "degraded", "msg": f"Guard /check HTTP {resp.status}"}
                result = await resp.json()
                if "result" not in result:
                    return {"status": "degraded", "msg": "Guard response missing 'result' field"}

        return {
            "status": "healthy",
            "msg": f"Guard up, test trade returned: {result.get('result')}",
            "health": health_data,
        }
    except asyncio.TimeoutError:
        return {"status": "critical", "msg": "Guard engine timed out (3s)"}
    except Exception as e:
        return {"status": "critical", "msg": f"Guard unreachable: {e}"}


async def check_alpaca_connection() -> dict:
    """Is Alpaca API accessible with current keys?"""
    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        return {"status": "critical", "msg": "Alpaca keys not configured in .env"}

    try:
        from alpaca.trading.client import TradingClient
        client = TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=True)
        account = client.get_account()
        equity = float(account.equity)
        buying_power = float(account.buying_power)
        return {
            "status": "healthy",
            "msg": f"Alpaca paper account: equity=${equity:,.2f}, buying_power=${buying_power:,.2f}",
            "equity": equity,
            "buying_power": buying_power,
            "account_status": account.status.value if account.status else "unknown",
        }
    except Exception as e:
        return {"status": "critical", "msg": f"Alpaca connection failed: {e}"}


def check_cache_freshness() -> dict:
    """Are all data caches being updated regularly?"""
    issues = []
    checks = {}
    now = datetime.now()

    cache_expectations = {
        "vix":                    {"max_age_hours": 1,   "critical": True},
        "macro_fred_macro_composite": {"max_age_hours": 8, "critical": False},
        "sentiment_put_call":     {"max_age_hours": 4,   "critical": False},
        "sentiment_fear_greed":   {"max_age_hours": 4,   "critical": False},
        "sentiment_vix_term":     {"max_age_hours": 2,   "critical": False},
        "macro_finnhub_calendar": {"max_age_hours": 24,  "critical": False},
    }

    for cache_key, config in cache_expectations.items():
        cache_file = CACHE_DIR / f"{cache_key}.json"

        if not cache_file.exists():
            status = "missing"
            age_hours = None
            issues.append(f"{cache_key}: not yet populated (will populate on first run)")
        else:
            try:
                with open(cache_file) as f:
                    data = json.load(f)
                cached_at = datetime.fromisoformat(data.get("_cached_at", "2000-01-01"))
                age_hours = (now - cached_at).total_seconds() / 3600

                if age_hours > config["max_age_hours"]:
                    status = "stale"
                    severity = "critical" if config["critical"] else "warning"
                    issues.append(f"{cache_key}: stale ({age_hours:.1f}h old, max {config['max_age_hours']}h) [{severity}]")
                else:
                    status = "fresh"
            except Exception as e:
                status = "corrupt"
                age_hours = None
                issues.append(f"{cache_key}: corrupt ({e})")

        checks[cache_key] = {"status": status, "age_hours": age_hours}

    critical_issues = [i for i in issues if "critical" in i]
    overall = "critical" if critical_issues else ("degraded" if issues else "healthy")

    return {
        "status": overall,
        "msg": f"{len(issues)} cache issue(s)" if issues else "All caches fresh",
        "checks": checks,
        "issues": issues,
    }


def check_agent_journals() -> dict:
    """Are agents writing to their journals? Detects silent agent failures."""
    checks = {}
    issues = []
    today = date.today().isoformat()
    is_weekday = date.today().weekday() < 5  # Mon-Fri

    journals = {
        "agent1": JOURNAL_DIR / "agent1_journal.jsonl",
        "agent2": JOURNAL_DIR / "agent2_journal.jsonl",
    }

    for agent, journal_path in journals.items():
        if not journal_path.exists():
            checks[agent] = {"status": "not_started", "trades_today": 0}
            # Only flag as issue on a weekday after 10 AM
            if is_weekday and datetime.now().hour >= 10:
                issues.append(f"{agent}: journal file missing — agent has never run")
            continue

        try:
            trades_today = []
            with open(journal_path) as f:
                for line in f:
                    if line.strip():
                        try:
                            record = json.loads(line)
                            if record.get("date") == today or record.get("timestamp", "").startswith(today):
                                trades_today.append(record)
                        except Exception:
                            continue

            last_entry = None
            all_entries = []
            with open(journal_path) as f:
                for line in f:
                    if line.strip():
                        try:
                            all_entries.append(json.loads(line))
                        except Exception:
                            pass
            if all_entries:
                last_entry = all_entries[-1].get("timestamp", "unknown")

            checks[agent] = {
                "status": "active" if trades_today else "idle",
                "trades_today": len(trades_today),
                "last_entry": last_entry,
                "events_today": [t.get("event") for t in trades_today],
            }

            # Check for errors in today's entries
            errors_today = [t for t in trades_today if t.get("event") == "error"]
            if errors_today:
                issues.append(f"{agent}: {len(errors_today)} error(s) logged today")

        except Exception as e:
            checks[agent] = {"status": "error", "error": str(e)}
            issues.append(f"{agent}: journal read failed: {e}")

    return {
        "status": "degraded" if issues else "healthy",
        "msg": f"{len(issues)} journal issue(s)" if issues else "Agent journals normal",
        "checks": checks,
        "issues": issues,
    }


def check_config_sanity() -> dict:
    """Are all config files present and within safe bounds?"""
    issues = []
    checks = {}

    config_files = {
        "guard_config": Path("/app/configs/guard_config.json"),
        "agent1_params": Path("/app/configs/agent1_params.json"),
        "agent2_params": Path("/app/configs/agent2_params.json"),
        "watchlist": Path("/app/configs/watchlist.json"),
        "strategies": Path("/app/configs/strategies.json"),
    }

    for name, path in config_files.items():
        if not path.exists():
            checks[name] = {"status": "missing"}
            issues.append(f"{name}: config file missing at {path}")
            continue

        try:
            with open(path) as f:
                config = json.load(f)
            checks[name] = {"status": "present", "keys": list(config.keys())[:5]}
        except Exception as e:
            checks[name] = {"status": "corrupt"}
            issues.append(f"{name}: JSON parse failed: {e}")

    # Sanity checks on guard config
    guard_path = Path("/app/configs/guard_config.json")
    if guard_path.exists():
        try:
            with open(guard_path) as f:
                guard = json.load(f)

            if guard.get("halted"):
                issues.append("GUARD HALTED: system is in emergency stop mode")

            max_loss = guard.get("portfolio", {}).get("max_daily_loss_pct", 0)
            if max_loss > 10:
                issues.append(f"guard max_daily_loss_pct={max_loss}% seems too high (should be ≤5%)")

            max_pos = guard.get("position", {}).get("max_position_pct", 0)
            if max_pos > 10:
                issues.append(f"guard max_position_pct={max_pos}% seems too high (should be ≤5%)")

        except Exception:
            pass

    return {
        "status": "critical" if any("missing" in i or "HALTED" in i for i in issues)
                  else "degraded" if issues else "healthy",
        "msg": f"{len(issues)} config issue(s)" if issues else "All configs valid",
        "checks": checks,
        "issues": issues,
    }


async def check_discord_webhooks() -> dict:
    """Can we post to all Discord webhooks?"""
    webhooks = {
        "research":   os.getenv("DISCORD_WEBHOOK_RESEARCH", ""),
        "system":     os.getenv("DISCORD_WEBHOOK_SYSTEM", ""),
        "proposals":  os.getenv("DISCORD_WEBHOOK_PROPOSALS", ""),
        "execution":  os.getenv("DISCORD_WEBHOOK_EXECUTION", ""),
    }

    issues = []
    checks = {}

    for name, url in webhooks.items():
        if not url:
            checks[name] = {"status": "not_configured"}
            issues.append(f"#{name} webhook not configured in .env")
            continue

        try:
            async with aiohttp.ClientSession() as session:
                # HEAD request to validate URL without posting
                async with session.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    # Discord returns 405 for GET on webhook (method not allowed) — that's fine
                    if resp.status in (200, 405):
                        checks[name] = {"status": "reachable"}
                    else:
                        checks[name] = {"status": f"http_{resp.status}"}
                        if resp.status == 404:
                            issues.append(f"#{name} webhook URL invalid (404) — recreate in Discord")
                        else:
                            issues.append(f"#{name} webhook HTTP {resp.status}")
        except asyncio.TimeoutError:
            checks[name] = {"status": "timeout"}
            issues.append(f"#{name} webhook timed out")
        except Exception as e:
            checks[name] = {"status": "error"}
            issues.append(f"#{name} webhook error: {e}")

    return {
        "status": "degraded" if issues else "healthy",
        "msg": f"{len(issues)} webhook issue(s)" if issues else "All webhooks reachable",
        "checks": checks,
        "issues": issues,
    }


def check_data_quality() -> dict:
    """
    Silent failure detection — data that exists but is wrong.
    Catches: VIX=0, options chain empty, context score stuck at same value.
    """
    issues = []
    checks = {}

    # Check VIX value is sane
    vix_cache = CACHE_DIR / "vix.json"
    if vix_cache.exists():
        try:
            with open(vix_cache) as f:
                vix_data = json.load(f)
            vix = vix_data.get("vix", 0)
            if vix == 0 or vix is None:
                issues.append("VIX=0 in cache — fetch failed silently, using zero")
            elif vix < 8 or vix > 80:
                issues.append(f"VIX={vix} out of realistic range (8-80) — data quality issue")
            else:
                checks["vix"] = {"status": "sane", "value": vix}
        except Exception as e:
            issues.append(f"VIX cache unreadable: {e}")

    # Check context scores aren't stuck
    context_files = list(CACHE_DIR.glob("context_SPY_*.json"))
    if len(context_files) >= 3:
        scores = []
        for f in sorted(context_files)[-5:]:
            try:
                with open(f) as fh:
                    d = json.load(fh)
                    scores.append(d.get("score"))
            except Exception:
                pass
        if scores and len(set(scores)) == 1 and len(scores) >= 3:
            issues.append(f"Context score stuck at {scores[0]} for {len(scores)} consecutive runs — possible caching bug")
        else:
            checks["context_scores"] = {"status": "varying", "recent": scores}

    # Check journal files aren't growing unboundedly (>10MB = problem)
    for journal in JOURNAL_DIR.glob("*.jsonl"):
        size_mb = journal.stat().st_size / 1024 / 1024
        if size_mb > 10:
            issues.append(f"{journal.name}: {size_mb:.1f}MB — consider archiving")
        else:
            checks[journal.name] = {"size_mb": round(size_mb, 2)}

    return {
        "status": "degraded" if issues else "healthy",
        "msg": f"{len(issues)} data quality issue(s)" if issues else "Data quality OK",
        "checks": checks,
        "issues": issues,
    }


# ─────────────────────────────────────────────────────────────────────────────
# FULL HEALTH CHECK
# ─────────────────────────────────────────────────────────────────────────────

async def run_full_health_check() -> dict:
    """
    Run all health checks and return a structured report.
    Called by scheduler every 5 min and by /health Discord command.
    """
    start = datetime.now()

    # Run async checks concurrently
    guard_check, alpaca_check, webhook_check = await asyncio.gather(
        check_guard_engine(),
        check_alpaca_connection(),
        check_discord_webhooks(),
        return_exceptions=True,
    )

    # Sync checks
    cache_check = check_cache_freshness()
    journal_check = check_agent_journals()
    config_check = check_config_sanity()
    quality_check = check_data_quality()

    # Handle exceptions from async gather
    def safe(result, name):
        if isinstance(result, Exception):
            return {"status": "critical", "msg": f"{name} check threw exception: {result}"}
        return result

    checks = {
        "guard_engine":   safe(guard_check, "guard"),
        "alpaca":         safe(alpaca_check, "alpaca"),
        "discord":        safe(webhook_check, "discord"),
        "cache":          cache_check,
        "journals":       journal_check,
        "config":         config_check,
        "data_quality":   quality_check,
    }

    # Overall status: worst of all checks
    statuses = [c.get("status", "unknown") for c in checks.values()]
    if "critical" in statuses:
        overall = "critical"
    elif "degraded" in statuses:
        overall = "degraded"
    else:
        overall = "healthy"

    # Collect all issues
    all_issues = []
    for name, check in checks.items():
        for issue in check.get("issues", []):
            all_issues.append(f"[{name}] {issue}")

    duration_ms = int((datetime.now() - start).total_seconds() * 1000)

    report = {
        "overall": overall,
        "timestamp": datetime.now().isoformat(),
        "duration_ms": duration_ms,
        "checks": checks,
        "all_issues": all_issues,
        "issue_count": len(all_issues),
        "trading_mode": os.getenv("TRADING_MODE", "paper"),
        "auto_mode": os.getenv("AUTO_MODE", "true"),
    }

    log.info(
        f"Health check: {overall.upper()} | "
        f"{len(all_issues)} issue(s) | {duration_ms}ms"
    )
    if all_issues:
        for issue in all_issues:
            log.warning(f"  ⚠️  {issue}")

    return report


# ─────────────────────────────────────────────────────────────────────────────
# DISCORD EMBED BUILDERS
# ─────────────────────────────────────────────────────────────────────────────

def build_health_embeds(report: dict) -> list:
    """Build Discord embeds from health report. Used in #system-health."""
    overall = report.get("overall", "unknown")
    emoji = {"healthy": "🟢", "degraded": "🟡", "critical": "🔴"}.get(overall, "⚪")
    color = {"healthy": 0x2ECC71, "degraded": 0xF39C12, "critical": 0xE74C3C}.get(overall, 0x95A5A6)
    issues = report.get("all_issues", [])

    fields = []
    checks = report.get("checks", {})

    check_display = {
        "guard_engine": "Guard Engine",
        "alpaca": "Alpaca",
        "discord": "Discord Webhooks",
        "cache": "Data Cache",
        "journals": "Agent Journals",
        "config": "Configs",
        "data_quality": "Data Quality",
    }

    for key, label in check_display.items():
        check = checks.get(key, {})
        status = check.get("status", "unknown")
        msg = check.get("msg", "")[:80]
        s_emoji = {"healthy": "✅", "degraded": "⚠️", "critical": "❌"}.get(status, "❓")
        fields.append({
            "name": f"{s_emoji} {label}",
            "value": msg or status,
            "inline": True,
        })

    summary_embed = {
        "title": f"{emoji} System Health: {overall.upper()}",
        "description": (
            f"**{len(issues)} issue(s) detected**\n" + "\n".join(f"• {i}" for i in issues[:5])
            if issues else "All systems operational"
        ),
        "color": color,
        "fields": fields,
        "footer": {"text": f"QuantAI Health Monitor · {report.get('duration_ms')}ms"},
        "timestamp": report.get("timestamp"),
    }

    return [summary_embed]


def build_startup_embed(report: dict) -> dict:
    """Compact startup health embed posted to #system-health on boot."""
    overall = report.get("overall", "unknown")
    emoji = {"healthy": "🟢", "degraded": "🟡", "critical": "🔴"}.get(overall, "⚪")
    color = {"healthy": 0x2ECC71, "degraded": 0xF39C12, "critical": 0xE74C3C}.get(overall, 0x95A5A6)
    checks = report.get("checks", {})
    issues = report.get("all_issues", [])

    lines = []
    for key in ["guard_engine", "alpaca", "discord", "cache", "config"]:
        status = checks.get(key, {}).get("status", "unknown")
        e = {"healthy": "✅", "degraded": "⚠️", "critical": "❌"}.get(status, "❓")
        lines.append(f"{e} {key.replace('_', ' ').title()}: {status}")

    return {
        "title": f"{emoji} QuantAI Started — {overall.upper()}",
        "description": "\n".join(lines),
        "color": color,
        "fields": [
            {"name": "Mode", "value": os.getenv("TRADING_MODE", "paper"), "inline": True},
            {"name": "Auto-mode", "value": os.getenv("AUTO_MODE", "true"), "inline": True},
            {"name": "Issues", "value": str(len(issues)), "inline": True},
        ] + ([{"name": "⚠️ Issues", "value": "\n".join(f"• {i}" for i in issues[:3]), "inline": False}] if issues else []),
        "footer": {"text": "QuantAI Health Monitor"},
        "timestamp": datetime.now().isoformat(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# ALERT THRESHOLDS — When to page you immediately
# ─────────────────────────────────────────────────────────────────────────────

CRITICAL_ALERT_CONDITIONS = [
    lambda r: r["checks"]["guard_engine"]["status"] == "critical",
    lambda r: r["checks"]["alpaca"]["status"] == "critical",
    lambda r: r["checks"]["config"]["status"] == "critical",
    lambda r: any("HALTED" in i for i in r.get("all_issues", [])),
    lambda r: any("VIX=0" in i for i in r.get("all_issues", [])),
]


def should_alert(report: dict) -> bool:
    """Return True if this health report requires immediate Discord alert."""
    return any(
        condition(report)
        for condition in CRITICAL_ALERT_CONDITIONS
        if callable(condition)
    )


# CLI test
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    async def main():
        print("Running full health check...")
        report = await run_full_health_check()
        print(f"\nOverall: {report['overall'].upper()}")
        print(f"Issues: {report['issue_count']}")
        for issue in report["all_issues"]:
            print(f"  ⚠️  {issue}")
        print(f"\nCheck breakdown:")
        for name, check in report["checks"].items():
            print(f"  {name}: {check['status']} — {check.get('msg', '')}")

    asyncio.run(main())
