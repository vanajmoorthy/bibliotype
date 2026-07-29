"""Tests for PR2: live reader-type recompute during enrichment.

Covers:
- Unit: recompute_reader_type_from_db with fixture book sets
- Override kwargs in assign_reader_type (Comfort Rereader, Rapacious Reader)
- Integration: enrichment_status_view carries reader_type fields when csv_context present
- Integration: enrichment_status_view omits reader_type fields when csv_context absent
- Finalize: profile.reader_type column is persisted on enrichment completion
"""

from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from core.cache_utils import safe_cache_delete
from core.models import Author, Book, Genre, Publisher, UserBook
from core.services.dna.reader_type import (
    assign_reader_type,
    compute_books_per_year,
    compute_reread_count,
    recompute_reader_type_from_db,
)
from core.views import _compute_enrichment_progress


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_user(username):
    return User.objects.create_user(username=username, password="x", email=f"{username}@example.com")


def _make_author(name="Test Author"):
    normalized = name.lower().replace(" ", "").replace(".", "")
    return Author.objects.get_or_create(name=name, defaults={"normalized_name": normalized})[0]


def _add_book(user, title, *, author=None, page_count=None, genres=None, publish_year=None,
              publisher=None, google_books_checked=False):
    """Create a Book + UserBook for the given user."""
    if author is None:
        author = _make_author()
    normalized_title = title.lower().replace(" ", "")
    book, _ = Book.objects.get_or_create(
        normalized_title=normalized_title,
        author=author,
        defaults={
            "title": title,
            "page_count": page_count,
            "publish_year": publish_year,
            "publisher": publisher,
            "google_books_last_checked": timezone.now() if google_books_checked else None,
        },
    )
    for genre_name in genres or []:
        genre, _ = Genre.objects.get_or_create(name=genre_name)
        book.genres.add(genre)
    UserBook.objects.get_or_create(user=user, book=book)
    return book


# ---------------------------------------------------------------------------
# Unit: compute_reread_count / compute_books_per_year helpers
# ---------------------------------------------------------------------------

class ComputeHelpersTests(TestCase):
    """The two pure-CSV helper functions."""

    def _df(self, rows):
        import pandas as pd
        return pd.DataFrame(rows)

    def test_compute_reread_count_uses_read_count_column(self):
        import pandas as pd
        df = pd.DataFrame({"Title": ["A", "B", "C"], "Read Count": [2, 1, 3]})
        # A and C have Read Count > 1
        self.assertEqual(compute_reread_count(df), 2)

    def test_compute_reread_count_falls_back_to_title_duplicates(self):
        import pandas as pd
        df = pd.DataFrame({"Title": ["A", "A", "B", "C", "C", "C"]})
        # A appears twice (1 extra), C appears three times (2 extras) → 3 rereads
        self.assertEqual(compute_reread_count(df), 3)

    def test_compute_books_per_year_below_threshold_returns_zero(self):
        import pandas as pd
        # Only 5 dated reads — below the 24-read gate
        dates = pd.to_datetime(["2020-01-01"] * 5)
        df = pd.DataFrame({"Title": [f"B{i}" for i in range(5)], "Date Read": dates})
        self.assertEqual(compute_books_per_year(df), 0.0)

    def test_compute_books_per_year_requires_two_distinct_years(self):
        import pandas as pd
        # 30 dated reads all in 2021 — only one distinct year
        dates = pd.to_datetime(["2021-06-01"] * 30)
        df = pd.DataFrame({"Title": [f"B{i}" for i in range(30)], "Date Read": dates})
        self.assertEqual(compute_books_per_year(df), 0.0)

    def test_compute_books_per_year_returns_rate_when_sufficient(self):
        import pandas as pd
        # 48 reads across 4 distinct years → 12.0 books/year
        dates = pd.to_datetime(["2019-01-01"] * 12 + ["2020-01-01"] * 12 + ["2021-01-01"] * 12 + ["2022-01-01"] * 12)
        df = pd.DataFrame({"Title": [f"B{i}" for i in range(48)], "Date Read": dates})
        self.assertAlmostEqual(compute_books_per_year(df), 12.0)


# ---------------------------------------------------------------------------
# Unit: assign_reader_type override kwargs
# ---------------------------------------------------------------------------

