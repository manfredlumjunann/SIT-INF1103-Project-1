"""
Contract Clause Analyzer - Logic Layer
Flask API backend with Hermes AI (OpenRouter) and Firecrawl integration.
Handles clause detection, risk assessment, case law research, and workaround generation.
"""

import os
import sys
import json
import asyncio
import tempfile
from pathlib import Path
from typing import List, Dict, Optional, Any
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
import functools
import logging
import secrets
import time
import requests
import time

from flask import Flask, request, jsonify, session, g
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv

# Document parsing
try:
    import PyPDF2
except ImportError:
    PyPDF2 = None

try:
    from docx import Document
except ImportError:
    Document = None

# AI and Web Search
from firecrawl import FirecrawlApp
from openai.types.chat import ChatCompletionMessageParam

sys.path.append(str(Path(__file__).resolve().parent.parent / 'ai-layer'))
import ai_service

# Data layer: in Docker data_manager.py is copied next to this file;
# when run locally it lives in ../data-layer
try:
    import data_manager
except ImportError:
    sys.path.append(str(Path(__file__).resolve().parent.parent / 'data-layer'))
    import data_manager

load_dotenv()

# Server-side log (visible with `docker compose logs logic-layer`). Error details go here,
# never into responses sent to the browser.
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s [%(name)s] %(message)s')
logger = logging.getLogger('contract-analyzer')

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max upload

# Session cookie used for login. SECRET_KEY signs the cookie; without a fixed key in .env
# a random one is used and everyone is signed out whenever the server restarts.
app.secret_key = os.getenv('SECRET_KEY') or secrets.token_hex(32)
if not os.getenv('SECRET_KEY'):
    print("⚠️ SECRET_KEY not set in .env - using a temporary key; sessions reset on restart")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
)

VALID_RISK_LEVELS = ('HIGH', 'MEDIUM', 'LOW')
TELEGRAM_MEDIUM_COUNT_CHAT_ID = "667740965"
TELEGRAM_HIGH_COUNT_CHAT_ID = "1116849976"
TELEGRAM_GROUP_CHAT_ID = "-5212170665"


def _send_telegram_message(chat_id: str, text: str, kind: str,
                           reply_markup: Optional[Dict] = None) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN not set; cannot send Telegram %s notification", kind)
        return

    payload: Dict[str, Any] = {"chat_id": chat_id, "text": text}
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    try:
        result = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage", json=payload, timeout=10
        )
    except requests.RequestException as error:
        logger.error("Failed to send Telegram %s notification (%s)", kind, type(error).__name__)
        return

    try:
        body = result.json()
    except ValueError:
        body = None

    if not 200 <= result.status_code < 300 or not isinstance(body, dict) or body.get('ok') is not True:
        code = body.get('error_code') if isinstance(body, dict) else None
        if not isinstance(code, int):
            code = result.status_code
        reasons = {
            400: 'check the recipient chat ID and bot membership',
            401: 'check TELEGRAM_BOT_TOKEN',
            403: 'check bot membership and that the recipient has started and not blocked the bot',
            429: 'Telegram rate limit exceeded',
        }
        reason = reasons.get(code, 'check Telegram configuration or service availability')
        logger.error("Telegram rejected %s notification (HTTP %s, error %s): %s",
                     kind, result.status_code, code, reason)
        return
    logger.info("Telegram %s notification sent", kind)


def send_telegram_notification(total_flagged: int, user_id: str, clauses: List[Dict]) -> None:
    """Send Telegram notification when flagged clauses exceed thresholds."""
    if total_flagged > 8:
        chat_id = TELEGRAM_HIGH_COUNT_CHAT_ID
    elif total_flagged > 6:
        chat_id = TELEGRAM_MEDIUM_COUNT_CHAT_ID
    else:
        return

    high_risk = sum(1 for clause in clauses if clause.get('risk_level') == 'HIGH')
    medium_risk = sum(1 for clause in clauses if clause.get('risk_level') == 'MEDIUM')
    low_risk = sum(1 for clause in clauses if clause.get('risk_level') == 'LOW')
    user = get_session_user()
    user_name = user['username'] if user else 'Unknown'

    _send_telegram_message(
        chat_id,
        (
            "Contract clause analysis completed\n"
            f"User ID: {user_id}\n"
            f"Username: {user_name}\n"
            f"Total clause flagged: {total_flagged}\n"
            f"High risk: {high_risk}\n"
            f"Medium Risk: {medium_risk}\n"
            f"Low risk: {low_risk}"
        ),
        'clause',
    )

<<<<<<< HEAD
def send_generic_message(text):
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        if not token:
            logger.warning("TELEGRAM_BOT_TOKEN not set; skipping error notification")
            return
    
        inf_group_id = "-5212170665"
        url_req = f"https://api.telegram.org/bot{token}/sendMessage"
        
        payload = {
            "chat_id": inf_group_id,
            "text": text,
        }
    
        results = requests.post(url_req, json=payload)
        print(results.json())

