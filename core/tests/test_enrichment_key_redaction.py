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

    def test_regex_scrubs_rotated_key_not_matching_env(self):
        """A key that no longer matches the configured env var is still scrubbed
        via the `key=` query-param regex (covers old keys in stale log strings)."""
        with patch.object(svc, "GOOGLE_BOOKS_API_KEY", "CURRENT_KEY"):
            out = svc._redact_api_key(
                "403 for url: https://www.googleapis.com/books/v1/volumes?q=isbn:1&key=AIzaOLD_ROTATED_KEY_xyz"
            )
        self.assertNotIn("AIzaOLD_ROTATED_KEY_xyz", out)
        self.assertIn("key=***REDACTED***", out)

    def test_regex_scrubs_key_when_env_unset(self):
        """Even with no env key configured, a `key=...` URL param is redacted."""
        with patch.object(svc, "GOOGLE_BOOKS_API_KEY", None):
            out = svc._redact_api_key("...volumes?q=intitle:x&key=AIzaSyLEAKED123")
        self.assertNotIn("AIzaSyLEAKED123", out)
        self.assertIn("key=***REDACTED***", out)

    def test_regex_stops_at_next_query_param(self):
        """Redaction replaces only the key value, not trailing params."""
        with patch.object(svc, "GOOGLE_BOOKS_API_KEY", None):
            out = svc._redact_api_key("?q=x&key=AIzaSyABC&country=US")
        self.assertNotIn("AIzaSyABC", out)
        self.assertIn("country=US", out)


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


class BackfillCommandRedactionTests(TestCase):
    """The backfill_isbn / backfill_covers management commands log GB errors
    directly; the request URL (with &key=...) must be scrubbed before logging."""

    def test_backfill_isbn_redacts_key_in_warning(self):
        from core.management.commands import backfill_isbn as cmd_mod

        cmd = cmd_mod.Command()
        cmd._warn = Mock()
        session = Mock()
        session.get.side_effect = requests.RequestException(
            "403 Client Error: Forbidden for url: "
            "https://www.googleapis.com/books/v1/volumes?q=intitle:x&key=AIzaSyLEAKED_BACKFILL"
        )

        with patch.object(cmd_mod, "GOOGLE_BOOKS_API_KEY", "AIzaSyLEAKED_BACKFILL"):
            result = cmd._search_google_books(session, "Some Title", "Some Author")

        self.assertIsNone(result)
        warned = cmd._warn.call_args[0][0]
        self.assertNotIn("AIzaSyLEAKED_BACKFILL", warned)
        self.assertIn("***REDACTED***", warned)
