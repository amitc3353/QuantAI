"""
Infra Agent — Dev + Ops Agent via Discord
==========================================
Can read/edit project files, git commit/push, create PRs,
restart Docker containers, view logs, and manage the system.

Security model:
  READ: auto-execute (logs, files, git status, health)
  WRITE: requires your approval via reaction (edit, commit, deploy)
  BLOCKED: .env secrets, rm -rf, destructive ops
"""

import os
import json
import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

import discord
from discord.ext import commands
from discord import app_commands
import aiohttp

log = logging.getLogger("infra-agent")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
HAIKU_MODEL = os.getenv("CLAUDE_HAIKU_MODEL", "claude-haiku-4-5-20251001")
SONNET_MODEL = os.getenv("CLAUDE_SONNET_MODEL", "claude-sonnet-4-20250514")
GUARD_URL = os.getenv("GUARD_URL", "http://trader-guards:8100")
PROJECT_DIR = "/app/project"

BLOCKED_PATTERNS = [
    "rm -rf", ".env", "SECRET", "API_KEY", "TOKEN",
    "passwd", "chmod 777", "mkfs", "dd if=",
    "> /dev/", "curl | bash", "wget | bash",
]


def is_blocked(cmd):
    return any(p.lower() in cmd.lower() for p in BLOCKED_PATTERNS)


# ---------------------------------------------------------------------------
# Shell execution — runs on the HOST via mounted volumes
# ---------------------------------------------------------------------------
async def run_shell(cmd, cwd=PROJECT_DIR, timeout=30):
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT, cwd=cwd,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        output = stdout.decode("utf-8", errors="replace")
        if len(output) > 1800:
            output = output[:1800] + "\n... (truncated)"
        return output, proc.returncode
    except asyncio.TimeoutError:
        return "Timed out", -1
    except Exception as e:
        return f"Error: {e}", -1


# ---------------------------------------------------------------------------
# File operations — read/edit project files
# ---------------------------------------------------------------------------
async def read_file(filepath):
    full_path = Path(PROJECT_DIR) / filepath
    if not full_path.exists():
        return f"File not found: {filepath}"
    try:
        content = full_path.read_text()
        if len(content) > 1800:
            content = content[:1800] + "\n... (truncated)"
        return content
    except Exception as e:
        return f"Error reading {filepath}: {e}"


async def write_file(filepath, content):
    full_path = Path(PROJECT_DIR) / filepath
    full_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        full_path.write_text(content)
        return f"Written {len(content)} chars to {filepath}"
    except Exception as e:
        return f"Error writing {filepath}: {e}"


async def edit_file(filepath, old_text, new_text):
    full_path = Path(PROJECT_DIR) / filepath
    if not full_path.exists():
        return f"File not found: {filepath}"
    try:
        content = full_path.read_text()
        if old_text not in content:
            return f"Text not found in {filepath}"
        updated = content.replace(old_text, new_text, 1)
        full_path.write_text(updated)
        return f"Edited {filepath}: replaced {len(old_text)} chars with {len(new_text)} chars"
    except Exception as e:
        return f"Error editing {filepath}: {e}"


# ---------------------------------------------------------------------------
# Git operations
# ---------------------------------------------------------------------------
async def git_status():
    output, _ = await run_shell("git status --short")
    return output or "Clean — no changes"


async def git_diff():
    output, _ = await run_shell("git diff --stat")
    return output or "No differences"


async def git_log(n=10):
    output, _ = await run_shell(f"git log --oneline -n {n}")
    return output


async def git_commit_and_push(message):
    results = []
    out, rc = await run_shell("git add -A")
    results.append(f"add: {out.strip()}")
    out, rc = await run_shell(f'git commit -m "{message}"')
    results.append(f"commit: {out.strip()}")
    if rc != 0:
        return "\n".join(results) + "\nCommit failed — nothing to commit?"
    import os as _os
    _token = _os.getenv("GITHUB_TOKEN", "")
    _repo = _os.getenv("GITHUB_REPO", "amitc3353/QuantAI")
    if _token:
        push_cmd = f"git push https://{_token}@github.com/{_repo}.git main"
    else:
        push_cmd = "git push"
    out, rc = await run_shell(push_cmd)
    if _token:
        out = out.replace(_token, "***")
    results.append(f"push: {out.strip()}")
    return "\n".join(results)


