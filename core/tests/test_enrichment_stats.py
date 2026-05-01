"""Unit tests for enrichment-stat view helpers.

Covers _compute_enrichment_stats, _recalculate_enrichment_stats, and
_compute_enrichment_progress in core.views.
"""

from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from core.cache_utils import safe_cache_delete
from core.models import Author, Book, Genre, Publisher, UserBook
from core.views import (
    _compute_enrichment_progress,
    _compute_enrichment_stats,
    _recalculate_enrichment_stats,
)


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
)
class ComputeEnrichmentStatsTests(TestCase):
    """_compute_enrichment_stats: DB → derived stats, with brief result cache."""

    def setUp(self):
        self.user = User.objects.create_user(username="enrich_user", password="x")
        self.author = Author.objects.create(name="A. Writer", normalized_name="awriter")
        # Always invalidate the cache between tests
        safe_cache_delete(f"enrichment_stats_{self.user.id}")

    def _add_book(self, title, *, page_count=None, genres=None, mainstream_author=False, mainstream_publisher=False):
        author = self.author
        if mainstream_author:
            author = Author.objects.create(name=f"Mainstream {title}", normalized_name=f"main{title}", is_mainstream=True)
        publisher = None
        if mainstream_publisher:
            publisher = Publisher.objects.create(name=f"Big {title}", normalized_name=f"big{title}", is_mainstream=True)
        book = Book.objects.create(
            title=title,
            normalized_title=title.lower().replace(" ", ""),
            author=author,
            publisher=publisher,
            page_count=page_count,
        )
        for genre_name in genres or []:
            genre, _ = Genre.objects.get_or_create(name=genre_name)
            book.genres.add(genre)
        UserBook.objects.create(user=self.user, book=book)
        return book

    def test_empty_book_set_returns_none(self):
        """User with no UserBooks returns None — no stats to compute."""
        result = _compute_enrichment_stats(self.user)
        self.assertIsNone(result)

    def test_mixed_fiction_nonfiction_split(self):
        """Canonical fiction vs non-fiction split is computed correctly."""
        self._add_book("Novel A", genres=["fantasy"])  # fiction
        self._add_book("Novel B", genres=["thriller"])  # fiction
        self._add_book("Memoir", genres=["biography"])  # non-fiction
        self._add_book("History Book", genres=["history"])  # non-fiction
        self._add_book("Untagged")  # neither

        result = _compute_enrichment_stats(self.user)

        self.assertEqual(result["fiction_nonfiction_split"], {"fiction_count": 2, "nonfiction_count": 2})

    def test_mainstream_count_via_author(self):
        """Mainstream score counts books whose author is_mainstream."""
        self._add_book("M1", mainstream_author=True)
        self._add_book("M2", mainstream_author=True)
        self._add_book("Indie")

        result = _compute_enrichment_stats(self.user)

        # 2/3 mainstream → 67%
        self.assertEqual(result["mainstream_score_percent"], 67)

    def test_mainstream_count_via_publisher(self):
        """Mainstream score also counts books whose publisher is_mainstream (even when author isn't)."""
        self._add_book("Big1", mainstream_publisher=True)
        self._add_book("Big2", mainstream_publisher=True)
        self._add_book("Indie")
        self._add_book("Indie2")

        result = _compute_enrichment_stats(self.user)

        # 2/4 mainstream → 50%
        self.assertEqual(result["mainstream_score_percent"], 50)

    def test_page_stats(self):
        """total_pages_read is the sum, avg_book_length the rounded mean."""
        self._add_book("Short", page_count=100)
        self._add_book("Medium", page_count=300)
        self._add_book("Long", page_count=500)
        self._add_book("Unknown")  # no page count → excluded

        result = _compute_enrichment_stats(self.user)

        self.assertEqual(result["total_pages_read"], 900)
        self.assertEqual(result["avg_book_length"], 300)

    def test_cache_hit_within_ttl(self):
        """Second call within the cache window returns the cached dict without re-querying."""
        from core.cache_utils import safe_cache_set
        from core.models import Book as _Book

        # Pre-populate the cache with a sentinel value
        safe_cache_set(f"enrichment_stats_{self.user.id}", {"sentinel": True}, timeout=30)

        # If the cache is honored, _compute_enrichment_stats returns the sentinel
        # WITHOUT touching the DB. We verify that by asserting the QuerySet is
        # never evaluated even when no books exist (which would otherwise return None).
        with patch.object(_Book.objects, "filter") as mock_filter:
            result = _compute_enrichment_stats(self.user)
            mock_filter.assert_not_called()

        self.assertEqual(result, {"sentinel": True})


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
)
class ComputeEnrichmentProgressTests(TestCase):
    """_compute_enrichment_progress: aggregate query + finalize transition."""

    def setUp(self):
        self.user = User.objects.create_user(username="prog_user", password="x")
        self.profile = self.user.userprofile
        self.author = Author.objects.create(name="P Writer", normalized_name="pwriter")
        safe_cache_delete(f"enrichment_stats_{self.user.id}")

    def _add_book(self, title, *, attempted=False, has_genre=False, page_count=None):
        from django.utils import timezone

        book = Book.objects.create(
            title=title,
            normalized_title=title.lower().replace(" ", ""),
            author=self.author,
            page_count=page_count,
            google_books_last_checked=timezone.now() if attempted else None,
        )
        if has_genre:
            genre, _ = Genre.objects.get_or_create(name="fantasy")
            book.genres.add(genre)
        UserBook.objects.create(user=self.user, book=book)

    def test_no_books_returns_none(self):
        """User with no UserBooks returns None."""
        result = _compute_enrichment_progress(self.user, self.profile, {})
        self.assertIsNone(result)

    def test_pending_when_attempted_below_total(self):
        """Returns pending=True with progress fields while books remain unattempted."""
        self._add_book("A", attempted=True)
        self._add_book("B", attempted=False)

        dna_data = {}
        result = _compute_enrichment_progress(self.user, self.profile, dna_data)

        self.assertTrue(result["pending"])
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["percent"], 50)

    def test_finalize_flips_exactly_once(self):
        """All books attempted → finalize block runs once; second call no-ops the save."""
        self._add_book("A", attempted=True, has_genre=True, page_count=300)
        self._add_book("B", attempted=True, has_genre=True, page_count=300)
        self.profile.dna_data = {"user_stats": {}}
        self.profile.save()

        with patch.object(type(self.profile), "save", wraps=self.profile.save) as mock_save:
            # First call: enrichment_finalized not set yet — should save
            result1 = _compute_enrichment_progress(self.user, self.profile, self.profile.dna_data)
            first_save_count = mock_save.call_count

            # Second call: enrichment_finalized already True — must NOT save again
            self.profile.refresh_from_db()
            result2 = _compute_enrichment_progress(self.user, self.profile, self.profile.dna_data)

        self.assertFalse(result1["pending"])
        self.assertFalse(result2["pending"])
        self.assertGreaterEqual(first_save_count, 1)
        # Save called at least once for the finalize transition; the second
        # call should NOT have triggered any additional save.
        self.assertEqual(mock_save.call_count, first_save_count)

        self.profile.refresh_from_db()
        self.assertTrue(self.profile.dna_data.get("enrichment_finalized"))


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
)
class RecalculateEnrichmentStatsTests(TestCase):
    """_recalculate_enrichment_stats: applies cached stats to dna_data in place."""

    def setUp(self):
        self.user = User.objects.create_user(username="recalc_user", password="x")
        self.author = Author.objects.create(name="R Writer", normalized_name="rwriter")
        safe_cache_delete(f"enrichment_stats_{self.user.id}")

    def test_no_books_leaves_dna_untouched(self):
        """When _compute_enrichment_stats returns None, dna_data is not mutated."""
        dna_data = {"top_genres": [["sci-fi", 5]], "user_stats": {"total_pages_read": 999}}
        snapshot = {**dna_data, "user_stats": dict(dna_data["user_stats"])}

        _recalculate_enrichment_stats(self.user, dna_data)

        self.assertEqual(dna_data, snapshot)

    def test_applies_cached_stats(self):
        """Stats from the DB land on dna_data."""
        book = Book.objects.create(
            title="Recalc",
            normalized_title="recalc",
            author=self.author,
            page_count=200,
        )
        genre, _ = Genre.objects.get_or_create(name="fantasy")
        book.genres.add(genre)
        UserBook.objects.create(user=self.user, book=book)

        dna_data = {}
        _recalculate_enrichment_stats(self.user, dna_data)

        self.assertEqual(dna_data["user_stats"]["total_pages_read"], 200)
        self.assertEqual(dna_data["user_stats"]["avg_book_length"], 200)
        self.assertEqual(dna_data["unique_genres_count"], 1)
        self.assertEqual(dna_data["mainstream_score_percent"], 0)
