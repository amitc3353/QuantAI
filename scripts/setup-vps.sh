#!/bin/bash
# ==============================================================
# Claude Auto-Trader — First-Time VPS Setup (Hetzner)
# ==============================================================
# Run this ONCE on a fresh Ubuntu 24.04 VPS.
# Usage: ssh root@your-vps-ip 'bash -s' < scripts/setup-vps.sh
# ==============================================================

set -euo pipefail

echo "=========================================="
echo "  Claude Auto-Trader — VPS Setup"
echo "=========================================="

# --- 1. System updates ---
echo "[1/8] Updating system..."
apt-get update && apt-get upgrade -y

# --- 2. Create non-root user ---
echo "[2/8] Creating 'trader' user..."
if ! id "trader" &>/dev/null; then
    adduser --disabled-password --gecos "" trader
    usermod -aG sudo trader
    echo "trader ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers.d/trader
    # Copy SSH keys from root
    mkdir -p /home/trader/.ssh
    cp /root/.ssh/authorized_keys /home/trader/.ssh/
    chown -R trader:trader /home/trader/.ssh
    chmod 700 /home/trader/.ssh
    chmod 600 /home/trader/.ssh/authorized_keys
fi

# --- 3. Install Docker ---
echo "[3/8] Installing Docker..."
if ! command -v docker &>/dev/null; then
    curl -fsSL https://get.docker.com | sh
    usermod -aG docker trader
fi

# --- 4. Install Docker Compose ---
echo "[4/8] Installing Docker Compose..."
if ! command -v docker compose &>/dev/null; then
    apt-get install -y docker-compose-plugin
fi

# --- 5. Firewall (UFW) ---
echo "[5/8] Configuring firewall..."
apt-get install -y ufw
ufw default deny incoming
ufw default allow outgoing
ufw allow ssh
# No other ports needed — bot connects outbound to Discord & Alpaca
ufw --force enable

# --- 6. Fail2ban ---
echo "[6/8] Installing fail2ban..."
apt-get install -y fail2ban
systemctl enable fail2ban
systemctl start fail2ban

# --- 7. SSH hardening ---
echo "[7/8] Hardening SSH..."
sed -i 's/#PermitRootLogin yes/PermitRootLogin no/' /etc/ssh/sshd_config
sed -i 's/#PasswordAuthentication yes/PasswordAuthentication no/' /etc/ssh/sshd_config
sed -i 's/PermitRootLogin yes/PermitRootLogin no/' /etc/ssh/sshd_config
sed -i 's/PasswordAuthentication yes/PasswordAuthentication no/' /etc/ssh/sshd_config
systemctl restart sshd

# --- 8. Create project directory ---
echo "[8/8] Setting up project directory..."
mkdir -p /home/trader/claude-auto-trader
chown -R trader:trader /home/trader/claude-auto-trader

echo ""
echo "=========================================="
echo "  ✅ VPS setup complete!"
echo "=========================================="
echo ""
echo "Next steps:"
echo "  1. SSH as trader: ssh trader@$(hostname -I | awk '{print $1}')"
echo "  2. Clone your repo into /home/trader/claude-auto-trader"
echo "  3. Copy .env.example to .env and fill in secrets"
echo "  4. Run: docker compose up -d"
echo ""
echo "⚠️  Root SSH login is now DISABLED."
echo "    Use the 'trader' user from now on."
echo "=========================================="
