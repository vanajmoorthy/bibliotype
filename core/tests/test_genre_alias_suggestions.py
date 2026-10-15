"""The weekly LLM task stores genre suggestions for unmapped subjects, for review only."""

from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from core.models import UnmappedSubject
from core.tasks import suggest_genre_aliases_task


class SuggestGenreAliasesTaskTests(TestCase):
    def setUp(self):
        UnmappedSubject.objects.create(name="elder wand", seen_count=9, example_title="Book One")
        UnmappedSubject.objects.create(name="dementors", seen_count=5, example_title="Book Two")
        UnmappedSubject.objects.create(name="made up", seen_count=3)

    @patch("core.services._llm.generate_json")
    def test_stores_valid_answers_and_drops_off_list_ones(self, mock_llm):
        mock_llm.return_value = {"elder wand": "Fantasy", "dementors": "ignore", "made up": "wizard stuff"}
        self.assertEqual(suggest_genre_aliases_task(), 2)
        self.assertEqual(UnmappedSubject.objects.get(name="elder wand").suggested_genre, "fantasy")
        self.assertEqual(UnmappedSubject.objects.get(name="dementors").suggested_genre, "ignore")
        self.assertIsNone(UnmappedSubject.objects.get(name="made up").suggested_at)

    @patch("core.services._llm.generate_json")
    def test_skips_already_suggested_and_survives_llm_failure(self, mock_llm):
        UnmappedSubject.objects.filter(name="elder wand").update(suggested_genre="fantasy", suggested_at=timezone.now())
        mock_llm.return_value = None
        self.assertEqual(suggest_genre_aliases_task(), 0)
        self.assertNotIn("elder wand", mock_llm.call_args.args[0])

    def test_analyze_genres_shows_suggestions(self):
        UnmappedSubject.objects.filter(name="elder wand").update(suggested_genre="fantasy", suggested_at=timezone.now())
        out = StringIO()
        call_command("analyze_genres", stdout=out)
        self.assertIn("'elder wand'  (e.g. 'Book One')  -> fantasy", out.getvalue())
        self.assertIn("fantasy: ['elder wand']", out.getvalue())
