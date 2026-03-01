"""
Tests for subtitle data features: new dna_data fields computed by calculate_full_dna(),
the contrariness scale, the backfill_subtitle_data management command, and
recommendations_meta stored by generate_recommendations_task.
"""

from io import StringIO
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from core.dna_constants import compute_contrariness
from core.models import Author, Book, Genre, UserBook, UserProfile
from core.tasks import generate_reading_dna_task


# ──────────────────────────────────────────────
# Helper: build a Goodreads-style CSV string
# ──────────────────────────────────────────────

CSV_HEADER = (
    "Title,Author,Exclusive Shelf,My Rating,Number of Pages,"
    "Original Publication Year,Date Read,Average Rating,My Review,ISBN13"
)


def _csv(*rows):
    """Join header + data rows into a single CSV string."""
    return "\n".join([CSV_HEADER] + list(rows))


# ──────────────────────────────────────────────
# 1. DNA Analyser — subtitle fields present and correct
# ──────────────────────────────────────────────


@override_settings(
    CELERY_TASK_ALWAYS_EAGER=True,
    CELERY_TASK_EAGER_PROPAGATES=True,
    CELERY_RESULT_BACKEND="django-db",
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "subtitle-data-tests",
        }
    },
)
class SubtitleFieldsIntegrationTests(TransactionTestCase):
    """Verify calculate_full_dna populates every new subtitle field."""

    def setUp(self):
        try:
            cache.clear()
        except Exception:
            pass

    # --- authenticated user with multiple books ---

    @patch("core.tasks.generate_recommendations_task.delay")
    @patch("core.tasks.check_author_mainstream_status_task.delay")
    @patch("core.tasks.enrich_book_task.delay")
    @patch("core.services.dna_analyser.generate_vibe_with_llm")
    def test_all_subtitle_fields_present_for_authenticated_user(
        self, mock_vibe, mock_enrich, mock_author_check, mock_rec_task
    ):
        """A multi-book CSV produces all new subtitle fields with plausible values."""
        mock_vibe.return_value = ["test vibe"]
        user = User.objects.create_user(username="subtest", password="pw")

        csv = _csv(
            "Book A,Author One,read,5,300,2020,2023/01/10,4.0,This is a fairly long and positive review.,9780000000010",
            "Book B,Author Two,read,3,250,2019,2023/02/15,4.5,This review is negative and quite disappointing overall.,9780000000020",
            "Book C,Author One,read,4,400,2018,2023/03/20,3.8,,9780000000030",
        )

        result = generate_reading_dna_task.delay(csv, user.id)
        dna = result.result

        # All new keys must exist
        for key in [
            "unique_authors_count",
            "unique_genres_count",
            "controversial_books_count",
            "avg_rating_difference",
            "contrariness_label",
            "contrariness_color",
            "total_reviews_count",
            "positive_reviews_count",
            "negative_reviews_count",
            "niche_books_count",
            "niche_threshold",
        ]:
            self.assertIn(key, dna, f"Missing field: {key}")

        # Type checks
        self.assertIsInstance(dna["unique_authors_count"], int)
        self.assertIsInstance(dna["unique_genres_count"], int)
        self.assertIsInstance(dna["controversial_books_count"], int)
        self.assertIsInstance(dna["avg_rating_difference"], float)
        self.assertIsInstance(dna["contrariness_label"], str)
        self.assertIsInstance(dna["contrariness_color"], str)
        self.assertIsInstance(dna["total_reviews_count"], int)
        self.assertIsInstance(dna["positive_reviews_count"], int)
        self.assertIsInstance(dna["negative_reviews_count"], int)
        self.assertIsInstance(dna["niche_books_count"], int)
        self.assertIsInstance(dna["niche_threshold"], int)

        # Value checks
        self.assertEqual(dna["unique_authors_count"], 2)  # Author One, Author Two
        self.assertEqual(dna["niche_threshold"], 5)
        # 3 books all have both My Rating > 0 and Average Rating
        self.assertEqual(dna["controversial_books_count"], 3)
        self.assertGreater(dna["avg_rating_difference"], 0.0)

        # contrariness_color should be a valid Tailwind class
        valid_colors = {"bg-brand-green", "bg-brand-cyan", "bg-brand-yellow", "bg-brand-orange", "bg-brand-pink"}
        self.assertIn(dna["contrariness_color"], valid_colors)

        # Review counts: 2 reviews > 15 chars with rating > 0
        self.assertEqual(dna["total_reviews_count"], 2)
        # positive + negative <= total
        self.assertLessEqual(dna["positive_reviews_count"] + dna["negative_reviews_count"], dna["total_reviews_count"])

    @patch("core.tasks.generate_recommendations_task.delay")
    @patch("core.tasks.check_author_mainstream_status_task.delay")
    @patch("core.tasks.enrich_book_task.delay")
    @patch("core.services.dna_analyser.generate_vibe_with_llm")
    def test_subtitle_fields_saved_to_user_profile(self, mock_vibe, mock_enrich, mock_author_check, mock_rec_task):
        """Subtitle fields are persisted inside UserProfile.dna_data."""
        mock_vibe.return_value = ["profile vibe"]
        user = User.objects.create_user(username="profilesub", password="pw")

        csv = _csv(
            "Single Book,Solo Author,read,4,200,2021,2023/05/01,4.2,A very detailed review of this book.,9780000000099",
        )

        generate_reading_dna_task.delay(csv, user.id)

        user.userprofile.refresh_from_db()
        dna = user.userprofile.dna_data
        self.assertIsNotNone(dna)
        self.assertIn("unique_authors_count", dna)
        self.assertEqual(dna["unique_authors_count"], 1)
        self.assertIn("contrariness_label", dna)
        self.assertIn("niche_threshold", dna)

    # --- anonymous user ---

    @patch("core.tasks.check_author_mainstream_status_task.delay")
    @patch("core.tasks.enrich_book_task.delay")
    @patch("core.services.dna_analyser.generate_vibe_with_llm")
    def test_subtitle_fields_present_for_anonymous_user(self, mock_vibe, mock_enrich, mock_author_check):
        """Anonymous DNA generation also includes subtitle fields."""
        mock_vibe.return_value = ["anon vibe"]

        csv = _csv(
            "Anon Book,Anon Author,read,5,180,2022,2023/06/01,3.9,An anonymous review that is detailed.,9780000000077",
        )

        result = generate_reading_dna_task.delay(csv, None)
        task_id = result.id
        cached = cache.get(f"dna_result_{task_id}")

        self.assertIsNotNone(cached)
        self.assertIn("unique_authors_count", cached)
        self.assertIn("contrariness_label", cached)
        self.assertIn("niche_threshold", cached)
        self.assertEqual(cached["niche_threshold"], 5)

    # --- niche books count ---

    @patch("core.tasks.generate_recommendations_task.delay")
    @patch("core.tasks.check_author_mainstream_status_task.delay")
    @patch("core.tasks.enrich_book_task.delay")
    @patch("core.services.dna_analyser.generate_vibe_with_llm")
    def test_niche_books_count_reflects_global_read_count(
        self, mock_vibe, mock_enrich, mock_author_check, mock_rec_task
    ):
        """Books with global_read_count <= 5 are counted as niche."""
        mock_vibe.return_value = ["niche vibe"]
        user = User.objects.create_user(username="nicheuser", password="pw")

        csv = _csv(
            "Niche A,Niche Auth,read,4,200,2020,2023/01/01,3.5,,9780000000111",
            "Niche B,Niche Auth,read,3,250,2019,2023/02/01,4.0,,9780000000112",
        )

        result = generate_reading_dna_task.delay(csv, user.id)
        dna = result.result

        # Both books are new, so global_read_count will be 1 each (incremented in processing).
        # 1 <= 5 so both should be niche.
        self.assertGreaterEqual(dna["niche_books_count"], 1)
        self.assertEqual(dna["niche_threshold"], 5)


