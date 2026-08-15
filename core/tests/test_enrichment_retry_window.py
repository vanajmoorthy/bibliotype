"""Tests for the 24h enrichment retry window (PR A).

A book attempted (google_books_last_checked set) within ENRICHMENT_RETRY_AFTER
is skipped; once the attempt is older it becomes eligible again so zero-genre
books get a second chance when the APIs may have data. Operator re-runs
(--process-all / --force) bypass the window.
"""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from core.dna_constants import ENRICHMENT_RETRY_AFTER
from core.models import Author, Book, UserBook


def _make_author(name="Retry Author"):
    normalized = name.lower().replace(" ", "").replace(".", "")
    return Author.objects.get_or_create(name=name, defaults={"normalized_name": normalized})[0]


def _make_book(title="Retry Book", *, checked_ago=None):
    """checked_ago: a timedelta for how long ago GB was checked, or None (never)."""
    last_checked = timezone.now() - checked_ago if checked_ago is not None else None
    return Book.objects.create(
        title=title,
        normalized_title=title.lower().replace(" ", ""),
        author=_make_author(),
        google_books_last_checked=last_checked,
    )


class EnrichBookFromApisGateTests(TestCase):
    """The GB fetch inside enrich_book_from_apis respects the retry window."""

    def setUp(self):
        self.ol_patcher = patch(
            "core.services.book_enrichment_service._fetch_from_open_library", return_value=({}, 0)
        )
        self.gb_patcher = patch(
            "core.services.book_enrichment_service._fetch_ratings_and_categories_from_google_books",
            return_value=({}, 1),
        )
        self.mock_ol = self.ol_patcher.start()
        self.mock_gb = self.gb_patcher.start()
        self.addCleanup(self.ol_patcher.stop)
        self.addCleanup(self.gb_patcher.stop)

    def _run(self, book):
        from core.services.book_enrichment_service import enrich_book_from_apis

        enrich_book_from_apis(book, session=object())

    def test_gb_not_refetched_when_recently_checked(self):
        book = _make_book(checked_ago=timedelta(hours=1))
        self._run(book)
        self.mock_gb.assert_not_called()

    def test_gb_refetched_when_check_is_stale(self):
        book = _make_book(checked_ago=ENRICHMENT_RETRY_AFTER + timedelta(hours=1))
        self._run(book)
        self.mock_gb.assert_called_once()

    def test_gb_fetched_when_never_checked(self):
        book = _make_book(checked_ago=None)
        self._run(book)
        self.mock_gb.assert_called_once()


@override_settings(CELERY_TASK_ALWAYS_EAGER=True, CELERY_TASK_EAGER_PROPAGATES=True)
class EnrichBookTaskWindowTests(TransactionTestCase):
    """The async task honours the window unless forced."""

    def setUp(self):
        self.inline_patcher = patch("core.services.book_enrichment_service.enrich_book_from_apis")
        self.mock_enrich = self.inline_patcher.start()
        self.addCleanup(self.inline_patcher.stop)

    def _run(self, book, **kwargs):
        from core.tasks import enrich_book_task

        enrich_book_task.apply(args=[book.pk], kwargs=kwargs)

    def test_recently_attempted_book_is_skipped(self):
        book = _make_book(checked_ago=timedelta(hours=1))
        self._run(book)
        self.mock_enrich.assert_not_called()

    def test_stale_book_is_reattempted(self):
        book = _make_book(checked_ago=ENRICHMENT_RETRY_AFTER + timedelta(hours=1))
        self._run(book)
        self.mock_enrich.assert_called_once()

    def test_never_attempted_book_runs(self):
        book = _make_book(checked_ago=None)
        self._run(book)
        self.mock_enrich.assert_called_once()

    def test_force_bypasses_window_for_recent_book(self):
        book = _make_book(checked_ago=timedelta(hours=1))
        self._run(book, force=True)
        self.mock_enrich.assert_called_once()


class EnrichBooksCommandWindowTests(TestCase):
    """The management command bypasses the window on --process-all / --force."""

    def _book_with_user(self, checked_ago):
        book = _make_book(checked_ago=checked_ago)
        user = User.objects.create_user(username="cmd_user", password="x", email="cmd@example.com")
        UserBook.objects.create(user=user, book=book)
        return book

    @patch("core.management.commands.enrich_books.enrich_book_task.delay")
    def test_plain_run_does_not_reset_timestamp_or_force(self, mock_delay):
        # A recently-checked book still matches the default queryset only if it's
        # missing something; give it no genres/pages so it qualifies.
        book = self._book_with_user(timedelta(hours=1))
        call_command("enrich_books")
        book.refresh_from_db()
        self.assertIsNotNone(book.google_books_last_checked)  # not reset
        # Dispatched with force=False so the task's window guard applies.
        _, kwargs = mock_delay.call_args
        self.assertFalse(kwargs.get("force", False))

    @patch("core.management.commands.enrich_books.enrich_book_task.delay")
    def test_force_resets_timestamp_and_forces(self, mock_delay):
        book = self._book_with_user(timedelta(hours=1))
        call_command("enrich_books", "--force")
        book.refresh_from_db()
        self.assertIsNone(book.google_books_last_checked)  # reset to force re-fetch
        _, kwargs = mock_delay.call_args
        self.assertTrue(kwargs.get("force"))

    @patch("core.management.commands.enrich_books.enrich_book_task.delay")
    def test_process_all_resets_timestamp_and_forces(self, mock_delay):
        book = self._book_with_user(timedelta(hours=1))
        call_command("enrich_books", "--process-all")
        book.refresh_from_db()
        self.assertIsNone(book.google_books_last_checked)
        _, kwargs = mock_delay.call_args
        self.assertTrue(kwargs.get("force"))
