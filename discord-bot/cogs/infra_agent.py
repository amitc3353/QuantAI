"""
Infra Agent — Discord Cog
===========================
An ops engineer that lives in Discord. Can monitor, diagnose, and fix
issues across all agents and infrastructure.

Security model:
  - READ ops (logs, health, configs, status): auto-execute
  - WRITE ops (edit files, restart, deploy): requires ✅ approval
  - DANGEROUS ops (delete, secrets, live keys): blocked entirely
"""

import os
import json
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import discord
from discord.ext import commands
from discord import app_commands
import aiohttp

log = logging.getLogger("infra-agent")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
HAIKU_MODEL = os.getenv("CLAUDE_HAIKU_MODEL", "claude-haiku-4-5-20251001")
SONNET_MODEL = os.getenv("CLAUDE_SONNET_MODEL", "claude-sonnet-4-20250514")
GUARD_URL = os.getenv("GUARD_URL", "http://trader-guards:8100")

# Commands that are safe to run without approval
SAFE_COMMANDS = {
    "docker compose ps",
    "docker compose logs --tail=50",
    "docker compose logs --tail=50 discord-bot",
    "docker compose logs --tail=50 guard-engine",
    "docker compose logs --tail=50 orchestrator",
    "docker compose logs --tail=50 alpaca-mcp",
    "cat configs/guard_config.json",
    "cat configs/watchlist.json",
    "cat configs/strategies.json",
    "cat configs/channels.json",
    "git status",
    "git log --oneline -10",
    "df -h",
    "free -m",
    "uptime",
}

# Patterns that are NEVER allowed
BLOCKED_PATTERNS = [
    "rm -rf",
    "docker system prune",
    "docker volume rm",
    ".env",
    "ALPACA_LIVE",
    "SECRET_KEY",
    "API_KEY",
    "passwd",
    "useradd",
    "userdel",
    "chmod 777",
    "curl | bash",
    "wget | bash",
    "> /dev/",
    "mkfs",
    "dd if=",
]


def is_safe_command(cmd: str) -> bool:
    """Check if a command is in the safe auto-execute list."""
    return cmd.strip() in SAFE_COMMANDS


def is_blocked(cmd: str) -> bool:
    """Check if a command matches any blocked pattern."""
    cmd_lower = cmd.lower()
    return any(pattern.lower() in cmd_lower for pattern in BLOCKED_PATTERNS)


def classify_command(cmd: str) -> str:
    """Classify a command as safe/approval/blocked."""
    if is_blocked(cmd):
        return "blocked"
    if is_safe_command(cmd):
        return "safe"
    return "approval"


# ---------------------------------------------------------------------------
# Shell execution (runs inside the Discord bot container)
# ---------------------------------------------------------------------------
async def run_shell(cmd: str, cwd: str = "/app") -> tuple[str, int]:
    """Execute a shell command and return (output, return_code)."""
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=cwd,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        output = stdout.decode("utf-8", errors="replace")
        # Truncate if too long for Discord
        if len(output) > 1900:
            output = output[:1900] + "\n... (truncated)"
        return output, proc.returncode
    except asyncio.TimeoutError:
        return "Command timed out (30s limit)", -1
    except Exception as e:
        return f"Error: {e}", -1


