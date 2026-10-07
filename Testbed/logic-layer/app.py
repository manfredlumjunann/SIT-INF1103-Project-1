"""
Contract Clause Analyzer - Logic Layer
Flask API backend with Hermes AI (OpenRouter) and Firecrawl integration.
Handles clause detection, risk assessment, case law research, and workaround generation.
"""

import os
import re
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

import requests

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
from openai import OpenAI
from firecrawl import FirecrawlApp

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


def send_telegram_notification(total_flagged: int) -> None:
    """Send Telegram notification when flagged clauses exceed thresholds."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.warning("TELEGRAM_BOT_TOKEN not set; skipping notification")
        return

    don_id = "667740965"
    man_id = "1116849976"
    url_req = f"https://api.telegram.org/bot{token}/sendMessage"

    if total_flagged > 8:
        chat_id = man_id
    elif total_flagged > 4:
        chat_id = don_id
    else:
        return

    payload = {
        "chat_id": chat_id,
        "text": f"Clauses Found in recent scanned clause: clauses: {total_flagged}"
    }
    try:
        results = requests.get(url_req, params=payload, timeout=10)
        logger.info("Telegram notification sent: %s", results.json())
    except Exception as exc:
        logger.error("Failed to send Telegram notification: %s", exc)


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
        return view(*args, **kwargs)
    return wrapper


def get_current_user_id() -> str:
    """Id of the signed-in user. Only valid inside routes decorated with @login_required."""
    return g.user_id


# Authentication

MIN_PASSWORD_LENGTH = 8
USERNAME_RULES_MESSAGE = "Username must be 3-30 characters using letters, numbers, '.', '_' or '-'."
LOGIN_FAILED_MESSAGE = 'Incorrect username or password.'


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
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')
FIRECRAWL_API_KEY = os.getenv('FIRECRAWL_API_KEY')
OPENROUTER_BASE_URL = 'https://openrouter.ai/api/v1'

# Free/OpenRouter models to try in failover order
# Verify current availability at: https://openrouter.ai/models?q=:free
FALLBACK_MODELS = [
    'nvidia/nemotron-3-ultra-550b-a55b:free',
    'google/gemma-pro-1.5-2b-it:free',
    'mistralai/mistral-7b-instruct-v0.3:free',
    'qwen/qwen-2.5-7b-instruct:free',
    'google/gemma-2-9b-it:free',
    'microsoft/phi-3-mini-128k-instruct:free',
    'meta-llama/llama-3.2-3b-instruct:free',
    'nousresearch/hermes-3-llama-3.1-8b:free',
]

PRIMARY_MODEL = 'nvidia/nemotron-3-ultra-550b-a55b:free'


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


def get_openrouter_client() -> OpenAI:
    """Create OpenRouter client."""
    if not OPENROUTER_API_KEY:
        raise ValueError("OPENROUTER_API_KEY not configured")
    return OpenAI(
        base_url=OPENROUTER_BASE_URL,
        api_key=OPENROUTER_API_KEY
    )


def call_ai_with_failover(messages: List[Dict], max_retries: int = 2) -> str:
    """
    Call AI with automatic failover through available models.
    Skips unavailable models (404) immediately and tries next.
    Returns response text or raises exception if all models fail.
    """
    client = get_openrouter_client()
    
    # Try primary model first, then fallbacks
    models_to_try = [PRIMARY_MODEL] + [m for m in FALLBACK_MODELS if m != PRIMARY_MODEL]
    
    last_error = None
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
                # Skip 404 (model unavailable) immediately - no retry
                if '404' in error_str or 'unavailable' in error_str.lower():
                    print(f"  ⏭ Model {model} unavailable (404), skipping...")
                    break  # Break inner retry loop, try next model
                print(f"  ✗ Failed with {model}: {error_str[:100]}")
                continue
    
    raise RuntimeError(
        f"All AI models are currently unavailable. Last error: {last_error}. "
        "Please try again later."
    )


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


def detect_clauses_with_ai(contract_text: str, context: str = '') -> List[Dict]:
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
    ])
    
    # Parse JSON from response
    try:
        # Extract JSON if wrapped in markdown
        json_match = re.search(r'\[.*\]', response, re.DOTALL)
        if json_match:
            clauses = json.loads(json_match.group())
            return clauses if isinstance(clauses, list) else []
        return []
    except json.JSONDecodeError:
        print("Failed to parse AI response as JSON")
        return []


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
        print(f"Firecrawl search failed: {e}")
        return []


def generate_workarounds_with_ai(clauses: List[Dict]) -> List[Dict]:
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
        ])
        
        json_match = re.search(r'\[.*\]', response, re.DOTALL)
        if json_match:
            enhanced = json.loads(json_match.group())
            return enhanced if isinstance(enhanced, list) else clauses
    except Exception as e:
        print(f"Workaround enhancement failed: {e}")
    
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
            contract_text = extract_text_from_document(tmp_path)

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
    # Step 2: AI clause detection
    print("🔍 Detecting clauses with AI...")
    clauses = detect_clauses_with_ai(contract_text, context)
    if not clauses:
        return []

    # Step 3: Research each clause with Firecrawl
    print("🔎 Researching case law...")
    for clause in clauses:
        clause['legal_references'] = research_clause_with_firecrawl(clause)

    # Step 4: Enhance workarounds
    print("💡 Generating detailed workarounds...")
    clauses = generate_workarounds_with_ai(clauses)

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
        send_telegram_notification(len(clauses))
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
    clauses = []
    for clause in analysis['clauses']:
        view = {key: value for key, value in clause.items() if key != 'references'}
        view['legal_references'] = data_manager.resolve_references(clause)
        view['line_number'] = clause['location']['raw']
        clauses.append(view)

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
    analysis = get_owned_analysis(analysis_id)
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


@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint with model availability test."""
    working_models = []
    failed_models = []
    
    if OPENROUTER_API_KEY:
        client = get_openrouter_client()
        all_models = [PRIMARY_MODEL] + [m for m in FALLBACK_MODELS if m != PRIMARY_MODEL]
        
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
        'openrouter_configured': bool(OPENROUTER_API_KEY),
        'firecrawl_configured': bool(FIRECRAWL_API_KEY),
        'working_models': working_models,
        'failed_models': failed_models,
        'primary_model': PRIMARY_MODEL,
        'timestamp': datetime.now().isoformat()
    })


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
