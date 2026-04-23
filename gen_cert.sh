#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════
#  Regenerate SSL certificate with the CURRENT LAN IP in the SAN.
#  Run this whenever your RPi's IP changes (static IP recommended).
#  Usage:  chmod +x gen_cert.sh && ./gen_cert.sh
# ══════════════════════════════════════════════════════════════
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

LOCAL_IP=$(python3 - <<'EOF'
import socket
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(0)
    s.connect(('8.8.8.8', 80))
    ip = s.getsockname()[0]
    s.close()
    print(ip)
except:
    print('127.0.0.1')
EOF
)

echo "Detected LAN IP: $LOCAL_IP"
echo "Generating cert.pem / key.pem for CN=navisrpi, SAN includes $LOCAL_IP …"

openssl req -x509 -newkey rsa:2048 -nodes \
    -keyout "$SCRIPT_DIR/key.pem" \
    -out    "$SCRIPT_DIR/cert.pem" \
    -days   3650 \
    -subj   "/CN=navisrpi" \
    -addext "subjectAltName=DNS:navisrpi,DNS:navisrpi.local,DNS:localhost,IP:$LOCAL_IP,IP:127.0.0.1"

echo ""
echo "✅  Done!  cert.pem and key.pem written to $SCRIPT_DIR"
echo "    Restart the server:  sudo systemctl restart navisheadrpi"
echo "    (Re-accept the certificate in your browser)"
