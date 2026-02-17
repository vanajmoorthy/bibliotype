import logging
import os
import re
import time

import requests
from django.core.management.base import BaseCommand
from django.db import IntegrityError

from core.models import Book

logger = logging.getLogger(__name__)

GOOGLE_BOOKS_API_KEY = os.getenv("GOOGLE_BOOKS_API_KEY")


class Command(BaseCommand):
    help = "Backfill ISBN13 for books missing it by querying Open Library and Google Books APIs."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show count of books missing ISBN13 without making API calls.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            help="Limit the number of books to process.",
        )

    def _log(self, msg):
        self.stdout.write(msg)
        logger.info(f"backfill_isbn: {msg}")

    def _warn(self, msg):
        self.stdout.write(self.style.WARNING(msg))
        logger.warning(f"backfill_isbn: {msg}")

    def _clean_title_for_api(self, title):
        """Clean Goodreads-style titles for API search."""
        clean = title
        # Remove parenthetical/bracketed text (series info, notes)
        clean = re.sub(r"[\(\[].*?[\)\]]", "", clean)
        # Remove "Publisher: ..." suffix pattern from Goodreads
        clean = re.sub(r"\s+Publisher:.*$", "", clean, flags=re.IGNORECASE)
        # Only split on colon if it looks like a subtitle (not embedded in the title)
        # Keep the first part if what follows the colon is long (likely a subtitle)
        if ":" in clean:
            parts = clean.split(":", 1)
            # If the part before the colon is a reasonable title, use just that
            if len(parts[0].strip()) >= 5:
                clean = parts[0]
        # Remove trailing junk
        clean = re.sub(r"\s*[-–—]\s*$", "", clean)
        return clean.strip()

    def _isbn10_to_isbn13(self, isbn10):
        """Convert an ISBN-10 to ISBN-13."""
        if len(isbn10) != 10:
            return None
        # Remove the old check digit, prefix with 978
        base = "978" + isbn10[:9]
        # Calculate ISBN-13 check digit
        total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(base))
        check = (10 - (total % 10)) % 10
        return base + str(check)

    def _find_isbn13(self, isbn_list):
        """Pick the first valid ISBN-13 from the list, converting ISBN-10s if needed."""
        # First pass: look for ISBN-13 directly
        for isbn in isbn_list:
            cleaned = isbn.strip()
            if len(cleaned) == 13 and cleaned.isdigit():
                return cleaned
        # Second pass: convert ISBN-10 to ISBN-13
        for isbn in isbn_list:
            cleaned = isbn.strip()
            if len(cleaned) == 10 and cleaned[:9].isdigit():
                converted = self._isbn10_to_isbn13(cleaned)
                if converted:
                    return converted
        return None

    def _search_open_library(self, session, title, author_name):
        """Search Open Library for ISBN. Returns ISBN-13 string or None."""
        clean_title = self._clean_title_for_api(title)
        search_url = "https://openlibrary.org/search.json"
        params = {
            "title": clean_title,
            "author": author_name,
            "limit": 3,
            "fields": "isbn,title,author_name",
        }

        try:
            res = session.get(search_url, params=params, timeout=10)
            res.raise_for_status()
            data = res.json()
        except requests.RequestException as e:
            self._warn(f"  OL API error for '{title}': {e}")
            return None

        docs = data.get("docs", [])
        # Check all returned docs, not just the first
        for doc in docs:
            if "isbn" in doc:
                isbn13 = self._find_isbn13(doc["isbn"])
                if isbn13:
                    return isbn13

        return None

    def _search_google_books(self, session, title, author_name):
        """Search Google Books for ISBN. Returns ISBN-13 string or None."""
        if not GOOGLE_BOOKS_API_KEY:
            return None

        clean_title = self._clean_title_for_api(title)
        title_q = requests.utils.quote(clean_title)
        author_q = requests.utils.quote(author_name)
        query = f"intitle:{title_q}+inauthor:{author_q}"
        url = f"https://www.googleapis.com/books/v1/volumes?q={query}&key={GOOGLE_BOOKS_API_KEY}"

        try:
            res = session.get(url, timeout=10)
            res.raise_for_status()
            data = res.json()
        except requests.RequestException as e:
            self._warn(f"  GB API error for '{title}': {e}")
            return None

        if data.get("totalItems", 0) == 0:
            return None

        # Check first few results for ISBN-13
        for item in data.get("items", [])[:3]:
            volume_info = item.get("volumeInfo", {})
            identifiers = volume_info.get("industryIdentifiers", [])

            # Prefer ISBN_13 directly
            for ident in identifiers:
                if ident.get("type") == "ISBN_13":
                    isbn = ident.get("identifier", "").strip()
                    if len(isbn) == 13 and isbn.isdigit():
                        return isbn

            # Fall back to converting ISBN_10
            for ident in identifiers:
                if ident.get("type") == "ISBN_10":
                    isbn10 = ident.get("identifier", "").strip()
                    converted = self._isbn10_to_isbn13(isbn10)
                    if converted:
                        return converted

        return None

    def handle(self, *args, **options):
        queryset = Book.objects.filter(isbn13__isnull=True).select_related("author")

        total = queryset.count()
        if total == 0:
            self._log("All books already have ISBN13. Nothing to do.")
            return

        self._log(f"Found {total} books missing ISBN13.")

        if options["limit"]:
            queryset = queryset[: options["limit"]]
            self._log(f"Limiting to {options['limit']} books.")

        if options["dry_run"]:
            for book in queryset.iterator():
                self._log(f"  Missing ISBN: '{book.title}' by {book.author.name}")
            self._log(f"Dry run — {total} books need ISBN13. No API calls made.")
            return

        if not GOOGLE_BOOKS_API_KEY:
            self._warn("GOOGLE_BOOKS_API_KEY not set — Google Books fallback disabled.")

        updated = 0
        not_found = 0
        conflicts = 0

        with requests.Session() as session:
            session.headers.update({"User-Agent": "BibliotypeApp/1.0"})

            for book in queryset.iterator():
                # Try Open Library first
                isbn13 = self._search_open_library(session, book.title, book.author.name)
                source = "OL"

                # Fall back to Google Books
                if not isbn13:
                    isbn13 = self._search_google_books(session, book.title, book.author.name)
                    source = "GB"

                if not isbn13:
                    self._log(f"  Not found: '{book.title}' by {book.author.name}")
                    not_found += 1
                    time.sleep(1.2)
                    continue

                # Check for existing book with this ISBN before saving
                if Book.objects.filter(isbn13=isbn13).exists():
                    self._warn(f"  ISBN conflict: '{book.title}' -> {isbn13} (already taken)")
                    conflicts += 1
                    time.sleep(1.2)
                    continue

                book.isbn13 = isbn13
                try:
                    book.save(update_fields=["isbn13"])
                    self._log(f"  Updated ({source}): '{book.title}' -> {isbn13}")
                    updated += 1
                except IntegrityError:
                    self._warn(f"  ISBN conflict on save: '{book.title}' -> {isbn13}")
                    conflicts += 1

                time.sleep(1.2)

        self._log(f"Done. Updated: {updated}, Not found: {not_found}, Conflicts: {conflicts}")