# ──────────────────────────────────────────────
# 2. Contrariness scale — boundary tests
# ──────────────────────────────────────────────


class ContrarinessScaleTests(TestCase):
    """Test compute_contrariness at every threshold boundary."""

    def test_zero_is_aligned(self):
        label, color = compute_contrariness(0.0)
        self.assertEqual(label, "Aligned with consensus")
        self.assertEqual(color, "bg-brand-green")

    def test_just_below_mildly(self):
        label, color = compute_contrariness(0.29)
        self.assertEqual(label, "Aligned with consensus")
        self.assertEqual(color, "bg-brand-green")

    def test_exactly_mildly(self):
        label, color = compute_contrariness(0.3)
        self.assertEqual(label, "Mildly contrarian")
        self.assertEqual(color, "bg-brand-cyan")

    def test_just_above_mildly(self):
        label, color = compute_contrariness(0.31)
        self.assertEqual(label, "Mildly contrarian")
        self.assertEqual(color, "bg-brand-cyan")

    def test_just_below_moderately(self):
        label, color = compute_contrariness(0.59)
        self.assertEqual(label, "Mildly contrarian")
        self.assertEqual(color, "bg-brand-cyan")

    def test_exactly_moderately(self):
        label, color = compute_contrariness(0.6)
        self.assertEqual(label, "Moderately contrarian")
        self.assertEqual(color, "bg-brand-yellow")

    def test_just_below_very(self):
        label, color = compute_contrariness(0.99)
        self.assertEqual(label, "Moderately contrarian")
        self.assertEqual(color, "bg-brand-yellow")

    def test_exactly_very(self):
        label, color = compute_contrariness(1.0)
        self.assertEqual(label, "Very contrarian")
        self.assertEqual(color, "bg-brand-orange")

    def test_just_below_wildly(self):
        label, color = compute_contrariness(1.49)
        self.assertEqual(label, "Very contrarian")
        self.assertEqual(color, "bg-brand-orange")

    def test_exactly_wildly(self):
        label, color = compute_contrariness(1.5)
        self.assertEqual(label, "Wildly contrarian")
        self.assertEqual(color, "bg-brand-pink")

    def test_extreme_wildly(self):
        label, color = compute_contrariness(4.0)
        self.assertEqual(label, "Wildly contrarian")
        self.assertEqual(color, "bg-brand-pink")