# ---------------------------------------------------------------------------
# Claude helper for interpreting natural language ops requests
# ---------------------------------------------------------------------------
async def ask_claude_ops(user_message: str, context: str = "") -> dict:
    """Ask Claude to interpret an ops request and return structured action."""
    if not ANTHROPIC_API_KEY:
        return {"action": "reply", "message": "No API key configured for infra agent."}

    system_prompt = """You are an infrastructure agent for a trading system running on Docker.
You interpret ops requests and return a JSON response with the action to take.

The system has these services: discord-bot, guard-engine, orchestrator, alpaca-mcp
Config files: configs/guard_config.json, configs/watchlist.json, configs/strategies.json
Logs: docker compose logs --tail=50 <service>
Project dir: /app (inside container) maps to /home/trader/QuantAI (on host)

Return ONLY valid JSON, no markdown:
{"action": "command", "cmd": "the shell command", "description": "what it does"}
{"action": "edit", "file": "path", "find": "old text", "replace": "new text", "description": "what changes"}
{"action": "reply", "message": "informational response"}
{"action": "multi", "steps": [array of action objects above]}

Rules:
- Never touch .env or secrets
- Never delete data or volumes
- For docker commands, use 'docker compose' (not docker-compose)
- Keep commands simple and safe
- If unsure, ask for clarification via reply action"""

    user_content = f"Request: {user_message}"
    if context:
        user_content += f"\n\nRecent context:\n{context}"

    headers = {
        "Content-Type": "application/json",
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
    }

    payload = {
        "model": HAIKU_MODEL,
        "max_tokens": 800,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_content}],
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.anthropic.com/v1/messages",
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                data = await resp.json()
                if resp.status != 200:
                    return {"action": "reply", "message": f"API error: {data}"}

                text = "".join(
                    b["text"] for b in data.get("content", []) if b.get("type") == "text"
                )
                # Parse JSON from response
                text = text.strip()
                if text.startswith("```"):
                    text = text.split("\n", 1)[1].rsplit("```", 1)[0]
                return json.loads(text)
    except json.JSONDecodeError:
        return {"action": "reply", "message": f"Could not parse response: {text[:500]}"}
    except Exception as e:
        return {"action": "reply", "message": f"Infra agent error: {e}"}


# ---------------------------------------------------------------------------
# Embed builders
# ---------------------------------------------------------------------------
def ops_embed(title: str, description: str, color: discord.Color = discord.Color.blue()) -> discord.Embed:
    return discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=datetime.now(timezone.utc),
    ).set_footer(text="Infra Agent")


def cmd_result_embed(cmd: str, output: str, return_code: int) -> discord.Embed:
    color = discord.Color.green() if return_code == 0 else discord.Color.red()
    status = "✅" if return_code == 0 else "❌"
    return ops_embed(
        f"{status} `{cmd}`",
        f"```\n{output}\n```" if output else "*(no output)*",
        color=color,
    )


