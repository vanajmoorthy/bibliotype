import os


def posthog_settings(request):
    """Expose PostHog configuration to templates for client-side tracking."""
    return {
        "POSTHOG_API_KEY": os.environ.get("POSTHOG_API_KEY", ""),
    }
