from unittest.mock import patch

from django.contrib.auth.models import User
from django.template.loader import render_to_string
from django.test import TestCase, override_settings

LONGEST = {"title": "Big Book", "author": "A", "page_count": 900, "cover_url": None, "initial": "B"}
SHORTEST = {"title": "Small Book", "author": "B", "page_count": 90, "cover_url": None, "initial": "S"}
SPLIT = {"fiction_count": 3, "nonfiction_count": 1, "defaulted_count": 0}
EMPTY_COPY = "Not enough page counts to compare yet"


class BookExtremesCardTests(TestCase):
    def test_renders_extremes_when_present(self):
        html = render_to_string(
            "core/partials/dna/book_extremes_card.html",
            {"dna": {"longest_book": LONGEST, "shortest_book": SHORTEST, "page_difference": 810}},
        )
        self.assertIn("Big Book", html)
        self.assertIn("810 page difference", html)
        self.assertNotIn(EMPTY_COPY, html)

    def test_renders_empty_state_without_extremes(self):
        html = render_to_string("core/partials/dna/book_extremes_card.html", {"dna": {"longest_book": None}})
        self.assertIn('id="book-extremes-card"', html)
        self.assertIn(EMPTY_COPY, html)
        self.assertNotIn("page difference", html)


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
class BookExtremesTileInPageTests(TestCase):
    """A split with no extremes used to leave the right half of the grid row blank."""

    def setUp(self):
        self.user = User.objects.create_user(username="fewpages", email="few@test.com", password="test123")
        self.user.userprofile.dna_data = {
            "reader_type": "Fantasy Fanatic",
            "top_genres": [["fantasy", 3]],
            "top_authors": [["A", 2]],
            "user_stats": {"total_books_read": 4, "total_pages_read": 0},
            "fiction_nonfiction_split": SPLIT,
            "longest_book": None,
            "shortest_book": None,
            "page_difference": None,
        }
        self.user.userprofile.reader_type = "Fantasy Fanatic"
        self.user.userprofile.is_public = True
        self.user.userprofile.save()

    @patch("core.tasks.generate_recommendations_task")
    def test_dashboard_renders_empty_extremes_tile(self, _mock_recs):
        self.client.force_login(self.user)
        response = self.client.get("/dashboard/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="book-extremes-card"')
        self.assertContains(response, EMPTY_COPY)

    def test_public_profile_renders_empty_extremes_tile(self):
        response = self.client.get(f"/u/{self.user.username}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="book-extremes-card"')
        self.assertContains(response, EMPTY_COPY)
