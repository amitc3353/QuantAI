"""
Conversational Agent — The #chat Brain
========================================
Listens in #chat for natural language messages.
Can discuss ideas, diagnose issues, write code, edit configs,
deploy changes, and learn from every interaction.

This is your co-founder in Discord. It remembers everything.

Security: same model as infra agent.
  - READ ops: auto-execute
  - WRITE ops: asks for confirmation
  - DANGEROUS: blocked
"""

import os
import json
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import discord
from discord.ext import commands
import aiohttp

from memory import (
    build_context,
    log_conversation,
    log_decision,
    log_lesson,
    log_event,
    get_trade_stats,
    get_lessons,
    get_recent_trades,
    get_recent_decisions,
    get_recent_events,
    search_lessons,
    search_trades,
    TRADING_MODE,
)

log = logging.getLogger("chat-agent")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
SONNET_MODEL = os.getenv("CLAUDE_SONNET_MODEL", "claude-sonnet-4-20250514")
HAIKU_MODEL = os.getenv("CLAUDE_HAIKU_MODEL", "claude-haiku-4-5-20251001")
GUARD_URL = os.getenv("GUARD_URL", "http://trader-guards:8100")

# Channel where this agent listens
CHAT_CHANNEL_ID = int(os.getenv("CHANNEL_CHAT", "0"))

# System prompt — the agent's identity and capabilities
SYSTEM_PROMPT = """You are the brain of a Claude-powered autonomous trading system called QuantAI.
You live in Discord and manage a team of specialized agents:
- Research Agent (market intelligence, morning briefs)
- Analysis Agent (options evaluation, Greeks, trade cards)
- Guard Engine (deterministic rule enforcement — NEVER bypassed)
- Execution Agent (Alpaca paper/live order submission)
- Infra Agent (ops, deploys, monitoring)

You are currently in {mode} trading mode.

Your capabilities:
1. DISCUSS: Strategy ideas, market analysis, architecture decisions
2. DIAGNOSE: Read logs, check health, trace why something failed
3. FIX: Edit code, update configs, propose rule changes
4. DEPLOY: Stage changes, get approval, push to production
5. LEARN: Extract lessons from trades and decisions, remember everything

Your rules:
- The Three Laws: Never break guard rules. Show your work. Paper first.
- For any WRITE action (code edit, config change, deploy), describe what you'll do and wait for "yes" or ✅
- For READ actions (logs, health, stats), just do it
- NEVER touch .env, secrets, or live trading keys
- ALWAYS log decisions and lessons to memory
- When you learn something new (pattern, mistake, insight), log it as a lesson
- Reference past lessons and trades when making recommendations
- Be concise. You're in Discord, not writing an essay.

When discussing trades or strategies:
- Always reference the current trading mode ({mode})
- Include relevant lessons learned from memory
- Check guard rules before suggesting any trade
- Paper trades and live trades are tracked separately
- Lessons are shared across both modes (they're universal knowledge)

Format for Discord:
- Use markdown sparingly (bold for emphasis, code blocks for configs/logs)
- Keep responses under 2000 chars (Discord limit)
- Use embeds for structured data (trade cards, stats, health checks)
- Split long responses into multiple messages

MEMORY CONTEXT:
{context}"""


# ---------------------------------------------------------------------------
# Claude API
# ---------------------------------------------------------------------------
async def chat_with_claude(
    user_message: str,
    conversation_history: list[dict] = None,
    context: str = "",
    model: str = None,
) -> str:
    """Send a message to Claude with memory context."""
    if not ANTHROPIC_API_KEY:
        return "No API key configured. Add ANTHROPIC_API_KEY to .env and redeploy."

    model = model or SONNET_MODEL

    system = SYSTEM_PROMPT.format(
        mode=TRADING_MODE,
        context=context,
    )

    # Build messages array with conversation history
    messages = []
    if conversation_history:
        for msg in conversation_history[-10:]:  # Last 10 messages for continuity
            messages.append({
                "role": msg["role"],
                "content": msg["content"][:1000],  # Truncate for token savings
            })
    messages.append({"role": "user", "content": user_message})

    headers = {
        "Content-Type": "application/json",
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
    }

    payload = {
        "model": model,
        "max_tokens": 1500,
        "system": system,
        "messages": messages,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.anthropic.com/v1/messages",
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=45),
            ) as resp:
                data = await resp.json()
                if resp.status != 200:
                    log.error(f"Claude API error: {data}")
                    return f"API error ({resp.status}). Check logs."

                text = "".join(
                    b["text"] for b in data.get("content", []) if b.get("type") == "text"
                )
                return text.strip()
    except asyncio.TimeoutError:
        return "Request timed out. Try a simpler question or check if the API is down."
    except Exception as e:
        log.error(f"Chat agent error: {e}")
        return f"Something went wrong: {e}"


