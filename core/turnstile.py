import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


def verify_turnstile_token(token, remote_ip=None):
    """Verify a Cloudflare Turnstile token.

    Returns True if the token is valid, False otherwise.
    If TURNSTILE_SECRET_KEY is not configured, returns True (dev mode).
    """
    secret_key = getattr(settings, "TURNSTILE_SECRET_KEY", "")

    if not secret_key:
        return True

    if not token:
        return False

    payload = {
        "secret": secret_key,
        "response": token,
    }
    if remote_ip:
        payload["remoteip"] = remote_ip

    try:
        response = requests.post(TURNSTILE_VERIFY_URL, data=payload, timeout=10)
        result = response.json()
        return result.get("success", False)
    except Exception as e:
        logger.error(f"Turnstile verification failed: {e}", exc_info=True)
        return False
