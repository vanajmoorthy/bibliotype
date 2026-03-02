from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from core.models import Author, Book


class BackfillCoversFastModeTests(TestCase):
    """Tests for the backfill_covers management command (fast mode)."""

    def setUp(self):
        self.author = Author.objects.create(name="Backfill Author")

        # Book with ISBN but no cover_url
        self.book_with_isbn = Book.objects.create(
            title="ISBN Book",
            author=self.author,
            isbn13="9780593099322",
            cover_url=None,
        )

        # Book without ISBN and no cover_url
        self.book_no_isbn = Book.objects.create(
            title="No ISBN Book",
            author=self.author,
            isbn13=None,
            cover_url=None,
        )

        # Book that already has cover_url
        self.book_with_cover = Book.objects.create(
            title="Has Cover Book",
            author=self.author,
            isbn13="9781234567890",
            cover_url="https://covers.openlibrary.org/b/id/123-M.jpg",
        )

    def test_fast_mode_sets_cover_url_from_isbn(self):
        """Books with ISBN get cover URL set."""
        out = StringIO()
        call_command("backfill_covers", stdout=out)

        self.book_with_isbn.refresh_from_db()
        self.assertEqual(
            self.book_with_isbn.cover_url,
            "https://covers.openlibrary.org/b/isbn/9780593099322-M.jpg",
        )

    def test_fast_mode_skips_books_without_isbn(self):
        """Books without ISBN are not touched in fast mode."""
        out = StringIO()
        call_command("backfill_covers", stdout=out)

        self.book_no_isbn.refresh_from_db()
        self.assertIsNone(self.book_no_isbn.cover_url)

    def test_fast_mode_skips_books_with_existing_cover_url(self):
        """Books that already have cover_url are not overwritten."""
        out = StringIO()
        call_command("backfill_covers", stdout=out)

        self.book_with_cover.refresh_from_db()
        self.assertEqual(
            self.book_with_cover.cover_url,
            "https://covers.openlibrary.org/b/id/123-M.jpg",
        )

    def test_dry_run_makes_no_changes(self):
        """Dry run reports counts but doesn't update DB."""
        out = StringIO()
        call_command("backfill_covers", "--dry-run", stdout=out)
        output = out.getvalue()

        self.assertIn("Dry run", output)

        self.book_with_isbn.refresh_from_db()
        self.assertIsNone(self.book_with_isbn.cover_url)