# ──────────────────────────────────────────────
# 3. Edge cases
# ──────────────────────────────────────────────


@override_settings(
    CELERY_TASK_ALWAYS_EAGER=True,
    CELERY_TASK_EAGER_PROPAGATES=True,
    CELERY_RESULT_BACKEND="django-db",
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "subtitle-edge-tests",
        }
    },
)
class SubtitleEdgeCaseTests(TransactionTestCase):
    """Edge cases: no reviews, no ratings, single book, empty-ish CSV."""

    def setUp(self):
        try:
            cache.clear()
        except Exception:
            pass

    @patch("core.tasks.generate_recommendations_task.delay")
    @patch("core.tasks.check_author_mainstream_status_task.delay")
    @patch("core.tasks.enrich_book_task.delay")
    @patch("core.services.dna_analyser.generate_vibe_with_llm")
    def test_no_reviews(self, mock_vibe, mock_enrich, mock_author_check, mock_rec_task):
        """Books with no reviews yield zero review counts."""
        mock_vibe.return_value = ["no review vibe"]
        user = User.objects.create_user(username="noreview", password="pw")

        csv = _csv(
            "No Review Book,NR Author,read,4,300,2020,2023/01/01,4.0,,9780000000201",
        )

        result = generate_reading_dna_task.delay(csv, user.id)
        dna = result.result

        self.assertEqual(dna["total_reviews_count"], 0)
        self.assertEqual(dna["positive_reviews_count"], 0)
        self.assertEqual(dna["negative_reviews_count"], 0)

    @patch("core.tasks.generate_recommendations_task.delay")
    @patch("core.tasks.check_author_mainstream_status_task.delay")
    @patch("core.tasks.enrich_book_task.delay")
    @patch("core.services.dna_analyser.generate_vibe_with_llm")
    def test_no_user_ratings(self, mock_vibe, mock_enrich, mock_author_check, mock_rec_task):
        """Books with My Rating = 0 produce zero controversial_books_count."""
        mock_vibe.return_value = ["no rating vibe"]
        user = User.objects.create_user(username="norating", password="pw")

        csv = _csv(
            "Unrated Book,UR Author,read,0,300,2020,2023/01/01,4.0,,9780000000301",
        )

        result = generate_reading_dna_task.delay(csv, user.id)
        dna = result.result

        self.assertEqual(dna["controversial_books_count"], 0)
        self.assertEqual(dna["avg_rating_difference"], 0.0)
        self.assertEqual(dna["contrariness_label"], "Aligned with consensus")
        self.assertEqual(dna["contrariness_color"], "bg-brand-green")

    @patch("core.tasks.generate_recommendations_task.delay")
    @patch("core.tasks.check_author_mainstream_status_task.delay")
    @patch("core.tasks.enrich_book_task.delay")
    @patch("core.services.dna_analyser.generate_vibe_with_llm")
    def test_single_book(self, mock_vibe, mock_enrich, mock_author_check, mock_rec_task):
        """A single rated book produces valid subtitle data."""
        mock_vibe.return_value = ["solo vibe"]
        user = User.objects.create_user(username="solobook", password="pw")

        csv = _csv(
            "Only Book,Only Author,read,5,400,2015,2022/12/01,3.0,A long enough review for sentiment.,9780000000401",
        )

        result = generate_reading_dna_task.delay(csv, user.id)
        dna = result.result

        self.assertEqual(dna["unique_authors_count"], 1)
        self.assertEqual(dna["controversial_books_count"], 1)
        # |5 - 3.0| = 2.0 => "Wildly contrarian"
        self.assertAlmostEqual(dna["avg_rating_difference"], 2.0, places=1)
        self.assertEqual(dna["contrariness_label"], "Wildly contrarian")
        self.assertEqual(dna["contrariness_color"], "bg-brand-pink")
        self.assertEqual(dna["total_reviews_count"], 1)

    @patch("core.tasks.generate_recommendations_task.delay")
    @patch("core.tasks.check_author_mainstream_status_task.delay")
    @patch("core.tasks.enrich_book_task.delay")
    @patch("core.services.dna_analyser.generate_vibe_with_llm")
    def test_short_review_not_counted(self, mock_vibe, mock_enrich, mock_author_check, mock_rec_task):
        """A review <= 15 chars is not counted as a review."""
        mock_vibe.return_value = ["short review vibe"]
        user = User.objects.create_user(username="shortrev", password="pw")

        csv = _csv(
            "Short Rev Book,SR Author,read,5,300,2020,2023/01/01,4.0,Too short.,9780000000501",
        )

        result = generate_reading_dna_task.delay(csv, user.id)
        dna = result.result

        self.assertEqual(dna["total_reviews_count"], 0)

    @patch("core.tasks.check_author_mainstream_status_task.delay")
    @patch("core.tasks.enrich_book_task.delay")
    @patch("core.services.dna_analyser.generate_vibe_with_llm")
    def test_empty_library_raises_error(self, mock_vibe, mock_enrich, mock_author_check):
        """A CSV with no 'read' books raises ValueError."""
        mock_vibe.return_value = ["nope"]

        csv = _csv(
            "TBR Book,TBR Author,to-read,0,300,2020,,4.0,,9780000000601",
        )

        with self.assertRaises(ValueError):
            generate_reading_dna_task.delay(csv, None)

    @patch("core.tasks.generate_recommendations_task.delay")
    @patch("core.tasks.check_author_mainstream_status_task.delay")
    @patch("core.tasks.enrich_book_task.delay")
    @patch("core.services.dna_analyser.generate_vibe_with_llm")
    def test_review_without_rating_not_counted(self, mock_vibe, mock_enrich, mock_author_check, mock_rec_task):
        """A review on a book with My Rating = 0 is not counted (per the analyser filter)."""
        mock_vibe.return_value = ["vibe"]
        user = User.objects.create_user(username="rev_no_rate", password="pw")

        csv = _csv(
            "Review No Rate,RNR Author,read,0,250,2021,2023/03/01,3.5,This review exists but rating is zero and should be skipped.,9780000000701",
        )

        result = generate_reading_dna_task.delay(csv, user.id)
        dna = result.result

        self.assertEqual(dna["total_reviews_count"], 0)