# ---------------------------------------------------------------------------
# Shell execution (same as infra agent)
# ---------------------------------------------------------------------------
async def run_shell(cmd: str, cwd: str = "/app") -> tuple[str, int]:
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=cwd,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        output = stdout.decode("utf-8", errors="replace")
        if len(output) > 1500:
            output = output[:1500] + "\n... (truncated)"
        return output, proc.returncode
    except asyncio.TimeoutError:
        return "Timed out (30s)", -1
    except Exception as e:
        return f"Error: {e}", -1


# ---------------------------------------------------------------------------
# Action handlers — things the chat agent can do
# ---------------------------------------------------------------------------
async def handle_stats(channel: discord.TextChannel):
    """Show trade statistics."""
    stats = get_trade_stats()
    embed = discord.Embed(
        title=f"📊 Trade Stats ({TRADING_MODE})",
        color=discord.Color.blue(),
        timestamp=datetime.now(timezone.utc),
    )
    for key, value in stats.items():
        if key != "mode":
            embed.add_field(name=key.replace("_", " ").title(), value=str(value), inline=True)
    await channel.send(embed=embed)


async def handle_lessons(channel: discord.TextChannel, query: str = ""):
    """Show lessons learned."""
    if query:
        lessons = search_lessons(query)
    else:
        lessons = get_lessons(15)

    if not lessons:
        await channel.send("No lessons logged yet. We'll build knowledge as we trade.")
        return

    text = "\n".join(
        f"• **{l.get('source', '?')}** ({l['timestamp'][:10]}): {l['lesson']}"
        for l in lessons[-10:]
    )
    embed = discord.Embed(
        title="🧠 Lessons Learned",
        description=text[:2000],
        color=discord.Color.purple(),
        timestamp=datetime.now(timezone.utc),
    )
    await channel.send(embed=embed)


async def handle_cto_task(channel: discord.TextChannel, task: str):
    """Queue a CTO Claude Code task and post result inline in #chat."""
    import json as _json
    import os as _os
    from datetime import datetime as _dt

    queue_file = "/app/data/cto_queue.json"
    # Use chat webhook if available so result posts back to #chat
    webhook = _os.getenv("DISCORD_WEBHOOK_CHAT") or _os.getenv("DISCORD_WEBHOOK_SYSTEM", "")

    desc = f"**Task:** {task}" + "\n\nClaude Code is reading the system. Result posts here in ~2 minutes."
    await channel.send(embed=discord.Embed(
        title="CTO Agent Working...",
        description=desc,
        color=0x3498DB,
    ))

    try:
        task_record = {
            "task": task,
            "webhook": webhook,
            "timestamp": _dt.now().isoformat(),
            "status": "pending"
        }
        with open(queue_file, "w") as qf:
            _json.dump(task_record, qf)
    except Exception as e:
        await channel.send("Could not queue task: " + str(e))


async def handle_cto_scan(channel: discord.TextChannel, topic: str = ""):
    """On-demand CTO tech intelligence scan."""
    import sys
    sys.path.insert(0, "/app/services")
    sys.path.insert(0, "/app/project/services")

    await channel.send(embed=discord.Embed(
        title="🔍 CTO Scan Running...",
        description=f"Scanning GitHub, arXiv, and packages{' for: ' + topic if topic else ''}. This takes ~30 seconds.",
        color=0x3498DB,
    ))

    try:
        from cto_agent import run_cto_scan, build_cto_scan_embeds
        result = await run_cto_scan(topic=topic if topic else None, days_back=14)
        embeds = build_cto_scan_embeds(result)
        for embed_dict in embeds[:4]:
            em = discord.Embed(
                title=embed_dict.get("title", "CTO Report"),
                description=embed_dict.get("description", ""),
                color=embed_dict.get("color", 0x3498DB),
            )
            for f in embed_dict.get("fields", []):
                em.add_field(name=f["name"], value=f["value"], inline=f.get("inline", False))
            if embed_dict.get("footer"):
                em.set_footer(text=embed_dict["footer"].get("text", ""))
            await channel.send(embed=em)
    except ImportError:
        await channel.send(
            "CTO agent not accessible from this container. "
            "Results post automatically every Monday 6:00 AM to #research."
        )
    except Exception as e:
        await channel.send(f"CTO scan error: {str(e)[:200]}")


async def handle_remember(channel: discord.TextChannel, lesson_text: str):
    """Manually log a lesson."""
    record = log_lesson(lesson_text, source="manual")
    await channel.send(f"✅ Lesson saved: *{lesson_text[:100]}*")


# ---------------------------------------------------------------------------
# Message listener patterns
# ---------------------------------------------------------------------------
QUICK_PATTERNS = {
    "stats": handle_stats,
    "trade stats": handle_stats,
    "performance": handle_stats,
    "how are we doing": handle_stats,
    "cto scan": lambda ch: handle_cto_scan(ch),
    "cto report": lambda ch: handle_cto_scan(ch),
    "tech scan": lambda ch: handle_cto_scan(ch),
}


