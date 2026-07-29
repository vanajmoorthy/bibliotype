"""The Google Books API key must never reach logs or analytics.

requests embeds the full request URL (with `&key=...`) in its exception string,
so the error path has to scrub the key before logging or persisting it.
"""

from unittest.mock import Mock, patch

import requests
from django.test import TestCase

from core.services import book_enrichment_service as svc


class RedactApiKeyHelperTests(TestCase):
    def test_redacts_key_from_url_string(self):
        with patch.object(svc, "GOOGLE_BOOKS_API_KEY", "SECRET_KEY_123"):
            out = svc._redact_api_key(
                "403 Client Error for url: https://www.googleapis.com/books/v1/volumes?q=isbn:1&key=SECRET_KEY_123"
            )
        self.assertNotIn("SECRET_KEY_123", out)
        self.assertIn("***REDACTED***", out)

    def test_accepts_exception_objects(self):
        with patch.object(svc, "GOOGLE_BOOKS_API_KEY", "SECRET_KEY_123"):
            out = svc._redact_api_key(Exception("boom key=SECRET_KEY_123"))
        self.assertNotIn("SECRET_KEY_123", out)

    def test_noop_when_no_key_configured(self):
        with patch.object(svc, "GOOGLE_BOOKS_API_KEY", None):
            self.assertEqual(svc._redact_api_key("plain text, no key"), "plain text, no key")

    def test_handles_none(self):
        self.assertIsNone(svc._redact_api_key(None))


class GoogleBooksErrorRedactionTests(TestCase):
    @patch("core.services.book_enrichment_service.track_external_api_call")
    def test_error_path_redacts_key_in_tracking(self, mock_track):
        book = Mock(pk=1, title="Test Book", isbn13="9781234567890")
        book.author.name = "Test Author"
        session = Mock()
        session.get.side_effect = requests.RequestException(
            "403 Client Error: Forbidden for url: "
            "https://www.googleapis.com/books/v1/volumes?q=isbn:9781234567890&key=SECRET_KEY_123"
        )

        with patch.object(svc, "GOOGLE_BOOKS_API_KEY", "SECRET_KEY_123"):
            result, calls = svc._fetch_ratings_and_categories_from_google_books(book, session, quick_mode=True)

        self.assertEqual(result, {})
        self.assertEqual(calls, 1)
        _, kwargs = mock_track.call_args
        self.assertNotIn("SECRET_KEY_123", kwargs["error_message"])
        self.assertIn("***REDACTED***", kwargs["error_message"])