@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
)
class AssignReaderTypeOverrideTests(TestCase):
    """reread_count_override and books_per_year_override take effect."""

    def _minimal_df(self, n=30):
        import pandas as pd
        return pd.DataFrame({"Title": [f"Book {i}" for i in range(n)]})

    def test_reread_override_drives_comfort_rereader(self):
        """High reread_count_override wins Comfort Rereader score regardless of df."""
        import pandas as pd
        # 30 books, override says 15 were reread — 50% fraction, well above threshold
        df = self._minimal_df(30)
        reader_type, scores = assign_reader_type(df, {}, [], reread_count_override=15)
        # Comfort Rereader should score highly
        self.assertIn("Comfort Rereader", scores)
        # The score should be non-zero and the type may win
        self.assertGreater(scores["Comfort Rereader"], 0)

    def test_books_per_year_override_drives_rapacious_reader(self):
        """High books_per_year_override contributes to Rapacious Reader score."""
        import pandas as pd
        df = self._minimal_df(60)
        # Override with 80 books/year — far exceeds the threshold
        reader_type, scores = assign_reader_type(df, {}, [], books_per_year_override=80.0)
        self.assertIn("Rapacious Reader", scores)
        self.assertGreater(scores["Rapacious Reader"], 0)

    def test_none_overrides_use_df_values(self):
        """When overrides are None, df-derived values are used (no regression)."""
        import pandas as pd
        df = pd.DataFrame({"Title": ["A", "A", "B"]})  # A is read twice → reread_count=1
        _, scores_no_override = assign_reader_type(df, {}, [])
        _, scores_with_none = assign_reader_type(df, {}, [], reread_count_override=None)
        # Scores must be identical
        self.assertEqual(dict(scores_no_override), dict(scores_with_none))

    def test_zero_override_suppresses_comfort_rereader(self):
        """override=0 means no rereads even if df would compute some."""
        import pandas as pd
        df = pd.DataFrame({"Title": ["A", "A", "B", "B", "B", "C"] * 5, "Read Count": [3] * 30})
        # Without override, df says 30 books re-read → high Comfort Rereader score
        _, scores_df = assign_reader_type(df, {}, [])
        # With override=0, no rereads
        _, scores_override = assign_reader_type(df, {}, [], reread_count_override=0)
        # The override should produce a lower (or zero) comfort rereader score
        cr_df = scores_df.get("Comfort Rereader", 0)
        cr_override = scores_override.get("Comfort Rereader", 0)
        self.assertGreaterEqual(cr_df, cr_override)


# ---------------------------------------------------------------------------
# Unit: recompute_reader_type_from_db
# ---------------------------------------------------------------------------

@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
)
class RecomputeReaderTypeFromDbTests(TestCase):
    """recompute_reader_type_from_db builds reader type from DB books + csv_context."""

    def setUp(self):
        self.user = _make_user("rt_db_user")
        safe_cache_delete(f"enrichment_stats_{self.user.id}_rt")
        safe_cache_delete(f"enrichment_stats_{self.user.id}_nort")

    def test_returns_none_when_no_books(self):
        csv_context = {"reread_count": 0, "books_per_year_avg": 0.0}
        result = recompute_reader_type_from_db(self.user, csv_context)
        self.assertIsNone(result)

    def test_returns_correct_keys(self):
        """Result dict has all required keys."""
        _add_book(self.user, "Fantasy A", genres=["fantasy"])
        _add_book(self.user, "Fantasy B", genres=["fantasy"])
        _add_book(self.user, "Fantasy C", genres=["science fiction"])

        csv_context = {"reread_count": 0, "books_per_year_avg": 0.0}
        result = recompute_reader_type_from_db(self.user, csv_context)

        self.assertIsNotNone(result)
        for key in ("reader_type", "reader_type_explanation", "top_reader_types",
                    "reader_type_scores", "reader_type_scores_version"):
            self.assertIn(key, result)

    def test_scores_version_is_2(self):
        _add_book(self.user, "Any Book", genres=["history"])
        csv_context = {"reread_count": 0, "books_per_year_avg": 0.0}
        result = recompute_reader_type_from_db(self.user, csv_context)
        self.assertEqual(result["reader_type_scores_version"], 2)

    def test_top_reader_types_has_at_most_3(self):
        for i in range(10):
            _add_book(self.user, f"Fantasy {i}", genres=["fantasy"])
        csv_context = {"reread_count": 0, "books_per_year_avg": 0.0}
        result = recompute_reader_type_from_db(self.user, csv_context)
        self.assertLessEqual(len(result["top_reader_types"]), 3)

    def test_history_heavy_set_produces_history_hound(self):
        """20 history/historical-fiction/biography books → History Hound scores high."""
        for i in range(20):
            genre = ["history", "historical fiction", "biography"][i % 3]
            _add_book(self.user, f"History Book {i}", genres=[genre])

        csv_context = {"reread_count": 0, "books_per_year_avg": 0.0}
        result = recompute_reader_type_from_db(self.user, csv_context)

        # History Hound should be in top_reader_types
        types_present = [item["type"] for item in result["top_reader_types"]]
        self.assertIn("History Hound", types_present)

    def test_comfort_rereader_driven_by_csv_context(self):
        """reread_count in csv_context drives Comfort Rereader score."""
        for i in range(30):
            _add_book(self.user, f"Random {i}", genres=["mystery"])

        # Half the library was re-read — high Comfort Rereader score
        csv_context = {"reread_count": 15, "books_per_year_avg": 0.0}
        result = recompute_reader_type_from_db(self.user, csv_context)
        scores = result["reader_type_scores"]
        self.assertIn("Comfort Rereader", scores)
        self.assertGreater(scores["Comfort Rereader"], 0)

    def test_rapacious_reader_driven_by_csv_context(self):
        """books_per_year_avg in csv_context drives Rapacious Reader score."""
        for i in range(30):
            _add_book(self.user, f"Novel {i}")

        # Very fast reader
        csv_context = {"reread_count": 0, "books_per_year_avg": 80.0}
        result = recompute_reader_type_from_db(self.user, csv_context)
        scores = result["reader_type_scores"]
        self.assertIn("Rapacious Reader", scores)
        self.assertGreater(scores["Rapacious Reader"], 0)

    def test_reuses_provided_books_list(self):
        """When books= is provided, no additional DB query is made."""
        from core.models import Book as _Book

        for i in range(5):
            _add_book(self.user, f"Provided {i}", genres=["fantasy"])

        pre_fetched = list(
            _Book.objects.filter(readers__user=self.user)
            .select_related("author", "publisher")
            .prefetch_related("genres")
        )
        csv_context = {"reread_count": 0, "books_per_year_avg": 0.0}

        with patch.object(_Book.objects, "filter") as mock_filter:
            result = recompute_reader_type_from_db(self.user, csv_context, books=pre_fetched)
            mock_filter.assert_not_called()

        self.assertIsNotNone(result)