# ---------------------------------------------------------------------------
# The Cog
# ---------------------------------------------------------------------------
class InfraAgent(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.pending_actions: dict[int, dict] = {}  # message_id -> action

    # --- Slash commands ---

    @app_commands.command(name="infra", description="Ask the infra agent to do something")
    @app_commands.describe(request="What do you need? e.g. 'show guard engine logs' or 'restart the discord bot'")
    async def cmd_infra(self, interaction: discord.Interaction, request: str):
        await interaction.response.defer()

        # Get Claude's interpretation
        action = await ask_claude_ops(request)
        await self._handle_action(interaction, action)

    @app_commands.command(name="logs", description="Quick: show recent logs for a service")
    @app_commands.describe(service="Service name: discord-bot, guard-engine, orchestrator, alpaca-mcp")
    async def cmd_logs(self, interaction: discord.Interaction, service: str = ""):
        await interaction.response.defer()
        if service:
            cmd = f"docker compose logs --tail=50 {service}"
        else:
            cmd = "docker compose logs --tail=30"

        output, rc = await run_shell(cmd, cwd="/app")
        await interaction.followup.send(embed=cmd_result_embed(cmd, output, rc))

    @app_commands.command(name="health", description="Quick: full system health check")
    async def cmd_health(self, interaction: discord.Interaction):
        await interaction.response.defer()

        checks = {}

        # Docker containers
        output, _ = await run_shell("docker compose ps --format json", cwd="/app")
        checks["containers"] = output

        # Guard engine
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{GUARD_URL}/health", timeout=aiohttp.ClientTimeout(total=3)) as resp:
                    guard_health = await resp.json()
                    checks["guard_engine"] = "✅ healthy" if resp.status == 200 else "❌ unhealthy"
                    checks["halted"] = "🚨 YES" if guard_health.get("halted") else "🟢 No"
        except Exception as e:
            checks["guard_engine"] = f"❌ unreachable: {e}"

        # System resources
        mem_output, _ = await run_shell("free -m | head -2")
        disk_output, _ = await run_shell("df -h / | tail -1")
        uptime_output, _ = await run_shell("uptime -p")

        embed = ops_embed(
            "🏥 System Health",
            f"**Guard Engine**: {checks.get('guard_engine', '?')}\n"
            f"**Halted**: {checks.get('halted', '?')}\n"
            f"**Uptime**: {uptime_output.strip()}\n"
            f"**Memory**:\n```{mem_output}```\n"
            f"**Disk**:\n```{disk_output}```",
            color=discord.Color.green(),
        )
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="deploy", description="Pull latest code from GitHub and redeploy")
    async def cmd_deploy(self, interaction: discord.Interaction):
        await interaction.response.defer()

        embed = ops_embed(
            "🚀 Deploy Requested",
            "This will:\n"
            "1. `git pull` latest code\n"
            "2. `docker compose down`\n"
            "3. `docker compose build`\n"
            "4. `docker compose up -d`\n\n"
            "React ✅ to confirm, ❌ to cancel.",
            color=discord.Color.gold(),
        )
        msg = await interaction.followup.send(embed=embed, wait=True)
        await msg.add_reaction("✅")
        await msg.add_reaction("❌")

        self.pending_actions[msg.id] = {
            "type": "deploy",
            "cmd": "cd /app && git pull && docker compose down && docker compose build && docker compose up -d",
            "user_id": interaction.user.id,
        }

    @app_commands.command(name="restart", description="Restart a specific service")
    @app_commands.describe(service="Service to restart: discord-bot, guard-engine, orchestrator, alpaca-mcp, all")
    async def cmd_restart(self, interaction: discord.Interaction, service: str = "all"):
        await interaction.response.defer()

        if service == "all":
            cmd = "docker compose restart"
        else:
            cmd = f"docker compose restart {service}"

        embed = ops_embed(
            "🔄 Restart Requested",
            f"Command: `{cmd}`\n\nReact ✅ to confirm, ❌ to cancel.",
            color=discord.Color.gold(),
        )
        msg = await interaction.followup.send(embed=embed, wait=True)
        await msg.add_reaction("✅")
        await msg.add_reaction("❌")

        self.pending_actions[msg.id] = {
            "type": "restart",
            "cmd": cmd,
            "user_id": interaction.user.id,
        }

    @app_commands.command(name="run", description="Run a shell command on the VPS (safe commands only)")
    @app_commands.describe(cmd="Shell command to execute")
    async def cmd_run(self, interaction: discord.Interaction, cmd: str):
        await interaction.response.defer()

        classification = classify_command(cmd)

        if classification == "blocked":
            await interaction.followup.send(
                embed=ops_embed("🚫 Blocked", f"Command `{cmd}` is not allowed.", discord.Color.red())
            )
            return

        if classification == "safe":
            output, rc = await run_shell(cmd, cwd="/app")
            await interaction.followup.send(embed=cmd_result_embed(cmd, output, rc))
            return

        # Needs approval
        embed = ops_embed(
            "⚠️ Approval Required",
            f"Command: `{cmd}`\n\nReact ✅ to execute, ❌ to cancel.",
            color=discord.Color.gold(),
        )
        msg = await interaction.followup.send(embed=embed, wait=True)
        await msg.add_reaction("✅")
        await msg.add_reaction("❌")

        self.pending_actions[msg.id] = {
            "type": "command",
            "cmd": cmd,
            "user_id": interaction.user.id,
        }

    @app_commands.command(name="config", description="View or edit a config file")
    @app_commands.describe(
        file="Config file: guards, watchlist, strategies",
        action="view or edit",
        key="JSON key path to edit (e.g. 'position.max_position_pct')",
        value="New value",
    )
    async def cmd_config(
        self,
        interaction: discord.Interaction,
        file: str = "guards",
        action: str = "view",
        key: str = "",
        value: str = "",
    ):
        await interaction.response.defer()

        file_map = {
            "guards": "/app/configs/guard_config.json",
            "watchlist": "/app/configs/watchlist.json",
            "strategies": "/app/configs/strategies.json",
        }

        filepath = file_map.get(file)
        if not filepath:
            await interaction.followup.send(
                embed=ops_embed("❌ Unknown config", f"Options: {', '.join(file_map.keys())}", discord.Color.red())
            )
            return

        if action == "view":
            output, rc = await run_shell(f"cat {filepath}")
            # Truncate for Discord
            if len(output) > 1800:
                output = output[:1800] + "\n..."
            await interaction.followup.send(
                embed=ops_embed(f"📄 {file}", f"```json\n{output}\n```")
            )
            return

        if action == "edit" and key and value:
            embed = ops_embed(
                "✏️ Config Edit Requested",
                f"**File**: {file}\n**Key**: `{key}`\n**New value**: `{value}`\n\n"
                "React ✅ to apply, ❌ to cancel.",
                color=discord.Color.gold(),
            )
            msg = await interaction.followup.send(embed=embed, wait=True)
            await msg.add_reaction("✅")
            await msg.add_reaction("❌")

            self.pending_actions[msg.id] = {
                "type": "config_edit",
                "filepath": filepath,
                "key": key,
                "value": value,
                "user_id": interaction.user.id,
            }
            return

        await interaction.followup.send(
            embed=ops_embed("❓ Usage", "`/config guards view` or `/config guards edit position.max_position_pct 3.0`")
        )

    # --- Reaction handler for approvals ---

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.user_id == self.bot.user.id:
            return

        action = self.pending_actions.get(payload.message_id)
        if not action:
            return

        # Only the requesting user can approve
        if payload.user_id != action.get("user_id"):
            return

        channel = self.bot.get_channel(payload.channel_id)
        emoji = str(payload.emoji)

        if emoji == "❌":
            del self.pending_actions[payload.message_id]
            await channel.send(embed=ops_embed("❌ Cancelled", "Action cancelled.", discord.Color.red()))
            return

        if emoji != "✅":
            return

        del self.pending_actions[payload.message_id]

        if action["type"] in ("command", "deploy", "restart"):
            await channel.send(embed=ops_embed("⏳ Executing...", f"`{action['cmd']}`", discord.Color.blue()))
            output, rc = await run_shell(action["cmd"], cwd="/app")
            await channel.send(embed=cmd_result_embed(action["cmd"], output, rc))

        elif action["type"] == "config_edit":
            try:
                filepath = action["filepath"]
                with open(filepath) as f:
                    config = json.load(f)

                # Navigate nested keys
                keys = action["key"].split(".")
                obj = config
                for k in keys[:-1]:
                    obj = obj[k]

                # Parse value type
                raw_value = action["value"]
                try:
                    parsed_value = json.loads(raw_value)
                except (json.JSONDecodeError, ValueError):
                    parsed_value = raw_value

                old_value = obj.get(keys[-1], "(not set)")
                obj[keys[-1]] = parsed_value

                with open(filepath, "w") as f:
                    json.dump(config, f, indent=2)

                await channel.send(
                    embed=ops_embed(
                        "✅ Config Updated",
                        f"**{action['key']}**: `{old_value}` → `{parsed_value}`\n\n"
                        "Reload guard engine to apply:\n`/restart guard-engine`",
                        discord.Color.green(),
                    )
                )
            except Exception as e:
                await channel.send(
                    embed=ops_embed("❌ Edit Failed", f"Error: {e}", discord.Color.red())
                )


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
async def setup(bot: commands.Bot):
    await bot.add_cog(InfraAgent(bot))
