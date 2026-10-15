from unittest.mock import patch

from django.test import TransactionTestCase

# Everything the DNA pipeline sends off-box: the LLM vibe, Open Library + Google Books
# enrichment, and the Open Library author lookup (runs when Celery is eager).
_PIPELINE_PATCHES = {
    "mock_vibe": ("core.services.dna.generate_vibe_with_llm", {"return_value": ["a vibe"]}),
    "mock_enrich": ("core.services.book_enrichment_service.enrich_book_from_apis", {}),
    "mock_author_status": (
        "core.tasks.enrichment.check_author_mainstream_status",
        {"return_value": {"is_mainstream": False, "reason": None, "error": None}},
    ),
}


class PipelineTestCase(TransactionTestCase):
    """Runs the DNA pipeline with every external API client patched out."""

    def setUp(self):
        super().setUp()
        for attr, (target, kwargs) in _PIPELINE_PATCHES.items():
            patcher = patch(target, **kwargs)
            setattr(self, attr, patcher.start())
            self.addCleanup(patcher.stop)
