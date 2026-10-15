"""Uniform LLM interface with a free multi-provider fallback chain.

All LLM consumers (`llm_service`, `publisher_service`) route through
`complete_json()` / `generate_json()`, which call LiteLLM with an ordered chain
of models from different free providers (Groq -> Gemini by default). LiteLLM
handles per-model failover, retries, and cooldowns, so one provider being
rate-limited doesn't take the feature down.

The chain and per-provider keys come from Django settings so a single env edit
retunes the whole app without a redeploy. `generate_json()` returns `None` on any
failure (no key configured, all models exhausted, unparseable output) so callers
degrade gracefully without crashing the request/task.
"""

import json
import logging
import re
import time
from dataclasses import dataclass

import litellm
from django.conf import settings
from litellm import completion

logger = logging.getLogger(__name__)

# Let LiteLLM silently drop request params a given provider doesn't support
# (e.g. response_format on smaller free models) instead of raising — the prompt
# already asks for JSON and we parse defensively below.
litellm.drop_params = True
# No "Give Feedback / Get Help" banner on every provider error; we log our own.
litellm.suppress_debug_info = True

_warned_missing_key = False

# Provider prefix (as used in LLM_MODELS, e.g. "groq/openai/gpt-oss-120b") -> the
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


@dataclass
class LLMResult:
    """Outcome of one chain call, with enough metadata for analytics.

    `data` is the parsed JSON (None on failure). `model` is the model that
    answered, or the chain's primary if none did. `error_type` is set on
    failure; content is never stored here, only its length.
    """

    data: object = None
    model: str = ""
    provider: str = ""
    latency_ms: int = 0
    response_chars: int = 0
    error_type: str = ""
    error_message: str = ""


def _answering_model(response, fallback: str) -> tuple:
    """(provider, "provider/model") of the model that actually answered.

    LiteLLM reports the provider in `_hidden_params` and the bare model name on
    `response.model`; after a fallback both describe the fallback, not chain[0].
    """
    hidden = getattr(response, "_hidden_params", None)
    provider = hidden.get("custom_llm_provider") if isinstance(hidden, dict) else None
    if not isinstance(provider, str) or not provider:
        provider = fallback.split("/", 1)[0]
    model = getattr(response, "model", None)
    if not isinstance(model, str) or not model:
        model = fallback.split("/", 1)[-1]
    if not model.startswith(f"{provider}/"):
        model = f"{provider}/{model}"
    return provider, model


def complete_json(prompt: str, *, temperature: float = 0.9, max_tokens: int = 1024) -> LLMResult:
    """Run `prompt` through the LLM fallback chain. Never raises.

    On failure `data` is None and `error_type` says why: "NotConfigured" (no key
    for any chain model), "<ExceptionName>" (every model failed),
    "UnexpectedResponseShape", or "JSONDecodeError" (unparseable output).
    """
    global _warned_missing_key

    chain = _model_chain()
    primary = chain[0] if chain else ""
    result = LLMResult(model=primary, provider=primary.split("/", 1)[0])

    if not chain or not is_configured():
        if not _warned_missing_key:
            logger.warning("No LLM provider API key configured (or LLM_MODELS empty); LLM calls will be skipped.")
            _warned_missing_key = True
        result.error_type = "NotConfigured"
        return result

    start = time.monotonic()
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
        result.latency_ms = int((time.monotonic() - start) * 1000)
        logger.error(f"LLM completion failed across all fallbacks: {e}", exc_info=True)
        result.error_type = type(e).__name__
        result.error_message = str(e)
        return result
    result.latency_ms = int((time.monotonic() - start) * 1000)
    result.provider, result.model = _answering_model(response, primary)

    try:
        content = response.choices[0].message.content
    except (AttributeError, IndexError, KeyError, TypeError) as e:
        logger.error(f"Unexpected LLM response shape: {e}", exc_info=True)
        result.error_type = "UnexpectedResponseShape"
        result.error_message = str(e)
        return result

    result.response_chars = len(content or "")
    result.data = _extract_json(content)
    if result.data is None:
        logger.error(f"Could not parse JSON from LLM output: {content!r}")
        result.error_type = "JSONDecodeError"
        result.error_message = f"invalid JSON in response ({result.response_chars} chars)"
    return result


def generate_json(prompt: str, *, temperature: float = 0.9, max_tokens: int = 1024):
    """Parsed JSON from the chain, or None on any failure. Never raises."""
    return complete_json(prompt, temperature=temperature, max_tokens=max_tokens).data
