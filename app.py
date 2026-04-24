from flask import Flask, render_template, request, jsonify
from groq import Groq
import os
import threading
import time
import socket
from difflib import SequenceMatcher
from dotenv import load_dotenv
from database import load_training_data, add_qa_pair, delete_qa_pair, init_storage

# ── Optional RPi GPIO servo control ───────────────────────────
try:
    import RPi.GPIO as GPIO
    GPIO.setmode(GPIO.BCM)
    GPIO_AVAILABLE = True
except (ImportError, RuntimeError):
    GPIO_AVAILABLE = False
    print("⚠️  RPi.GPIO not available — servo control disabled (running on non-RPi hardware).")

load_dotenv()

app = Flask(__name__)

# ── Configuration ──────────────────────────────────────────────
GROQ_API_KEY = os.getenv('GROQ_API_KEY', '')
SIMILARITY_THRESHOLD = 0.72
MODEL = "llama-3.3-70b-versatile"

# GPIO pin for mouth servo (BCM numbering)
SERVO_PIN = int(os.getenv('SERVO_PIN', '18'))   # GPIO18 = hardware PWM pin

# Servo PWM parameters
SERVO_FREQ      = 50     # Hz (standard servo)
SERVO_OPEN_DC   = float(os.getenv('SERVO_OPEN_DC', '7.5'))    # duty cycle when mouth open
SERVO_CLOSED_DC = float(os.getenv('SERVO_CLOSED_DC', '5.0'))  # duty cycle when mouth closed

SYSTEM_PROMPT = """You are Navis, an advanced AI assistant developed by Robo Manthan.

Your personality:
- Professional yet friendly and approachable
- Knowledgeable across a wide range of topics
- Clear, concise, and helpful
- Proud of being created by the Robo Manthan team

About you:
- Name: Navis
- Created by: Rahul and the Robo Manthan team
- Capabilities: Text & voice Q&A. You understand English, Hindi, and Kannada.

About Robo Manthan (Robomanthan Pvt. Ltd.):
- An Indian robotech company specializing in robotics, AI, machine learning, and embedded product development
- CEO: Saurav Kumar | CTO: Tanuj Kashyap
- Incubated at IIT Patna, headquartered in Bengaluru (BTM 2nd Stage)
- Incorporated: January 8, 2021
- Motto: 'आपके उन्नति का साथी' (Your partner in progress)
- Products: Humanoid robots, autonomous systems, smart wheelchairs, educational robotics kits
- Services: STEM education, workshops, internships, ATAL Tinkering Labs, 50+ college MoUs

Keep responses concise but thorough. Use markdown formatting when helpful. Your answers will be spoken aloud, so keep them conversational."""

LANG_INSTRUCTIONS = {
    'hi-IN': '[RESPOND IN HINDI using Devanagari script (हिन्दी). Keep it conversational and natural.]',
    'kn-IN': '[RESPOND IN KANNADA using Kannada script (ಕನ್ನಡ). Keep it conversational and natural.]',
    'en-IN': '',
}

# ── Global Hardware State ──────────────────────────────────────
# speaking: 1=talking, 0=silent
bot_state = {
    "speaking": 0,
}
state_lock = threading.Lock()
_mouth_thread_running = False

# ── Servo PWM handle ───────────────────────────────────────────
_servo_pwm = None

def _init_servo():
    """Set up PWM on SERVO_PIN and position servo to closed."""
    global _servo_pwm
    if not GPIO_AVAILABLE:
        return
    try:
        GPIO.setup(SERVO_PIN, GPIO.OUT)
        _servo_pwm = GPIO.PWM(SERVO_PIN, SERVO_FREQ)
        _servo_pwm.start(0)  # Start unpowered to prevent initial jitter
        print(f"✅  Servo PWM ready on GPIO{SERVO_PIN} (idle at 0% duty).")
    except Exception as e:
        print(f"⚠️  Servo init error: {e}")

def _set_servo(duty_cycle: float):
    """Move servo to the given duty cycle (non-blocking)."""
    if _servo_pwm is None:
        return
    try:
        _servo_pwm.ChangeDutyCycle(duty_cycle)
    except Exception as e:
        print(f"⚠️  Servo move error: {e}")

