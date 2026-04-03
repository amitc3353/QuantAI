#!/bin/bash
# Syncs all QuantAI files from git repo (/root/quantai-v2) to runtime path (/home/trader/QuantAI).
# Run after ANY update to scripts, workspace files, or AGENTS.md.
# Usage: bash /home/trader/QuantAI/scripts/sync_workspaces.sh

REPO="/root/quantai-v2"
RUNTIME="/home/trader/QuantAI"

echo "Syncing QuantAI files to runtime..."

# ── Workspace files (AGENTS.md, SOUL.md) ─────────────────────────────
echo ""
echo "Workspace files:"
for agent in orchestrator research infra journal; do
    src="$REPO/v2/workspace-$agent"
    dst="$RUNTIME/v2/workspace-$agent"
    mkdir -p "$dst"
    for f in AGENTS.md SOUL.md; do
        if [ -f "$src/$f" ]; then
            cp "$src/$f" "$dst/$f" && echo "  ✅ $agent/$f"
        fi
    done
done

# ── Python scripts (pipeline, execution, debate, etc.) ───────────────
echo ""
echo "Scripts:"
mkdir -p "$RUNTIME/v2/shared-data/scripts"
for f in "$REPO/v2/shared-data/scripts/"*.py; do
    fname=$(basename "$f")
    cp "$f" "$RUNTIME/v2/shared-data/scripts/$fname" && echo "  ✅ $fname"
done

echo ""
echo "Done. Cron + OpenClaw will use updated files on next run."
