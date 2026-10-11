"""The compose files pass an explicit env list to web and worker, so a setting
read from os.environ silently stays unset in the containers unless it is listed.
Guards the LLM chain vars (GROQ_API_KEY was missing in prod after #169)."""

from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

LLM_ENV_VARS = ["GEMINI_API_KEY", "GROQ_API_KEY", "CEREBRAS_API_KEY", "LLM_MODELS", "LLM_TIMEOUT_SECONDS", "LLM_NUM_RETRIES"]


class ComposeEnvPassthroughTests(SimpleTestCase):
    def test_llm_vars_reach_web_and_worker(self):
        for name in ("docker-compose.prod.yml", "docker-compose.local.yml"):
            path = Path(settings.BASE_DIR) / name
            if not path.exists():  # .dockerignore drops the local file from built images
                continue
            text = path.read_text()
            for var in LLM_ENV_VARS:
                # One entry each for the web and worker services
                self.assertEqual(text.count(f"- {var}="), 2, f"{var} not passed to web and worker in {name}")
