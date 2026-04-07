import json
from unittest.mock import MagicMock, patch

import requests
from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import (
    AnonymousUserSession,
    Author,
    Book,
    Genre,
    Publisher,
    UserBook,
    UserProfile,
)

# ──────────────────────────────────────────────
# Class 1: Book Enrichment Integration Tests
# ──────────────────────────────────────────────


@override_settings(
    CELERY_TASK_ALWAYS_EAGER=True,
    CELERY_TASK_EAGER_PROPAGATES=True,
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
)
class BookEnrichmentIntegrationTests(TestCase):

    def setUp(self):
        self.author = Author.objects.create(name="Test Author")
        self.book = Book.objects.create(
            title="Test Book",
            author=self.author,
            isbn13=None,
            page_count=None,
            publish_year=None,
            google_books_last_checked=None,
        )

    @patch("core.book_enrichment_service._fetch_ratings_and_categories_from_google_books")
    @patch("core.book_enrichment_service._fetch_from_open_library")
    def test_enrich_book_task_updates_book_with_api_data(self, mock_ol, mock_gb):
        """Full enrichment pipeline: task → service → DB updates.
        When OL returns genres, GB is skipped (optimization) so google_books_last_checked
        is set but ratings come from the GB-skip path (no actual GB call)."""
        from core.tasks import enrich_book_task

        mock_ol.return_value = (
            {
                "genres": ["fantasy"],
                "publish_year": 2020,
                "page_count": 350,
                "publisher": "Tor Books",
                "isbn_13": None,
            },
            2,
        )
        mock_gb.return_value = ({"ratings_count": 500, "average_rating": 4.1}, 1)

        enrich_book_task.delay(self.book.id)

        self.book.refresh_from_db()
        self.assertEqual(self.book.publish_year, 2020)
        self.assertEqual(self.book.page_count, 350)
        self.assertIsNotNone(self.book.google_books_last_checked)
        # GB is skipped when OL found genres, so no ratings from GB
        mock_gb.assert_not_called()

        # Check publisher was created and linked
        self.assertIsNotNone(self.book.publisher)
        self.assertEqual(self.book.publisher.name, "Tor Books")

        # Check genres
        genre_names = set(self.book.genres.values_list("name", flat=True))
        self.assertIn("fantasy", genre_names)

    def test_enrich_book_task_book_not_found(self):
        """Graceful exit when book_id doesn't exist."""
        from core.tasks import enrich_book_task

        # Should not raise
        result = enrich_book_task.delay(99999)
        self.assertIsNone(result.result)

    @override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
    @patch("core.book_enrichment_service.enrich_book_from_apis")
    def test_enrich_book_task_skipped_when_superseded_by_newer_upload(self, mock_enrich):
        """Enrichment task exits early if a newer upload nonce is in the cache."""
        from core.cache_utils import safe_cache_set
        from core.tasks import enrich_book_task

        # Set a newer nonce than what the task carries
        safe_cache_set("upload_nonce_42", "new-upload-id", timeout=3600)

        enrich_book_task.delay(self.book.id, user_id=42, upload_nonce="old-upload-id")

        # enrich_book_from_apis should NOT have been called
        mock_enrich.assert_not_called()

    @override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
    @patch("core.book_enrichment_service.enrich_book_from_apis")
    def test_enrich_book_task_runs_when_nonce_matches(self, mock_enrich):
        """Enrichment task runs normally when upload nonce is current."""
        from core.cache_utils import safe_cache_set
        from core.tasks import enrich_book_task

        safe_cache_set("upload_nonce_42", "current-upload-id", timeout=3600)

        enrich_book_task.delay(self.book.id, user_id=42, upload_nonce="current-upload-id")

        mock_enrich.assert_called_once()

    @patch("core.book_enrichment_service.enrich_book_from_apis")
    def test_enrich_book_task_retries_on_api_failure(self, mock_enrich):
        """Task should retry on API failures."""
        from core.tasks import enrich_book_task

        mock_enrich.side_effect = requests.RequestException("API timeout")

        # In eager mode with propagation, the retry raises immediately
        with self.assertRaises(Exception):
            enrich_book_task.delay(self.book.id)

        # The enrichment function was called at least once
        self.assertGreaterEqual(mock_enrich.call_count, 1)

    @patch("core.book_enrichment_service._fetch_ratings_and_categories_from_google_books")
    @patch("core.book_enrichment_service._fetch_from_open_library")
    def test_enrich_book_genre_canonicalization(self, mock_ol, mock_gb):
        """Canonical genres from OL become Genre objects on the Book."""
        from core.book_enrichment_service import enrich_book_from_apis

        mock_ol.return_value = (
            {
                "genres": ["fantasy", "science fiction"],
                "publish_year": None,
                "page_count": None,
                "publisher": None,
                "isbn_13": None,
            },
            2,
        )
        mock_gb.return_value = ({}, 1)

        session = MagicMock()
        enrich_book_from_apis(self.book, session)

        self.book.refresh_from_db()
        genre_names = set(self.book.genres.values_list("name", flat=True))
        self.assertEqual(genre_names, {"fantasy", "science fiction"})

        # Verify Genre objects exist in DB
        self.assertTrue(Genre.objects.filter(name="fantasy").exists())
        self.assertTrue(Genre.objects.filter(name="science fiction").exists())

    @patch("core.book_enrichment_service._fetch_ratings_and_categories_from_google_books")
    @patch("core.book_enrichment_service._fetch_from_open_library")
    def test_google_books_genres_used_when_ol_has_no_genres(self, mock_ol, mock_gb):
        """When OL returns no genres, GB is called and its categories are used."""
        from core.book_enrichment_service import enrich_book_from_apis

        mock_ol.return_value = (
            {
                "genres": [],
                "publish_year": None,
                "page_count": None,
                "publisher": None,
                "isbn_13": None,
            },
            2,
        )
        # Google Books returns Science Fiction category as fallback
        mock_gb.return_value = (
            {"categories": ["Science Fiction"], "ratings_count": 100, "average_rating": 4.0},
            1,
        )

        session = MagicMock()
        enrich_book_from_apis(self.book, session)

        self.book.refresh_from_db()
        genre_names = set(self.book.genres.values_list("name", flat=True))
        self.assertIn("science fiction", genre_names)
        mock_gb.assert_called_once()

    @patch("core.book_enrichment_service._fetch_ratings_and_categories_from_google_books")
    @patch("core.book_enrichment_service._fetch_from_open_library")
    def test_gb_skipped_when_ol_found_genres(self, mock_ol, mock_gb):
        """When OL returns genres, GB is skipped entirely (optimization)."""
        from core.book_enrichment_service import enrich_book_from_apis

        mock_ol.return_value = (
            {
                "genres": ["fantasy"],
                "publish_year": None,
                "page_count": None,
                "publisher": None,
                "isbn_13": None,
            },
            2,
        )

        session = MagicMock()
        enrich_book_from_apis(self.book, session)

        mock_gb.assert_not_called()
        self.book.refresh_from_db()
        self.assertIsNotNone(self.book.google_books_last_checked)
        genre_names = set(self.book.genres.values_list("name", flat=True))
        self.assertIn("fantasy", genre_names)

    def test_ol_isbn_direct_lookup_skips_search(self):
        """When book has isbn13, _fetch_from_open_library uses /isbn/ endpoint, not search."""
        from core.book_enrichment_service import _fetch_from_open_library

        self.book.isbn13 = "9780451524935"
        self.book.save()

        session = MagicMock()
        # ISBN endpoint returns edition data with a works key
        isbn_response = MagicMock()
        isbn_response.status_code = 200
        isbn_response.json.return_value = {
            "number_of_pages": 328,
            "publishers": ["Signet"],
            "publish_date": "1961",
            "covers": [12345],
            "works": [{"key": "/works/OL1168083W"}],
        }
        # Work endpoint returns genres
        work_response = MagicMock()
        work_response.status_code = 200
        work_response.json.return_value = {"subjects": ["Dystopian fiction", "Totalitarianism"]}

        session.get.side_effect = [isbn_response, work_response]

        with patch("core.book_enrichment_service.track_external_api_call"):
            details, api_calls = _fetch_from_open_library(self.book, session)

        self.assertEqual(api_calls, 2)  # ISBN + work, no search
        self.assertEqual(details["page_count"], 328)
        self.assertEqual(details["publisher"], "Signet")
        self.assertEqual(details["publish_year"], 1961)
        self.assertEqual(details["cover_id"], 12345)
        self.assertIn("dystopian", details["genres"])

        # Verify the ISBN endpoint was called, not the search endpoint
        first_call_url = session.get.call_args_list[0][0][0]
        self.assertIn("/isbn/9780451524935.json", first_call_url)

    def test_ol_isbn_lookup_falls_through_to_search_on_404(self):
        """When ISBN endpoint returns 404, falls through to title+author search."""
        from core.book_enrichment_service import _fetch_from_open_library

        self.book.isbn13 = "9780000000000"
        self.book.save()

        session = MagicMock()
        # ISBN endpoint returns 404
        isbn_response = MagicMock()
        isbn_response.status_code = 404

        # Search endpoint returns results
        search_response = MagicMock()
        search_response.status_code = 200
        search_response.raise_for_status = MagicMock()
        search_response.json.return_value = {
            "docs": [{"key": "/works/OL123W", "cover_edition_key": "OL456M", "cover_i": 999}]
        }
        # Work endpoint
        work_response = MagicMock()
        work_response.status_code = 200
        work_response.json.return_value = {"subjects": ["Fantasy"]}
        # Edition endpoint
        edition_response = MagicMock()
        edition_response.status_code = 200
        edition_response.json.return_value = {"number_of_pages": 200, "publishers": ["Tor"]}

        session.get.side_effect = [isbn_response, search_response, work_response, edition_response]

        with patch("core.book_enrichment_service.track_external_api_call"):
            details, api_calls = _fetch_from_open_library(self.book, session)

        # 4 calls: ISBN (failed) + search + work + edition
        self.assertEqual(api_calls, 4)
        self.assertIn("fantasy", details["genres"])
        self.assertEqual(details["page_count"], 200)

    @patch("core.book_enrichment_service._fetch_ratings_and_categories_from_google_books")
    @patch("core.book_enrichment_service._fetch_from_open_library")
    def test_enrich_book_sets_cover_url_from_cover_id(self, mock_ol, mock_gb):
        """OL search with cover_i → cover_url uses cover ID URL."""
        from core.book_enrichment_service import enrich_book_from_apis

        mock_ol.return_value = (
            {
                "genres": [],
                "publish_year": None,
                "page_count": None,
                "publisher": None,
                "isbn_13": None,
                "cover_id": 12345,
            },
            1,
        )
        mock_gb.return_value = ({}, 1)

        session = MagicMock()
        enrich_book_from_apis(self.book, session)

        self.book.refresh_from_db()
        self.assertEqual(self.book.cover_url, "https://covers.openlibrary.org/b/id/12345-M.jpg")

    @patch("core.book_enrichment_service._fetch_ratings_and_categories_from_google_books")
    @patch("core.book_enrichment_service._fetch_from_open_library")
    def test_enrich_book_sets_cover_url_from_isbn_when_no_cover_id(self, mock_ol, mock_gb):
        """OL search without cover_i, book has ISBN → cover_url uses ISBN URL."""
        from core.book_enrichment_service import enrich_book_from_apis

        self.book.isbn13 = "9780593099322"
        self.book.save()

        mock_ol.return_value = (
            {
                "genres": [],
                "publish_year": None,
                "page_count": None,
                "publisher": None,
                "isbn_13": None,
                "cover_id": None,
            },
            1,
        )
        mock_gb.return_value = ({}, 1)

        session = MagicMock()
        enrich_book_from_apis(self.book, session)

        self.book.refresh_from_db()
        self.assertEqual(self.book.cover_url, "https://covers.openlibrary.org/b/isbn/9780593099322-M.jpg")

    @patch("core.book_enrichment_service._fetch_ratings_and_categories_from_google_books")
    @patch("core.book_enrichment_service._fetch_from_open_library")
    def test_enrich_book_sets_cover_url_from_google_books_thumbnail(self, mock_ol, mock_gb):
        """OL without cover_i, no ISBN, GB with thumbnail → cover_url is HTTPS thumbnail."""
        from core.book_enrichment_service import enrich_book_from_apis

        mock_ol.return_value = (
            {
                "genres": [],
                "publish_year": None,
                "page_count": None,
                "publisher": None,
                "isbn_13": None,
                "cover_id": None,
            },
            1,
        )
        mock_gb.return_value = (
            {"thumbnail_url": "https://books.google.com/books/content?id=abc&printsec=frontcover&img=1"},
            1,
        )

        session = MagicMock()
        enrich_book_from_apis(self.book, session)

        self.book.refresh_from_db()
        self.assertEqual(
            self.book.cover_url,
            "https://books.google.com/books/content?id=abc&printsec=frontcover&img=1",
        )

    @patch("core.book_enrichment_service._fetch_ratings_and_categories_from_google_books")
    @patch("core.book_enrichment_service._fetch_from_open_library")
    def test_enrich_book_preserves_existing_cover_url(self, mock_ol, mock_gb):
        """Re-enrichment should not overwrite an existing cover_url."""
        from core.book_enrichment_service import enrich_book_from_apis

        self.book.cover_url = "https://covers.openlibrary.org/b/id/999-M.jpg"
        self.book.save()

        mock_ol.return_value = (
            {
                "genres": [],
                "publish_year": None,
                "page_count": None,
                "publisher": None,
                "isbn_13": None,
                "cover_id": 55555,
            },
            1,
        )
        mock_gb.return_value = ({}, 1)

        session = MagicMock()
        enrich_book_from_apis(self.book, session)

        self.book.refresh_from_db()
        self.assertEqual(self.book.cover_url, "https://covers.openlibrary.org/b/id/999-M.jpg")