def wait_for_single_reply(last_update_id):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.warning("TELEGRAM_BOT_TOKEN not set; skipping error notification")
        return

    inf_group_id = "-5212170665"
    url_req = f"https://api.telegram.org/bot{token}/getUpdates"

    print("📱 Telegram listener ACTIVE. Waiting for button press...")

    while True:

        payload = {
            "offset": last_update_id + 1,
            "timeout": 10,
            "allowed_updates": ["callback_query"]
        }

        try:
            results = requests.get(
                url_req,
                params=payload,
                timeout=15
            ).json()

            if results.get("ok") and results.get("result"):

                for update in results["result"]:

                    last_update_id = max(
                        last_update_id,
                        update["update_id"]
                    )

                    callback = update.get("callback_query")

                    if not callback:
                        continue

                    # Verify sender
                    if str(callback["message"]["chat"]["id"]) != str(inf_group_id):
                        continue

                    # Get button selection
                    user_choice = callback.get("data")

                    # Acknowledge button press
                    requests.post(
                        f"https://api.telegram.org/bot{token}/editMessageReplyMarkup",
                        json={
                            "chat_id": callback["message"]["chat"]["id"],
                            "message_id": callback["message"]["message_id"],
                            "reply_markup": {
                                "inline_keyboard": []
                            }
                        }
                    )

                    if user_choice:
                        return user_choice, last_update_id

        except requests.RequestException as e:
            print(f"Error checking Telegram: {e}")

        except ValueError as e:
            print(f"Invalid Telegram response: {e}")

        time.sleep(1)
        
def call_manual_fallback_ai(messages, user_input):

    client = get_openrouter_client()

    print(f"🔄 Trying manually selected model: {user_input}")

    response = client.chat.completions.create(
        model=user_input,
        messages=messages,
        temperature=0.3,
        max_tokens=4096
    )

    content = response.choices[0].message.content

    if not content:
        raise RuntimeError("Backup AI returned an empty response")

    print(f"✅ Manual fallback successful: {user_input}")

    return content

'''def execute_vscode_output(command, messages):
    print(f"⚙️ Manual AI fallback requested: {command}")

    if command.lower() == "accept":
        send_generic_message("🔄 Attempting manual AI recovery...")

        try:
            response = call_manual_fallback_ai(
                messages,
                user_input="nvidia/nemotron-3-ultra-550b-a55b:free"
            )

            send_generic_message("✅ Backup AI successfully recovered!")

            return response

        except Exception as e:
            send_generic_message(f"❌ Backup AI failed: {str(e)[:200]}")
            raise

    elif command.lower() == "reject":
        send_generic_message("🛑 Manual recovery cancelled.")
        return None'''
=======
def send_generic_msg(text):
    _send_telegram_message(TELEGRAM_GROUP_CHAT_ID, text, 'generic')
>>>>>>> c9250c3eb6d71f4741b6d731fc82723239c95f88

def send_telegram_error_notification(error_message: str) -> None:
    """Send AI error notifications to the INF group chat."""
    _send_telegram_message(
        TELEGRAM_GROUP_CHAT_ID,
        f"⚠️ AI Processing Error\n\n{error_message}",
        'AI error',
        reply_markup={
            "inline_keyboard": [[
                {"text": "✅ Fix", "callback_data": "accept"},
                {"text": "❌ Stop", "callback_data": "reject"},
            ]]
        },
    )

def execute_vscode_output(command):
    """Triggers the specific action inside VS Code based on your phone's text."""
    print(f"⚙️ VS Code is executing local task for: '{command}'")
    
    # Example Output Actions
    if command.lower() == "accept":
        send_generic_msg("Testing")
        
    elif command.lower() == "reject":
        send_generic_msg("Welp you rejected it so i guess it works")
        
    else:
        send_generic_msg(f"⚠️ *VS Code:* Unknown command '{command}'. Script closed anyway.")

def wait_for_single_reply(last_update_id):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.warning("TELEGRAM_BOT_TOKEN not set; skipping error notification")
        return

    url_req = f"https://api.telegram.org/bot{token}/getUpdates"

<<<<<<< HEAD
    payload = {
        "chat_id": inf_group_id,
        "text": f"⚠️ AI Processing Error\n\n{error_message}",
        #"parse_mode": "Markdown",
        "reply_markup": {
            "inline_keyboard": [
                [
                    {"text": "✅ Fix", "callback_data": "accept"},
                    {"text": "❌ Stop", "callback_data": "reject"}
                ]
            ]
        }
    }
    try:
        results = requests.get(url_req, json=payload, timeout=10)
        logger.info("Telegram error notification sent: %s", results.json())
    except Exception as exc:
        logger.error("Failed to send Telegram error notification: %s", exc)
=======
    print("📱 Telegram listener ACTIVE. Waiting for button press...")
>>>>>>> c9250c3eb6d71f4741b6d731fc82723239c95f88

    while True:

        payload = {
            "offset": last_update_id + 1,
            "timeout": 10,
            "allowed_updates": ["callback_query"]
        }

        try:
            results = requests.get(
                url_req,
                params=payload,
                timeout=15
            ).json()

            if results.get("ok") and results.get("result"):

                for update in results["result"]:

                    last_update_id = max(
                        last_update_id,
                        update["update_id"]
                    )

                    callback = update.get("callback_query")

                    if not callback:
                        continue

                    # Verify sender
                    if str(callback["message"]["chat"]["id"]) != str(inf_group_id):
                        continue

                    # Get button selection
                    user_choice = callback.get("data")

                    # Acknowledge button press
                    requests.post(
                        f"https://api.telegram.org/bot{token}/editMessageReplyMarkup",
                        json={
                            "chat_id": callback["message"]["chat"]["id"],
                            "message_id": callback["message"]["message_id"],
                            "reply_markup": {
                                "inline_keyboard": []
                            }
                        }
                    )

                    if user_choice:
                        return user_choice, last_update_id

        except requests.RequestException as e:
            print(f"Error checking Telegram: {e}")

        except ValueError as e:
            print(f"Invalid Telegram response: {e}")

        time.sleep(1)