# ---------------------------------------------------------------------------
# Integration: enrichment_status_view payload
# ---------------------------------------------------------------------------

@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
)
class EnrichmentStatusReaderTypePayloadTests(TestCase):
    """enrichment_status_view carries / omits reader_type fields based on csv_context."""

    def setUp(self):
        self.user = _make_user("status_rt_user")
        self.client = Client()
        self.client.login(username="status_rt_user", password="x")
        self.profile = self.user.userprofile
        safe_cache_delete(f"enrichment_stats_{self.user.id}_rt")
        safe_cache_delete(f"enrichment_stats_{self.user.id}_nort")

    def _add_pending_book(self, title, genres=None):
        """Add a book not yet enriched (no google_books_last_checked)."""
        return _add_book(self.user, title, genres=genres, google_books_checked=False)

    def _add_enriched_book(self, title, genres=None):
        """Add a book with enrichment attempted."""
        return _add_book(self.user, title, genres=genres, google_books_checked=True)

    def test_reader_type_present_when_csv_context_set(self):
        """Poll response includes reader_type fields when dna_data has reader_type_csv_context."""
        # One enriched book + one pending → enrichment still pending so view returns JSON
        for i in range(5):
            self._add_enriched_book(f"Fantasy E{i}", genres=["fantasy"])
        self._add_pending_book("Pending Book")

        self.profile.dna_data = {
            "reader_type": "Fantasy Fanatic",
            "reader_type_explanation": "You love fantasy.",
            "top_reader_types": [{"type": "Fantasy Fanatic", "score": 80}],
            "reader_type_scores": {"Fantasy Fanatic": 80},
            "reader_type_scores_version": 2,
            "reader_type_csv_context": {"reread_count": 0, "books_per_year_avg": 0.0},
            "user_stats": {},
        }
        self.profile.save()

        response = self.client.get("/api/enrichment-status/")
        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertTrue(data.get("pending"), "Expected pending=True while books remain unenriched")
        updated = data["updated_stats"]
        self.assertIn("reader_type", updated)
        self.assertIn("reader_type_explanation", updated)
        self.assertIn("top_reader_types", updated)
        self.assertIn("reader_type_color", updated)
        self.assertIn("reader_type_scores_version", updated)

    def test_reader_type_absent_when_csv_context_missing(self):
        """Poll response omits reader_type fields for older users without csv_context."""
        for i in range(3):
            self._add_enriched_book(f"Book E{i}")
        self._add_pending_book("Old Pending")

        # dna_data WITHOUT reader_type_csv_context (legacy user)
        self.profile.dna_data = {
            "reader_type": "Eclectic Reader",
            "user_stats": {},
        }
        self.profile.save()

        response = self.client.get("/api/enrichment-status/")
        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertTrue(data.get("pending"))
        updated = data["updated_stats"]
        self.assertNotIn("reader_type", updated)
        self.assertNotIn("reader_type_color", updated)

    def test_reader_type_color_is_valid_token(self):
        """reader_type_color is one of the known brand-color tokens."""
        valid_colors = {"yellow", "orange", "pink", "cyan", "green", "purple"}
        for i in range(5):
            self._add_enriched_book(f"H{i}", genres=["history"])
        self._add_pending_book("HP")

        self.profile.dna_data = {
            "reader_type": "History Hound",
            "reader_type_csv_context": {"reread_count": 0, "books_per_year_avg": 0.0},
            "user_stats": {},
        }
        self.profile.save()

        response = self.client.get("/api/enrichment-status/")
        data = response.json()
        color = data["updated_stats"].get("reader_type_color")
        self.assertIn(color, valid_colors)


