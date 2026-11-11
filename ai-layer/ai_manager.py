"""AI Manager: builds prompts, calls the AI with model failover, and validates what comes back."""

import json
import logging
import re
from typing import Dict, List, Optional, Sequence, Tuple

from openai import OpenAI

from notifications import ApprovalChannel

logger = logging.getLogger('contract-analyzer')

OPENROUTER_BASE_URL = 'https://openrouter.ai/api/v1'


class RecoverySession:
    """What one analysis has learned about AI trouble: whether the engineers were already
    alerted, and which backup model they approved. One instance is shared by every AI call
    of that analysis, so the team is alerted at most once per analysis."""

    def __init__(self) -> None:
        self._notified = False
        self._manual_model: Optional[str] = None

    @property
    def manual_model(self) -> Optional[str]:
        return self._manual_model

    def was_notified(self) -> bool:
        return self._notified

    def mark_notified(self) -> None:
        self._notified = True

    def approve(self, model: str) -> None:
        self._manual_model = model


class AIManager:
    PRIMARY_MODEL = 'nvidia/nemotron-3-ultra-550b-a55b:free'
    # Tried in order after the primary model. Check availability at https://openrouter.ai/models?q=:free
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
        self._models = [primary_model] + [model for model in fallback_models if model != primary_model]
        self._max_retries = max_retries

    @property
    def primary_model(self) -> str:
        return self._models[0]

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    # Pipeline stages

    def detect_clauses(self, contract_text: str, context: str,
                       recovery: RecoverySession) -> List[Dict]:
        """Ask the AI for problematic clauses; returns only well-formed clause objects."""
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
            parsed = self._parse_json_array(response)
        except json.JSONDecodeError:
            logger.warning("Failed to parse AI response as JSON")
            return []
        return self._validated_clauses(parsed)

    def improve_workarounds(self, clauses: List[Dict], recovery: RecoverySession) -> List[Dict]:
        """Ask the AI for fuller workarounds. If that fails for any reason the clauses are returned unchanged."""
        user_prompt = f"""Enhance these flagged clauses with detailed workarounds:

{json.dumps(clauses, indent=2)}

Return JSON array with enhanced workaround fields."""
        try:
            response = self._call_with_failover([
                {'role': 'system', 'content': self.WORKAROUND_SYSTEM_PROMPT},
                {'role': 'user', 'content': user_prompt},
            ], recovery)
            enhanced = self._parse_json_array(response)
            if enhanced is not None:
                return self._validated_clauses(enhanced) if enhanced else enhanced
        except Exception as error:
            logger.warning("Workaround enhancement failed: %s", error)
        return clauses

    def probe_models(self, limit: int = 4) -> Tuple[List[str], List[Dict[str, str]]]:
        """Ask the first few models a trivial question; returns (working models, failures)."""
        working: List[str] = []
        failed: List[Dict[str, str]] = []
        if not self.is_configured:
            return working, failed
        client = self._client()
        for model in self._models[:limit]:
            try:
                response = client.chat.completions.create(
                    model=model, messages=[{'role': 'user', 'content': 'Hi'}], max_tokens=5)
                if response.choices[0].message.content:
                    working.append(model)
            except Exception as error:
                failed.append({'model': model, 'error': str(error)[:80]})
        return working, failed

    # Calling the AI

    def _client(self) -> OpenAI:
        if not self._api_key:
            raise ValueError("OPENROUTER_API_KEY not configured")
        return OpenAI(base_url=OPENROUTER_BASE_URL, api_key=self._api_key)

    def _call_with_failover(self, messages: List[Dict], recovery: RecoverySession) -> str:
        """Try each model in turn (skipping unavailable ones). When every model fails, alert the
        engineers once and let them approve a backup model or stop the analysis."""
        client = self._client()

        if recovery.manual_model:
            logger.info("Using previously approved backup AI")
            return self._call_model(client, recovery.manual_model, messages, backup=True)

        last_error: Optional[Exception] = None
        error_category = None
        for model in self._models:
            for attempt in range(self._max_retries):
                try:
                    logger.info("Trying model: %s (attempt %s)", model, attempt + 1)
                    content = self._call_model(client, model, messages)
                    if content:
                        logger.info("Success with %s", model)
                        return content
                except Exception as error:
                    last_error = error
                    error_category = self._classify_error(error)
                    # An unavailable model (404) is skipped immediately, no retry
                    if '404' in str(error) or 'unavailable' in str(error).lower():
                        logger.warning("Model %s unavailable (404), skipping...", model)
                        break
                    logger.warning("Failed with %s: %s", model, str(error)[:100])

        if not recovery.was_notified():
            recovery.mark_notified()
            decision = self._approval.request_decision(error_category)
            if decision == 'accept':
                recovery.approve(self.MANUAL_BACKUP_MODEL)
                self._approval.announce("🔄 Attempting manual AI recovery...")
                try:
                    response = self._call_model(client, self.MANUAL_BACKUP_MODEL, messages, backup=True)
                    self._approval.announce("✅ Backup AI successfully recovered!")
                    return response
                except Exception as error:
                    self._approval.announce(f"❌ Backup AI failed: {str(error)[:200]}")
                    raise
            elif decision == 'reject':
                self._approval.announce("🛑 Manual recovery cancelled.")
                raise RuntimeError("Manual AI recovery rejected by engineer")

        raise RuntimeError(
            f"All AI models are currently unavailable. Last error: {last_error}. "
            "Please try again later."
        )

    @staticmethod
    def _call_model(client: OpenAI, model: str, messages: List[Dict], backup: bool = False) -> str:
        response = client.chat.completions.create(
            model=model, messages=messages, temperature=0.3, max_tokens=4096)
        content = response.choices[0].message.content
        if backup and not content:
            raise RuntimeError("Backup AI returned an empty response")
        return content

    @staticmethod
    def _classify_error(error: Exception) -> str:
        """A short, user-friendly category for the engineering alert."""
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

    # Reading the answer

    @staticmethod
    def _parse_json_array(response: str) -> Optional[List]:
        """The JSON array inside a reply (it may be wrapped in markdown), or None if there is none.
        Raises json.JSONDecodeError when something array-shaped is not valid JSON."""
        match = re.search(r'\[.*\]', response, re.DOTALL)
        if not match:
            return None
        parsed = json.loads(match.group())
        return parsed if isinstance(parsed, list) else None

    @staticmethod
    def _validated_clauses(items: Optional[List]) -> List[Dict]:
        """Keep objects that identify a clause by its type or its text; drop everything else."""
        clauses = []
        for item in items or []:
            if isinstance(item, dict) and (item.get('clause_type') or item.get('clause_text')):
                clauses.append(item)
            else:
                logger.warning("Dropped a malformed clause from the AI response")
        return clauses