async def git_create_branch(branch_name):
    out, _ = await run_shell(f"git checkout -b {branch_name}")
    return out


async def git_checkout_main():
    out, _ = await run_shell("git checkout main")
    return out


# ---------------------------------------------------------------------------
# Docker operations
# ---------------------------------------------------------------------------
async def docker_ps():
    out, _ = await run_shell("docker ps", cwd="/")
    return out if out.strip() else "No containers found"


async def docker_logs(service, lines=30):
    name_map = {"discord-bot": "trader-discord", "guard-engine": "trader-guards",
                "orchestrator": "trader-orchestrator", "alpaca-mcp": "trader-alpaca"}
    container = name_map.get(service, service)
    if container:
        out, _ = await run_shell(f"docker logs --tail={lines} {container}", cwd="/")
    else:
        out, _ = await run_shell(f"docker logs --tail={lines} trader-discord", cwd="/")
    return out


async def docker_restart(service=""):
    name_map = {"discord-bot": "trader-discord", "guard-engine": "trader-guards",
                "orchestrator": "trader-orchestrator", "alpaca-mcp": "trader-alpaca"}
    if service:
        container = name_map.get(service, service)
        out, _ = await run_shell(f"docker restart {container}", cwd="/")
    else:
        out, _ = await run_shell("docker restart trader-discord trader-guards trader-orchestrator trader-alpaca", cwd="/")
    return out


async def docker_rebuild_and_restart(service=""):
    return "Full rebuild requires deploy-trader from Mac. Use /restart for quick restarts."


# ---------------------------------------------------------------------------
# Embeds
# ---------------------------------------------------------------------------
def ops_embed(title, description, color=discord.Color.blue()):
    return discord.Embed(
        title=title, description=description, color=color,
        timestamp=datetime.now(timezone.utc),
    ).set_footer(text="Infra Agent")


