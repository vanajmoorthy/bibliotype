"""Tests for the Google Books quota circuit breaker and the vibe-failure retry fix."""

from unittest.mock import Mock, patch

import requests
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, TransactionTestCase, override_settings

from core.models import Author, Book
from core.services import book_enrichment_service as svc
from core.services.llm_service import generate_vibe_with_llm

LOCMEM_CACHE = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "gb-quota-tests",
    }
}


def _http_error_response(status_code, reason=None):
    response = Mock()
    response.status_code = status_code
    body = {"error": {"errors": [{"reason": reason}]}} if reason else {}
    response.json = Mock(return_value=body)
    return response


def _session_raising(status_code, reason=None):
    session = Mock()
    response = _http_error_response(status_code, reason)
    error = requests.HTTPError("boom", response=response)
    ok_response = Mock()
    ok_response.raise_for_status.side_effect = error
    session.get.return_value = ok_response
    return session


def _mock_book():
    book = Mock(pk=1, title="Test Book", isbn13="9781234567890")
    book.author.name = "Test Author"
    return book


@override_settings(CACHES=LOCMEM_CACHE)
class GoogleBooksQuotaCooldownTests(TestCase):
    def setUp(self):
        cache.clear()

    @patch("core.services.book_enrichment_service.track_external_api_call")
    def test_429_returns_none_and_trips_cooldown(self, mock_track):
        session = _session_raising(429, reason="rateLimitExceeded")

        with patch.object(svc, "GOOGLE_BOOKS_API_KEY", "test-key"):
            result, calls = svc._fetch_ratings_and_categories_from_google_books(_mock_book(), session)

        self.assertIsNone(result)
        self.assertEqual(calls, 1)
        self.assertTrue(cache.get(svc.GOOGLE_BOOKS_COOLDOWN_KEY))

    @patch("core.services.book_enrichment_service.track_external_api_call")
    def test_active_cooldown_short_circuits_without_calling_api(self, mock_track):
        cache.set(svc.GOOGLE_BOOKS_COOLDOWN_KEY, 1, timeout=60)
        session = Mock()

        with patch.object(svc, "GOOGLE_BOOKS_API_KEY", "test-key"):
            result, calls = svc._fetch_ratings_and_categories_from_google_books(_mock_book(), session)

        self.assertIsNone(result)
        self.assertEqual(calls, 0)
        session.get.assert_not_called()

    @patch("core.services.book_enrichment_service.track_external_api_call")
    def test_403_without_quota_reason_logs_config_error_and_trips_cooldown(self, mock_track):
        session = _session_raising(403, reason="forbidden")

        with patch.object(svc, "GOOGLE_BOOKS_API_KEY", "test-key"):
            with self.assertLogs("core.services.book_enrichment_service", level="ERROR") as logs:
                result, calls = svc._fetch_ratings_and_categories_from_google_books(_mock_book(), session)

        self.assertIsNone(result)
        self.assertTrue(any("API key or API-enablement problem" in line for line in logs.output))
        self.assertTrue(cache.get(svc.GOOGLE_BOOKS_COOLDOWN_KEY))

    @patch("core.services.book_enrichment_service.track_external_api_call")
    def test_403_with_quota_reason_logs_quota_warning(self, mock_track):
        session = _session_raising(403, reason="dailyLimitExceeded")

        with patch.object(svc, "GOOGLE_BOOKS_API_KEY", "test-key"):
            with self.assertLogs("core.services.book_enrichment_service", level="WARNING") as logs:
                result, _ = svc._fetch_ratings_and_categories_from_google_books(_mock_book(), session)

        self.assertIsNone(result)
        self.assertTrue(any("quota exhausted" in line for line in logs.output))
        self.assertTrue(cache.get(svc.GOOGLE_BOOKS_COOLDOWN_KEY))

    @patch("core.services.book_enrichment_service.track_external_api_call")
    def test_plain_transport_error_does_not_trip_cooldown(self, mock_track):
        session = Mock()
        session.get.side_effect = requests.ConnectionError("read timed out")

        with patch.object(svc, "GOOGLE_BOOKS_API_KEY", "test-key"):
            result, calls = svc._fetch_ratings_and_categories_from_google_books(_mock_book(), session)

        self.assertIsNone(result)
        self.assertEqual(calls, 1)
        self.assertIsNone(cache.get(svc.GOOGLE_BOOKS_COOLDOWN_KEY))


