#!/bin/bash
# claude_code_bridge.sh
# Called by Discord bot when user asks CTO to do something.
# Runs Claude Code with the task and posts result back to Discord.
#
# Usage: ./claude_code_bridge.sh "fix the empty chain error" "WEBHOOK_URL"
# The script:
#   1. Runs Claude Code with the task in the QuantAI project directory
#   2. Claude Code reads CLAUDE.md first (its constitution)
#   3. Result gets posted back to Discord webhook

TASK="$1"
WEBHOOK_URL="$2"
PROJECT_DIR="/home/trader/QuantAI"
LOG_FILE="/home/trader/QuantAI/data/logs/claude_code_$(date +%Y%m%d_%H%M%S).log"

mkdir -p "$(dirname "$LOG_FILE")"

# Post "working on it" to Discord immediately
post_discord() {
    local message="$1"
    local color="${2:-3447003}"
    if [ -n "$WEBHOOK_URL" ]; then
        curl -s -X POST "$WEBHOOK_URL" \
            -H "Content-Type: application/json" \
            -d "{\"embeds\":[{\"title\":\"🤖 CTO Agent\",\"description\":\"$message\",\"color\":$color}]}" \
            > /dev/null 2>&1
    fi
}

# Validate inputs
if [ -z "$TASK" ]; then
    post_discord "No task provided." 16711680
    exit 1
fi

if [ ! -f "$PROJECT_DIR/CLAUDE.md" ]; then
    post_discord "CLAUDE.md not found — cannot proceed safely." 16711680
    exit 1
fi

post_discord "Working on: **$TASK**\n\nReading system state and logs..." 3447003

# Check Claude Code is installed
if ! command -v claude &> /dev/null; then
    post_discord "Claude Code not installed. Run: npm install -g @anthropic-ai/claude-code" 16776960
    exit 1
fi

# Run Claude Code
cd "$PROJECT_DIR"

# Build the full prompt — give it context and the task
FULL_PROMPT="Read CLAUDE.md first. Then: $TASK

After completing the task:
1. Summarize what you found and what you did (max 3 sentences)
2. List any files changed
3. If you created a fix, confirm syntax is clean
4. If approval is needed before deploying, say so explicitly"

# Run with timeout and capture output
timeout 300 claude \
    --print \
    --no-color \
    "$FULL_PROMPT" \
    > "$LOG_FILE" 2>&1

EXIT_CODE=$?

if [ $EXIT_CODE -eq 124 ]; then
    post_discord "⏰ Task timed out after 5 minutes. Check logs: \`$LOG_FILE\`" 16711680
    exit 1
fi

# Read output — trim to Discord limit
OUTPUT=$(tail -100 "$LOG_FILE" | head -80)
CHAR_COUNT=${#OUTPUT}

if [ $CHAR_COUNT -gt 1800 ]; then
    OUTPUT="${OUTPUT:0:1800}...\n\n*(Full output in $LOG_FILE)*"
fi

if [ $EXIT_CODE -eq 0 ]; then
    post_discord "✅ Done\n\n\`\`\`\n$OUTPUT\n\`\`\`" 2664261
else
    post_discord "⚠️ Completed with issues (exit $EXIT_CODE)\n\n\`\`\`\n$OUTPUT\n\`\`\`" 16776960
fi