def init_data_layer() -> None:
    """Load all saved records and mark analyses that were interrupted by a restart as failed."""
    counts = data_manager.init_storage()
    print(f"💾 Loaded saved records: {counts}")

    # Single worker process: anything still 'processing' at startup can never finish
    for stale in data_manager.list_analyses(status='processing'):
        data_manager.fail_analysis(stale['id'])


init_data_layer()


def login_required(view):
    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        user = get_session_user()
        if user is None:
            return jsonify({'error': 'Please sign in to continue.'}), 401
        g.user_id = user['id']
        g.user_role = user['role']
        return view(*args, **kwargs)
    return wrapper


def admin_required(view):
    """Signed in as an admin. Everyone else gets 403, so admin data is protected by the
    server and not by which page the browser happens to load."""
    @functools.wraps(view)
    @login_required
    def wrapper(*args, **kwargs):
        if not current_user_is_admin():
            return jsonify({'error': 'This area is for administrators only.'}), 403
        return view(*args, **kwargs)
    return wrapper


def get_current_user_id() -> str:
    """Id of the signed-in user. Only valid inside routes decorated with @login_required."""
    return g.user_id


def current_user_is_admin() -> bool:
    """Only valid inside routes decorated with @login_required."""
    return g.user_role == 'admin'


# Authentication

MIN_PASSWORD_LENGTH = 8
USERNAME_RULES_MESSAGE = "Username must be 3-30 characters using letters, numbers, '.', '_' or '-'."
LOGIN_FAILED_MESSAGE = 'Incorrect username or password.'


def seed_admin_user() -> None:
    """Create the admin account named by ADMIN_USERNAME / ADMIN_PASSWORD in .env, if it does
    not exist yet. Registration only ever creates customers, so this is how an admin is made."""
    username = (os.getenv('ADMIN_USERNAME') or '').strip().lower()
    password = os.getenv('ADMIN_PASSWORD') or ''
    if not username and not password:
        return
    if not username or len(password) < MIN_PASSWORD_LENGTH:
        logger.warning("Admin account not created: set both ADMIN_USERNAME and an ADMIN_PASSWORD "
                       "of at least %d characters in .env", MIN_PASSWORD_LENGTH)
        return

    existing = data_manager.find_user_by_username(username)
    if existing is not None:
        if existing['role'] != 'admin':
            # Never change an existing account's role or password from here
            logger.warning("ADMIN_USERNAME '%s' already belongs to a %s account; it was left unchanged",
                           username, existing['role'])
        return

    try:
        data_manager.create_user(username, generate_password_hash(password), role='admin')
    except ValueError as e:
        logger.warning("Admin account not created: %s", e)
        return
    logger.info("Created admin account '%s'", username)


seed_admin_user()


def public_user(user: Dict) -> Dict:
    return {'id': user['id'], 'username': user['username'], 'role': user['role']}


def start_session(user: Dict) -> None:
    # Clear first so a session id set before login cannot be reused afterwards
    session.clear()
    session.permanent = True
    session['user_id'] = user['id']


def get_session_user() -> Optional[Dict]:
    user_id = session.get('user_id')
    if not user_id:
        return None
    user = data_manager.get_user(user_id)
    if user is None:
        session.clear()
    return user


def read_json_fields(*names: str) -> Dict[str, str]:
    payload = request.get_json(silent=True) or {}
    return {name: str(payload.get(name) or '') for name in names}


@app.route('/api/auth/register', methods=['POST'])
def register():
    fields = read_json_fields('username', 'password')
    username = fields['username'].strip().lower()
    password = fields['password']

    if not data_manager.USERNAME_PATTERN.match(username):
        return jsonify({'error': USERNAME_RULES_MESSAGE}), 400
    if len(password) < MIN_PASSWORD_LENGTH:
        return jsonify({'error': f'Password must be at least {MIN_PASSWORD_LENGTH} characters.'}), 400
    if data_manager.find_user_by_username(username):
        return jsonify({'error': 'That username is already taken. Please choose another.'}), 409

    try:
        user = data_manager.create_user(username, generate_password_hash(password))
    except ValueError as e:
        return jsonify({'error': str(e)}), 400

    start_session(user)
    return jsonify({'user': public_user(user)}), 201


@app.route('/api/auth/login', methods=['POST'])
def login():
    fields = read_json_fields('username', 'password')
    username = fields['username']
    password = fields['password']

    if not username.strip() or not password:
        return jsonify({'error': 'Please enter your username and password.'}), 400

    user = data_manager.find_user_by_username(username)
    if user is None or not check_password_hash(user['password_hash'], password):
        return jsonify({'error': LOGIN_FAILED_MESSAGE}), 401

    start_session(user)
    return jsonify({'user': public_user(user)})


