# Navis Head RPi 🤖

Raspberry Pi–hosted chatbot server for the **Navis** robot head.

- **LLM chatbot** powered by Groq (llama-3.3-70b-versatile)  
- **Servo mouth** driven **directly from RPi GPIO** — no ESP32 needed  
- **HTTPS on `navisrpi:5000`** — microphone works on any mobile browser  
- **Auto IP detection** — server always prints and embeds the current LAN IP  
- **Training panel** — teach Navis custom Q&A pairs (persisted locally or in Supabase)

---

## Quick Start (on the RPi)

```bash
git clone <this-repo> navisheadrpi
cd navisheadrpi

# One-shot setup (installs deps, SSL cert, mDNS, systemd service)
chmod +x setup_rpi.sh
./setup_rpi.sh

# Edit .env with your Groq API key
nano .env

# Start the server
sudo systemctl start navisheadrpi

# Check logs
journalctl -u navisheadrpi -f
```

Open **`https://navisrpi:5000`** from any device on the same Wi-Fi.  
Accept the browser's self-signed certificate warning once.

### Manual Run (for Debugging)
If you want to run the server locally or debug issues without the background service:

```bash
# Stop the background service first (if running)
sudo systemctl stop navisheadrpi

# Activate the virtual environment
source venv/bin/activate

# Start the Flask app directly
python app.py
```

---

## Hardware Wiring (Servo)

| Servo Wire | RPi Pin (BCM) |
|------------|---------------|
| Signal     | GPIO 18 (default, set `SERVO_PIN` in `.env`) |
| VCC (5 V)  | Pin 2 or 4 |
| GND        | Any GND pin |

> **Tip:** Do NOT power a servo directly from the RPi 5V rail under load.  
> Use an external 5V supply sharing GND with the RPi.

Servo duty-cycle defaults (SG90/MG996R compatible):

| Position | Duty Cycle |
|----------|-----------|
| Closed   | 5.0 %     |
| Open     | 7.5 %     |

Tune `SERVO_OPEN_DC` / `SERVO_CLOSED_DC` in `.env` for your specific servo.

---

## Environment Variables (`.env`)

| Variable          | Default | Description |
|-------------------|---------|-------------|
| `GROQ_API_KEY`    | —       | Required. Get a free key at console.groq.com |
| `DATABASE_URL`    | —       | Optional Supabase PG URL; falls back to local JSON |
| `SERVO_PIN`       | `18`    | BCM GPIO pin for servo signal |
| `SERVO_OPEN_DC`   | `7.5`   | PWM duty cycle % when mouth is open |
| `SERVO_CLOSED_DC` | `5.0`   | PWM duty cycle % when mouth is closed |
| `PORT`            | `5000`  | Flask server port |

---

## SSL / HTTPS

The `setup_rpi.sh` generates `cert.pem` / `key.pem` locally using OpenSSL.  
The certificate's Subject Alternative Name (SAN) includes:

- `DNS:navisrpi`
- `DNS:navisrpi.local`
- `DNS:localhost`
- `IP:<current LAN IP>`

If the RPi's IP changes, run:

```bash
./gen_cert.sh
sudo systemctl restart navisheadrpi
```

---

## mDNS (navisrpi hostname)

`avahi-daemon` is installed and the hostname is set to `navisrpi`.  
Devices that support mDNS (Android, iOS, macOS, Linux) can reach the server at:

```
https://navisrpi.local:5000
```

Windows users may need to install [Bonjour](https://support.apple.com/kb/DL999) or use the raw IP.

---

## Project Structure

```
navisheadrpi/
├── app.py                # Flask server + servo GPIO
├── database.py           # Training data storage (JSON or PostgreSQL)
├── requirements.txt
├── .env.example
├── setup_rpi.sh          # One-shot RPi setup script
├── gen_cert.sh           # Regenerate SSL cert with current IP
├── training_data.json    # Local Q&A storage (auto-created)
├── cert.pem / key.pem    # TLS certificates (auto-generated)
├── templates/
│   └── index.html
└── static/
    ├── css/style.css
    ├── js/app.js
    └── images/
        └── robomanthan_logo.png
```