# ──────────────────────────────────────────────
# 4. Backfill management command
# ──────────────────────────────────────────────


class BackfillSubtitleDataTests(TestCase):
    """Tests for the backfill_subtitle_data management command."""

    def setUp(self):
        self.user = User.objects.create_user(username="backfilluser", password="pw")
        self.author1 = Author.objects.create(name="Backfill Author A")
        self.author2 = Author.objects.create(name="Backfill Author B")

        self.genre = Genre.objects.create(name="fantasy")

        self.book1 = Book.objects.create(
            title="Backfill Book One", author=self.author1, average_rating=4.0, global_read_count=2
        )
        self.book1.genres.add(self.genre)

        self.book2 = Book.objects.create(
            title="Backfill Book Two", author=self.author2, average_rating=3.5, global_read_count=10
        )

        UserBook.objects.create(user=self.user, book=self.book1, user_rating=5, user_review="A very positive and long review text.")
        UserBook.objects.create(user=self.user, book=self.book2, user_rating=2, user_review="Negative long enough review text here.")

        # DNA exists but has no subtitle fields
        self.user.userprofile.dna_data = {
            "reader_type": "Fantasy Fanatic",
            "top_genres": [["fantasy", 1]],
            "user_stats": {"total_books_read": 2},
        }
        self.user.userprofile.save()

    def test_backfill_adds_all_subtitle_fields(self):
        out = StringIO()
        call_command("backfill_subtitle_data", "--username", "backfilluser", stdout=out)

        self.user.userprofile.refresh_from_db()
        dna = self.user.userprofile.dna_data

        self.assertEqual(dna["unique_authors_count"], 2)
        self.assertEqual(dna["unique_genres_count"], 1)  # only "fantasy" mapped
        self.assertEqual(dna["controversial_books_count"], 2)
        self.assertGreater(dna["avg_rating_difference"], 0.0)
        self.assertIn(dna["contrariness_label"], [
            "Aligned with consensus", "Mildly contrarian", "Moderately contrarian",
            "Very contrarian", "Wildly contrarian",
        ])
        self.assertTrue(dna["contrariness_color"].startswith("bg-brand-"))
        self.assertEqual(dna["total_reviews_count"], 2)
        self.assertGreaterEqual(dna["positive_reviews_count"], 0)
        self.assertGreaterEqual(dna["negative_reviews_count"], 0)
        # book1 has global_read_count=2 (<=5), book2 has 10 (>5)
        self.assertEqual(dna["niche_books_count"], 1)
        self.assertEqual(dna["niche_threshold"], 5)

    def test_backfill_preserves_existing_dna_keys(self):
        """Backfill merges into dna_data without removing existing keys."""
        out = StringIO()
        call_command("backfill_subtitle_data", "--username", "backfilluser", stdout=out)

        self.user.userprofile.refresh_from_db()
        dna = self.user.userprofile.dna_data

        self.assertEqual(dna["reader_type"], "Fantasy Fanatic")
        self.assertEqual(dna["user_stats"], {"total_books_read": 2})

    def test_backfill_dry_run_does_not_modify(self):
        out = StringIO()
        call_command("backfill_subtitle_data", "--username", "backfilluser", "--dry-run", stdout=out)
        output = out.getvalue()

        self.assertIn("Dry run complete", output)

        self.user.userprofile.refresh_from_db()
        dna = self.user.userprofile.dna_data
        self.assertNotIn("contrariness_label", dna)

    def test_backfill_skips_already_backfilled_without_force(self):
        """Profiles that already have subtitle fields are skipped unless --force."""
        # Manually add the sentinel fields
        dna = self.user.userprofile.dna_data.copy()
        dna["contrariness_label"] = "Old label"
        dna["unique_authors_count"] = 999
        self.user.userprofile.dna_data = dna
        self.user.userprofile.save()

        out = StringIO()
        call_command("backfill_subtitle_data", "--username", "backfilluser", stdout=out)

        self.user.userprofile.refresh_from_db()
        # Should not have changed
        self.assertEqual(self.user.userprofile.dna_data["unique_authors_count"], 999)

    def test_backfill_force_overwrites(self):
        """--force overwrites existing subtitle fields."""
        dna = self.user.userprofile.dna_data.copy()
        dna["contrariness_label"] = "Old label"
        dna["unique_authors_count"] = 999
        self.user.userprofile.dna_data = dna
        self.user.userprofile.save()

        out = StringIO()
        call_command("backfill_subtitle_data", "--username", "backfilluser", "--force", stdout=out)

        self.user.userprofile.refresh_from_db()
        self.assertNotEqual(self.user.userprofile.dna_data["unique_authors_count"], 999)
        self.assertEqual(self.user.userprofile.dna_data["unique_authors_count"], 2)

    def test_backfill_limit_flag(self):
        """--limit restricts number of profiles processed."""
        user2 = User.objects.create_user(username="backfill2", password="pw")
        user2.userprofile.dna_data = {"reader_type": "Test"}
        user2.userprofile.save()

        out = StringIO()
        call_command("backfill_subtitle_data", "--limit", "1", stdout=out)
        output = out.getvalue()

        self.assertIn("Found 1 profiles to process", output)

    def test_backfill_skips_user_without_userbooks(self):
        """A user with dna_data but no UserBook records is skipped."""
        user_empty = User.objects.create_user(username="emptybf", password="pw")
        user_empty.userprofile.dna_data = {"reader_type": "Empty"}
        user_empty.userprofile.save()

        out = StringIO()
        call_command("backfill_subtitle_data", "--username", "emptybf", stdout=out)
        output = out.getvalue()

        self.assertIn("no UserBook records", output)

    def test_backfill_contrariness_values_correct(self):
        """Verify the actual contrariness values from backfill match expected thresholds."""
        out = StringIO()
        call_command("backfill_subtitle_data", "--username", "backfilluser", stdout=out)

        self.user.userprofile.refresh_from_db()
        dna = self.user.userprofile.dna_data

        # Book 1: user_rating=5, avg=4.0 => diff=1.0
        # Book 2: user_rating=2, avg=3.5 => diff=1.5
        # Mean diff = (1.0 + 1.5) / 2 = 1.25
        self.assertAlmostEqual(dna["avg_rating_difference"], 1.25, places=2)
        self.assertEqual(dna["contrariness_label"], "Very contrarian")
        self.assertEqual(dna["contrariness_color"], "bg-brand-orange")