@app.route('/api/auth/me', methods=['GET'])
def current_user():
    """Who is signed in; 401 if nobody is."""
    user = get_session_user()
    if user is None:
        return jsonify({'error': 'Not signed in'}), 401
    return jsonify({'user': public_user(user)})


@app.route('/api/auth/logout', methods=['POST'])
def logout():
    """End the session. POST (not GET) so other sites cannot sign users out via a link."""
    session.clear()
    return jsonify({'message': 'Signed out'})

# Configuration
FIRECRAWL_API_KEY = os.getenv('FIRECRAWL_API_KEY')

MANUAL_BACKUP_MODEL = 'nvidia/nemotron-3-ultra-550b-a55b:free'


@dataclass
class FlaggedClause:
    """Represents a flagged contract clause."""
    clause_text: str
    clause_type: str
    risk_level: str
    issue_description: str
    workaround: str
    legal_references: List[Dict[str, str]]
    line_number: Optional[int] = None


<<<<<<< HEAD
def get_openrouter_client() -> OpenAI:
    """Create OpenRouter client."""
    if not OPENROUTER_API_KEY:
        raise ValueError("OPENROUTER_API_KEY not configured")
    return OpenAI(
        base_url=OPENROUTER_BASE_URL,
        api_key=OPENROUTER_API_KEY
    )


def _classify_ai_error(error: Exception) -> str:
    """Classify AI error into a user-friendly category for Telegram notifications."""
    error_str = str(error).lower()
    
    if 'context_length' in error_str or 'maximum context' in error_str or 'too long' in error_str:
        return 'Context length exceeded - document too large for AI processing'
    if 'rate_limit' in error_str or '429' in error_str or 'quota' in error_str:
        return 'Rate limit exceeded - too many requests'
    if '404' in error_str or 'not found' in error_str:
        return 'AI model unavailable'
    if 'timeout' in error_str or 'timed out' in error_str:
        return 'AI request timed out'
    if 'authentication' in error_str or '401' in error_str or '403' in error_str:
        return 'AI authentication failed'
    if 'invalid' in error_str or 'malformed' in error_str:
        return 'Invalid request to AI service'
    return f'AI processing error: {str(error)[:200]}'


def call_ai_with_failover(messages: List[Dict], max_retries: int = 2, recovery_state: Optional[Dict] = None
) -> str:
    """
    Call AI with automatic failover through available models.
    Skips unavailable models (404) immediately and tries next.
    Sends Telegram notification on critical errors.
    Returns response text or raises exception if all models fail.
    """
    client = get_openrouter_client()

    if recovery_state is None:
        recovery_state = {}

    if recovery_state.get("manual_model"):

        print("🔄 Using previously approved backup AI")

        return call_manual_fallback_ai(
            messages,
            user_input=recovery_state["manual_model"]
        )
    
    # Try primary model first, then fallbacks
    models_to_try = [PRIMARY_MODEL] + [m for m in FALLBACK_MODELS if m != PRIMARY_MODEL]
    
    last_error = None
    error_category = None
    for model in models_to_try:
        for attempt in range(max_retries):
            try:
                print(f"  Trying model: {model} (attempt {attempt + 1})")
                response = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=0.3,
                    max_tokens=4096
                )
                content = response.choices[0].message.content
                if content:
                    print(f"  ✓ Success with {model}")
                    return content
            except Exception as e:
                error_str = str(e)
                last_error = e
                error_category = _classify_ai_error(e)
                # Skip 404 (model unavailable) immediately - no retry
                if '404' in error_str or 'unavailable' in error_str.lower():
                    print(f"  ⏭ Model {model} unavailable (404), skipping...")
                    break  # Break inner retry loop, try next model
                print(f"  ✗ Failed with {model}: {error_str[:100]}")
                continue
    
    # Send Telegram notification for AI failures
    if not recovery_state.get("notification_sent"):
        recovery_state["notification_sent"] = True
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        if not token:
            raise RuntimeError("Telegram bot token not configured")
        url_req = f"https://api.telegram.org/bot{token}/getUpdates"
        try:
            initial_res = requests.get(url_req, params={"offset": -1},timeout=15).json()
        except (requests.RequestException, ValueError):
            initial_res = {}

        last_update_id = 0

        if initial_res.get("result"):
            last_update_id = max(
                item["update_id"]
                for item in initial_res["result"]
            )

        send_telegram_error_notification(error_category)

        t_user_input, last_update_id = wait_for_single_reply(last_update_id)

        if t_user_input == "accept":
            recovery_state["manual_model"] = MANUAL_BACKUP_MODEL
            send_generic_message("🔄 Attempting manual AI recovery...")
            try:
                response = call_manual_fallback_ai(messages, user_input=MANUAL_BACKUP_MODEL)
                send_generic_message("✅ Backup AI successfully recovered!")

                return response
            
            except Exception as e:
                send_generic_message(f"❌ Backup AI failed: {str(e)[:200]}")
                raise

        elif t_user_input == "reject":
            send_generic_message("🛑 Manual recovery cancelled.")
            raise RuntimeError("Manual AI recovery rejected by engineer")
    
    raise RuntimeError(
        f"All AI models are currently unavailable. Last error: {last_error}. "
        "Please try again later."
    )