# ──────────────────────────────────────────────
# Class 2: Publisher Research Integration Tests
# ──────────────────────────────────────────────


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class PublisherResearchIntegrationTests(TestCase):

    def setUp(self):
        self.pub_never_checked = Publisher.objects.create(
            name="Never Checked Press",
            mainstream_last_checked=None,
        )
        self.pub_stale = Publisher.objects.create(
            name="Stale Check Press",
            mainstream_last_checked=timezone.now() - timezone.timedelta(days=100),
        )
        self.pub_recent = Publisher.objects.create(
            name="Recent Check Press",
            mainstream_last_checked=timezone.now() - timezone.timedelta(days=10),
        )
        # Publisher with parent should be excluded
        parent = Publisher.objects.create(name="Parent Corp", mainstream_last_checked=timezone.now())
        self.pub_with_parent = Publisher.objects.create(
            name="Child Imprint",
            parent=parent,
            mainstream_last_checked=None,
        )

    @patch("time.sleep")
    @patch("core.services.publisher_service.research_publisher_identity")
    def test_updates_unchecked_and_stale_publishers(self, mock_research, mock_sleep):
        """Task picks unchecked + stale publishers, updates is_mainstream and parent."""
        from core.tasks import research_publisher_mainstream_task

        mock_research.return_value = {
            "is_mainstream": True,
            "parent_company_name": "PRH",
            "error": None,
        }

        result = research_publisher_mainstream_task.delay()

        # Should only process never_checked and stale (not recent or child)
        self.assertEqual(mock_research.call_count, 2)

        self.pub_never_checked.refresh_from_db()
        self.assertTrue(self.pub_never_checked.is_mainstream)
        self.assertIsNotNone(self.pub_never_checked.mainstream_last_checked)

        self.pub_stale.refresh_from_db()
        self.assertTrue(self.pub_stale.is_mainstream)
        self.assertIsNotNone(self.pub_stale.mainstream_last_checked)

        # Parent "PRH" should have been created
        self.assertTrue(Publisher.objects.filter(name="PRH").exists())

        # Return value should be 2 (both updated successfully)
        self.assertEqual(result.result, 2)

    @patch("time.sleep")
    @patch("core.services.publisher_service.research_publisher_identity")
    def test_handles_api_error_gracefully(self, mock_research, mock_sleep):
        """Error on one publisher doesn't crash; still sets mainstream_last_checked."""
        from core.tasks import research_publisher_mainstream_task

        mock_research.side_effect = [
            {"is_mainstream": None, "parent_company_name": None, "error": "API timeout"},
            {"is_mainstream": True, "parent_company_name": None, "error": None},
        ]

        result = research_publisher_mainstream_task.delay()

        # Both should have mainstream_last_checked set
        self.pub_never_checked.refresh_from_db()
        self.assertIsNotNone(self.pub_never_checked.mainstream_last_checked)

        self.pub_stale.refresh_from_db()
        self.assertIsNotNone(self.pub_stale.mainstream_last_checked)

        # Only one was successfully updated
        self.assertEqual(result.result, 1)

    @patch("core.services.publisher_service.research_publisher_identity")
    def test_no_publishers_to_check(self, mock_research):
        """Returns 0 when all publishers recently checked."""
        # Mark all parentless publishers as recently checked
        Publisher.objects.filter(parent__isnull=True).update(mainstream_last_checked=timezone.now())

        from core.tasks import research_publisher_mainstream_task

        result = research_publisher_mainstream_task.delay()
        mock_research.assert_not_called()
        self.assertEqual(result.result, 0)


