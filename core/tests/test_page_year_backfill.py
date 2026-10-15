"""Goodreads uploads fill blank pages/year from the shared Book table, like StoryGraph uploads."""

from unittest.mock import patch

from django.contrib.auth.models import User
from django.template.loader import render_to_string
from django.test import TestCase

from core.services.dna import calculate_full_dna

HEADER = (
    "Title,Author,Exclusive Shelf,Number of Pages,Date Read,My Rating,My Review,"
    "Original Publication Year,Average Rating,ISBN13,Binding\n"
)


def _run(csv_text, username):
    user = User.objects.create_user(username=username, password="x")
    with (
        patch("core.services.dna.generate_vibe_with_llm", return_value=["a", "b"]),
        patch("core.tasks.enrich_book_task.apply_async"),
        patch("core.services.book_enrichment_service.enrich_book_from_apis"),
    ):
        calculate_full_dna(csv_text, user=user)
    user.userprofile.refresh_from_db()
    return user.userprofile.dna_data


class GoodreadsPageYearBackfillTests(TestCase):
    def test_blank_csv_pages_and_year_come_from_known_books(self):
        _run(
            HEADER
            + '"Book One","Author One",read,300,2024/01/01,4,,1950,4.0,,Paperback\n'
            + '"Book Two","Author Two",read,100,2024/02/01,4,,1970,4.0,,Paperback\n',
            "seed_reader",
        )
        dna = _run(
            HEADER
            + '"Book One","Author One",read,,2024/03/01,5,,,4.0,,Paperback\n'
            + '"Book Two","Author Two",read,,2024/04/01,3,,,4.0,,Paperback\n',
            "blank_reader",
        )
        self.assertEqual(dna["user_stats"]["total_pages_read"], 400)
        self.assertEqual(dna["user_stats"]["avg_book_length"], 200)
        self.assertEqual(dna["user_stats"]["avg_publish_year"], 1960)

    def test_stats_row_shows_dash_when_no_pages_anywhere(self):
        html = render_to_string(
            "core/partials/dna/key_stats_row.html",
            {"dna": {"user_stats": {"total_books_read": 2, "total_pages_read": 0}, "average_rating_overall": 4.0}},
        )
        self.assertIn(">–</div>", html)
        self.assertIn("this.targetPages ? ", html)
