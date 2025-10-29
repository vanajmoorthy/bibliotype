import os
import posthog
from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self):
        posthog.api_key = os.environ.get("POSTHOG_API_KEY", "")
        posthog.host = "https://eu.i.posthog.com"