# ──────────────────────────────────────────────
# Class 3: Admin Command Runner Integration Tests
# ──────────────────────────────────────────────


@override_settings(
    CELERY_TASK_ALWAYS_EAGER=True,
    CELERY_TASK_STORE_EAGER_RESULT=True,
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
)
class AdminCommandRunnerIntegrationTests(TestCase):

    def setUp(self):
        self.superuser = User.objects.create_superuser(username="admin", password="adminpass", email="admin@test.com")
        self.regular_user = User.objects.create_user(username="regular", password="regularpass")
        self.client.login(username="admin", password="adminpass")

    @patch("core.tasks.run_management_command_task")
    def test_dispatches_whitelisted_command(self, mock_task):
        """Valid command dispatches task and returns task_id."""
        mock_result = MagicMock()
        mock_result.id = "fake-task-id"
        mock_task.delay.return_value = mock_result

        response = self.client.post(
            "/admin/api/command-run/",
            data=json.dumps({"command": "enrich_books", "arguments": {}}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["task_id"], "fake-task-id")
        mock_task.delay.assert_called_once()

    def test_rejects_non_whitelisted_command(self):
        """Security: commands not in ALLOWED_COMMANDS rejected."""
        response = self.client.post(
            "/admin/api/command-run/",
            data=json.dumps({"command": "flush", "arguments": {}}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.json())

    @patch("core.tasks.run_management_command_task")
    def test_parses_flag_and_int_arguments(self, mock_task):
        """--dry-run (flag) and --limit (int) parsed correctly."""
        mock_result = MagicMock()
        mock_result.id = "task-123"
        mock_task.delay.return_value = mock_result

        response = self.client.post(
            "/admin/api/command-run/",
            data=json.dumps(
                {
                    "command": "enrich_books",
                    "arguments": {"--dry-run": True, "--limit": "50"},
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        mock_task.delay.assert_called_once_with(
            "enrich_books", kwargs={"dry_run": True, "limit": 50, "sync": False, "process_all": False}
        )

    def test_rejects_invalid_integer_argument(self):
        """Non-numeric value for int arg returns 400."""
        response = self.client.post(
            "/admin/api/command-run/",
            data=json.dumps(
                {
                    "command": "enrich_books",
                    "arguments": {"--limit": "not-a-number"},
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Invalid integer", response.json()["error"])

    def test_requires_staff_user(self):
        """Non-staff user gets redirected."""
        self.client.logout()
        self.client.login(username="regular", password="regularpass")

        response = self.client.post(
            "/admin/api/command-run/",
            data=json.dumps({"command": "enrich_books", "arguments": {}}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 302)

    @patch("celery.result.AsyncResult")
    def test_result_api_pending_then_complete(self, mock_async_result_cls):
        """Polling lifecycle: pending → complete."""
        mock_result = MagicMock()
        mock_async_result_cls.return_value = mock_result

        # First call: pending
        mock_result.ready.return_value = False
        response = self.client.get("/admin/api/command-result/test-task-id/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "pending")

        # Second call: complete
        mock_result.ready.return_value = True
        mock_result.successful.return_value = True
        mock_result.get.return_value = {"status": "success", "stdout": "Done!\n", "stderr": ""}
        response = self.client.get("/admin/api/command-result/test-task-id/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "complete")
        self.assertEqual(data["result"]["stdout"], "Done!\n")

    @patch("django.core.management.call_command")
    def test_run_management_command_task_captures_output(self, mock_call_command):
        """Task wraps call_command and captures stdout."""
        from core.tasks import run_management_command_task

        def write_output(command_name, *args, stdout=None, stderr=None, **kwargs):
            if stdout:
                stdout.write("Command output here\n")

        mock_call_command.side_effect = write_output

        result = run_management_command_task("test_command")
        self.assertEqual(result["status"], "success")
        self.assertIn("Command output here", result["stdout"])

    @patch("django.core.management.call_command")
    def test_run_management_command_task_captures_error(self, mock_call_command):
        """Task catches CommandError and returns error result."""
        from core.tasks import run_management_command_task

        mock_call_command.side_effect = CommandError("Something went wrong")

        result = run_management_command_task("failing_command")
        self.assertEqual(result["status"], "error")
        self.assertIn("Something went wrong", result["error"])

    @patch("core.tasks.run_management_command_task")
    def test_regenerate_recommendations_is_whitelisted(self, mock_task):
        """regenerate_recommendations command is accepted by command runner."""
        mock_result = MagicMock()
        mock_result.id = "task-regen-rec"
        mock_task.delay.return_value = mock_result

        response = self.client.post(
            "/admin/api/command-run/",
            data=json.dumps({"command": "regenerate_recommendations", "arguments": {}}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["task_id"], "task-regen-rec")

    @patch("core.tasks.run_management_command_task")
    def test_regenerate_dna_with_recommendations_flag_parsed(self, mock_task):
        """--with-recommendations flag is correctly parsed for regenerate_dna."""
        mock_result = MagicMock()
        mock_result.id = "task-dna-rec"
        mock_task.delay.return_value = mock_result

        response = self.client.post(
            "/admin/api/command-run/",
            data=json.dumps(
                {
                    "command": "regenerate_dna",
                    "arguments": {"--with-recommendations": True},
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        call_kwargs = mock_task.delay.call_args
        self.assertEqual(call_kwargs[0][0], "regenerate_dna")
        self.assertTrue(call_kwargs[1]["kwargs"]["with_recommendations"])


# ──────────────────────────────────────────────
# Class 4: Create UserBooks From Anonymous Session
# ──────────────────────────────────────────────


class CreateUserbooksFromAnonymousSessionTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username="claimuser", password="password")
        self.author = Author.objects.create(name="Session Author")
        self.book1 = Book.objects.create(title="Book One", author=self.author)
        self.book2 = Book.objects.create(title="Book Two", author=self.author)
        self.book3 = Book.objects.create(title="Book Three", author=self.author)
        self.session_key = "test-session-key-123"
        self.anon_session = AnonymousUserSession.objects.create(
            session_key=self.session_key,
            dna_data={"reader_type": "Test Reader"},
            books_data=[self.book1.id, self.book2.id, self.book3.id],
            top_books_data=[self.book1.id, self.book2.id],
            expires_at=timezone.now() + timezone.timedelta(days=7),
        )

    @patch("core.services.top_books_service.calculate_and_store_top_books")
    def test_creates_userbooks_and_marks_top_books(self, mock_calc_top):
        """Happy path: UserBooks created, top books marked with positions."""
        from core.tasks import _create_userbooks_from_anonymous_session

        _create_userbooks_from_anonymous_session(self.user, self.session_key)

        # All 3 UserBooks should exist
        self.assertEqual(UserBook.objects.filter(user=self.user).count(), 3)

        # Top books should be marked
        ub1 = UserBook.objects.get(user=self.user, book=self.book1)
        self.assertTrue(ub1.is_top_book)
        self.assertEqual(ub1.top_book_position, 1)

        ub2 = UserBook.objects.get(user=self.user, book=self.book2)
        self.assertTrue(ub2.is_top_book)
        self.assertEqual(ub2.top_book_position, 2)

        # Non-top book
        ub3 = UserBook.objects.get(user=self.user, book=self.book3)
        self.assertFalse(ub3.is_top_book)

        mock_calc_top.assert_called_once_with(self.user, limit=5)

    @patch("core.services.top_books_service.calculate_and_store_top_books")
    def test_handles_missing_book_ids(self, mock_calc_top):
        """Nonexistent book ID in books_data skipped gracefully."""
        from core.tasks import _create_userbooks_from_anonymous_session

        self.anon_session.books_data = [self.book1.id, 99999, self.book3.id]
        self.anon_session.save()

        _create_userbooks_from_anonymous_session(self.user, self.session_key)

        # Only 2 valid books created
        self.assertEqual(UserBook.objects.filter(user=self.user).count(), 2)

    def test_handles_missing_session(self):
        """Nonexistent session_key doesn't crash."""
        from core.tasks import _create_userbooks_from_anonymous_session

        _create_userbooks_from_anonymous_session(self.user, "nonexistent-session-key")

        self.assertEqual(UserBook.objects.filter(user=self.user).count(), 0)

    @patch("core.services.top_books_service.calculate_and_store_top_books")
    def test_handles_empty_books_data(self, mock_calc_top):
        """Empty books_data returns early."""
        from core.tasks import _create_userbooks_from_anonymous_session

        self.anon_session.books_data = []
        self.anon_session.save()

        _create_userbooks_from_anonymous_session(self.user, self.session_key)

        self.assertEqual(UserBook.objects.filter(user=self.user).count(), 0)
        mock_calc_top.assert_not_called()


# ──────────────────────────────────────────────
# Class 5: Management Command Integration Tests
# ──────────────────────────────────────────────


class ManagementCommandIntegrationTests(TestCase):

    def setUp(self):
        self.author = Author.objects.create(name="Command Author", is_mainstream=True)
        self.genre_fantasy = Genre.objects.create(name="fantasy")
        self.genre_scifi = Genre.objects.create(name="science fiction")

        # Book with genres but missing Google Books check
        self.book1 = Book.objects.create(
            title="Fantasy Book",
            author=self.author,
            publish_year=2020,
            page_count=300,
            google_books_last_checked=None,
        )
        self.book1.genres.add(self.genre_fantasy)

        # Book missing publish_year
        self.book2 = Book.objects.create(
            title="Unknown Year Book",
            author=self.author,
            publish_year=None,
            google_books_last_checked=timezone.now(),
        )

        # Book fully enriched
        self.book3 = Book.objects.create(
            title="Enriched Book",
            author=self.author,
            publish_year=2021,
            page_count=400,
            google_books_last_checked=timezone.now(),
        )
        self.book3.genres.add(self.genre_scifi)

        # User with DNA data and UserBook records
        self.user = User.objects.create_user(username="dnauser", password="password")
        self.user.userprofile.dna_data = {
            "reader_type": "Novella Navigator",
            "top_genres": [],
            "reader_type_scores": {"Novella Navigator": 5},
            "top_reader_types": [{"type": "Novella Navigator", "score": 5}],
            "mainstream_score_percent": 0,
            "reader_type_explanation": "Old explanation",
        }
        self.user.userprofile.reader_type = "Novella Navigator"
        self.user.userprofile.save()

        UserBook.objects.create(user=self.user, book=self.book1, user_rating=5)
        UserBook.objects.create(user=self.user, book=self.book3, user_rating=4)

    @patch("core.management.commands.enrich_books.enrich_book_task")
    def test_enrich_books_dry_run(self, mock_task):
        """Identifies books needing enrichment but dispatches nothing."""
        from io import StringIO

        out = StringIO()
        call_command("enrich_books", "--dry-run", stdout=out)
        output = out.getvalue()

        self.assertIn("missing", output.lower())
        mock_task.delay.assert_not_called()

    @patch("core.management.commands.enrich_books.enrich_book_task")
    def test_enrich_books_async_with_limit(self, mock_task):
        """Dispatches only up to --limit tasks."""
        from io import StringIO

        out = StringIO()
        call_command("enrich_books", "--limit", "1", stdout=out)

        self.assertEqual(mock_task.delay.call_count, 1)

    def test_regenerate_dna_updates_genres_and_reader_type(self):
        """After enrichment, regenerate_dna updates dna_data fields."""
        from io import StringIO

        out = StringIO()
        call_command("regenerate_dna", "--username", "dnauser", stdout=out)

        self.user.userprofile.refresh_from_db()
        dna = self.user.userprofile.dna_data

        # top_genres should contain fantasy (from book1) and science fiction (from book3)
        genre_names = [g[0] for g in dna["top_genres"]]
        self.assertIn("fantasy", genre_names)
        self.assertIn("science fiction", genre_names)

        # Reader type should have been recalculated
        self.assertIn("reader_type", dna)

        # Mainstream score should be recalculated (author is mainstream)
        self.assertEqual(dna["mainstream_score_percent"], 100)

    @patch("core.tasks.generate_recommendations_task")
    def test_regenerate_dna_with_recommendations_dispatches_tasks(self, mock_rec_task):
        """--with-recommendations dispatches recommendation tasks for updated profiles."""
        mock_rec_task.delay = MagicMock()
        from io import StringIO

        out = StringIO()
        call_command("regenerate_dna", "--username", "dnauser", "--with-recommendations", stdout=out)
        output = out.getvalue()

        self.assertIn("Dispatched recommendations", output)
        mock_rec_task.delay.assert_called_once_with(self.user.id)

    @patch("core.tasks.generate_recommendations_task")
    def test_regenerate_dna_with_recommendations_dry_run_does_not_dispatch(self, mock_rec_task):
        """--with-recommendations + --dry-run does not dispatch tasks."""
        mock_rec_task.delay = MagicMock()
        from io import StringIO

        out = StringIO()
        call_command("regenerate_dna", "--username", "dnauser", "--dry-run", "--with-recommendations", stdout=out)

        mock_rec_task.delay.assert_not_called()

    @patch("core.tasks.generate_recommendations_task")
    def test_regenerate_dna_without_flag_does_not_dispatch_recommendations(self, mock_rec_task):
        """Without --with-recommendations, no recommendation tasks are dispatched."""
        mock_rec_task.delay = MagicMock()
        from io import StringIO

        out = StringIO()
        call_command("regenerate_dna", "--username", "dnauser", stdout=out)

        mock_rec_task.delay.assert_not_called()


class RegenerateRecommendationsCommandTests(TestCase):
    """Tests for the regenerate_recommendations management command."""

    def setUp(self):
        self.user1 = User.objects.create_user(username="recuser1", password="test123")
        self.user1.userprofile.dna_data = {"reader_type": "Test", "top_genres": []}
        self.user1.userprofile.save()

        self.user2 = User.objects.create_user(username="recuser2", password="test123")
        self.user2.userprofile.dna_data = {"reader_type": "Test", "top_genres": []}
        self.user2.userprofile.recommendations_data = [{"book_id": 1}]
        self.user2.userprofile.save()

        # User with no DNA data — should be skipped
        self.user_no_dna = User.objects.create_user(username="nodna", password="test123")

    @patch("core.management.commands.regenerate_recommendations.generate_recommendations_task")
    def test_dispatches_tasks_for_users_with_dna(self, mock_task):
        """Dispatches tasks for all users with dna_data."""
        mock_task.delay = MagicMock()
        from io import StringIO

        out = StringIO()
        call_command("regenerate_recommendations", stdout=out)
        output = out.getvalue()

        self.assertEqual(mock_task.delay.call_count, 2)
        self.assertIn("Dispatched recommendation generation for 2 users", output)

    @patch("core.management.commands.regenerate_recommendations.generate_recommendations_task")
    def test_dry_run_does_not_dispatch(self, mock_task):
        """--dry-run shows info but doesn't dispatch tasks."""
        mock_task.delay = MagicMock()
        from io import StringIO

        out = StringIO()
        call_command("regenerate_recommendations", "--dry-run", stdout=out)
        output = out.getvalue()

        mock_task.delay.assert_not_called()
        self.assertIn("Dry run complete", output)
        self.assertIn("recuser1", output)
        self.assertIn("recuser2", output)

    @patch("core.management.commands.regenerate_recommendations.generate_recommendations_task")
    def test_username_filter(self, mock_task):
        """--username processes only the specified user."""
        mock_task.delay = MagicMock()
        from io import StringIO

        out = StringIO()
        call_command("regenerate_recommendations", "--username", "recuser1", stdout=out)

        mock_task.delay.assert_called_once_with(self.user1.id)

    @patch("core.management.commands.regenerate_recommendations.generate_recommendations_task")
    def test_limit_flag(self, mock_task):
        """--limit restricts number of profiles processed."""
        mock_task.delay = MagicMock()
        from io import StringIO

        out = StringIO()
        call_command("regenerate_recommendations", "--limit", "1", stdout=out)

        self.assertEqual(mock_task.delay.call_count, 1)

    @patch("core.management.commands.regenerate_recommendations.generate_recommendations_task")
    def test_dry_run_shows_current_rec_count(self, mock_task):
        """--dry-run displays how many recommendations each user currently has."""
        mock_task.delay = MagicMock()
        from io import StringIO

        out = StringIO()
        call_command("regenerate_recommendations", "--dry-run", stdout=out)
        output = out.getvalue()

        self.assertIn("recuser1: current recommendations = 0", output)
        self.assertIn("recuser2: current recommendations = 1", output)

    @patch("core.management.commands.regenerate_recommendations.generate_recommendations_task")
    def test_skips_users_without_dna(self, mock_task):
        """Users without dna_data are excluded."""
        mock_task.delay = MagicMock()
        from io import StringIO

        out = StringIO()
        call_command("regenerate_recommendations", stdout=out)
        output = out.getvalue()

        self.assertNotIn("nodna", output)
