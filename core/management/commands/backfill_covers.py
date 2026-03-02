import logging
import os
import re
import time

import requests
from django.core.management.base import BaseCommand

from core.models import Book

logger = logging.getLogger(__name__)

GOOGLE_BOOKS_API_KEY = os.getenv("GOOGLE_BOOKS_API_KEY")


class Command(BaseCommand):
    help = "Populate cover URLs for books. Fast mode uses ISBN (no API calls). Use --with-api for books missing ISBN."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be updated without saving.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            help="Max books to process.",
        )
        parser.add_argument(
            "--with-api",
            action="store_true",
            help="Also fetch covers for books without ISBN via API calls.",
        )

    def _log(self, msg):
        self.stdout.write(msg)
        logger.info(f"backfill_covers: {msg}")

    def _warn(self, msg):
        self.stdout.write(self.style.WARNING(msg))
        logger.warning(f"backfill_covers: {msg}")

    def _clean_title_for_api(self, title):
        clean = re.sub(r"[\(\[].*?[\)\]]", "", title)
        clean = clean.split(":")[0]
        return clean.strip()

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        limit = options.get("limit")
        with_api = options["with_api"]

        # --- Fast mode: set cover_url from ISBN for books that have ISBN but no cover ---
        isbn_qs = Book.objects.filter(cover_url__isnull=True, isbn13__isnull=False)
        fast_count = isbn_qs.count()
        self._log(f"Fast mode: {fast_count} books with ISBN but no cover_url.")

        if dry_run:
            self._log(f"Dry run — would update {fast_count} books from ISBN.")
        else:
            batch = []
            updated = 0
            for book in isbn_qs.iterator():
                if limit and updated >= limit:
                    break
                book.cover_url = f"https://covers.openlibrary.org/b/isbn/{book.isbn13}-M.jpg"
                batch.append(book)
                updated += 1

                if len(batch) >= 500:
                    Book.objects.bulk_update(batch, ["cover_url"])
                    batch = []

            if batch:
                Book.objects.bulk_update(batch, ["cover_url"])

            self._log(f"Fast mode complete. Updated {updated} books from ISBN.")

        # --- Full mode: API calls for remaining books without cover_url ---
        if not with_api:
            remaining = Book.objects.filter(cover_url__isnull=True).count()
            if remaining > 0:
                self._log(f"{remaining} books still without cover_url. Run with --with-api to fetch via API.")
            return

        api_qs = Book.objects.filter(cover_url__isnull=True).select_related("author")
        api_count = api_qs.count()
        self._log(f"API mode: {api_count} books without cover_url remaining.")

        if api_count == 0:
            self._log("All books have cover_url. Nothing to do.")
            return

        if dry_run:
            for book in api_qs.iterator():
                self._log(f"  Would fetch cover for: '{book.title}' by {book.author.name}")
            self._log(f"Dry run — would attempt API lookup for {api_count} books.")
            return

        if not GOOGLE_BOOKS_API_KEY:
            self._warn("GOOGLE_BOOKS_API_KEY not set — Google Books fallback disabled.")

        found = 0
        not_found = 0
        processed = 0

        with requests.Session() as session:
            session.headers.update({"User-Agent": "BibliotypeApp/1.0"})

            for book in api_qs.iterator():
                if limit and processed >= limit:
                    break

                cover_url = self._fetch_cover_from_ol(session, book)
                if not cover_url:
                    cover_url = self._fetch_cover_from_gb(session, book)

                if cover_url:
                    book.cover_url = cover_url
                    book.save(update_fields=["cover_url"])
                    self._log(f"  Found cover: '{book.title}' -> {cover_url[:80]}...")
                    found += 1
                else:
                    self._log(f"  No cover found: '{book.title}' by {book.author.name}")
                    not_found += 1

                processed += 1
                time.sleep(1.2)

        self._log(f"API mode complete. Found: {found}, Not found: {not_found}")

    def _fetch_cover_from_ol(self, session, book):
        """Search Open Library for cover_i. Returns cover URL or None."""
        search_url = "https://openlibrary.org/search.json"
        params = {
            "title": self._clean_title_for_api(book.title),
            "author": book.author.name,
            "limit": 1,
            "fields": "cover_i",
        }

        try:
            res = session.get(search_url, params=params, timeout=10)
            res.raise_for_status()
            data = res.json()
        except requests.RequestException as e:
            self._warn(f"  OL API error for '{book.title}': {e}")
            return None

        docs = data.get("docs", [])
        if docs and docs[0].get("cover_i"):
            cover_id = docs[0]["cover_i"]
            return f"https://covers.openlibrary.org/b/id/{cover_id}-M.jpg"

        return None

    def _fetch_cover_from_gb(self, session, book):
        """Search Google Books for thumbnail. Returns HTTPS URL or None."""
        if not GOOGLE_BOOKS_API_KEY:
            return None

        title_q = requests.utils.quote(book.title)
        author_q = requests.utils.quote(book.author.name)
        query = f"intitle:{title_q}+inauthor:{author_q}"
        url = f"https://www.googleapis.com/books/v1/volumes?q={query}&key={GOOGLE_BOOKS_API_KEY}"

        try:
            res = session.get(url, timeout=10)
            res.raise_for_status()
            data = res.json()
        except requests.RequestException as e:
            self._warn(f"  GB API error for '{book.title}': {e}")
            return None

        if data.get("totalItems", 0) == 0:
            return None

        volume_info = data["items"][0].get("volumeInfo", {})
        image_links = volume_info.get("imageLinks", {})
        if thumbnail := image_links.get("thumbnail"):
            return thumbnail.replace("http://", "https://")

        return None