# ── Auto-detect LAN IP ─────────────────────────────────────────
def get_local_ip() -> str:
    """
    Finds the machine's outbound LAN IP by briefly opening a UDP socket
    to an external address (nothing is actually sent).  Falls back to
    localhost if no network is available.
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0)
        try:
            s.connect(('8.8.8.8', 80))
            ip = s.getsockname()[0]
        finally:
            s.close()
        return ip
    except Exception:
        return '127.0.0.1'

# ─────────────────────────────────────────────────────────────
def _mouth_animation_loop():
    global _mouth_thread_running
    is_open = False
    
    if not GPIO_AVAILABLE:
        print("[SERVO-SIM] Mouth animation loop started... 🗣️")
    
    while True:
        with state_lock:
            speaking = bot_state.get('speaking', 0)
            
        if not speaking:
            break
            
        if GPIO_AVAILABLE:
            if is_open:
                _set_servo(SERVO_CLOSED_DC)
            else:
                _set_servo(SERVO_OPEN_DC)
                
        is_open = not is_open
        time.sleep(0.2)  # Toggle very quickly (0.2s) to match speech

    # Done speaking, close mouth and power off
    if GPIO_AVAILABLE:
        _set_servo(SERVO_CLOSED_DC)
        time.sleep(0.5)
        with state_lock:
            if bot_state.get('speaking', 0) == 0:
                _set_servo(0)
    else:
        print("[SERVO-SIM] Mouth animation loop stopped. 🤐")

    with state_lock:
        _mouth_thread_running = False

def update_hardware():
    """
    Reads the current bot_state and drives the GPIO servo accordingly.
    Speaking = 1  → starts a loop to continuously open and close the mouth
    Speaking = 0  → loop exits, servo closes and rests
    """
    global _mouth_thread_running
    
    with state_lock:
        speaking = bot_state['speaking']
        if speaking and not _mouth_thread_running:
            _mouth_thread_running = True
            threading.Thread(target=_mouth_animation_loop, daemon=True).start()


# ── Groq Init ─────────────────────────────────────────────────
client = None
conversation_history = []

def init_groq():
    global client, conversation_history
    if GROQ_API_KEY:
        client = Groq(api_key=GROQ_API_KEY)
        conversation_history = []
        return True
    return False

def chat_with_groq(message, lang='en-IN'):
    """Send a message using Groq and maintain conversation history."""
    global conversation_history

    lang_instruction = LANG_INSTRUCTIONS.get(lang, '')
    full_message = f"{lang_instruction}\n{message}" if lang_instruction else message

    conversation_history.append({"role": "user", "content": full_message})
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + conversation_history

    response = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        temperature=0.7,
        max_tokens=2048,
    )

    assistant_text = response.choices[0].message.content
    conversation_history.append({"role": "assistant", "content": assistant_text})

    if len(conversation_history) > 40:
        conversation_history = conversation_history[-40:]

    return assistant_text


# ── Training Data Helpers ──────────────────────────────────────
def find_matching_qa(question):
    """Find the best matching trained Q&A for a given question."""
    data = load_training_data()
    q_lower = question.lower().strip()
    best_match = None
    best_score = 0

    for qa in data.get('qa_pairs', []):
        trained_q = qa['question'].lower().strip()
        seq_score = SequenceMatcher(None, q_lower, trained_q).ratio()
        t_words = set(trained_q.split())
        q_words = set(q_lower.split())
        overlap = len(t_words & q_words) / max(len(t_words), 1)
        combined = (seq_score + overlap) / 2
        if combined > best_score:
            best_score = combined
            best_match = qa

    if best_score >= SIMILARITY_THRESHOLD and best_match:
        return best_match['answer']
    return None


# ── Routes ─────────────────────────────────────────────────────
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/health')
def health():
    return jsonify({
        'status': 'ok',
        'model': MODEL,
        'groq': client is not None,
        'gpio_servo': GPIO_AVAILABLE,
        'bot_state': bot_state,
        'server_ip': get_local_ip(),
    })

@app.route('/api/chat', methods=['POST'])
def chat():
    data = request.json
    message = data.get('message', '').strip()
    lang = data.get('lang', 'en-IN')
    if not message:
        return jsonify({'error': 'Empty message'}), 400

    # 1) Check training data first
    trained_answer = find_matching_qa(message)
    if trained_answer:
        return jsonify({'response': trained_answer, 'source': 'trained', 'lang': lang})

    # 2) Fall back to Groq AI
    if not client:
        return jsonify({
            'response': "I'm not fully configured yet. Please add your GROQ_API_KEY to a `.env` file and restart the server.",
            'source': 'error',
            'lang': lang
        })

    try:
        response_text = chat_with_groq(message, lang)
        return jsonify({'response': response_text, 'source': 'ai', 'lang': lang})
    except Exception as e:
        return jsonify({'response': f"Sorry, I encountered an error: {str(e)}", 'source': 'error', 'lang': lang})


@app.route('/api/mouth', methods=['POST'])
def mouth():
    """
    Called by app.js to sync the RPi GPIO servo mouth with the browser's TTS.
    Body: { "state": 1 }  → mouth open / speaking starts
    Body: { "state": 0 }  → mouth close / speaking ends
    """
    data = request.json or {}
    state = int(data.get('state', 0))
    state = 1 if state else 0  # Sanitize to strict 0/1

    with state_lock:
        bot_state["speaking"] = state

    update_hardware()

    action = "START ANIMATION (speaking)" if state else "STOP ANIMATION (silent)"
    print(f"[MOUTH] State: {state}  → Servo {action}")
    return jsonify({'success': True, 'speaking': state, 'bot_state': bot_state})


@app.route('/api/train', methods=['POST'])
def train():
    data = request.json
    question = data.get('question', '').strip()
    answer = data.get('answer', '').strip()
    if not question or not answer:
        return jsonify({'error': 'Both question and answer are required'}), 400
    new_id = add_qa_pair(question, answer)
    return jsonify({'success': True, 'id': new_id})

@app.route('/api/training-data', methods=['GET'])
def get_training_data():
    return jsonify(load_training_data())

@app.route('/api/training-data/<int:qa_id>', methods=['DELETE'])
def delete_training_data(qa_id):
    delete_qa_pair(qa_id)
    return jsonify({'success': True})

@app.route('/api/reset', methods=['POST'])
def reset_chat():
    global conversation_history
    conversation_history = []
    return jsonify({'success': True})


# ── Initialize on import ───────────────────────────────────────
try:
    init_storage()
    init_groq()
    _init_servo()
except Exception as e:
    print(f"⚠️  Init warning: {e}")


# ── Main ───────────────────────────────────────────────────────
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))

    local_ip = get_local_ip()

    # SSL detection
    base_dir = os.path.dirname(os.path.abspath(__file__))
    cert_file = os.path.join(base_dir, 'cert.pem')
    key_file  = os.path.join(base_dir, 'key.pem')
    use_ssl   = os.path.exists(cert_file) and os.path.exists(key_file)

    protocol = 'https' if use_ssl else 'http'
    from database import use_database
    storage = 'PostgreSQL (Supabase)' if use_database() else 'Local JSON file'

    print("\n🤖  Navis AI Assistant  (Raspberry Pi Head Edition)")
    print(f"   AI Engine : {'✅ Groq (' + MODEL + ')' if client else '❌ No key — set GROQ_API_KEY in .env'}")
    print(f"   Storage   : {storage}")
    print(f"   Servo     : {'✅ GPIO' + str(SERVO_PIN) + ' (BCM) at ' + str(SERVO_FREQ) + ' Hz' if GPIO_AVAILABLE else '⚠️  GPIO not available (non-RPi)'}")
    if use_ssl:
        print(f"   🔒 HTTPS  : Enabled (microphone will work on mobile)")
    else:
        print(f"   ⚠️  No SSL certs — run with HTTPS for mobile mic access")
    print(f"   🌐 Local  : {protocol}://localhost:{port}")
    print(f"   🌐 LAN    : {protocol}://{local_ip}:{port}")
    print(f"   🌐 mDNS   : {protocol}://navisrpi.local:{port}  (if avahi-daemon is running)\n")

    ssl_ctx = (cert_file, key_file) if use_ssl else None
    try:
        app.run(
            debug=False,
            use_reloader=False,
            host='0.0.0.0',
            port=port,
            ssl_context=ssl_ctx,
        )
    finally:
        # Clean up servo PWM and GPIO on exit
        if _servo_pwm is not None:
            try:
                _servo_pwm.stop()
            except Exception:
                pass
        if GPIO_AVAILABLE:
            try:
                GPIO.cleanup()
                print("🔌  GPIO cleaned up.")
            except Exception:
                pass