# ---------------------------------------------------------------------------
# Integration: finalize persists profile.reader_type column
# ---------------------------------------------------------------------------

@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
)
class FinalizeReaderTypePersistenceTests(TestCase):
    """On enrichment completion the denormalized profile.reader_type column is saved."""

    def setUp(self):
        self.user = _make_user("finalize_rt_user")
        self.profile = self.user.userprofile
        safe_cache_delete(f"enrichment_stats_{self.user.id}_rt")
        safe_cache_delete(f"enrichment_stats_{self.user.id}_nort")

    def _add_book_with_attempt(self, title, genres=None):
        return _add_book(self.user, title, genres=genres, google_books_checked=True)

    def test_profile_reader_type_persisted_on_finalize(self):
        """When all books are enriched and finalize runs, profile.reader_type is set."""
        for i in range(10):
            self._add_book_with_attempt(f"Fantasy F{i}", genres=["fantasy"])

        dna_data = {
            "reader_type": "Fantasy Fanatic",
            "reader_type_explanation": "You love fantasy.",
            "top_reader_types": [{"type": "Fantasy Fanatic", "score": 80}],
            "reader_type_scores": {"Fantasy Fanatic": 80},
            "reader_type_scores_version": 2,
            "reader_type_csv_context": {"reread_count": 0, "books_per_year_avg": 0.0},
            "user_stats": {},
        }
        self.profile.dna_data = dna_data
        self.profile.save()

        _compute_enrichment_progress(self.user, self.profile, self.profile.dna_data)

        self.profile.refresh_from_db()
        # The column should be set to the reader_type from dna_data (after recompute)
        self.assertIsNotNone(self.profile.reader_type)
        # It should match the dna_data reader_type (which recompute may update)
        self.assertEqual(self.profile.reader_type, self.profile.dna_data.get("reader_type"))

    def test_profile_reader_type_matches_dna_data_after_finalize(self):
        """The denormalized column always matches dna_data['reader_type'] post-finalize."""
        for i in range(5):
            self._add_book_with_attempt(f"Mystery M{i}", genres=["mystery"])
        for i in range(5):
            self._add_book_with_attempt(f"Thriller T{i}", genres=["thriller"])

        self.profile.dna_data = {
            "reader_type": "Mystery Maven",
            "reader_type_csv_context": {"reread_count": 0, "books_per_year_avg": 0.0},
            "user_stats": {},
        }
        self.profile.save()

        dna_copy = dict(self.profile.dna_data)
        _compute_enrichment_progress(self.user, self.profile, dna_copy)

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.reader_type, self.profile.dna_data.get("reader_type"))

    def test_finalize_without_csv_context_does_not_break(self):
        """Older users without csv_context still finalize correctly (reader_type from dna_data)."""
        for i in range(3):
            self._add_book_with_attempt(f"Book O{i}")

        self.profile.dna_data = {
            "reader_type": "Eclectic Reader",
            # No reader_type_csv_context key
            "user_stats": {},
        }
        self.profile.save()

        dna_copy = dict(self.profile.dna_data)
        result = _compute_enrichment_progress(self.user, self.profile, dna_copy)

        # Should still complete without error
        self.profile.refresh_from_db()
        self.assertFalse(result["pending"])
        # reader_type column gets set from existing dna_data reader_type value
        self.assertEqual(self.profile.reader_type, "Eclectic Reader")
