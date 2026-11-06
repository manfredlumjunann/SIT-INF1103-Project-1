"""Shared OpenRouter configuration and error reporting."""

import logging
import json
import os
import re
from typing import Callable, Dict, List, Optional

from openai import OpenAI
from openai.types.chat import ChatCompletionMessageParam

logger = logging.getLogger(__name__)

OPENROUTER_BASE_URL = 'https://openrouter.ai/api/v1'

# Models are tried in this order by both the AI and logic layers.
PRIMARY_MODEL = 'nvidia/nemotron-3-ultra-550b-a55b:free'
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


class AIServiceError(RuntimeError):
    """A safe, actionable error that can be reported by the calling layer."""


def classify_error(error: Exception) -> str:
    """Classify provider failures without exposing provider response contents."""
    text = str(error).lower()
    status = getattr(error, 'status_code', None)
    if 'not a valid model id' in text or 'invalid model id' in text:
        category = 'Invalid model ID'
    elif 'context_length' in text or 'maximum context' in text or 'too long' in text:
        category = 'Context length exceeded'
    elif status in (401, 403) or 'authentication' in text or '401' in text or '403' in text:
        category = 'Authentication or permission failed'
    elif status == 402 or 'insufficient credits' in text:
        category = 'Insufficient provider credits'
    elif status == 429 or 'rate_limit' in text or '429' in text or 'quota' in text:
        category = 'Rate limit or quota exceeded'
    elif isinstance(status, int) and status >= 500:
        category = 'Provider service error'
    elif status == 404 or '404' in text or 'not found' in text or 'unavailable' in text:
        category = 'Model unavailable'
    elif 'timeout' in text or 'timed out' in text or 'timeout' in type(error).__name__.lower():
        category = 'Request timed out'
    elif 'connection' in type(error).__name__.lower():
        category = 'Connection to provider failed'
    elif status == 400 or 'invalid' in text or 'malformed' in text:
        category = 'Invalid request or response'
    elif isinstance(status, int):
        category = 'Provider error'
    else:
        return f'Provider error ({type(error).__name__})'
    return f'{category} (HTTP {status})' if isinstance(status, int) else category


def get_openrouter_client() -> OpenAI:
    api_key = os.getenv('OPENROUTER_API_KEY')
    if not api_key:
        raise AIServiceError('OpenRouter API key is not configured')
    return OpenAI(base_url=OPENROUTER_BASE_URL, api_key=api_key)


def parse_ai_response(response: str, stage: str) -> List[Dict]:
    match = re.search(r'\[.*\]', response, re.DOTALL)
    if not match:
        raise AIServiceError(f'AI returned no JSON array during {stage}')
    try:
        parsed = json.loads(match.group())
    except json.JSONDecodeError as error:
        raise AIServiceError(f'AI returned malformed JSON during {stage}') from error
    if not isinstance(parsed, list) or not all(isinstance(item, dict) for item in parsed):
        raise AIServiceError(f'AI returned an invalid JSON array during {stage}')
    return parsed


def configured_models() -> tuple:
    """Return a configured primary and unique, nonempty fallback models."""
    primary = globals().get('PRIMARY_MODEL')
    primary = primary.strip() if isinstance(primary, str) else ''
    configured_fallbacks = globals().get('FALLBACK_MODELS')
    if configured_fallbacks is None:
        configured_fallbacks = []
    if not isinstance(configured_fallbacks, (list, tuple)):
        raise AIServiceError('Fallback model configuration must be a list')
    fallbacks = []
    for model in configured_fallbacks:
        if not isinstance(model, str):
            raise AIServiceError('Fallback model configuration contains a non-string model')
        model = model.strip()
        if model and model != primary and model not in fallbacks:
            fallbacks.append(model)
    return primary, fallbacks


def call_ai_with_failover(
    messages: List[ChatCompletionMessageParam],
    max_retries: int = 2,
    on_primary_failure: Optional[Callable[[AIServiceError], None]] = None,
) -> str:
    """Try configured models and report a failed primary before attempting fallbacks."""
    primary, fallbacks = configured_models()
    if not primary:
        warning = AIServiceError('Primary model is not configured')
        if on_primary_failure:
            on_primary_failure(warning)
        else:
            logger.warning('%s', warning)

    models = ([primary] if primary else []) + fallbacks
    if not models:
        raise AIServiceError('No primary or fallback AI models are configured')

    client = get_openrouter_client()
    failures = []
    for model in models:
        category = 'Empty AI response'
        for attempt in range(max_retries):
            try:
                response = client.chat.completions.create(
                    model=model, messages=messages, temperature=0.3, max_tokens=4096
                )
                content = response.choices[0].message.content
                if content:
                    return content
                category = 'Empty AI response'
            except Exception as error:
                category = classify_error(error)
                if category.startswith(('Model unavailable', 'Invalid model ID')):
                    break
            logger.warning('AI model %s failed (attempt %s): %s', model, attempt + 1, category)

        failures.append(f'{model}: {category}')
        if model == primary:
            warning = AIServiceError(f'Primary model {model} failed: {category}')
            if on_primary_failure:
                on_primary_failure(warning)
            else:
                logger.warning('%s', warning)

    raise AIServiceError('All AI models failed. ' + '; '.join(failures))
