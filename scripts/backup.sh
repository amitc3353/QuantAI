#!/bin/bash
# ==============================================================
# Claude Auto-Trader — Backup trade data
# ==============================================================
# Backs up journal, briefs, and configs to a timestamped archive.
# Run daily via cron: 0 5 * * * /home/trader/claude-auto-trader/scripts/backup.sh
# ==============================================================

set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/home/trader/claude-auto-trader}"
BACKUP_DIR="${BACKUP_DIR:-/home/trader/backups}"
TIMESTAMP=$(date +%Y-%m-%d_%H%M)

mkdir -p "$BACKUP_DIR"

echo "Backing up trade data..."
tar czf "$BACKUP_DIR/trader_backup_$TIMESTAMP.tar.gz" \
    -C "$PROJECT_DIR" \
    data/journal \
    data/briefs \
    configs/

# Keep only last 30 days of backups
find "$BACKUP_DIR" -name "trader_backup_*.tar.gz" -mtime +30 -delete

echo "✅ Backup saved: $BACKUP_DIR/trader_backup_$TIMESTAMP.tar.gz"