# ---------------------------------------------------------------------------
# The Cog
# ---------------------------------------------------------------------------
class InfraAgent(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.pending = {}

    # --- File Operations ---

    @app_commands.command(name="read", description="Read a project file")
    @app_commands.describe(filepath="Path relative to project root (e.g. configs/guard_config.json)")
    async def cmd_read(self, interaction: discord.Interaction, filepath: str):
        await interaction.response.defer()
        if is_blocked(filepath):
            await interaction.followup.send(embed=ops_embed("Blocked", f"`{filepath}` is restricted", discord.Color.red()))
            return
        content = await read_file(filepath)
        await interaction.followup.send(embed=ops_embed(f"`{filepath}`", f"```\n{content}\n```"))

    @app_commands.command(name="ls", description="List files in a project directory")
    @app_commands.describe(path="Directory path (e.g. discord-bot/cogs)")
    async def cmd_ls(self, interaction: discord.Interaction, path: str = ""):
        await interaction.response.defer()
        out, _ = await run_shell(f"ls -la {path}" if path else "ls -la")
        await interaction.followup.send(embed=ops_embed(f"ls {path or '.'}", f"```\n{out}\n```"))

    @app_commands.command(name="edit", description="Edit a file (find and replace)")
    @app_commands.describe(filepath="File path", find="Text to find", replace="Text to replace with")
    async def cmd_edit(self, interaction: discord.Interaction, filepath: str, find: str, replace: str):
        await interaction.response.defer()
        if is_blocked(filepath) or is_blocked(replace):
            await interaction.followup.send(embed=ops_embed("Blocked", "Restricted operation", discord.Color.red()))
            return
        embed = ops_embed(
            "Edit Requested",
            f"**File**: `{filepath}`\n**Find**: `{find[:100]}`\n**Replace**: `{replace[:100]}`\n\nReact to confirm.",
            discord.Color.gold(),
        )
        msg = await interaction.followup.send(embed=embed, wait=True)
        await msg.add_reaction("✅")
        await msg.add_reaction("❌")
        self.pending[msg.id] = {"type": "edit", "filepath": filepath, "find": find, "replace": replace, "user_id": interaction.user.id}

    # --- Git Operations ---

    @app_commands.command(name="git", description="Run git operations")
    @app_commands.describe(action="status, diff, log, branch, checkout-main", arg="Branch name or log count")
    async def cmd_git(self, interaction: discord.Interaction, action: str, arg: str = ""):
        await interaction.response.defer()
        if action == "status":
            out = await git_status()
        elif action == "diff":
            out = await git_diff()
        elif action == "log":
            out = await git_log(int(arg) if arg.isdigit() else 10)
        elif action == "branch":
            if not arg:
                await interaction.followup.send(embed=ops_embed("Usage", "`/git branch my-feature-branch`"))
                return
            out = await git_create_branch(arg)
        elif action == "checkout-main":
            out = await git_checkout_main()
        else:
            out = f"Unknown action: {action}. Use: status, diff, log, branch, checkout-main"
        await interaction.followup.send(embed=ops_embed(f"git {action}", f"```\n{out}\n```"))

    @app_commands.command(name="commit", description="Git add, commit, and push (requires approval)")
    @app_commands.describe(message="Commit message")
    async def cmd_commit(self, interaction: discord.Interaction, message: str):
        await interaction.response.defer()
        status = await git_status()
        embed = ops_embed(
            "Commit + Push",
            f"**Message**: {message}\n**Changes**:\n```\n{status}\n```\nReact to confirm.",
            discord.Color.gold(),
        )
        msg = await interaction.followup.send(embed=embed, wait=True)
        await msg.add_reaction("✅")
        await msg.add_reaction("❌")
        self.pending[msg.id] = {"type": "commit", "message": message, "user_id": interaction.user.id}

    @app_commands.command(name="deploy", description="Pull, rebuild, and restart (requires approval)")
    @app_commands.describe(service="Specific service or leave empty for all")
    async def cmd_deploy(self, interaction: discord.Interaction, service: str = ""):
        await interaction.response.defer()
        embed = ops_embed(
            "Deploy Requested",
            f"Will: `git pull` → `docker compose build {service}` → `docker compose up -d {service}`\n\nReact to confirm.",
            discord.Color.gold(),
        )
        msg = await interaction.followup.send(embed=embed, wait=True)
        await msg.add_reaction("✅")
        await msg.add_reaction("❌")
        self.pending[msg.id] = {"type": "deploy", "service": service, "user_id": interaction.user.id}

    # --- Docker Operations ---

    @app_commands.command(name="logs", description="View service logs")
    @app_commands.describe(service="Service name (discord-bot, guard-engine, orchestrator, alpaca-mcp)", lines="Number of lines")
    async def cmd_logs(self, interaction: discord.Interaction, service: str = "", lines: int = 30):
        await interaction.response.defer()
        out = await docker_logs(service, lines)
        await interaction.followup.send(embed=ops_embed(f"Logs: {service or 'all'}", f"```\n{out}\n```"))

    @app_commands.command(name="health", description="System health — containers, guard, resources")
    async def cmd_health(self, interaction: discord.Interaction):
        await interaction.response.defer()

        # Guard engine check
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{GUARD_URL}/health", timeout=aiohttp.ClientTimeout(total=3)) as resp:
                    gdata = await resp.json()
                    guard_status = "✅ healthy" if resp.status == 200 else "❌ unhealthy"
                    halted = "⛔ YES — HALTED" if gdata.get("halted") else "✅ No"
        except Exception as e:
            guard_status = f"❌ unreachable ({e})"
            halted = "unknown"

        # Container status
        ps_out = await docker_ps()

        # System resources
        mem, _ = await run_shell("free -h | head -2")
        disk, _ = await run_shell("df -h / | tail -1")
        git_st = await git_status()

        # Cache status — check key files directly
        import os
        cache_dir = "/app/data/cache"
        cache_files = ["vix.json", "macro_fred_macro_composite.json",
                       "sentiment_put_call.json", "sentiment_fear_greed.json"]
        cache_status = []
        for cf in cache_files:
            path = os.path.join(cache_dir, cf)
            if os.path.exists(path):
                age_min = (os.path.getmtime(path) and
                    (__import__("time").time() - os.path.getmtime(path)) / 60)
                cache_status.append(f"✅ {cf.replace('.json','')}: {age_min:.0f}m ago")
            else:
                cache_status.append(f"⏳ {cf.replace('.json','')}: not yet populated")

        embed = ops_embed(
            "🟢 System Health",
            f"**Guard Engine**: {guard_status}\n"
            f"**Halted**: {halted}\n"
            f"**Mode**: paper | **Auto**: true\n\n"
            f"**Containers**:\n```\n{ps_out[:400]}\n```\n"
            f"**Cache**:\n" + "\n".join(cache_status) + "\n\n"
            f"**Memory**:\n```\n{mem}\n```\n"
            f"**Disk**:\n```\n{disk}\n```\n"
            f"**Git**:\n```\n{git_st}\n```",
            discord.Color.green(),
        )
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="restart", description="Restart a service (requires approval)")
    @app_commands.describe(service="Service name or 'all'")
    async def cmd_restart(self, interaction: discord.Interaction, service: str = "all"):
        await interaction.response.defer()
        embed = ops_embed("Restart", f"Restart `{service}`?\n\nReact to confirm.", discord.Color.gold())
        msg = await interaction.followup.send(embed=embed, wait=True)
        await msg.add_reaction("✅")
        await msg.add_reaction("❌")
        self.pending[msg.id] = {"type": "restart", "service": "" if service == "all" else service, "user_id": interaction.user.id}

    @app_commands.command(name="run", description="Run a shell command on the VPS")
    @app_commands.describe(cmd="Command to execute")
    async def cmd_run(self, interaction: discord.Interaction, cmd: str):
        await interaction.response.defer()
        if is_blocked(cmd):
            await interaction.followup.send(embed=ops_embed("Blocked", f"`{cmd}` is restricted", discord.Color.red()))
            return
        # Safe read-only commands auto-execute
        safe = ["ls", "cat", "head", "tail", "grep", "find", "wc", "du", "df", "free",
                "uptime", "git status", "git log", "git diff", "docker compose ps"]
        if any(cmd.strip().startswith(s) for s in safe):
            out, rc = await run_shell(cmd)
            color = discord.Color.green() if rc == 0 else discord.Color.red()
            await interaction.followup.send(embed=ops_embed(f"`{cmd}`", f"```\n{out}\n```", color))
        else:
            embed = ops_embed("Approval Required", f"Command: `{cmd}`\n\nReact to confirm.", discord.Color.gold())
            msg = await interaction.followup.send(embed=embed, wait=True)
            await msg.add_reaction("✅")
            await msg.add_reaction("❌")
            self.pending[msg.id] = {"type": "shell", "cmd": cmd, "user_id": interaction.user.id}

    @app_commands.command(name="config", description="View or edit a config file")
    @app_commands.describe(file="guards, watchlist, strategies", action="view or edit",
                           key="JSON key path", value="New value")
    async def cmd_config(self, interaction: discord.Interaction, file: str = "guards",
                         action: str = "view", key: str = "", value: str = ""):
        await interaction.response.defer()
        file_map = {"guards": "configs/guard_config.json", "watchlist": "configs/watchlist.json",
                    "strategies": "configs/strategies.json"}
        filepath = file_map.get(file)
        if not filepath:
            await interaction.followup.send(embed=ops_embed("Unknown", f"Options: {', '.join(file_map)}"))
            return
        if action == "view":
            content = await read_file(filepath)
            await interaction.followup.send(embed=ops_embed(f"{file}", f"```json\n{content}\n```"))
        elif action == "edit" and key and value:
            embed = ops_embed("Config Edit", f"**{file}** → `{key}` = `{value}`\n\nReact to confirm.", discord.Color.gold())
            msg = await interaction.followup.send(embed=embed, wait=True)
            await msg.add_reaction("✅")
            await msg.add_reaction("❌")
            self.pending[msg.id] = {"type": "config_edit", "filepath": PROJECT_DIR + "/" + filepath,
                                     "key": key, "value": value, "user_id": interaction.user.id}

    # --- Approval handler ---

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        if payload.user_id == self.bot.user.id:
            return
        action = self.pending.get(payload.message_id)
        if not action or payload.user_id != action.get("user_id"):
            return

        channel = self.bot.get_channel(payload.channel_id)
        emoji = str(payload.emoji)

        if emoji == "❌":
            del self.pending[payload.message_id]
            await channel.send(embed=ops_embed("Cancelled", "Action cancelled.", discord.Color.red()))
            return
        if emoji != "✅":
            return

        del self.pending[payload.message_id]
        await channel.send(embed=ops_embed("Executing...", "Working on it...", discord.Color.blue()))

        if action["type"] == "edit":
            result = await edit_file(action["filepath"], action["find"], action["replace"])
            await channel.send(embed=ops_embed("Edit Result", result, discord.Color.green()))

        elif action["type"] == "commit":
            result = await git_commit_and_push(action["message"])
            await channel.send(embed=ops_embed("Commit Result", f"```\n{result}\n```", discord.Color.green()))

        elif action["type"] == "deploy":
            service = action.get("service", "")

            # Pre-flight: check GITHUB_TOKEN is configured
            import os as _os
            if not _os.getenv("GITHUB_TOKEN", ""):
                await channel.send(embed=ops_embed(
                    "❌ Deploy Failed — Missing GITHUB_TOKEN",
                    "Add `GITHUB_TOKEN=your_pat` to `/home/trader/QuantAI/.env` on the VPS, then restart the bot.\n"
                    "Generate a PAT at: github.com → Settings → Developer Settings → Tokens\n"
                    "Needs `repo` scope (read + write).",
                    discord.Color.red()
                ))
                return

            await channel.send(embed=ops_embed("🚀 Deploying...", "Step 1/3: Pulling latest code from GitHub...", discord.Color.blue()))

            # Step 1: git pull — use GITHUB_TOKEN for auth (private repo)
            github_token = _os.getenv("GITHUB_TOKEN", "")
            github_repo = _os.getenv("GITHUB_REPO", "amitc3353/QuantAI")
            pull_cmd = f"git pull https://{github_token}@github.com/{github_repo}.git main"
            pull_out, pull_rc = await run_shell(pull_cmd, cwd=PROJECT_DIR)
            pull_out_safe = pull_out.replace(github_token, "***") if github_token else pull_out
            pull_status = "✅ Up to date" if pull_rc == 0 else f"❌ git pull failed (rc={pull_rc})"
            await channel.send(embed=ops_embed("Git Pull", f"```\n{pull_out_safe[:800]}\n```\n{pull_status}", discord.Color.green() if pull_rc == 0 else discord.Color.red()))

            if pull_rc != 0:
                await channel.send(embed=ops_embed("❌ Deploy Aborted", "git pull failed. Check GitHub token or network.", discord.Color.red()))
                return

            # Step 2: docker compose build
            await channel.send(embed=ops_embed("Building...", "Step 2/3: Rebuilding containers (this takes ~60s)...", discord.Color.blue()))
            # Use -p to set project name matching the VPS deployment
            build_cmd = f"docker compose -p quantai build {service}" if service else "docker compose -p quantai build"
            build_out, build_rc = await run_shell(build_cmd, cwd=PROJECT_DIR, timeout=300)
            build_status = "✅ Build complete" if build_rc == 0 else f"❌ Build failed (rc={build_rc})"
            # Trim build output — it's very verbose
            build_lines = build_out.strip().split("\n")
            build_summary = "\n".join(build_lines[-15:]) if len(build_lines) > 15 else build_out
            await channel.send(embed=ops_embed("Docker Build", f"```\n{build_summary[:800]}\n```\n{build_status}", discord.Color.green() if build_rc == 0 else discord.Color.red()))

            if build_rc != 0:
                await channel.send(embed=ops_embed("❌ Deploy Aborted", "docker build failed. Check logs for errors.", discord.Color.red()))
                return

            # Step 3: restart services (excluding discord-bot to avoid self-deadlock)
            await channel.send(embed=ops_embed("Starting...", "Step 3/3: Restarting services...", discord.Color.blue()))

            # Restart each service individually using docker restart
            # Never restart discord-bot from within itself — it kills the connection
            if service and service != "discord-bot":
                containers = [f"trader-{service}"]
            else:
                containers = ["trader-guards", "trader-orchestrator", "trader-alpaca"]

            restart_lines = []
            all_ok = True
            for container in containers:
                r_out, r_rc = await run_shell(f"docker restart {container}", cwd="/", timeout=30)
                status = "✅" if r_rc == 0 else "❌"
                restart_lines.append(f"{status} {container}")
                if r_rc != 0:
                    all_ok = False

            up_status = "\n".join(restart_lines)
            await channel.send(embed=ops_embed(
                "Services Restarted",
                f"```\n{up_status}\n```",
                discord.Color.green() if all_ok else discord.Color.orange()
            ))

            # discord-bot restarts itself last via a delayed shell command
            await channel.send(embed=ops_embed(
                "✅ Deploy Complete",
                "All services restarted.\n⚠️ discord-bot will self-restart in 3 seconds.",
                discord.Color.green()
            ))
            await asyncio.sleep(1)
            # Fire-and-forget restart of self — connection will drop briefly then recover
            asyncio.create_task(run_shell("sleep 3 && docker restart trader-discord", cwd="/", timeout=15))

        elif action["type"] == "restart":
            result = await docker_restart(action.get("service", ""))
            await channel.send(embed=ops_embed("Restart Complete", f"```\n{result}\n```", discord.Color.green()))

        elif action["type"] == "shell":
            out, rc = await run_shell(action["cmd"])
            color = discord.Color.green() if rc == 0 else discord.Color.red()
            await channel.send(embed=ops_embed(f"`{action['cmd']}`", f"```\n{out}\n```", color))

        elif action["type"] == "config_edit":
            try:
                fp = action["filepath"]
                with open(fp) as f:
                    config = json.load(f)
                keys = action["key"].split(".")
                obj = config
                for k in keys[:-1]:
                    obj = obj[k]
                try:
                    parsed = json.loads(action["value"])
                except (json.JSONDecodeError, ValueError):
                    parsed = action["value"]
                old = obj.get(keys[-1], "(not set)")
                obj[keys[-1]] = parsed
                with open(fp, "w") as f:
                    json.dump(config, f, indent=2)
                await channel.send(embed=ops_embed("Config Updated",
                    f"`{action['key']}`: `{old}` → `{parsed}`", discord.Color.green()))
            except Exception as e:
                await channel.send(embed=ops_embed("Edit Failed", str(e), discord.Color.red()))




    @app_commands.command(name="cto", description="Ask the CTO agent to investigate, fix, or research anything")
    @app_commands.describe(task="What to investigate, fix, or research")
    async def cmd_cto(self, interaction: discord.Interaction, task: str):
        await interaction.response.defer()
        import os as _os

        webhook = _os.getenv("DISCORD_WEBHOOK_SYSTEM", "")
        # /app/project is the host QuantAI directory mounted into discord-bot
        bridge = "/app/project/scripts/claude_code_bridge.sh"

        # Check bridge exists (mounted from host)
        bridge_check, _ = await run_shell(f"test -f {bridge} && echo exists || echo missing", cwd="/")
        if "missing" in bridge_check:
            await interaction.followup.send(embed=ops_embed(
                "Bridge Script Missing",
                "Check that /app/project is mounted in docker-compose.yml",
                discord.Color.orange()
            ))
            return

        # Check Claude Code installed on host
        claude_check, _ = await run_shell(
            "export PATH=$PATH:/home/trader/.npm-global/bin && which claude || echo notfound",
            cwd="/"
        )
        if "notfound" in claude_check or not claude_check.strip():
            await interaction.followup.send(embed=ops_embed(
                "Claude Code Not Found",
                "Install it:\n```\nnpm install -g @anthropic-ai/claude-code\n```",
                discord.Color.orange()
            ))
            return

        await interaction.followup.send(embed=ops_embed(
            "CTO Agent Started",
            f"**Task:** {task}\n\nClaude Code is working on it. Results post here in ~2 minutes.",
            discord.Color.blue()
        ))

        safe_task = task.replace("'", "").replace('"', '').replace('`', '')
        asyncio.create_task(run_shell(
            f"export PATH=$PATH:/home/trader/.npm-global/bin && bash {bridge} '{safe_task}' '{webhook}'",
            cwd="/app/project",
            timeout=320
        ))




async def setup(bot):
    await bot.add_cog(InfraAgent(bot))
