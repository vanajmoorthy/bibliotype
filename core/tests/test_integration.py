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
        """Full enrichment pipeline: task → service → DB updates."""
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
        self.assertEqual(self.book.google_books_ratings_count, 500)
        self.assertAlmostEqual(self.book.google_books_average_rating, 4.1)

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
    def test_google_books_genres_replace_open_library_genres(self, mock_ol, mock_gb):
        """When Google Books returns categories, they replace OL genres."""
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
        # Google Books returns Science Fiction category
        mock_gb.return_value = (
            {"categories": ["Science Fiction"], "ratings_count": 100, "average_rating": 4.0},
            1,
        )

        session = MagicMock()
        enrich_book_from_apis(self.book, session)

        self.book.refresh_from_db()
        genre_names = set(self.book.genres.values_list("name", flat=True))
        self.assertIn("science fiction", genre_names)
        self.assertNotIn("fantasy", genre_names)


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
        parent = Publisher.objects.create(
            name="Parent Corp", mainstream_last_checked=timezone.now()
        )
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
        Publisher.objects.filter(parent__isnull=True).update(
            mainstream_last_checked=timezone.now()
        )

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
        self.superuser = User.objects.create_superuser(
            username="admin", password="adminpass", email="admin@test.com"
        )
        self.regular_user = User.objects.create_user(
            username="regular", password="regularpass"
        )
        self.client.login(username="admin", password="adminpass")

    @patch("core.tasks.run_management_command_task")
    def test_dispatches_whitelisted_command(self, mock_task):
        """Valid command dispatches task and returns task_id."""
        mock_result = MagicMock()
        mock_result.id = "fake-task-id"
        mock_task.delay.return_value = mock_result

        response = self.client.post(
            "/admin/api/command-run/",
            data=json.dumps({"command": "backfill_enrichment", "arguments": {}}),
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
            data=json.dumps({
                "command": "backfill_enrichment",
                "arguments": {"--dry-run": True, "--limit": "50"},
            }),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        mock_task.delay.assert_called_once_with(
            "backfill_enrichment", kwargs={"dry_run": True, "limit": 50}
        )

    def test_rejects_invalid_integer_argument(self):
        """Non-numeric value for int arg returns 400."""
        response = self.client.post(
            "/admin/api/command-run/",
            data=json.dumps({
                "command": "backfill_enrichment",
                "arguments": {"--limit": "not-a-number"},
            }),
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
            data=json.dumps({"command": "backfill_enrichment", "arguments": {}}),
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

    @patch("core.management.commands.backfill_enrichment.enrich_book_task")
    def test_backfill_enrichment_dry_run(self, mock_task):
        """Identifies books needing enrichment but dispatches nothing."""
        from io import StringIO

        out = StringIO()
        call_command("backfill_enrichment", "--dry-run", stdout=out)
        output = out.getvalue()

        self.assertIn("missing", output.lower())
        mock_task.delay.assert_not_called()

    @patch("core.management.commands.backfill_enrichment.enrich_book_task")
    def test_backfill_enrichment_with_limit(self, mock_task):
        """Dispatches only up to --limit tasks."""
        from io import StringIO

        out = StringIO()
        call_command("backfill_enrichment", "--limit", "1", stdout=out)

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