# ---------------------------------------------------------------------------
# The Cog
# ---------------------------------------------------------------------------
class ChatAgent(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.conversation_cache: list[dict] = []  # In-memory recent history
        self.typing_lock = asyncio.Lock()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Ignore bots (including self)
        if message.author.bot:
            return

        # Only respond in #chat channel (or if mentioned anywhere)
        is_chat_channel = message.channel.id == CHAT_CHANNEL_ID
        is_mentioned = self.bot.user in message.mentions

        if not is_chat_channel and not is_mentioned:
            return

        # Strip the bot mention if present
        content = message.content
        if is_mentioned:
            content = content.replace(f"<@{self.bot.user.id}>", "").strip()

        if not content:
            return

        # Log the user message to memory
        log_conversation("user", content, channel=message.channel.name)

        # Check for quick patterns first
        content_lower = content.lower().strip()
        for pattern, handler in QUICK_PATTERNS.items():
            if content_lower == pattern or content_lower.startswith(pattern):
                await handler(message.channel)
                return

        # Check for "remember" / "lesson" commands
        if content_lower.startswith("remember:") or content_lower.startswith("lesson:"):
            lesson_text = content.split(":", 1)[1].strip()
            await handle_remember(message.channel, lesson_text)
            return

        # Check for "lessons" query
        if content_lower.startswith("lessons"):
            query = content[7:].strip()
            await handle_lessons(message.channel, query)
            return

        if content_lower.startswith("cto scan") or content_lower.startswith("cto report"):
            topic = content[8:].strip() if content_lower.startswith("cto scan") else content[10:].strip()
            await handle_cto_scan(message.channel, topic=topic)
            return

        if content_lower.startswith("cto:") or content_lower.startswith("cto "):
            task = content[4:].strip()
            if task:
                await handle_cto_task(message.channel, task)
                return

        # Full conversational response
        async with self.typing_lock:
            async with message.channel.typing():
                # Build memory context
                context = build_context(
                    query=content,
                    include_trades=True,
                    include_lessons=True,
                    include_decisions=True,
                    include_events=True,
                    include_conversation=True,
                    max_tokens_estimate=2000,
                )

                # Build conversation history from cache
                history = [
                    {"role": msg["role"], "content": msg["content"]}
                    for msg in self.conversation_cache[-10:]
                ]

                # Call Claude
                response = await chat_with_claude(
                    user_message=content,
                    conversation_history=history,
                    context=context,
                )

                # Log the assistant response to memory
                log_conversation("assistant", response, channel=message.channel.name)

                # Update in-memory cache
                self.conversation_cache.append({"role": "user", "content": content})
                self.conversation_cache.append({"role": "assistant", "content": response})
                if len(self.conversation_cache) > 40:
                    self.conversation_cache = self.conversation_cache[-30:]

                # Auto-extract lessons if Claude mentions learning something
                if any(phrase in response.lower() for phrase in [
                    "lesson learned", "note to self", "we should remember",
                    "key takeaway", "important insight", "pattern:",
                ]):
                    log_event("auto_lesson_detected", f"Potential lesson in response to: {content[:100]}")

                # Send response (split if over Discord limit)
                if len(response) <= 2000:
                    await message.reply(response, mention_author=False)
                else:
                    # Split at paragraph boundaries
                    chunks = []
                    current = ""
                    for line in response.split("\n"):
                        if len(current) + len(line) + 1 > 1900:
                            chunks.append(current)
                            current = line
                        else:
                            current += "\n" + line if current else line
                    if current:
                        chunks.append(current)

                    for i, chunk in enumerate(chunks):
                        if i == 0:
                            await message.reply(chunk, mention_author=False)
                        else:
                            await message.channel.send(chunk)

    # --- Slash commands for memory management ---

    @commands.hybrid_command(name="remember", description="Save a lesson to memory")
    async def cmd_remember(self, ctx: commands.Context, *, lesson: str):
        record = log_lesson(lesson, source="manual")
        await ctx.send(f"✅ Lesson saved: *{lesson[:100]}*")

    # lessons and stats commands are in trading.py

    @commands.hybrid_command(name="decide", description="Log a decision with reasoning")
    async def cmd_decide(
        self,
        ctx: commands.Context,
        category: str,
        description: str,
        reasoning: str,
    ):
        """Log a decision. Categories: rule_change, strategy, architecture, bug_fix, config, feature"""
        record = log_decision(category, description, reasoning)
        await ctx.send(
            f"✅ Decision logged [{category}]: *{description[:100]}*"
        )


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
async def setup(bot: commands.Bot):
    await bot.add_cog(ChatAgent(bot))
