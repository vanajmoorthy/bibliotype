import logging
import re
import time

import requests
from django.core.management.base import BaseCommand
from django.db import IntegrityError

from core.models import Book

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Backfill ISBN13 for books missing it by querying Open Library search API."

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

    def _clean_title_for_api(self, title):
        clean_title = re.sub(r"[\(\[].*?[\)\]]", "", title)
        clean_title = clean_title.split(":")[0]
        return clean_title.strip()

    def _find_isbn13(self, isbn_list):
        """Pick the first 13-digit ISBN from the list."""
        for isbn in isbn_list:
            cleaned = isbn.strip()
            if len(cleaned) == 13 and cleaned.isdigit():
                return cleaned
        return None

    def handle(self, *args, **options):
        queryset = Book.objects.filter(isbn13__isnull=True).select_related("author")

        total = queryset.count()
        if total == 0:
            self.stdout.write(self.style.SUCCESS("All books already have ISBN13. Nothing to do."))
            return

        self.stdout.write(f"Found {total} books missing ISBN13.")
        logger.info(f"backfill_isbn: Found {total} books missing ISBN13.")

        if options["limit"]:
            queryset = queryset[: options["limit"]]
            self.stdout.write(self.style.NOTICE(f"Limiting to {options['limit']} books."))

        if options["dry_run"]:
            for book in queryset.iterator():
                self.stdout.write(f"  Missing ISBN: '{book.title}' by {book.author.name}")
            self.stdout.write(self.style.WARNING(f"Dry run — {total} books need ISBN13. No API calls made."))
            return

        updated = 0
        skipped = 0
        not_found = 0
        conflicts = 0

        with requests.Session() as session:
            session.headers.update({"User-Agent": "BibliotypeApp/1.0"})

            for book in queryset.iterator():
                clean_title = self._clean_title_for_api(book.title)
                search_url = "https://openlibrary.org/search.json"
                params = {
                    "title": clean_title,
                    "author": book.author.name,
                    "limit": 1,
                    "fields": "isbn,title,author_name",
                }

                try:
                    res = session.get(search_url, params=params, timeout=10)
                    res.raise_for_status()
                    data = res.json()
                except requests.RequestException as e:
                    msg = f"API error for '{book.title}': {e}"
                    self.stdout.write(self.style.WARNING(f"  {msg}"))
                    logger.warning(f"backfill_isbn: {msg}")
                    skipped += 1
                    time.sleep(1.2)
                    continue

                docs = data.get("docs", [])
                if not docs or "isbn" not in docs[0]:
                    msg = f"No ISBN found: '{book.title}'"
                    self.stdout.write(f"  {msg}")
                    logger.info(f"backfill_isbn: {msg}")
                    not_found += 1
                    time.sleep(1.2)
                    continue

                isbn13 = self._find_isbn13(docs[0]["isbn"])
                if not isbn13:
                    msg = f"No ISBN-13 in results: '{book.title}'"
                    self.stdout.write(f"  {msg}")
                    logger.info(f"backfill_isbn: {msg}")
                    not_found += 1
                    time.sleep(1.2)
                    continue

                # Check for existing book with this ISBN before saving
                if Book.objects.filter(isbn13=isbn13).exists():
                    msg = f"ISBN conflict: '{book.title}' -> {isbn13} (already taken)"
                    self.stdout.write(self.style.WARNING(f"  {msg}"))
                    logger.warning(f"backfill_isbn: {msg}")
                    conflicts += 1
                    time.sleep(1.2)
                    continue

                book.isbn13 = isbn13
                try:
                    book.save(update_fields=["isbn13"])
                    msg = f"Updated: '{book.title}' -> {isbn13}"
                    self.stdout.write(self.style.SUCCESS(f"  {msg}"))
                    logger.info(f"backfill_isbn: {msg}")
                    updated += 1
                except IntegrityError:
                    msg = f"ISBN conflict on save: '{book.title}' -> {isbn13}"
                    self.stdout.write(self.style.WARNING(f"  {msg}"))
                    logger.warning(f"backfill_isbn: {msg}")
                    conflicts += 1

                time.sleep(1.2)

        summary = f"Done. Updated: {updated}, Not found: {not_found}, Conflicts: {conflicts}, API errors: {skipped}"
        self.stdout.write("")
        self.stdout.write(f"Updated: {updated}")
        self.stdout.write(f"Not found: {not_found}")
        self.stdout.write(f"Conflicts: {conflicts}")
        self.stdout.write(f"API errors: {skipped}")
        self.stdout.write(self.style.SUCCESS(f"Done. {updated} books updated with ISBN13."))
        logger.info(f"backfill_isbn: {summary}")