# ──────────────────────────────────────────────
# 5. Recommendations meta
# ──────────────────────────────────────────────


@override_settings(
    CELERY_TASK_ALWAYS_EAGER=True,
    CELERY_TASK_EAGER_PROPAGATES=True,
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "rec-meta-tests",
        }
    },
)
class RecommendationsMetaTests(TransactionTestCase):
    """Test that generate_recommendations_task stores recommendations_meta on UserProfile."""

    def setUp(self):
        try:
            cache.clear()
        except Exception:
            pass

    @patch("core.services.recommendation_service.get_recommendations_for_user")
    def test_stores_meta_with_similar_users(self, mock_get_recs):
        """When recommendations include similar_user sources, meta captures count and min overlap."""
        user = User.objects.create_user(username="recmeta", password="pw")
        user.userprofile.dna_data = {"reader_type": "Test", "top_genres": []}
        user.userprofile.save()

        author = Author.objects.create(name="Rec Author")
        book = Book.objects.create(title="Rec Book", author=author, average_rating=4.0)

        mock_get_recs.return_value = [
            {
                "book": book,
                "confidence": 0.8,
                "score": 0.75,
                "recommender_count": 2,
                "genre_alignment": 0.5,
                "sources": [
                    {"type": "similar_user", "user_id": 10, "similarity_score": 0.7, "username": "user10"},
                    {"type": "similar_user", "user_id": 20, "similarity_score": 0.5, "username": "user20"},
                ],
                "explanation_components": {"reason": "test"},
            },
        ]

        from core.tasks import generate_recommendations_task

        generate_recommendations_task.delay(user.id)

        user.userprofile.refresh_from_db()
        meta = user.userprofile.recommendations_meta
        self.assertIsNotNone(meta)
        self.assertEqual(meta["similar_users_count"], 2)
        self.assertEqual(meta["min_overlap_pct"], 50)  # 0.5 * 100 = 50

    @patch("core.services.recommendation_service.get_recommendations_for_user")
    def test_stores_meta_without_similar_users(self, mock_get_recs):
        """When no similar_user sources, meta defaults to zero."""
        user = User.objects.create_user(username="recmeta_none", password="pw")
        user.userprofile.dna_data = {"reader_type": "Test", "top_genres": []}
        user.userprofile.save()

        author = Author.objects.create(name="Rec Author2")
        book = Book.objects.create(title="Rec Book2", author=author, average_rating=3.5)

        mock_get_recs.return_value = [
            {
                "book": book,
                "confidence": 0.6,
                "score": 0.5,
                "recommender_count": 0,
                "genre_alignment": 0.3,
                "sources": [{"type": "genre_match"}],
                "explanation_components": {},
            },
        ]

        from core.tasks import generate_recommendations_task

        generate_recommendations_task.delay(user.id)

        user.userprofile.refresh_from_db()
        meta = user.userprofile.recommendations_meta
        self.assertEqual(meta["similar_users_count"], 0)
        self.assertEqual(meta["min_overlap_pct"], 0)

    @patch("core.services.recommendation_service.get_recommendations_for_user")
    def test_stores_meta_with_empty_recommendations(self, mock_get_recs):
        """When recommendation engine returns nothing, meta is still stored."""
        user = User.objects.create_user(username="recmeta_empty", password="pw")
        user.userprofile.dna_data = {"reader_type": "Test", "top_genres": []}
        user.userprofile.save()

        mock_get_recs.return_value = []

        from core.tasks import generate_recommendations_task

        generate_recommendations_task.delay(user.id)

        user.userprofile.refresh_from_db()
        meta = user.userprofile.recommendations_meta
        self.assertEqual(meta["similar_users_count"], 0)
        self.assertEqual(meta["min_overlap_pct"], 0)

    @patch("core.services.recommendation_service.get_recommendations_for_user")
    def test_meta_picks_minimum_overlap(self, mock_get_recs):
        """min_overlap_pct should be the lowest similarity across all similar_user sources."""
        user = User.objects.create_user(username="recmeta_min", password="pw")
        user.userprofile.dna_data = {"reader_type": "Test", "top_genres": []}
        user.userprofile.save()

        author = Author.objects.create(name="Min Auth")
        book1 = Book.objects.create(title="Min Book1", author=author, average_rating=4.0)
        book2 = Book.objects.create(title="Min Book2", author=author, average_rating=3.5)

        mock_get_recs.return_value = [
            {
                "book": book1,
                "confidence": 0.8,
                "score": 0.7,
                "recommender_count": 1,
                "genre_alignment": 0.5,
                "sources": [
                    {"type": "similar_user", "user_id": 100, "similarity_score": 0.9, "username": "highsim"},
                ],
                "explanation_components": {},
            },
            {
                "book": book2,
                "confidence": 0.6,
                "score": 0.5,
                "recommender_count": 1,
                "genre_alignment": 0.3,
                "sources": [
                    {"type": "similar_user", "user_id": 200, "similarity_score": 0.35, "username": "lowsim"},
                ],
                "explanation_components": {},
            },
        ]

        from core.tasks import generate_recommendations_task

        generate_recommendations_task.delay(user.id)

        user.userprofile.refresh_from_db()
        meta = user.userprofile.recommendations_meta
        # 3 unique users: 100, 200
        self.assertEqual(meta["similar_users_count"], 2)
        # min similarity is 0.35 => 35%
        self.assertEqual(meta["min_overlap_pct"], 35)

    def test_recommendations_meta_default_empty_dict(self):
        """New UserProfile has recommendations_meta defaulting to empty dict."""
        user = User.objects.create_user(username="newprofile", password="pw")
        user.userprofile.refresh_from_db()
        self.assertEqual(user.userprofile.recommendations_meta, {})
