#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════
#  Navis Head RPi – One-shot Setup Script
#  Run once on your Raspberry Pi to configure everything.
#  Usage:  chmod +x setup_rpi.sh && ./setup_rpi.sh
# ══════════════════════════════════════════════════════════════
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_FILE="/etc/systemd/system/navisheadrpi.service"

echo ""
echo "══════════════════════════════════════════════════════"
echo "  Navis Head RPi – Setup"
echo "══════════════════════════════════════════════════════"

# ── 1) Install system packages ─────────────────────────────
echo ""
echo "[1/6] Installing system packages…"
sudo apt-get update -qq
sudo apt-get install -y python3 python3-pip python3-venv \
     avahi-daemon openssl libssl-dev libffi-dev \
     python3-rpi.gpio -qq

# ── 2) Python virtual environment ──────────────────────────
echo ""
echo "[2/6] Creating Python virtual environment…"
if [ ! -d "$SCRIPT_DIR/venv" ]; then
    python3 -m venv "$SCRIPT_DIR/venv"
fi
source "$SCRIPT_DIR/venv/bin/activate"
pip install --upgrade pip -q
pip install -r "$SCRIPT_DIR/requirements.txt" -q
deactivate
echo "   ✅  venv ready at $SCRIPT_DIR/venv"

# ── 3) Create .env if it doesn't exist ────────────────────
echo ""
echo "[3/6] Checking .env…"
if [ ! -f "$SCRIPT_DIR/.env" ]; then
    cp "$SCRIPT_DIR/.env.example" "$SCRIPT_DIR/.env"
    echo "   ⚠️  Created .env from .env.example — EDIT IT and add your GROQ_API_KEY!"
else
    echo "   ✅  .env already exists."
fi

# ── 4) Generate self-signed SSL certificate ────────────────
echo ""
echo "[4/6] Generating self-signed TLS certificate (navisrpi)…"
if [ ! -f "$SCRIPT_DIR/cert.pem" ] || [ ! -f "$SCRIPT_DIR/key.pem" ]; then
    # Detect the current LAN IP automatically
    LOCAL_IP=$(python3 - <<'EOF'
import socket
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(0)
    s.connect(('8.8.8.8', 80))
    print(s.getsockname()[0])
    s.close()
except:
    print('127.0.0.1')
EOF
)
    echo "   Detected LAN IP: $LOCAL_IP"

    openssl req -x509 -newkey rsa:2048 -nodes \
        -keyout "$SCRIPT_DIR/key.pem" \
        -out    "$SCRIPT_DIR/cert.pem" \
        -days   3650 \
        -subj   "/CN=navisrpi" \
        -addext "subjectAltName=DNS:navisrpi,DNS:localhost,IP:$LOCAL_IP"

    echo "   ✅  cert.pem + key.pem generated (valid 10 years, SAN=$LOCAL_IP)"
else
    echo "   ✅  SSL certificates already exist."
fi

# ── 5) Enable avahi-daemon (mDNS → navisrpi.local) ─────────
echo ""
echo "[5/6] Enabling avahi-daemon (mDNS)…"
sudo systemctl enable avahi-daemon --now 2>/dev/null || true

# Set hostname to navisrpi so mDNS broadcasts as navisrpi.local
CURRENT_HOSTNAME=$(hostname)
if [ "$CURRENT_HOSTNAME" != "navisrpi" ]; then
    echo "   Current hostname: $CURRENT_HOSTNAME → changing to navisrpi"
    sudo hostnamectl set-hostname navisrpi
    # Update /etc/hosts
    sudo sed -i "s/127\.0\.1\.1.*/127.0.1.1\tnavisrpi/" /etc/hosts 2>/dev/null || \
        echo "127.0.1.1 navisrpi" | sudo tee -a /etc/hosts > /dev/null
    echo "   ✅  Hostname set to navisrpi (reboot required for full effect)"
else
    echo "   ✅  Hostname already navisrpi"
fi

# ── 6) Install systemd service ──────────────────────────────
echo ""
echo "[6/6] Installing systemd service (navisheadrpi)…"
sudo tee "$SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=Navis Head RPi – AI Chatbot Server
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=60
StartLimitBurst=5

[Service]
Type=simple
User=$(whoami)
WorkingDirectory=$SCRIPT_DIR
EnvironmentFile=$SCRIPT_DIR/.env
ExecStart=$SCRIPT_DIR/venv/bin/python $SCRIPT_DIR/app.py
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=navisheadrpi

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable navisheadrpi
echo "   ✅  Service installed and enabled."

# ── Done ───────────────────────────────────────────────────
echo ""
echo "══════════════════════════════════════════════════════"
echo "  ✅  Setup complete!"
echo ""
echo "  Next steps:"
echo "  1.  Edit .env and add your GROQ_API_KEY"
echo "  2.  Reboot (or run):  sudo systemctl start navisheadrpi"
echo "  3.  Open on any LAN device:"
echo "        https://navisrpi:5000"
echo "        https://$(python3 -c "import socket;s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.connect(('8.8.8.8',80));print(s.getsockname()[0]);s.close()"):5000"
echo ""
echo "  ⚠️  You will need to accept the self-signed certificate"
echo "      in your browser the first time."
echo "══════════════════════════════════════════════════════"
echo ""
