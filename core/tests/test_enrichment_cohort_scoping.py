"""Tests for cohort-scoped enrichment completion (PR B).

`_compute_enrichment_progress` must scope its total/attempted counts to the
`enrichment_cohort_ids` persisted at generation. Otherwise a re-upload that adds
a few new books reads as "complete" immediately, because the user's previously
enriched books already carry google_books_last_checked.

Legacy profiles (generated before the key existed) fall back to counting all of
the user's books.
"""

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone

from core.cache_utils import safe_cache_delete
from core.models import Author, Book, Genre, UserBook
from core.views._helpers import _compute_enrichment_progress


def _make_user(username):
    return User.objects.create_user(username=username, password="x", email=f"{username}@example.com")


def _make_author(name="Test Author"):
    normalized = name.lower().replace(" ", "").replace(".", "")
    return Author.objects.get_or_create(name=name, defaults={"normalized_name": normalized})[0]


def _add_book(user, title, *, genres=None, page_count=200, publish_year=2000, google_books_checked=False):
    author = _make_author()
    normalized_title = title.lower().replace(" ", "")
    book, _ = Book.objects.get_or_create(
        normalized_title=normalized_title,
        author=author,
        defaults={
            "title": title,
            "page_count": page_count,
            "publish_year": publish_year,
            "google_books_last_checked": timezone.now() if google_books_checked else None,
        },
    )
    for genre_name in genres or []:
        genre, _ = Genre.objects.get_or_create(name=genre_name)
        book.genres.add(genre)
    UserBook.objects.get_or_create(user=user, book=book)
    return book


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
class CohortScopingTests(TestCase):
    def setUp(self):
        self.user = _make_user("cohort_user")
        self.profile = self.user.userprofile
        safe_cache_delete(f"enrichment_stats_{self.user.id}_rt")
        safe_cache_delete(f"enrichment_stats_{self.user.id}_nort")

    def _base_dna(self, cohort_ids=None):
        dna = {"user_stats": {}}
        if cohort_ids is not None:
            dna["enrichment_cohort_ids"] = cohort_ids
        return dna

    def test_pending_when_cohort_books_unattempted_despite_old_attempted_books(self):
        """10 old attempted books + 3 new unattempted; cohort = the 3 new → pending."""
        for i in range(10):
            _add_book(self.user, f"Old {i}", genres=["fantasy"], google_books_checked=True)
        new_books = [_add_book(self.user, f"New {i}", genres=["mystery"]) for i in range(3)]

        dna = self._base_dna(cohort_ids=[b.id for b in new_books])
        result = _compute_enrichment_progress(self.user, self.profile, dna)

        self.assertTrue(result["pending"])
        self.assertEqual(result["total"], 3)  # scoped to cohort, not 13
        self.assertEqual(result["percent"], 0)  # none of the 3 attempted yet
        # Finalize must NOT have fired while the cohort is unattempted.
        self.assertFalse(dna.get("enrichment_finalized"))

    def test_percent_reflects_partial_cohort_progress(self):
        """As cohort books get attempted, percent tracks N/cohort, not N/all."""
        for i in range(10):
            _add_book(self.user, f"Old {i}", genres=["fantasy"], google_books_checked=True)
        new_books = [_add_book(self.user, f"New {i}", genres=["mystery"]) for i in range(3)]
        # Attempt one of the three cohort books.
        Book.objects.filter(pk=new_books[0].pk).update(google_books_last_checked=timezone.now())

        dna = self._base_dna(cohort_ids=[b.id for b in new_books])
        result = _compute_enrichment_progress(self.user, self.profile, dna)

        self.assertTrue(result["pending"])
        self.assertEqual(result["total"], 3)
        self.assertEqual(result["percent"], 33)  # 1/3

    def test_finalizes_when_all_cohort_books_attempted(self):
        """Once every cohort book is attempted, enrichment finalizes (not pending)."""
        for i in range(10):
            _add_book(self.user, f"Old {i}", genres=["fantasy"], google_books_checked=True)
        new_books = [_add_book(self.user, f"New {i}", genres=["mystery"], google_books_checked=True)
                     for i in range(3)]

        dna = self._base_dna(cohort_ids=[b.id for b in new_books])
        self.profile.dna_data = dna
        self.profile.save()

        result = _compute_enrichment_progress(self.user, self.profile, self.profile.dna_data)

        self.assertFalse(result["pending"])
        self.assertEqual(result["total"], 3)

    def test_legacy_profile_without_cohort_key_counts_all_books(self):
        """No enrichment_cohort_ids → fall back to counting all of the user's books."""
        for i in range(10):
            _add_book(self.user, f"Old {i}", genres=["fantasy"], google_books_checked=True)
        for i in range(3):
            _add_book(self.user, f"New {i}", genres=["mystery"])  # unattempted

        dna = self._base_dna(cohort_ids=None)  # legacy: key absent
        result = _compute_enrichment_progress(self.user, self.profile, dna)

        self.assertTrue(result["pending"])
        self.assertEqual(result["total"], 13)  # all books, no scoping
        self.assertEqual(result["percent"], round(10 / 13 * 100))

    def test_empty_cohort_list_falls_back_to_all_books(self):
        """An empty (falsy) cohort list is treated as legacy — count all books."""
        for i in range(5):
            _add_book(self.user, f"Old {i}", genres=["fantasy"], google_books_checked=True)

        dna = self._base_dna(cohort_ids=[])
        result = _compute_enrichment_progress(self.user, self.profile, dna)

        # All 5 attempted → finalized; the empty list must not scope to 0 books
        # (which would leave total=0 → None).
        self.assertIsNotNone(result)
        self.assertFalse(result["pending"])
        self.assertEqual(result["total"], 5)
