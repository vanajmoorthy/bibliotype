"""Tests for llm_backfill_genres. The LLM chain is always mocked."""

import io
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from core.management.commands import llm_backfill_genres
from core.models import Author, Book, Genre

CMD = "core.management.commands.llm_backfill_genres"


def _run(*args):
    out = io.StringIO()
    call_command("llm_backfill_genres", *args, stdout=out)
    return out.getvalue()


@patch(f"{CMD}.time.sleep")
@patch(f"{CMD}._llm.is_configured", return_value=True)
class LlmBackfillGenresTests(TestCase):
    def setUp(self):
        self.jemisin = Author.objects.create(name="N. K. Jemisin")
        self.golding = Author.objects.create(name="William Golding")
        self.obscure = Author.objects.create(name="Some Farmer")
        self.fifth = Book.objects.create(title="The Fifth Season", author=self.jemisin, publish_year=2015)
        self.flies = Book.objects.create(title="Sineklerin Tanrısı", author=self.golding)
        self.cownomics = Book.objects.create(title="Cownomics", author=self.obscure)
        self.has_genres = Book.objects.create(title="Already Done", author=self.obscure)
        self.has_genres.genres.add(Genre.objects.create(name="history"))

    def _response(self):
        return {
            "results": [
                {"id": self.fifth.id, "genres": ["Fantasy", "science fiction", "afrofuturism", "Fiction"]},
                {"id": self.flies.id, "genres": ["classic fiction", "literary fiction"]},
                {"id": self.cownomics.id, "genres": "unknown"},
            ]
        }

    def _genres(self, book):
        return sorted(book.genres.values_list("name", flat=True))

    def test_only_books_without_genres_are_sent(self, _configured, _sleep):
        with patch(f"{CMD}._llm.generate_json", return_value=self._response()) as gen:
            _run()
        prompt = gen.call_args.args[0]
        self.assertIn("Cownomics", prompt)
        self.assertNotIn("Already Done", prompt)
        self.assertEqual(gen.call_args.kwargs["temperature"], 0)
        self.assertIn('"romance" means genre romance', prompt)

    def test_dry_run_writes_nothing_and_reports(self, _configured, _sleep):
        with patch(f"{CMD}._llm.generate_json", return_value=self._response()):
            out = _run()
        for book in (self.fifth, self.flies, self.cownomics):
            self.assertEqual(self._genres(book), [])
            book.refresh_from_db()
            self.assertFalse(book.genres_from_llm)
        self.assertIn("Books sent:               3", out)
        self.assertIn("Classified:               2", out)
        self.assertIn("Unknown:                  1", out)
        self.assertIn("Invalid genres dropped:   2", out)
        self.assertIn("-> fantasy, science fiction", out)
        self.assertIn("Dry run: nothing was written", out)

    def test_apply_drops_invalid_genres_and_flags_books(self, _configured, _sleep):
        with patch(f"{CMD}._llm.generate_json", return_value=self._response()):
            _run("--apply")
        self.assertEqual(self._genres(self.fifth), ["fantasy", "science fiction"])
        self.assertEqual(self._genres(self.flies), ["classic fiction", "literary fiction"])
        self.assertFalse(Genre.objects.filter(name__in=["afrofuturism", "fiction"]).exists())
        self.fifth.refresh_from_db()
        self.assertTrue(self.fifth.genres_from_llm)

    def test_unknown_writes_nothing(self, _configured, _sleep):
        with patch(f"{CMD}._llm.generate_json", return_value=self._response()):
            _run("--apply")
        self.assertEqual(self._genres(self.cownomics), [])
        self.cownomics.refresh_from_db()
        self.assertFalse(self.cownomics.genres_from_llm)

    def test_only_invalid_genres_writes_nothing(self, _configured, _sleep):
        response = {"results": [{"id": self.fifth.id, "genres": ["grimdark", "Fiction"]}]}
        with patch(f"{CMD}._llm.generate_json", return_value=response):
            out = _run("--apply")
        self.assertEqual(self._genres(self.fifth), [])
        self.assertIn("Only invalid genres:      1", out)

    def test_apply_skips_book_that_gained_genres_meanwhile(self, _configured, _sleep):
        def gain_genres_during_call(*args, **kwargs):
            self.fifth.genres.add(Genre.objects.create(name="romance"))
            return self._response()

        with patch(f"{CMD}._llm.generate_json", side_effect=gain_genres_during_call):
            out = _run("--apply")
        self.assertEqual(self._genres(self.fifth), ["romance"])
        self.fifth.refresh_from_db()
        self.assertFalse(self.fifth.genres_from_llm)
        self.assertEqual(self._genres(self.flies), ["classic fiction", "literary fiction"])
        self.assertIn("Skipped (gained genres):  1", out)

    def test_failed_batch_does_not_stop_the_run(self, _configured, _sleep):
        with (
            patch(f"{CMD}.BATCH_SIZE", 1),
            patch(f"{CMD}._llm.generate_json", side_effect=[None, RuntimeError("boom"), self._response()]),
        ):
            out = _run("--apply")
        # Batches go in id order: fifth (None), flies (raises), cownomics (answered)
        self.assertIn("Failed batches:           2", out)
        self.assertIn("Unknown:                  1", out)
        self.assertEqual(self._genres(self.fifth), [])

    def test_garbage_shapes_are_ignored(self, _configured, _sleep):
        other = Author.objects.create(name="Other")
        stranger = Book.objects.create(title="Not In Batch", author=other)
        response = {
            "results": [
                "nonsense",
                {"id": stranger.id + 1000, "genres": ["fantasy"]},
                {"id": str(self.fifth.id), "genres": ["fantasy"]},
                {"id": self.flies.id, "genres": {"bad": "shape"}},
                {"id": True, "genres": ["horror"]},
            ]
        }
        with patch(f"{CMD}._llm.generate_json", return_value=response):
            _run("--apply", "--limit", "2")
        self.assertEqual(self._genres(self.fifth), ["fantasy"])
        self.assertEqual(self._genres(self.flies), [])
        for bad in ([1, 2], "text", None):
            with patch(f"{CMD}._llm.generate_json", return_value=bad):
                _run("--limit", "1")

    def test_id_keyed_mapping_is_accepted(self, _configured, _sleep):
        response = {str(self.fifth.id): ["fantasy"], str(self.flies.id): ["unknown", "unknown"]}
        with patch(f"{CMD}._llm.generate_json", return_value=response):
            out = _run("--apply", "--limit", "2")
        self.assertEqual(self._genres(self.fifth), ["fantasy"])
        self.assertEqual(self._genres(self.flies), [])
        self.assertIn("Unknown:                  1", out)

    def test_after_id_and_limit_select_the_next_books(self, _configured, _sleep):
        with patch(f"{CMD}._llm.generate_json", return_value={"results": []}) as gen:
            out = _run("--after-id", str(self.fifth.id), "--limit", "1")
        prompt = gen.call_args.args[0]
        self.assertIn("Sineklerin Tanrısı", prompt)
        self.assertNotIn("The Fifth Season", prompt)
        self.assertIn(f"--after-id {self.flies.id}", out)

    def test_canonical_list_has_no_generic_fiction(self, _configured, _sleep):
        self.assertIn("fantasy", llm_backfill_genres.CANONICAL_GENRES)
        self.assertNotIn("fiction", llm_backfill_genres.CANONICAL_GENRES)


class EnrichmentClearsLlmFlagTests(TestCase):
    @patch("core.services.book_enrichment_service._fetch_ratings_and_categories_from_google_books")
    @patch("core.services.book_enrichment_service._fetch_from_open_library")
    def test_api_genres_clear_the_flag(self, mock_ol, mock_gb):
        from core.services.book_enrichment_service import enrich_book_from_apis

        author = Author.objects.create(name="John Gwynne")
        book = Book.objects.create(title="Malice", author=author, genres_from_llm=True)
        book.genres.add(Genre.objects.create(name="adventure"))
        mock_ol.return_value = ({"genres": ["fantasy"]}, 1)
        mock_gb.return_value = ({"categories": []}, 1)

        book, _, _ = enrich_book_from_apis(book, session=None)

        self.assertEqual(list(book.genres.values_list("name", flat=True)), ["fantasy"])
        self.assertFalse(book.genres_from_llm)