=======
def call_ai_with_failover(messages: List[ChatCompletionMessageParam], max_retries: int = 2) -> str:
    """Report primary and terminal AI errors while retaining model failover."""
    try:
        return ai_service.call_ai_with_failover(
            messages, max_retries,
            on_primary_failure=lambda error: send_telegram_error_notification(str(error)),
        )
    except ai_service.AIServiceError as error:
        send_telegram_error_notification(str(error))
        raise
    except Exception as error:
        logger.exception("Unexpected AI service failure")
        send_telegram_error_notification(
            f'Unexpected AI service failure ({type(error).__name__})'
        )
        raise
>>>>>>> c9250c3eb6d71f4741b6d731fc82723239c95f88


def extract_text_from_document(file_path: str) -> str:
    """Extract text from uploaded document."""
    path = Path(file_path)
    suffix = path.suffix.lower()
    
    if suffix == '.pdf':
        if PyPDF2 is None:
            raise ImportError("PyPDF2 not installed")
        text = []
        with open(file_path, 'rb') as f:
            reader = PyPDF2.PdfReader(f)
            for page in reader.pages:
                page_text = page.extract_text()
                if page_text:
                    text.append(page_text)
        return '\n\n'.join(text)
    
    elif suffix == '.docx':
        if Document is None:
            raise ImportError("python-docx not installed")
        doc = Document(file_path)
        return '\n\n'.join([p.text for p in doc.paragraphs if p.text.strip()])
    
    elif suffix == '.txt':
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()
    
    else:
        raise ValueError(f"Unsupported format: {suffix}")


def detect_clauses_with_ai(contract_text: str, context: str = '',recovery_state: Optional[Dict] = None) -> List[Dict]:
    """Use AI to dynamically detect and categorize contract clauses."""
    
    system_prompt = """You are an expert contract lawyer and risk analyst. Analyze the contract text and identify ALL potentially problematic clauses.

For each clause found, provide:
1. clause_type: A descriptive name (e.g., "Unlimited Liability", "Automatic Renewal", "IP Assignment")
2. risk_level: HIGH, MEDIUM, or LOW
3. issue_description: Why this clause is problematic
4. workaround: Specific negotiation strategy or alternative language
5. clause_text: The exact text from the contract (quote it)
6. line_number: Approximate location if identifiable

Focus on clauses that create liability, restrict rights, impose unfair obligations, or lack mutuality.
Return ONLY valid JSON array of objects with these exact keys."""

    user_prompt = f"""Analyze this contract and flag all problematic clauses:

{'CONTEXT: ' + context if context else ''}

CONTRACT TEXT:
{contract_text[:15000]}

Return JSON array of flagged clauses."""

    response = call_ai_with_failover([
        {'role': 'system', 'content': system_prompt},
        {'role': 'user', 'content': user_prompt}
    ],
    recovery_state=recovery_state
    )
    
    try:
        return ai_service.parse_ai_response(response, 'clause detection')
    except ai_service.AIServiceError as error:
        send_telegram_error_notification(str(error))
        raise


def research_clause_with_firecrawl(clause: Dict) -> List[Dict[str, str]]:
    """Search for case law and precedents using Firecrawl."""
    if not FIRECRAWL_API_KEY:
        return []
    
    try:
        fc = FirecrawlApp(api_key=FIRECRAWL_API_KEY)
        
        query = f"contract law \"{clause['clause_type']}\" court case precedent ruling outcome"
        results = fc.search(query=query, params={'limit': 3})
        
        references = []
        if results and results.get('data'):
            for item in results['data'][:3]:
                references.append({
                    'title': item.get('title', 'Unknown Case'),
                    'url': item.get('url', ''),
                    'summary': item.get('description', '')[:300]
                })
        return references
    except Exception as e:
        logger.warning("Firecrawl research failed: %s", ai_service.classify_error(e))
        send_telegram_error_notification(
            f'Firecrawl research failed: {ai_service.classify_error(e)}'
        )
        return []


def generate_workarounds_with_ai(clauses: List[Dict], recovery_state: Optional[Dict] = None) -> List[Dict]:
    """Enhance clauses with detailed workarounds using AI."""
    
    system_prompt = """You are a contract negotiation expert. For each flagged clause, provide a detailed, actionable workaround.
Include specific alternative language, negotiation tactics, and reference any relevant legal principles.
Return ONLY valid JSON array matching the input structure but with enhanced 'workaround' fields."""

    user_prompt = f"""Enhance these flagged clauses with detailed workarounds:

{json.dumps(clauses, indent=2)}

Return JSON array with enhanced workaround fields."""

    try:
        response = call_ai_with_failover([
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_prompt}
        ],
        recovery_state=recovery_state
        )
        
    except Exception:
        logger.warning("Workaround enhancement failed; retaining original clauses")
        return clauses

    try:
        return ai_service.parse_ai_response(response, 'workaround enhancement')
    except ai_service.AIServiceError as error:
        logger.warning("%s", error)
        send_telegram_error_notification(str(error))
    
    return clauses


