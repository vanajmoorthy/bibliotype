"""Tests for the LiteLLM multi-provider fallback layer (`core/services/_llm.py`)
and the two adapters that route through it. All LiteLLM calls are mocked — no
network per the CLAUDE.md testing rules.
"""

from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from core.services import _llm
from core.services.llm_service import generate_vibe_with_llm
from core.services.publisher_service import research_publisher_identity


def _completion_response(content):
    """Minimal OpenAI-shaped object as LiteLLM's completion() returns."""
    message = MagicMock()
    message.content = content
    choice = MagicMock()
    choice.message = message
    response = MagicMock()
    response.choices = [choice]
    return response


@override_settings(
    LLM_MODELS="groq/llama-3.3-70b,cerebras/llama-3.3-70b,gemini/gemini-flash-lite-latest",
    GROQ_API_KEY="test-groq-key",
    CEREBRAS_API_KEY="",
    GEMINI_API_KEY="",
    LLM_NUM_RETRIES=1,
    LLM_TIMEOUT_SECONDS=20,
)
class GenerateJsonTests(TestCase):
    def test_no_key_short_circuits(self):
        with override_settings(GROQ_API_KEY="", CEREBRAS_API_KEY="", GEMINI_API_KEY=""):
            self.assertFalse(_llm.is_configured())
            with patch("core.services._llm.completion") as mock_completion:
                self.assertIsNone(_llm.generate_json("hi"))
                mock_completion.assert_not_called()

    def test_configured_when_any_chain_provider_has_key(self):
        self.assertTrue(_llm.is_configured())

    def test_happy_path_passes_chain_and_parses(self):
        resp = _completion_response('{"vibe_phrases": ["a wry sentence"]}')
        with patch("core.services._llm.completion", return_value=resp) as mock_completion:
            data = _llm.generate_json("prompt")
        self.assertEqual(data, {"vibe_phrases": ["a wry sentence"]})
        _, kwargs = mock_completion.call_args
        self.assertEqual(kwargs["model"], "groq/llama-3.3-70b")
        self.assertEqual(kwargs["fallbacks"], ["cerebras/llama-3.3-70b", "gemini/gemini-flash-lite-latest"])
        self.assertEqual(kwargs["num_retries"], 1)

    def test_fenced_json_is_recovered(self):
        resp = _completion_response('```json\n{"a": 1}\n```')
        with patch("core.services._llm.completion", return_value=resp):
            self.assertEqual(_llm.generate_json("p"), {"a": 1})

    def test_prose_wrapped_json_is_recovered(self):
        resp = _completion_response('Sure! Here you go: {"a": 1} — hope that helps')
        with patch("core.services._llm.completion", return_value=resp):
            self.assertEqual(_llm.generate_json("p"), {"a": 1})

    def test_unparseable_output_returns_none(self):
        resp = _completion_response("I cannot help with that.")
        with patch("core.services._llm.completion", return_value=resp):
            self.assertIsNone(_llm.generate_json("p"))

    def test_all_providers_failing_returns_none(self):
        # Simulates LiteLLM raising after every model in the chain 429s/errors.
        with patch("core.services._llm.completion", side_effect=Exception("all providers exhausted")):
            self.assertIsNone(_llm.generate_json("p"))

    def test_empty_model_chain_returns_none(self):
        with override_settings(LLM_MODELS=""):
            with patch("core.services._llm.completion") as mock_completion:
                self.assertIsNone(_llm.generate_json("p"))
                mock_completion.assert_not_called()


@override_settings(GROQ_API_KEY="test-groq-key", CEREBRAS_API_KEY="", GEMINI_API_KEY="")
class VibeAdapterTests(TestCase):
    def test_returns_first_phrase_only(self):
        with patch("core.services._llm.generate_json", return_value={"vibe_phrases": ["one", "two"]}):
            self.assertEqual(generate_vibe_with_llm({"reader_type": "x"}), ["one"])

    def test_none_on_failure(self):
        with patch("core.services._llm.generate_json", return_value=None):
            self.assertIsNone(generate_vibe_with_llm({"reader_type": "x"}))

    def test_none_on_unexpected_shape(self):
        with patch("core.services._llm.generate_json", return_value={"vibe_phrases": "not a list"}):
            self.assertIsNone(generate_vibe_with_llm({"reader_type": "x"}))


class PublisherAdapterTests(TestCase):
    def _wiki_session(self):
        session = MagicMock()
        wiki_resp = MagicMock()
        wiki_resp.status_code = 200
        wiki_resp.json.return_value = {
            "extract": "Viking Press is an American imprint of Penguin.",
            "title": "Viking Press",
        }
        session.get.return_value = wiki_resp
        return session

    @override_settings(GROQ_API_KEY="test-groq-key", CEREBRAS_API_KEY="", GEMINI_API_KEY="")
    def test_maps_llm_findings(self):
        llm_out = {
            "is_mainstream": True,
            "parent_company_name": "Penguin Random House",
            "reasoning": "Viking is an imprint of Penguin Random House.",
        }
        with patch("core.services._llm.generate_json", return_value=llm_out):
            findings = research_publisher_identity("Viking Press", self._wiki_session())
        self.assertTrue(findings["is_mainstream"])
        self.assertEqual(findings["parent_company_name"], "Penguin Random House")
        self.assertIsNone(findings["error"])

    @override_settings(GROQ_API_KEY="test-groq-key", CEREBRAS_API_KEY="", GEMINI_API_KEY="")
    def test_error_when_llm_returns_none(self):
        with patch("core.services._llm.generate_json", return_value=None):
            findings = research_publisher_identity("Viking Press", self._wiki_session())
        self.assertFalse(findings["is_mainstream"])
        self.assertIsNotNone(findings["error"])

    @override_settings(GROQ_API_KEY="", CEREBRAS_API_KEY="", GEMINI_API_KEY="")
    def test_no_key_sets_error_before_network(self):
        session = MagicMock()
        findings = research_publisher_identity("Viking Press", session)
        self.assertIn("No LLM provider", findings["error"])
        session.get.assert_not_called()
