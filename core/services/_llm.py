"""Uniform LLM interface with a free multi-provider fallback chain.

Replaces the single-provider `_gemini.client()` path. All LLM consumers
(`llm_service`, `publisher_service`) route through `generate_json()`, which calls
LiteLLM with an ordered chain of models from different free providers
(Groq -> Cerebras -> Gemini by default). LiteLLM handles per-model failover,
retries, and cooldowns, so one provider being rate-limited doesn't take the
feature down.

The chain and per-provider keys come from Django settings so a single env edit
retunes the whole app without a redeploy. `generate_json()` returns `None` on any
failure (no key configured, all models exhausted, unparseable output) so callers
degrade gracefully without crashing the request/task.
"""

import json
import logging
import re

import litellm
from django.conf import settings
from litellm import completion

logger = logging.getLogger(__name__)

# Let LiteLLM silently drop request params a given provider doesn't support
# (e.g. response_format on smaller free models) instead of raising — the prompt
# already asks for JSON and we parse defensively below.
litellm.drop_params = True

_warned_missing_key = False

# Provider prefix (as used in LLM_MODELS, e.g. "groq/llama-3.3-70b") -> the
# Django settings attribute holding that provider's API key. LiteLLM itself reads
# these from the environment by convention; we mirror them in settings only to
# answer is_configured() without importing os here.
_PROVIDER_KEY_ATTRS = {
    "groq": "GROQ_API_KEY",
    "cerebras": "CEREBRAS_API_KEY",
    "gemini": "GEMINI_API_KEY",
}


def _model_chain() -> list:
    """Ordered model list from settings.LLM_MODELS (primary first, rest fallbacks)."""
    return [m.strip() for m in settings.LLM_MODELS.split(",") if m.strip()]


def is_configured() -> bool:
    """True if at least one provider in the chain has an API key set."""
    for model in _model_chain():
        provider = model.split("/", 1)[0]
        key_attr = _PROVIDER_KEY_ATTRS.get(provider)
        if key_attr and getattr(settings, key_attr, ""):
            return True
    return False


def _extract_json(text: str):
    """Best-effort parse of a JSON object from raw model output.

    Handles markdown code fences and leading/trailing prose that weaker free
    models add despite the prompt. Returns a dict/list, or None if nothing
    parseable is found.
    """
    if not text:
        return None

    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```$", "", cleaned).strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Fallback: slice from the first "{" to the last "}".
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            return None
    return None


def generate_json(prompt: str, *, temperature: float = 0.9, max_tokens: int = 1024):
    """Run `prompt` through the LLM fallback chain and return parsed JSON.

    Returns a dict/list on success, or None on any failure (no key configured,
    every model exhausted, or output that can't be parsed as JSON). Never raises.
    """
    global _warned_missing_key

    if not is_configured():
        if not _warned_missing_key:
            logger.warning("No LLM provider API key configured; LLM calls will be skipped.")
            _warned_missing_key = True
        return None

    chain = _model_chain()
    if not chain:
        logger.error("LLM_MODELS is empty; cannot generate.")
        return None

    try:
        response = completion(
            model=chain[0],
            messages=[{"role": "user", "content": prompt}],
            fallbacks=chain[1:],
            num_retries=settings.LLM_NUM_RETRIES,
            timeout=settings.LLM_TIMEOUT_SECONDS,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
    except Exception as e:
        logger.error(f"LLM completion failed across all fallbacks: {e}", exc_info=True)
        return None

    try:
        content = response.choices[0].message.content
    except (AttributeError, IndexError, KeyError, TypeError) as e:
        logger.error(f"Unexpected LLM response shape: {e}", exc_info=True)
        return None

    data = _extract_json(content)
    if data is None:
        logger.error(f"Could not parse JSON from LLM output: {content!r}")
    return data