@app.route('/api/analyze', methods=['POST'])
@login_required
def analyze_contract():
    """Main analysis endpoint."""
    try:
        # Validate request
        if 'contract' not in request.files:
            return jsonify({'error': 'No contract file provided'}), 400
        
        file = request.files['contract']
        context = request.form.get('context', '').strip()
        
        if file.filename == '':
            return jsonify({'error': 'No file selected'}), 400
        
        # Save uploaded file temporarily
        filename = secure_filename(file.filename)
        with tempfile.NamedTemporaryFile(delete=False, suffix=Path(filename).suffix) as tmp:
            file.save(tmp.name)
            tmp_path = tmp.name
        
        try:
            # Step 1: Extract text
            print("📄 Extracting text from document...")
            try:
                contract_text = extract_text_from_document(tmp_path)
            except Exception as error:
                logger.exception("Document text extraction failed")
                send_telegram_error_notification(
                    f'Document text extraction failed ({type(error).__name__})'
                )
                raise

            if not contract_text.strip():
                return jsonify({'error': 'Could not extract text from document'}), 400

            return analyze_and_save(get_current_user_id(), filename, contract_text, context)

        finally:
            # Cleanup temp file
            os.unlink(tmp_path)

    except Exception as e:
        return analysis_error_response(e)


def run_analysis_pipeline(contract_text: str, context: str) -> List[Dict]:
    """AI clause detection, case-law research and workaround enhancement."""

    # Shared recovery state for this entire analysis
    recovery_state = {
        "notification_sent": False,
        "manual_model": None
    }

    # Step 2: AI clause detection
    print("🔍 Detecting clauses with AI...")
    clauses = detect_clauses_with_ai(contract_text, context, recovery_state=recovery_state)
    if not clauses:
        return []

    # Step 3: Research each clause with Firecrawl
    print("🔎 Researching case law...")
    for clause in clauses:
        clause['legal_references'] = research_clause_with_firecrawl(clause)

    # Step 4: Enhance workarounds
    print("💡 Generating detailed workarounds...")
    clauses = generate_workarounds_with_ai(clauses, recovery_state=recovery_state)

    # Ensure all required fields exist and risk levels are ones the system understands
    normalised = []
    for clause in clauses:
        if not isinstance(clause, dict):
            continue
        clause.setdefault('issue_description', 'Potential risk identified')
        clause.setdefault('workaround', 'Consult legal counsel')
        clause.setdefault('legal_references', [])
        clause.setdefault('line_number', None)
        risk_level = str(clause.get('risk_level') or '').strip().upper()
        clause['risk_level'] = risk_level if risk_level in VALID_RISK_LEVELS else 'MEDIUM'
        normalised.append(clause)
    return normalised


def analyze_and_save(owner_id: str, filename: str, contract_text: str, context: str):
    """Return the saved result for an identical contract + context, otherwise run the
    pipeline and persist it. Empty results are never reused, so a bad AI response
    does not get stuck in the cache."""
    contract_hash = data_manager.compute_contract_hash(contract_text, context)
    cached = data_manager.find_analysis_by_hash(owner_id, contract_hash)
    if cached and cached['clauses']:
        print(f"♻️ Returning saved analysis {cached['id']} (same contract and context)")
        return jsonify(analysis_response(cached, cached_result=True))

    analysis = data_manager.create_analysis(owner_id, filename, contract_text, context)
    try:
        clauses = run_analysis_pipeline(contract_text, context)
        saved = data_manager.complete_analysis(analysis['id'], clauses)
        send_telegram_notification(len(clauses), owner_id, clauses)
    except Exception:
        try:
            data_manager.fail_analysis(analysis['id'])
        except Exception as save_error:
            print(f"Could not mark analysis {analysis['id']} as failed: {save_error}")
        raise
    return jsonify(analysis_response(saved))


def analysis_error_response(error: Exception):
    """Log the full error server-side and send the user a generic message.
    Raw exception text can contain file paths or AI provider responses, so it is never returned."""
    reference = secrets.token_hex(4)
    if isinstance(error, RuntimeError):
        # AI failover exhausted
        logger.warning("AI service unavailable [ref %s]: %s", reference, error)
        return jsonify({'error': 'The AI service is currently unavailable. Please try again in a few minutes.',
                        'reference': reference}), 503
    logger.exception("Analysis failed [ref %s]", reference, exc_info=error)
    return jsonify({'error': 'Something went wrong while analysing the contract. '
                             f'Please try again. (Reference: {reference})',
                    'reference': reference}), 500


def analysis_response(analysis: Dict, cached_result: bool = False) -> Dict:
    """Full saved analysis in the shape the frontend renders."""
    clauses = [clause_view(clause) for clause in analysis['clauses']]

    response = {
        'id': analysis['id'],
        'filename': analysis['filename'],
        'context': analysis['context'],
        'status': analysis['status'],
        'counts': analysis['counts'],
        'clauses': clauses,
        'total_flagged': len(clauses),
        'timestamp': analysis['created_at'],
        'cached': cached_result,
    }
    if not clauses:
        response['message'] = ('This analysis failed before any clauses were saved.'
                               if analysis['status'] == 'failed'
                               else 'No problematic clauses detected')
    return response


def clause_view(clause: Dict) -> Dict:
    """One stored clause in the shape the frontend renders."""
    view = {key: value for key, value in clause.items() if key != 'references'}
    view['legal_references'] = data_manager.resolve_references(clause)
    view['line_number'] = clause['location']['raw']
    return view