@override_settings(CACHES=LOCMEM_CACHE)
class FailedFetchStampingTests(TestCase):
    def setUp(self):
        cache.clear()
        author = Author.objects.create(name="Stamp Author")
        self.book = Book.objects.create(title="Stamp Book", author=author, isbn13="9780000000101")

    @patch("core.services.book_enrichment_service._fetch_from_open_library", return_value=({}, 0))
    @patch("core.services.book_enrichment_service._fetch_ratings_and_categories_from_google_books")
    def test_failed_gb_fetch_leaves_book_unstamped(self, mock_gb, mock_ol):
        mock_gb.return_value = (None, 1)

        svc.enrich_book_from_apis(self.book, Mock())

        self.book.refresh_from_db()
        self.assertIsNone(self.book.google_books_last_checked)

    @patch("core.services.book_enrichment_service._fetch_from_open_library", return_value=({}, 0))
    @patch("core.services.book_enrichment_service._fetch_ratings_and_categories_from_google_books")
    def test_completed_gb_fetch_stamps_book(self, mock_gb, mock_ol):
        mock_gb.return_value = ({}, 1)  # clean "not found" is a completed exchange

        svc.enrich_book_from_apis(self.book, Mock())

        self.book.refresh_from_db()
        self.assertIsNotNone(self.book.google_books_last_checked)


class VibeFailureTests(TestCase):
    @patch("core.services._gemini.client")
    def test_api_exception_returns_none(self, mock_client):
        mock_client.return_value.generate_content.side_effect = Exception("404 model gone")
        self.assertIsNone(generate_vibe_with_llm({"reader_type": "x"}))

    @patch("core.services._gemini.client", return_value=None)
    def test_unconfigured_key_returns_none(self, mock_client):
        self.assertIsNone(generate_vibe_with_llm({"reader_type": "x"}))

    @patch("core.services._gemini.client")
    def test_valid_response_returns_single_phrase(self, mock_client):
        # The dashboard shows one sentence; extra phrases from the model are dropped.
        mock_client.return_value.generate_content.return_value = Mock(text='{"vibe_phrases": ["a", "b"]}')
        self.assertEqual(generate_vibe_with_llm({"reader_type": "x"}), ["a"])


GOODREADS_CSV = (
    "Book Id,Title,Author,ISBN13,My Rating,Average Rating,Number of Pages,Year Published,"
    "Original Publication Year,Date Read,Date Added,Exclusive Shelf,My Review\n"
    + "\n".join(
        f'{i},"Vibe Book {i}","Vibe Author {i}","=""97800000002{i:02d}""",4,4.0,300,2015,2015,'
        f"2024/03/0{i + 1},2024/01/15,read,"
        for i in range(5)
    )
    + "\n"
)


@override_settings(
    CELERY_TASK_ALWAYS_EAGER=True,
    CELERY_TASK_STORE_EAGER_RESULT=True,
    CACHES=LOCMEM_CACHE,
)
@patch("core.services.book_enrichment_service.enrich_book_from_apis")
class VibeFailureRetryPipelineTests(TransactionTestCase):
    """A failed vibe must not be cached: the next DNA run retries the LLM."""

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="vibe_user", email="vibe@test.com", password="x")

    def tearDown(self):
        from django.db import connections

        connections.close_all()
        super().tearDown()

    def test_failed_vibe_is_retried_on_next_run(self, mock_enrich):
        from core.services.dna import calculate_full_dna

        with patch("core.services.dna.generate_vibe_with_llm", return_value=None):
            calculate_full_dna(GOODREADS_CSV, self.user)

        profile = self.user.userprofile
        profile.refresh_from_db()
        self.assertEqual(profile.reading_vibe, [])
        self.assertIsNone(profile.vibe_data_hash)

        with patch("core.services.dna.generate_vibe_with_llm", return_value=["a real vibe"]) as mock_vibe:
            calculate_full_dna(GOODREADS_CSV, self.user)

        profile.refresh_from_db()
        mock_vibe.assert_called_once()
        self.assertEqual(profile.reading_vibe, ["a real vibe"])
        self.assertIsNotNone(profile.vibe_data_hash)
