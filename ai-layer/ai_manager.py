# Talks to the AI: builds the prompts, fails over between models and checks the answers.

import json
import logging
import re
from typing import Dict, List, Optional, Sequence, Tuple

from openai import OpenAI

from approval_channel import ApprovalChannel
from recovery_session import RecoverySession

logger = logging.getLogger('contract-analyzer')

OPENROUTER_BASE_URL = 'https://openrouter.ai/api/v1'


class AIManager:
    PRIMARY_MODEL = 'nvidia/nemotron-3-ultra-550b-a55b:free'
    # Tried in order after the primary. Check what is still free at https://openrouter.ai/models?q=:free
    FALLBACK_MODELS = (
        'nvidia/nemotron-3-ultra-550b-a55b:free',
        'google/gemma-pro-1.5-2b-it:free',
        'mistralai/mistral-7b-instruct-v0.3:free',
        'qwen/qwen-2.5-7b-instruct:free',
        'google/gemma-2-9b-it:free',
        'microsoft/phi-3-mini-128k-instruct:free',
        'meta-llama/llama-3.2-3b-instruct:free',
        'nousresearch/hermes-3-llama-3.1-8b:free',
    )
    MANUAL_BACKUP_MODEL = 'nvidia/nemotron-3-ultra-550b-a55b:free'
    CONTRACT_CHARACTER_LIMIT = 15000

    DETECTION_SYSTEM_PROMPT = """You are an expert contract lawyer and risk analyst. Analyze the contract text and identify ALL potentially problematic clauses.

For each clause found, provide:
1. clause_type: A descriptive name (e.g., "Unlimited Liability", "Automatic Renewal", "IP Assignment")
2. risk_level: HIGH, MEDIUM, or LOW
3. issue_description: Why this clause is problematic
4. workaround: Specific negotiation strategy or alternative language
5. clause_text: The exact text from the contract (quote it)
6. line_number: Approximate location if identifiable

Focus on clauses that create liability, restrict rights, impose unfair obligations, or lack mutuality.
Return ONLY valid JSON array of objects with these exact keys."""

    WORKAROUND_SYSTEM_PROMPT = """You are a contract negotiation expert. For each flagged clause, provide a detailed, actionable workaround.
Include specific alternative language, negotiation tactics, and reference any relevant legal principles.
Return ONLY valid JSON array matching the input structure but with enhanced 'workaround' fields."""

    def __init__(self, api_key: Optional[str], approval: ApprovalChannel,
                 primary_model: str = PRIMARY_MODEL,
                 fallback_models: Sequence[str] = FALLBACK_MODELS,
                 max_retries: int = 2) -> None:
        self._api_key = api_key
        self._approval = approval
        self._models = [primary_model] + [m for m in fallback_models if m != primary_model]
        self._max_retries = max_retries

    @property
    def primary_model(self) -> str:
        return self._models[0]

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    def detect_clauses(self, contract_text: str, context: str,
                       recovery: RecoverySession) -> List[Dict]:
        context_line = 'CONTEXT: ' + context if context else ''
        user_prompt = f"""Analyze this contract and flag all problematic clauses:

{context_line}

CONTRACT TEXT:
{contract_text[:self.CONTRACT_CHARACTER_LIMIT]}

Return JSON array of flagged clauses."""
        response = self._call_with_failover([
            {'role': 'system', 'content': self.DETECTION_SYSTEM_PROMPT},
            {'role': 'user', 'content': user_prompt},
        ], recovery)
        try:
            return self._valid_clauses(self._parse_json_array(response))
        except json.JSONDecodeError:
            logger.warning("Failed to parse AI response as JSON")
            return []

    # Ask for fuller workarounds. This step is optional, so on any failure the clauses come back unchanged.
    def improve_workarounds(self, clauses: List[Dict], recovery: RecoverySession) -> List[Dict]:
        user_prompt = f"""Enhance these flagged clauses with detailed workarounds:

{json.dumps(clauses, indent=2)}

Return JSON array with enhanced workaround fields."""
        try:
            response = self._call_with_failover([
                {'role': 'system', 'content': self.WORKAROUND_SYSTEM_PROMPT},
                {'role': 'user', 'content': user_prompt},
            ], recovery)
            improved = self._parse_json_array(response)
            if improved is not None:
                return self._valid_clauses(improved)
        except Exception as error:
            logger.warning("Workaround enhancement failed: %s", error)
        return clauses

    # Ask the first few models a trivial question. Returns (working models, failures).
    def probe_models(self, limit: int = 4) -> Tuple[List[str], List[Dict[str, str]]]:
        working, failed = [], []
        if not self.is_configured:
            return working, failed
        client = self._client()
        for model in self._models[:limit]:
            try:
                reply = client.chat.completions.create(
                    model=model, messages=[{'role': 'user', 'content': 'Hi'}], max_tokens=5)
                if reply.choices[0].message.content:
                    working.append(model)
            except Exception as error:
                failed.append({'model': model, 'error': str(error)[:80]})
        return working, failed

    def _client(self) -> OpenAI:
        if not self._api_key:
            raise ValueError("OPENROUTER_API_KEY not configured")
        return OpenAI(base_url=OPENROUTER_BASE_URL, api_key=self._api_key)

    def _call_with_failover(self, messages: List[Dict], recovery: RecoverySession) -> str:
        client = self._client()

        if recovery.manual_model:
            logger.info("Using previously approved backup AI")
            return self._ask(client, recovery.manual_model, messages, backup=True)

        last_error: Optional[Exception] = None
        error_category = None
        for model in self._models:
            for attempt in range(self._max_retries):
                try:
                    logger.info("Trying model: %s (attempt %s)", model, attempt + 1)
                    content = self._ask(client, model, messages)
                    if content:
                        logger.info("Success with %s", model)
                        return content
                except Exception as error:
                    last_error = error
                    error_category = self._classify_error(error)
                    if '404' in str(error) or 'unavailable' in str(error).lower():
                        logger.warning("Model %s unavailable (404), skipping...", model)
                        break
                    logger.warning("Failed with %s: %s", model, str(error)[:100])

        if not recovery.was_notified():
            recovery.mark_notified()
            backup_response = self._ask_engineers(client, messages, recovery, error_category)
            if backup_response is not None:
                return backup_response

        raise RuntimeError(
            f"All AI models are currently unavailable. Last error: {last_error}. "
            "Please try again later."
        )

    # Every model failed: let the engineers choose Fix (try the backup model) or Stop.
    # Returns the backup model's answer, or None if they pressed neither button.
    def _ask_engineers(self, client: OpenAI, messages: List[Dict], recovery: RecoverySession,
                       error_category: Optional[str]) -> Optional[str]:
        decision = self._approval.request_decision(error_category)
        if decision == 'accept':
            recovery.manual_model = self.MANUAL_BACKUP_MODEL
            self._approval.announce("🔄 Attempting manual AI recovery...")
            try:
                response = self._ask(client, self.MANUAL_BACKUP_MODEL, messages, backup=True)
            except Exception as error:
                self._approval.announce(f"❌ Backup AI failed: {str(error)[:200]}")
                raise
            self._approval.announce("✅ Backup AI successfully recovered!")
            return response
        if decision == 'reject':
            self._approval.announce("🛑 Manual recovery cancelled.")
            raise RuntimeError("Manual AI recovery rejected by engineer")
        return None

    @staticmethod
    def _ask(client: OpenAI, model: str, messages: List[Dict], backup: bool = False) -> str:
        reply = client.chat.completions.create(
            model=model, messages=messages, temperature=0.3, max_tokens=4096)
        content = reply.choices[0].message.content
        if backup and not content:
            raise RuntimeError("Backup AI returned an empty response")
        return content

    # A short category for the engineering alert.
    @staticmethod
    def _classify_error(error: Exception) -> str:
        text = str(error).lower()
        if 'context_length' in text or 'maximum context' in text or 'too long' in text:
            return 'Context length exceeded - document too large for AI processing'
        if 'rate_limit' in text or '429' in text or 'quota' in text:
            return 'Rate limit exceeded - too many requests'
        if '404' in text or 'not found' in text:
            return 'AI model unavailable'
        if 'timeout' in text or 'timed out' in text:
            return 'AI request timed out'
        if 'authentication' in text or '401' in text or '403' in text:
            return 'AI authentication failed'
        if 'invalid' in text or 'malformed' in text:
            return 'Invalid request to AI service'
        return f'AI processing error: {str(error)[:200]}'

    # The JSON array inside a reply (it may be wrapped in markdown), or None if there is none.
    @staticmethod
    def _parse_json_array(response: str) -> Optional[List]:
        match = re.search(r'\[.*\]', response, re.DOTALL)
        if not match:
            return None
        parsed = json.loads(match.group())
        return parsed if isinstance(parsed, list) else None

    # Keep objects that have a clause type or clause text; drop everything else.
    @staticmethod
    def _valid_clauses(items: Optional[List]) -> List[Dict]:
        clauses = []
        for item in items or []:
            if isinstance(item, dict) and (item.get('clause_type') or item.get('clause_text')):
                clauses.append(item)
            else:
                logger.warning("Dropped a malformed clause from the AI response")
        return clauses