def analysis_summary(analysis: Dict) -> Dict:
    """Lightweight history entry (no contract text or clauses)."""
    return {
        'id': analysis['id'],
        'filename': analysis['filename'],
        'context': analysis['context'],
        'status': analysis['status'],
        'counts': analysis['counts'],
        'total_flagged': sum(analysis['counts'].values()),
        'timestamp': analysis['created_at'],
    }


def get_owned_analysis(analysis_id: str) -> Optional[Dict]:
    """The analysis if it exists and belongs to the current user, else None."""
    analysis = data_manager.get_analysis(analysis_id)
    if analysis is None or analysis['owner_id'] != get_current_user_id():
        return None
    return analysis


def get_readable_analysis(analysis_id: str) -> Optional[Dict]:
    """The analysis if the current user may view it: their own, or any analysis for an admin."""
    if current_user_is_admin():
        return data_manager.get_analysis(analysis_id)
    return get_owned_analysis(analysis_id)


@app.route('/api/analyses', methods=['GET'])
@login_required
def list_saved_analyses():
    """History list, newest first. Optional filters: ?status=done|failed|processing&risk=high|medium|low"""
    try:
        analyses = data_manager.list_analyses(
            owner_id=get_current_user_id(),
            status=request.args.get('status') or None,
            risk_level=request.args.get('risk') or None,
        )
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    return jsonify({'analyses': [analysis_summary(a) for a in analyses]})


@app.route('/api/analyses/<analysis_id>', methods=['GET'])
@login_required
def get_saved_analysis(analysis_id):
    """Reopen a saved analysis without calling the AI."""
    analysis = get_readable_analysis(analysis_id)
    if analysis is None:
        return jsonify({'error': 'Analysis not found'}), 404
    return jsonify(analysis_response(analysis))


@app.route('/api/analyses/<analysis_id>', methods=['DELETE'])
@login_required
def delete_saved_analysis(analysis_id):
    """Permanently delete one of the current user's analyses."""
    analysis = get_owned_analysis(analysis_id)
    if analysis is None:
        return jsonify({'error': 'Analysis not found'}), 404
    if analysis['status'] == 'processing':
        # The pipeline still needs this record to save its result
        return jsonify({'error': 'This analysis is still in progress. Try again once it has finished.'}), 409
    data_manager.delete_analysis(analysis_id)
    return jsonify({'deleted': analysis_id})


# Comparing two analyses

RISK_RANK = {'LOW': 1, 'MEDIUM': 2, 'HIGH': 3}
# Tried in this order: the same quoted text is a surer sign of the same clause than the same name
CLAUSE_MATCH_FIELDS = ('clause_text', 'clause_type')


def clause_match_key(clause: Dict, field: str) -> str:
    """Lowercase with single spaces, so case and line wrapping do not prevent a match."""
    return ' '.join(str(clause.get(field) or '').lower().split())


def match_clauses(clauses_a: List[Dict], clauses_b: List[Dict]) -> Dict[str, List]:
    """Pair up the clauses of two analyses without calling the AI, so the same two analyses
    always give the same comparison. Each clause is used at most once; when a key appears
    several times, clauses pair up in the order they were flagged."""
    pairs = []
    left_a = list(range(len(clauses_a)))
    left_b = list(range(len(clauses_b)))

    for field in CLAUSE_MATCH_FIELDS:
        waiting: Dict[str, List[int]] = {}
        for index_b in left_b:
            key = clause_match_key(clauses_b[index_b], field)
            if key:
                waiting.setdefault(key, []).append(index_b)

        unmatched_a = []
        matched_b = set()
        for index_a in left_a:
            candidates = waiting.get(clause_match_key(clauses_a[index_a], field))
            if candidates:
                index_b = candidates.pop(0)
                matched_b.add(index_b)
                pairs.append((index_a, index_b, field))
            else:
                unmatched_a.append(index_a)
        left_a = unmatched_a
        left_b = [index_b for index_b in left_b if index_b not in matched_b]

    pairs.sort()
    return {'pairs': pairs, 'only_a': left_a, 'only_b': left_b}


def risk_change(clause_a: Dict, clause_b: Dict) -> str:
    """How the risk in B compares with A: 'higher', 'lower' or 'same'."""
    difference = RISK_RANK.get(clause_b['risk_level'], 0) - RISK_RANK.get(clause_a['risk_level'], 0)
    if difference == 0:
        return 'same'
    return 'higher' if difference > 0 else 'lower'


def comparison_response(analysis_a: Dict, analysis_b: Dict) -> Dict:
    clauses_a = analysis_a['clauses']
    clauses_b = analysis_b['clauses']
    matched = match_clauses(clauses_a, clauses_b)

    usernames = {}
    sides = {}
    for side, analysis in (('a', analysis_a), ('b', analysis_b)):
        owner_id = analysis['owner_id']
        if owner_id not in usernames:
            owner = data_manager.get_user(owner_id)
            usernames[owner_id] = owner['username'] if owner else 'Deleted user'
        sides[side] = analysis_summary(analysis)
        sides[side]['owner_username'] = usernames[owner_id]

    return {
        'a': sides['a'],
        'b': sides['b'],
        'same_contract': analysis_a['contract_text'] == analysis_b['contract_text'],
        'in_both': [{
            'a': clause_view(clauses_a[index_a]),
            'b': clause_view(clauses_b[index_b]),
            'matched_on': 'text' if field == 'clause_text' else 'type',
            'risk_change': risk_change(clauses_a[index_a], clauses_b[index_b]),
        } for index_a, index_b, field in matched['pairs']],
        'only_a': [clause_view(clauses_a[index]) for index in matched['only_a']],
        'only_b': [clause_view(clauses_b[index]) for index in matched['only_b']],
    }


