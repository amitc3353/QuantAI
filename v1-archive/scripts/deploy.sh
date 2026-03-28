#!/bin/bash
# ==============================================================
# Claude Auto-Trader — Deploy to Hetzner VPS
# ==============================================================
# Usage: ./scripts/deploy.sh [VPS_IP]
# ==============================================================

set -euo pipefail

VPS_IP="${1:-${VPS_IP:-}}"
VPS_USER="${VPS_USER:-trader}"
REMOTE_DIR="/home/trader/claude-auto-trader"

if [ -z "$VPS_IP" ]; then
    echo "Usage: ./scripts/deploy.sh <VPS_IP>"
    echo "  or: VPS_IP=1.2.3.4 ./scripts/deploy.sh"
    exit 1
fi

echo "=========================================="
echo "  Deploying to $VPS_USER@$VPS_IP"
echo "=========================================="

# --- Sync files (excluding secrets and data) ---
echo "[1/4] Syncing files..."
rsync -avz --progress \
    --exclude '.env' \
    --exclude 'data/logs/*' \
    --exclude 'data/journal/*' \
    --exclude 'data/briefs/*' \
    --exclude 'data/cache/*' \
    --exclude '__pycache__' \
    --exclude '.git' \
    --exclude 'node_modules' \
    --exclude '.venv' \
    ./ "$VPS_USER@$VPS_IP:$REMOTE_DIR/"

# --- Rebuild and restart containers ---
echo "[2/4] Building containers..."
ssh "$VPS_USER@$VPS_IP" "cd $REMOTE_DIR && docker compose build"

echo "[3/4] Restarting services..."
ssh "$VPS_USER@$VPS_IP" "cd $REMOTE_DIR && docker compose down && docker compose up -d"

echo "[4/4] Checking health..."
sleep 5
ssh "$VPS_USER@$VPS_IP" "cd $REMOTE_DIR && docker compose ps"

echo ""
echo "=========================================="
echo "  ✅ Deploy complete!"
echo "=========================================="
echo ""
echo "View logs:"
echo "  ssh $VPS_USER@$VPS_IP 'cd $REMOTE_DIR && docker compose logs -f'"
echo ""
