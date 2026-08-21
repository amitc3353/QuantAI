"""
Self-Improvement Engine
========================
Makes the system smarter every day.

1. Daily: If EOD score < 90, generate a fix PR on GitHub
2. Weekly (Friday EOD): Aggregate the week, extract patterns, propose rule changes
3. All changes go through paper validation — never auto-merge

Runs as part of the orchestrator's scheduled tasks.
"""

import os
import json
import logging
import asyncio
from datetime import datetime, timezone, timedelta
from pathlib import Path

import aiohttp

log = logging.getLogger("self-improve")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
SONNET_MODEL = os.getenv("CLAUDE_SONNET_MODEL", "claude-sonnet-4-20250514")
HAIKU_MODEL = os.getenv("CLAUDE_HAIKU_MODEL", "claude-haiku-4-5-20251001")
TRADING_MODE = os.getenv("TRADING_MODE", "paper")

# GitHub — for auto-PR
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO = os.getenv("GITHUB_REPO", "amitc3353/QuantAI")

WEBHOOK_SYSTEM = os.getenv("DISCORD_WEBHOOK_SYSTEM", "")
WEBHOOK_PROPOSALS = os.getenv("DISCORD_WEBHOOK_PROPOSALS", "")

SCORE_THRESHOLD = int(os.getenv("IMPROVEMENT_SCORE_THRESHOLD", "90"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def post_to_discord(webhook_url, embeds, content=""):
    if not webhook_url:
        return
    payload = {}
    if content:
        payload["content"] = content
    if embeds:
        payload["embeds"] = embeds
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(webhook_url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                return resp.status in (200, 204)
    except Exception as e:
        log.error(f"Discord post error: {e}")


def make_embed(title, description, color=0x3498DB, fields=None, footer=None):
    embed = {"title": title, "description": description, "color": color,
             "timestamp": datetime.now(timezone.utc).isoformat()}
    if fields:
        embed["fields"] = fields
    if footer:
        embed["footer"] = {"text": footer}
    return embed


async def call_claude(prompt, system="", model=None, max_tokens=2000):
    if not ANTHROPIC_API_KEY:
        return '{"error": "no api key"}'
    model = model or SONNET_MODEL
    payload = {"model": model, "max_tokens": max_tokens,
               "messages": [{"role": "user", "content": prompt}]}
    if system:
        payload["system"] = system
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.anthropic.com/v1/messages",
                json=payload,
                headers={"Content-Type": "application/json", "x-api-key": ANTHROPIC_API_KEY,
                          "anthropic-version": "2023-06-01"},
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                data = await resp.json()
                if resp.status != 200:
                    return json.dumps({"error": str(data)})
                return "".join(b["text"] for b in data.get("content", []) if b.get("type") == "text")
    except Exception as e:
        return json.dumps({"error": str(e)})


# ---------------------------------------------------------------------------
# GitHub API — create branches and PRs
# ---------------------------------------------------------------------------
async def github_api(method, endpoint, data=None):
    """Call GitHub API."""
    if not GITHUB_TOKEN:
        log.warning("No GITHUB_TOKEN configured")
        return None
    url = f"https://api.github.com/repos/{GITHUB_REPO}{endpoint}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }
    try:
        async with aiohttp.ClientSession() as session:
            if method == "GET":
                async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    return await resp.json()
            elif method == "POST":
                async with session.post(url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    return await resp.json()
            elif method == "PUT":
                async with session.put(url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    return await resp.json()
            elif method == "PATCH":
                async with session.patch(url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    return await resp.json()
    except Exception as e:
        log.error(f"GitHub API error: {e}")
        return None


async def get_main_sha():
    """Get the SHA of the main branch HEAD."""
    result = await github_api("GET", "/git/ref/heads/main")
    if result and "object" in result:
        return result["object"]["sha"]
    return None


async def create_branch(branch_name, from_sha):
    """Create a new branch from a SHA."""
    return await github_api("POST", "/git/refs", {
        "ref": f"refs/heads/{branch_name}",
        "sha": from_sha,
    })


async def update_file(branch, path, content, message):
    """Create or update a file on a branch."""
    import base64
    # Check if file exists to get its SHA
    existing = await github_api("GET", f"/contents/{path}?ref={branch}")
    file_sha = existing.get("sha") if existing and "sha" in existing else None

    data = {
        "message": message,
        "content": base64.b64encode(content.encode()).decode(),
        "branch": branch,
    }
    if file_sha:
        data["sha"] = file_sha

    return await github_api("PUT", f"/contents/{path}", data)


async def create_pull_request(title, body, branch):
    """Create a pull request from branch to main."""
    return await github_api("POST", "/pulls", {
        "title": title,
        "body": body,
        "head": branch,
        "base": "main",
    })


# ---------------------------------------------------------------------------
# Daily Self-Improvement — runs after EOD scoring
# ---------------------------------------------------------------------------
async def run_daily_improvement(score_data: dict, today: str):
    """
    If score < threshold, generate improvement suggestions and create a PR.
    """
    score = score_data.get("score", 100)

    if score >= SCORE_THRESHOLD:
        log.info(f"Score {score} >= {SCORE_THRESHOLD}, no improvement needed")
        return

    log.info(f"Score {score} < {SCORE_THRESHOLD} — generating improvement PR")

    # Gather context
    lessons = score_data.get("lessons", [])
    patterns = score_data.get("patterns", [])
    suggestions = score_data.get("rule_suggestions", [])

    # Load current guard config
    guard_config_path = Path("/app/configs/guard_config.json")
    current_guards = "{}"
    if guard_config_path.exists():
        current_guards = guard_config_path.read_text()

    # Load current strategies
    strategies_path = Path("/app/configs/strategies.json")
    current_strategies = "{}"
    if strategies_path.exists():
        current_strategies = strategies_path.read_text()

    # Ask Claude for specific fixes
    fix_prompt = f"""Today's trading score: {score}/100
Patterns observed: {json.dumps(patterns)}
Lessons learned: {json.dumps(lessons)}
Rule suggestions: {json.dumps(suggestions)}

Current guard config:
{current_guards}

Current strategies:
{current_strategies}

Based on today's performance, propose SPECIFIC changes to improve the system.
Output ONLY valid JSON:
{{
  "guard_changes": {{
    "description": "What to change in guard_config.json and why",
    "new_config": {{...partial config with only changed fields...}}
  }},
  "strategy_changes": {{
    "description": "What to change in strategies.json and why",
    "new_config": {{...partial config with only changed fields...}}
  }},
  "new_lessons": ["lesson 1", "lesson 2"],
  "pr_title": "Short PR title",
  "pr_body": "Detailed explanation of changes"
}}

Rules:
- Only suggest changes supported by today's data
- Never loosen risk limits without strong evidence
- Be conservative — small adjustments only
- Every change must include reasoning"""

    fix_result = await call_claude(fix_prompt, model=SONNET_MODEL, max_tokens=2000)

    try:
        cleaned = fix_result.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0]
        fix_data = json.loads(cleaned)
    except json.JSONDecodeError:
        log.error("Could not parse improvement suggestions")
        await post_to_discord(WEBHOOK_SYSTEM, [
            make_embed("⚠️ Improvement Failed", "Could not parse Claude's fix suggestions",
                        color=0xF39C12, footer="Self-Improvement Engine")
        ])
        return

    # Validate proposed param changes via backtesting before creating PR
    import sys
    sys.path.insert(0, "/app/services")
    agent1_validated = True
    agent2_validated = True
    validation_results = {}

    guard_changes = fix_data.get("guard_changes", {})
    agent_params = fix_data.get("agent_param_changes", {})

    # Validate Agent 1 param changes if proposed
    if agent_params.get("agent1"):
        try:
            from backtester import validate_param_change
            import json as _json
            from pathlib import Path as _Path
            old_a1 = _json.loads(_Path("/app/configs/agent1_params.json").read_text()) if _Path("/app/configs/agent1_params.json").exists() else {}
            new_a1 = {**old_a1, **agent_params["agent1"]}
            v1 = validate_param_change("agent1_iron_condor", old_a1, new_a1)
            validation_results["agent1"] = v1
            agent1_validated = v1.get("validated", True)
            log.info(f"Agent 1 param validation: {v1.get('recommendation')} — {v1.get('reason')}")
        except Exception as e:
            log.warning(f"Agent 1 backtest failed: {e} — proceeding without validation")

    # Validate Agent 2 param changes if proposed
    if agent_params.get("agent2"):
        try:
            from backtester import validate_param_change
            import json as _json
            from pathlib import Path as _Path
            old_a2 = _json.loads(_Path("/app/configs/agent2_params.json").read_text()) if _Path("/app/configs/agent2_params.json").exists() else {}
            new_a2 = {**old_a2, **agent_params["agent2"]}
            v2 = validate_param_change("agent2_covered_call", old_a2, new_a2)
            validation_results["agent2"] = v2
            agent2_validated = v2.get("validated", True)
            log.info(f"Agent 2 param validation: {v2.get('recommendation')} — {v2.get('reason')}")
        except Exception as e:
            log.warning(f"Agent 2 backtest failed: {e} — proceeding without validation")

    # Post validation results to Discord
    if validation_results:
        validation_fields = []
        for agent_name, vr in validation_results.items():
            emoji = "✅" if vr.get("validated") else "❌"
            validation_fields.append({
                "name": f"{emoji} {agent_name} backtest",
                "value": f"{vr.get('recommendation', '?')}: {vr.get('reason', '')[:150]}",
                "inline": False,
            })
        await post_to_discord(WEBHOOK_SYSTEM, [make_embed(
            "🧪 Backtest Validation Results",
            f"Tested proposed changes against {max(v.get('trades_tested', 0) for v in validation_results.values())} historical trades",
            color=0x2ECC71 if (agent1_validated and agent2_validated) else 0xE74C3C,
            fields=validation_fields,
            footer="QuantAI Backtester · self_improve.py"
        )])

    # Only create PR if validation passed (or no validation was needed)
    if not (agent1_validated and agent2_validated):
        log.warning("Param changes failed backtest validation — discarding PR, posting reason")
        rejected_fields = []
        for agent_name, vr in validation_results.items():
            if not vr.get("validated"):
                rejected_fields.append({
                    "name": f"❌ {agent_name} rejected",
                    "value": vr.get("reason", "Unknown reason")[:200],
                    "inline": False,
                })
        await post_to_discord(WEBHOOK_SYSTEM, [make_embed(
            "🚫 Improvement PR Discarded",
            f"Score {score} triggered improvement, but proposed param changes "
            f"did not improve simulated outcomes. PR not created.",
            color=0xE74C3C,
            fields=rejected_fields,
            footer="QuantAI Self-Improve · backtest gate"
        )])
        return

    # Create GitHub PR if token is configured
    if GITHUB_TOKEN:
        await create_improvement_pr(fix_data, today, score, validation_results)
    else:
        # No GitHub token — just post to Discord
        await post_improvement_to_discord(fix_data, today, score)


async def create_improvement_pr(fix_data, today, score, validation_results=None):
    """Create a GitHub PR with the suggested improvements."""
    branch_name = f"auto-improve/{today}-score-{score}"

    # Get main SHA
    main_sha = await get_main_sha()
    if not main_sha:
        log.error("Could not get main branch SHA")
        return

    # Create branch
    branch_result = await create_branch(branch_name, main_sha)
    if not branch_result or "ref" not in branch_result:
        log.error(f"Could not create branch: {branch_result}")
        return

    files_updated = []

    # Update guard config if changes suggested
    guard_changes = fix_data.get("guard_changes", {})
    if guard_changes.get("new_config"):
        # Merge with existing config
        guard_config_path = Path("/app/configs/guard_config.json")
        if guard_config_path.exists():
            current = json.loads(guard_config_path.read_text())
        else:
            current = {}

        # Deep merge new values
        new_config = guard_changes["new_config"]
        for section, values in new_config.items():
            if isinstance(values, dict) and section in current:
                current[section].update(values)
            else:
                current[section] = values

        await update_file(
            branch_name,
            "configs/guard_config.json",
            json.dumps(current, indent=2),
            f"auto-improve: update guard config (score {score})"
        )
        files_updated.append("configs/guard_config.json")

    # Update strategies if changes suggested
    strategy_changes = fix_data.get("strategy_changes", {})
    if strategy_changes.get("new_config"):
        strategies_path = Path("/app/configs/strategies.json")
        if strategies_path.exists():
            current = json.loads(strategies_path.read_text())
        else:
            current = {"strategies": {}}

        new_config = strategy_changes["new_config"]
        if "strategies" in new_config:
            for name, values in new_config["strategies"].items():
                if name in current.get("strategies", {}):
                    current["strategies"][name].update(values)
                else:
                    current["strategies"][name] = values

        await update_file(
            branch_name,
            "configs/strategies.json",
            json.dumps(current, indent=2),
            f"auto-improve: update strategies (score {score})"
        )
        files_updated.append("configs/strategies.json")

    # Add improvement log
    log_content = json.dumps({
        "date": today,
        "score": score,
        "changes": fix_data,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }, indent=2)
    await update_file(
        branch_name,
        f"data/improvements/{today}_score_{score}.json",
        log_content,
        f"auto-improve: log improvement for {today}"
    )
    files_updated.append(f"data/improvements/{today}_score_{score}.json")

    # Create PR
    pr_title = fix_data.get("pr_title", f"Auto-improve: score {score} on {today}")
    pr_body = f"""## Auto-Generated Improvement PR

**Score**: {score}/100 (threshold: {SCORE_THRESHOLD})
**Date**: {today}
**Mode**: {TRADING_MODE}

### Changes
{fix_data.get('guard_changes', {}).get('description', 'No guard changes')}

{fix_data.get('strategy_changes', {}).get('description', 'No strategy changes')}

### Files Updated
{chr(10).join(f'- `{f}`' for f in files_updated)}

### New Lessons
{chr(10).join(f'- {l}' for l in fix_data.get('new_lessons', []))}

---
⚠️ **Review required**: This PR was auto-generated by the self-improvement engine.\n"""
    
    # Add validation results to PR body if available
    if validation_results:
        pr_body += "\n### Backtest Validation\n"
        for agent_name, vr in validation_results.items():
            emoji = "✅" if vr.get("validated") else "❌"
            pr_body += f"- {emoji} **{agent_name}**: {vr.get('recommendation')} — {vr.get('reason', '')}\n"
            if vr.get("trades_tested", 0) > 0:
                pr_body += f"  - Tested on {vr['trades_tested']} trades over {vr.get('days_tested', 30)} days\n"
            if vr.get("win_rate_diff") is not None:
                pr_body += f"  - Win rate: {vr.get('old_win_rate')}% → {vr.get('new_win_rate')}% ({vr['win_rate_diff']:+.1f}pp)\n"
        pr_body += "\n---\nMerge only after reviewing. Changes run on paper for 2 weeks before live.\n"


    pr_result = await create_pull_request(pr_title, pr_body, branch_name)
    pr_url = pr_result.get("html_url", "") if pr_result else ""

    log.info(f"PR created: {pr_url}")

    # Post to Discord
    await post_to_discord(WEBHOOK_SYSTEM, [
        make_embed(
            f"🔧 Auto-Improvement PR: Score {score}",
            f"**{pr_title}**\n\n"
            f"Files: {', '.join(files_updated)}\n"
            f"[Review PR]({pr_url})" if pr_url else "PR created on GitHub",
            color=0x9B59B6,
            fields=[
                {"name": "Guard Changes", "value": guard_changes.get("description", "None")[:200], "inline": False},
                {"name": "Strategy Changes", "value": strategy_changes.get("description", "None")[:200], "inline": False},
            ],
            footer="Self-Improvement Engine · Review before merging",
        )
    ])


async def post_improvement_to_discord(fix_data, today, score):
    """Post improvement suggestions to Discord when no GitHub token."""
    guard_desc = fix_data.get("guard_changes", {}).get("description", "None")
    strategy_desc = fix_data.get("strategy_changes", {}).get("description", "None")
    lessons = fix_data.get("new_lessons", [])

    await post_to_discord(WEBHOOK_SYSTEM, [
        make_embed(
            f"🔧 Improvement Suggestions: Score {score}",
            f"Score {score} < {SCORE_THRESHOLD} — changes recommended.\n\n"
            f"**Guard changes**: {guard_desc[:300]}\n\n"
            f"**Strategy changes**: {strategy_desc[:300]}\n\n"
            f"**Lessons**: {chr(10).join(f'• {l}' for l in lessons[:5])}\n\n"
            f"⚠️ No GITHUB_TOKEN configured — apply manually and run on paper for 2 weeks.",
            color=0x9B59B6,
            footer="Self-Improvement Engine",
        )
    ])


# ---------------------------------------------------------------------------
# Weekly Review — runs Friday EOD
# ---------------------------------------------------------------------------
async def run_weekly_review():
    """Aggregate the week's performance and propose strategic adjustments."""
    log.info("=== Weekly Review ===")

    today = datetime.now(timezone.utc)
    week_start = (today - timedelta(days=today.weekday())).strftime("%Y-%m-%d")
    week_end = today.strftime("%Y-%m-%d")

    # Gather all scores from this week
    score_path = Path("/app/data/journal")
    weekly_scores = []
    for f in sorted(score_path.glob(f"score_{TRADING_MODE}_*.json")):
        fname = f.stem
        date_part = fname.replace(f"score_{TRADING_MODE}_", "")
        if date_part >= week_start:
            try:
                weekly_scores.append(json.loads(f.read_text()))
            except json.JSONDecodeError:
                continue

    # Gather all lessons from this week
    lessons_file = Path("/app/data/memory/shared/lessons.jsonl")
    weekly_lessons = []
    if lessons_file.exists():
        with open(lessons_file) as f:
            for line in f:
                if line.strip():
                    try:
                        record = json.loads(line)
                        if record.get("timestamp", "")[:10] >= week_start:
                            weekly_lessons.append(record.get("lesson", ""))
                    except json.JSONDecodeError:
                        continue

    # Gather trade journal entries
    memory_journal = Path(f"/app/data/memory/{TRADING_MODE}/trade_journal.jsonl")
    weekly_trades = []
    if memory_journal.exists():
        with open(memory_journal) as f:
            for line in f:
                if line.strip():
                    try:
                        record = json.loads(line)
                        if record.get("timestamp", "")[:10] >= week_start:
                            weekly_trades.append(record)
                    except json.JSONDecodeError:
                        continue

    # Claude weekly analysis
    review_prompt = f"""Weekly Review: {week_start} to {week_end}
Mode: {TRADING_MODE}

Daily scores this week: {json.dumps(weekly_scores[:5], indent=1)}
Lessons accumulated: {json.dumps(weekly_lessons[:20])}
Trades this week: {len(weekly_trades)}

Produce a weekly review. Output ONLY valid JSON:
{{
  "week_summary": "2-3 sentence summary",
  "avg_score": 0-100,
  "best_day": "which day and why",
  "worst_day": "which day and why",
  "top_patterns": ["pattern 1", "pattern 2"],
  "strategic_recommendations": ["rec 1", "rec 2"],
  "rules_to_add": ["rule description"],
  "rules_to_tighten": ["rule and how"],
  "rules_to_relax": ["rule and why, with evidence"],
  "next_week_focus": "What to focus on next week"
}}

Be specific. Reference actual trades and scores."""

    result = await call_claude(review_prompt, model=SONNET_MODEL, max_tokens=1500)

    # Save
    review_path = Path("/app/data/journal")
    review_path.mkdir(parents=True, exist_ok=True)
    with open(review_path / f"weekly_review_{TRADING_MODE}_{week_end}.json", "w") as f:
        f.write(result)

    # Parse and post
    try:
        cleaned = result.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0]
        review_data = json.loads(cleaned)
    except json.JSONDecodeError:
        review_data = None

    if review_data:
        fields = [
            {"name": "Avg Score", "value": str(review_data.get("avg_score", "?")), "inline": True},
            {"name": "Trades", "value": str(len(weekly_trades)), "inline": True},
            {"name": "Mode", "value": TRADING_MODE, "inline": True},
        ]
        if review_data.get("top_patterns"):
            fields.append({"name": "Patterns", "value": "\n".join(f"• {p}" for p in review_data["top_patterns"][:3]), "inline": False})
        if review_data.get("strategic_recommendations"):
            fields.append({"name": "Recommendations", "value": "\n".join(f"• {r}" for r in review_data["strategic_recommendations"][:3]), "inline": False})
        if review_data.get("next_week_focus"):
            fields.append({"name": "Next Week Focus", "value": review_data["next_week_focus"], "inline": False})

        await post_to_discord(WEBHOOK_SYSTEM, [
            make_embed(
                f"📅 Weekly Review: {week_start} → {week_end}",
                review_data.get("week_summary", "See details"),
                color=0x3498DB, fields=fields,
                footer=f"QuantAI Weekly Review · {TRADING_MODE}",
            )
        ])
    else:
        await post_to_discord(WEBHOOK_SYSTEM, [
            make_embed("📅 Weekly Review", result[:2000], color=0x3498DB)
        ])

    log.info("Weekly review complete")
