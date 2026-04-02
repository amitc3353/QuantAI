#!/bin/bash
# Syncs workspace files from git repo to OpenClaw gateway.
# Run after any AGENTS.md or SOUL.md update.
# Usage: bash /home/trader/QuantAI/scripts/sync_workspaces.sh

REPO="/home/trader/QuantAI/v2"
OC="/root/quantai-v2"

echo "Syncing workspace files to OpenClaw..."

for agent in orchestrator research infra journal; do
    src="$REPO/workspace-$agent"
    dst="$OC/workspace-$agent"
    if [ -f "$src/AGENTS.md" ]; then
        cp "$src/AGENTS.md" "$dst/AGENTS.md" && echo "  ✅ $agent/AGENTS.md"
    fi
    if [ -f "$src/SOUL.md" ]; then
        cp "$src/SOUL.md" "$dst/SOUL.md" && echo "  ✅ $agent/SOUL.md"
    fi
done

echo ""
echo "Done. OpenClaw reads updated files on next message."
