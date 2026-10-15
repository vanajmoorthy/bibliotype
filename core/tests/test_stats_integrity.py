"""
Regression tests for community-stat integrity:
- expired anonymous sessions with too few books still get cleaned up
- re-uploads don't inflate Book.global_read_count or total_profiles_counted
- compare page keeps the "rated by both" count when correlation is undefined
"""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from core.models import AggregateAnalytics, AnonymousUserSession, Book
from core.services.anonymization_service import batch_anonymize_expired_sessions
from core.services.dna import calculate_full_dna
from core.services.user_similarity_service import calculate_user_similarity_from_context

CSV_HEADER = (
    "Title,Author,Exclusive Shelf,My Rating,Number of Pages,"
    "Original Publication Year,Date Read,Average Rating,My Review,ISBN13"
)
CSV = "\n".join(
    [
        CSV_HEADER,
        "Reupload A,Reupload Author,read,4,200,2020,2023/01/01,3.5,,9780000000901",
        "Reupload B,Reupload Author,read,3,250,2019,2023/02/01,4.0,,9780000000902",
    ]
)
CSV_WITHOUT_B = "\n".join(CSV.split("\n")[:2])


class AnonymousSessionCleanupTests(TestCase):
    def test_small_expired_session_is_eventually_deleted(self):
        session = AnonymousUserSession.objects.create(
            session_key="small-session",
            dna_data={"user_stats": {"total_books_read": 3}},
            expires_at=timezone.now() - timedelta(days=1),
        )

        self.assertEqual(batch_anonymize_expired_sessions(), 0)
        session.refresh_from_db()
        self.assertTrue(session.anonymized)

        # Past the 30-day retention window the cleanup removes it.
        AnonymousUserSession.objects.filter(pk=session.pk).update(expires_at=timezone.now() - timedelta(days=31))
        batch_anonymize_expired_sessions()
        self.assertFalse(AnonymousUserSession.objects.filter(pk=session.pk).exists())


@override_settings(
    CELERY_TASK_ALWAYS_EAGER=True,
    CELERY_TASK_EAGER_PROPAGATES=True,
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "stats-integrity"}},
)
@patch("core.tasks.generate_recommendations_task.delay")
@patch("core.tasks.check_author_mainstream_status_task.apply_async")
@patch("core.tasks.enrich_book_task.apply_async")
@patch("core.services.dna.vibe.generate_vibe_with_llm", return_value=["vibe"])
@patch("core.services.book_enrichment_service.enrich_book_from_apis")
class ReuploadCountTests(TransactionTestCase):
    def setUp(self):
        cache.clear()

    def _read_counts(self):
        return sorted(Book.objects.filter(title__startswith="Reupload").values_list("global_read_count", flat=True))

    def test_authenticated_reupload_counts_each_book_once(self, *mocks):
        user = User.objects.create_user(username="reuploader", password="pw")
        calculate_full_dna(CSV, user=user)
        calculate_full_dna(CSV, user=user)
        self.assertEqual(self._read_counts(), [1, 1])

    def test_anonymous_reupload_does_not_double_count(self, *mocks):
        calculate_full_dna(CSV, session_key="anon-reupload")
        profiles_after_first = AggregateAnalytics.objects.get(pk=1).total_profiles_counted

        calculate_full_dna(CSV, session_key="anon-reupload")

        self.assertEqual(self._read_counts(), [1, 1])
        self.assertEqual(AggregateAnalytics.objects.get(pk=1).total_profiles_counted, profiles_after_first)

    def _count(self, title):
        return Book.objects.get(title=title).global_read_count

    def test_authenticated_reupload_decrements_dropped_books(self, *mocks):
        user = User.objects.create_user(username="dropper", password="pw")
        calculate_full_dna(CSV, user=user)
        calculate_full_dna(CSV_WITHOUT_B, user=user)
        self.assertEqual((self._count("Reupload A"), self._count("Reupload B")), (1, 0))

        # Re-adding the dropped book counts it once, not twice.
        calculate_full_dna(CSV, user=user)
        self.assertEqual(self._read_counts(), [1, 1])

    def test_anonymous_reupload_decrements_dropped_books(self, *mocks):
        calculate_full_dna(CSV, session_key="anon-dropper")
        calculate_full_dna(CSV_WITHOUT_B, session_key="anon-dropper")
        self.assertEqual((self._count("Reupload A"), self._count("Reupload B")), (1, 0))

    def test_duplicate_rows_for_one_book_count_once(self, *mocks):
        user = User.objects.create_user(username="duplicator", password="pw")
        duplicate_a = CSV.split("\n")[1].replace("2023/01/01", "2024/05/01")
        calculate_full_dna(f"{CSV}\n{duplicate_a}", user=user)
        self.assertEqual(self._read_counts(), [1, 1])

    def test_duplicate_rows_for_one_book_count_once_in_own_stats(self, *mocks):
        user = User.objects.create_user(username="own-duplicator", password="pw")
        row_a = CSV.split("\n")[1]
        dna = calculate_full_dna(f"{CSV}\n{row_a}", user=user)
        self.assertEqual(dna["user_stats"]["total_books_read"], 2)

    def test_dropped_book_count_never_goes_below_zero(self, *mocks):
        user = User.objects.create_user(username="floor", password="pw")
        calculate_full_dna(CSV, user=user)
        Book.objects.filter(title="Reupload B").update(global_read_count=0)
        calculate_full_dna(CSV_WITHOUT_B, user=user)
        self.assertEqual(self._count("Reupload B"), 0)


def _ctx(ratings):
    return {
        "book_ratings": ratings,
        "book_ids": set(ratings),
        "top_book_ids": set(),
        "genre_weights": {},
        "author_weights": {},
        "rating_dist": {},
        "years_weighted": [],
        "total_books": len(ratings),
    }


class SharedRatedCountTests(TestCase):
    def test_identical_flat_ratings_keep_shared_rated_count(self):
        # Both readers gave the same 10 books 5 stars: zero variance, so no
        # Pearson correlation — but they still rated 10 books in common.
        ratings = {book_id: 5 for book_id in range(10)}
        result = calculate_user_similarity_from_context(_ctx(ratings), _ctx(dict(ratings)))

        self.assertEqual(result["shared_rated_count"], 10)
        self.assertEqual(result["weights_used"]["shared_correlation"], 0)

    def test_jaccard_gets_full_weight_when_correlation_has_too_few_books(self):
        # 3 shared ratings: correlation is defined but below the 5-book
        # threshold, so it contributes nothing and Jaccard keeps 0.25.
        r1 = {1: 1, 2: 3, 3: 5}
        r2 = {1: 2, 2: 3, 3: 4}
        result = calculate_user_similarity_from_context(_ctx(r1), _ctx(r2))

        weights = result["weights_used"]
        self.assertEqual(weights["shared_correlation"], 0)
        # top_overlap is a fixed 0.20 raw weight; the ratio survives normalisation.
        self.assertAlmostEqual(weights["jaccard"] / weights["top_overlap"], 0.25 / 0.20)
