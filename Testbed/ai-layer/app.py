"""
Contract Clause Analyzer - Logic Layer
Flask API backend with Hermes AI (OpenRouter) and Firecrawl integration.
Handles clause detection, risk assessment, case law research, and workaround generation.
"""

import os
import re
import json
import asyncio
import tempfile
from pathlib import Path
from typing import List, Dict, Optional, Any
from dataclasses import dataclass, asdict
from datetime import datetime

from flask import Flask, request, jsonify
from werkzeug.utils import secure_filename
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

load_dotenv()

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max upload

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
    
    system_prompt = """You are an elite forensic contract lawyer, commercial risk analyst, and expert in identifying legal loopholes. Your primary objective is to ruthlessly scrutinize contracts to uncover hidden traps, predatory clauses, asymmetric obligations, and subtle loopholes that could expose a party to varying degrees of legal, financial, or operational severity.

Analyze the contract exhaustively. Do not just look at the surface-level meaning of the text; look for what is deliberately omitted, vaguely defined, or cross-referenced in a way that creates a loophole.

Whenever you encounter highly unusual clauses, questionable enforceability, or unfamiliar regulatory references, search the web to verify current market standards, recent legal precedents, or specific statutory limitations before finalizing your risk assessment. 

Look specifically for the following loopholes and risks:
- Asymmetric or hidden liability caps (e.g., capped for them, unlimited for us)
- "Sneaky" financial traps (e.g., hidden fee escalations, predatory auto-renewals, disproportionate penalties)
- Vague phrasing that creates massive loopholes (e.g., "sole discretion," "reasonable efforts," "customary")
- Overreaching IP grabs or broad data usage/monetization rights
- Unilateral rights to amend terms, terminate the agreement, or change scope without consent
- One-sided indemnities, non-competes, or non-solicitation restrictions
- Impossible or highly restrictive termination notice periods
- Disadvantageous jurisdictional, governing law, or forced arbitration clauses
- Interactions between seemingly harmless clauses that, when combined, create a severe risk

For every distinct loophole or risk, evaluate its severity and return the findings using the following scale:
- HIGH: A critical loophole or severe risk causing substantial financial, IP, or legal damage. Deal-breaker if unmitigated.
- MEDIUM: An unbalanced clause or moderate loophole that creates operational friction or unfair disadvantages. Requires strong negotiation.
- LOW: A minor ambiguity, standard boilerplate friction, or slight deviation from market norms. Worth noting for cleanup.

Important:
- Be exhaustive but do not create duplicate findings. 
- One clause may produce multiple distinct loopholes or risks.
- Do not invent risks unsupported by the contract text.
- Quote the exact contract language.
- Use your web search capabilities to validate if a legally questionable provision is actually enforceable in standard commercial law.

Return ONLY a valid JSON array with these exact keys:
1. "clause_type": The category of the clause (e.g., "Liability", "Termination", "IP Rights").
2. "risk_level": "HIGH", "MEDIUM", or "LOW".
3. "issue_description": A detailed explanation of the specific loophole, trap, or risk, and exactly how the wording creates it.
4. "workaround": A specific, actionable negotiation strategy, redline suggestion, or safer alternative wording.
5. "clause_text": The exact supporting text from the contract.
6. "line_number": The approximate source line number, or null if unavailable."""
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
def analyze_contract():
    """Main analysis endpoint."""
    try:
        # Validate request
        if 'contract' not in request.files:
            return jsonify({'error': 'No contract file provided'}), 400
        
        file = request.files['contract']
        context = request.form.get('context', '')
        
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
            
            # Step 2: AI clause detection
            print("🔍 Detecting clauses with AI...")
            clauses = detect_clauses_with_ai(contract_text, context)
            
            if not clauses:
                return jsonify({
                    'clauses': [],
                    'message': 'No problematic clauses detected',
                    'timestamp': datetime.now().isoformat()
                })
            
            # Step 3: Research each clause with Firecrawl
            print("🔎 Researching case law...")
            for clause in clauses:
                clause['legal_references'] = research_clause_with_firecrawl(clause)
            
            # Step 4: Enhance workarounds
            print("💡 Generating detailed workarounds...")
            clauses = generate_workarounds_with_ai(clauses)
            
            # Ensure all required fields exist
            for clause in clauses:
                clause.setdefault('risk_level', 'MEDIUM')
                clause.setdefault('issue_description', 'Potential risk identified')
                clause.setdefault('workaround', 'Consult legal counsel')
                clause.setdefault('legal_references', [])
                clause.setdefault('line_number', None)
            
            return jsonify({
                'clauses': clauses,
                'total_flagged': len(clauses),
                'timestamp': datetime.now().isoformat()
            })
            
        finally:
            # Cleanup temp file
            os.unlink(tmp_path)
            
    except RuntimeError as e:
        # AI failover exhausted
        return jsonify({'error': str(e)}), 503
    except Exception as e:
        print(f"Analysis error: {e}")
        return jsonify({'error': f'Analysis failed: {str(e)}'}), 500


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
