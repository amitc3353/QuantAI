#!/bin/bash
# cto_listener.sh — Host-side CTO task runner
# Runs on the VPS HOST (not inside a container)
# Watches /home/trader/QuantAI/data/cto_queue.json for tasks
# When a task appears, runs Claude Code and posts result to Discord
#
# Start it: bash /home/trader/QuantAI/scripts/cto_listener.sh &
# Stop it:  kill $(cat /tmp/cto_listener.pid)
# Auto-start on reboot: add to crontab: @reboot bash /home/trader/QuantAI/scripts/cto_listener.sh &

QUEUE_FILE="/home/trader/QuantAI/data/cto_queue.json"
PROJECT_DIR="/home/trader/QuantAI"
LOG_DIR="/home/trader/QuantAI/data/logs"
PID_FILE="/tmp/cto_listener.pid"

# Save PID for easy stopping
echo $$ > "$PID_FILE"
echo "CTO Listener started (PID $$)"

mkdir -p "$LOG_DIR"

# Add npm-global to PATH so claude command is found
export PATH="$PATH:/home/trader/.npm-global/bin"

post_discord() {
    local webhook="$1"
    local title="$2"
    local message="$3"
    local color="${4:-3447003}"
    if [ -n "$webhook" ]; then
        # Escape for JSON
        message=$(echo "$message" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read()))" 2>/dev/null || echo "\"$message\"")
        curl -s -X POST "$webhook" \
            -H "Content-Type: application/json" \
            -d "{\"embeds\":[{\"title\":\"$title\",\"description\":$message,\"color\":$color}]}" \
            > /dev/null 2>&1
    fi
}

while true; do
    # Check if queue file exists and has a pending task
    if [ -f "$QUEUE_FILE" ]; then
        STATUS=$(python3 -c "
import json, sys
try:
    data = json.load(open('$QUEUE_FILE'))
    print(data.get('status', 'unknown'))
except:
    print('error')
" 2>/dev/null)

        if [ "$STATUS" = "pending" ]; then
            # Read task details
            TASK=$(python3 -c "
import json
data = json.load(open('$QUEUE_FILE'))
print(data.get('task', ''))
" 2>/dev/null)
            WEBHOOK=$(python3 -c "
import json
data = json.load(open('$QUEUE_FILE'))
print(data.get('webhook', ''))
" 2>/dev/null)

            if [ -n "$TASK" ]; then
                # Mark as running
                python3 -c "
import json
data = json.load(open('$QUEUE_FILE'))
data['status'] = 'running'
json.dump(data, open('$QUEUE_FILE', 'w'))
" 2>/dev/null

                echo "$(date): Running CTO task: $TASK"
                LOG_FILE="$LOG_DIR/cto_$(date +%Y%m%d_%H%M%S).log"

                # Run Claude Code
                cd "$PROJECT_DIR"
                FULL_PROMPT="Read CLAUDE.md first. Then: $TASK

After completing:
1. Summarize what you found and what you did (max 3 sentences)
2. List any files changed
3. If you need approval before deploying, say so explicitly"

                timeout 300 claude \
                    --print \
                    --no-color \
                    "$FULL_PROMPT" \
                    > "$LOG_FILE" 2>&1

                EXIT_CODE=$?

                # Read output
                OUTPUT=$(tail -60 "$LOG_FILE" | head -50)
                if [ ${#OUTPUT} -gt 1800 ]; then
                    OUTPUT="${OUTPUT:0:1800}..."
                fi

                if [ $EXIT_CODE -eq 124 ]; then
                    post_discord "$WEBHOOK" "⏰ CTO Timed Out" "Task timed out after 5 minutes." 16711680
                elif [ $EXIT_CODE -eq 0 ]; then
                    post_discord "$WEBHOOK" "✅ CTO Done: $TASK" "\`\`\`\n$OUTPUT\n\`\`\`" 2664261
                else
                    post_discord "$WEBHOOK" "⚠️ CTO Completed (exit $EXIT_CODE)" "\`\`\`\n$OUTPUT\n\`\`\`" 16776960
                fi

                # Mark as done
                python3 -c "
import json
data = json.load(open('$QUEUE_FILE'))
data['status'] = 'done'
json.dump(data, open('$QUEUE_FILE', 'w'))
" 2>/dev/null

                echo "$(date): Task complete (exit $EXIT_CODE)"
            fi
        fi
    fi

    sleep 2
done
