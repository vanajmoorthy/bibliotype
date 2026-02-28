import os

from django.conf import settings


def posthog_settings(request):
    """Expose PostHog configuration to templates for client-side tracking."""
    return {
        "POSTHOG_API_KEY": os.environ.get("POSTHOG_API_KEY", ""),
    }


def turnstile_context(request):
    """Expose Turnstile site key to templates for CAPTCHA widget rendering."""
    return {
        "TURNSTILE_SITE_KEY": getattr(settings, "TURNSTILE_SITE_KEY", ""),
    }