@app.route('/api/compare', methods=['GET'])
@login_required
def compare_analyses():
    """Compare two finished analyses: ?a=<id>&b=<id>. Customers can compare their own;
    an admin can compare any two, including analyses of different users."""
    id_a = request.args.get('a') or ''
    id_b = request.args.get('b') or ''
    if not id_a or not id_b:
        return jsonify({'error': 'Choose two analyses to compare.'}), 400
    if id_a == id_b:
        return jsonify({'error': 'Choose two different analyses to compare.'}), 400

    analysis_a = get_readable_analysis(id_a)
    analysis_b = get_readable_analysis(id_b)
    if analysis_a is None or analysis_b is None:
        return jsonify({'error': 'Analysis not found'}), 404
    if analysis_a['status'] != 'done' or analysis_b['status'] != 'done':
        return jsonify({'error': 'Only finished analyses can be compared.'}), 409
    if request.args.get('chronological') and not is_older(analysis_a, analysis_b):
        analysis_a, analysis_b = analysis_b, analysis_a
    return jsonify(comparison_response(analysis_a, analysis_b))


def is_older(first: Dict, second: Dict) -> bool:
    """Older analysis first; when created at the same moment, ids are handed out in order."""
    if first['created_at'] != second['created_at']:
        return first['created_at'] < second['created_at']
    return str(first['id']) <= str(second['id'])


def is_same_file(first: Dict, second: Dict) -> bool:
    """Same filename, ignoring case: a re-run with different context or a revised contract."""
    name = str(first.get('filename') or '').strip().lower()
    return name != '' and name == str(second.get('filename') or '').strip().lower()


@app.route('/api/analyses/<analysis_id>/compare-options', methods=['GET'])
@login_required
def compare_options(analysis_id):
    """The current user's other finished analyses that this one can be compared with,
    newest first, split into earlier analyses of the same file and the rest."""
    analysis = get_owned_analysis(analysis_id)
    if analysis is None:
        return jsonify({'error': 'Analysis not found'}), 404
    if analysis['status'] != 'done':
        return jsonify({'error': 'Only finished analyses can be compared.'}), 409

    others = [a for a in data_manager.list_analyses(owner_id=get_current_user_id(), status='done')
              if a['id'] != analysis['id']]
    return jsonify({
        'same_file': [analysis_summary(a) for a in others if is_same_file(a, analysis)],
        'others': [analysis_summary(a) for a in others if not is_same_file(a, analysis)],
    })


# Admin: read-only view of every user's analyses

@app.route('/api/admin/users', methods=['GET'])
@admin_required
def list_all_users():
    """Every account with how many analyses it has saved."""
    analysis_counts: Dict[str, int] = {}
    for analysis in data_manager.list_analyses():
        analysis_counts[analysis['owner_id']] = analysis_counts.get(analysis['owner_id'], 0) + 1

    users = []
    for user in data_manager.list_users():
        entry = public_user(user)
        entry['created_at'] = user['created_at']
        entry['analysis_count'] = analysis_counts.get(user['id'], 0)
        users.append(entry)
    return jsonify({'users': users})


@app.route('/api/admin/analyses', methods=['GET'])
@admin_required
def list_all_analyses():
    """Analyses of one user (?user=<id>) or of everyone, newest first, labelled with their owner."""
    owner_id = request.args.get('user') or None
    if owner_id is not None and data_manager.get_user(owner_id) is None:
        return jsonify({'error': 'User not found'}), 404

    usernames = {user['id']: user['username'] for user in data_manager.list_users()}
    analyses = []
    for analysis in data_manager.list_analyses(owner_id=owner_id):
        entry = analysis_summary(analysis)
        entry['owner_id'] = analysis['owner_id']
        entry['owner_username'] = usernames.get(analysis['owner_id'], 'Deleted user')
        analyses.append(entry)
    return jsonify({'analyses': analyses})


@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint with model availability test."""
    working_models = []
    failed_models = []
    
    primary, fallbacks = ai_service.configured_models()
    if os.getenv('OPENROUTER_API_KEY'):
        client = ai_service.get_openrouter_client()
        all_models = ([primary] if primary else []) + fallbacks
        
        for model in all_models[:4]:  # Test top 4 models only
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=[{'role': 'user', 'content': 'Hi'}],
                    max_tokens=5
                )
                if response.choices[0].message.content:
                    working_models.append(model)
            except Exception as e:
                failed_models.append({'model': model, 'error': str(e)[:80]})
    
    status = 'healthy' if working_models else 'degraded'
    return jsonify({
        'status': status,
        'openrouter_configured': bool(os.getenv('OPENROUTER_API_KEY')),
        'firecrawl_configured': bool(FIRECRAWL_API_KEY),
        'working_models': working_models,
        'failed_models': failed_models,
        'primary_model': primary,
        'timestamp': datetime.now().isoformat()
    })


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
