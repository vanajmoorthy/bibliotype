"""Single Gemini client/configuration helper (US-037).

All Gemini consumers (`llm_service`, `publisher_service`, etc.) must go
through `client()` rather than calling `genai.configure` themselves. The
key + model id come from Django settings so a single env var swap (e.g.
`GEMINI_MODEL=gemini-2.0-flash-lite`) takes effect everywhere.

Returns `None` when `GEMINI_API_KEY` is empty so callers can degrade
gracefully without crashing the request/task.
"""

import logging

import google.generativeai as genai
from django.conf import settings

logger = logging.getLogger(__name__)

_configured = False
_warned_missing_key = False


def is_configured() -> bool:
    return bool(settings.GEMINI_API_KEY)


def client():
    """Return a configured `GenerativeModel`, or `None` if no API key is set."""
    global _configured, _warned_missing_key

    if not settings.GEMINI_API_KEY:
        if not _warned_missing_key:
            logger.warning("GEMINI_API_KEY not configured. Gemini calls will be skipped.")
            _warned_missing_key = True
        return None

    if not _configured:
        genai.configure(api_key=settings.GEMINI_API_KEY)
        _configured = True

    return genai.GenerativeModel(settings.GEMINI_MODEL)
