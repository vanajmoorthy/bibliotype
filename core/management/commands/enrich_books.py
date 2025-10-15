import os
import re

import requests
from django.core.management.base import BaseCommand
from django.db.models import Q

from core.book_enrichment_service import enrich_book_from_apis
from core.models import Author, Book


class Command(BaseCommand):
    help = "Enriches Book entries with external APIs, with a specific limit for the Google Books API."

    # This limit now ONLY applies to Google Books
    GOOGLE_BOOKS_API_LIMIT = 950

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            help=f"Set a custom Google Books API request limit for this run (default: {self.GOOGLE_BOOKS_API_LIMIT}).",
        )
        parser.add_argument(
            "--process-all",
            action="store_true",
            help="Process all books, even those previously checked.",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.headers = {
            # It's good practice to set a custom User-Agent for API requests
            "User-Agent": "BibliotypeApp/1.0 (YourContactEmail@example.com)"
        }
        self.session = requests.Session()
        self.session.headers.update(self.headers)
        # Create separate counters for each API
        self.gb_api_calls = 0
        self.ol_api_calls = 0

    def handle(self, *args, **options):
        self.stdout.write("🚀 Starting enrichment of Book entries with external APIs...")

        gb_limit = options["limit"] or self.GOOGLE_BOOKS_API_LIMIT
        self.stdout.write(self.style.NOTICE(f"Google Books API request limit for this run is set to {gb_limit}."))

        if options["process_all"]:
            self.stdout.write(self.style.WARNING("--process-all flag set. Re-checking all books."))
            queryset = Book.objects.all()
        else:
            self.stdout.write(self.style.NOTICE("Processing books that have not been checked or are missing genres."))
            # A more robust query: get books that either haven't been checked OR still have no genres.
            queryset = Book.objects.filter(
                Q(google_books_last_checked__isnull=True) | Q(genres__isnull=True)
            ).distinct()

        total_books_to_process = queryset.count()
        if total_books_to_process == 0:
            self.stdout.write(self.style.SUCCESS("No books found that need enrichment. All done!"))
            return

        self.stdout.write(f"Found {total_books_to_process} books to process.")

        processed_books = 0
        for book in queryset.iterator():
            # The check now only applies to the Google Books counter
            if self.gb_api_calls >= gb_limit:
                self.stdout.write(
                    self.style.WARNING(f"\nGoogle Books API request limit of {gb_limit} reached. Stopping.")
                )
                break

            processed_books += 1
            self.stdout.write(
                f'  -> Processing: "{book.title}" ({processed_books}/{total_books_to_process}) | OL Calls: {self.ol_api_calls} | GB Calls: {self.gb_api_calls}'
            )

            # Unpack the new return values from the service
            updated_book, ol_calls, gb_calls = enrich_book_from_apis(book, self.session, slow_down=True)

            # Increment the separate counters
            self.ol_api_calls += ol_calls
            self.gb_api_calls += gb_calls

            # The polite delay is now correctly handled inside the service, so we don't need a sleep here.

        self.stdout.write(
            self.style.SUCCESS(
                f"\n✅ Finished enriching books. Processed {processed_books} books."
                f"\n   - Open Library Calls: {self.ol_api_calls}"
                f"\n   - Google Books Calls: {self.gb_api_calls}"
            )
        )
