import os
import posthog
from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self):
        # Initialize PostHog (handled by analytics module)
        # Keep this for backward compatibility, but analytics module handles initialization
        posthog.api_key = os.environ.get("POSTHOG_API_KEY", "")
        posthog.host = "https://eu.i.posthog.com"
        
        # Initialize analytics module
        from .analytics.posthog_client import _initialize_posthog
        _initialize_posthog()
