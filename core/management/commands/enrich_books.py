import logging

import requests
from django.core.management.base import BaseCommand
from django.db.models import Q

from core.book_enrichment_service import enrich_book_from_apis
from core.models import Book
from core.tasks import enrich_book_task

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Enrich books missing metadata. Default: async Celery tasks. Use --sync for direct API calls."

    GOOGLE_BOOKS_API_LIMIT = 950

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Show counts without processing")
        parser.add_argument("--limit", type=int, help="Limit number of books to process")
        parser.add_argument("--sync", action="store_true", help="Run synchronously via APIs instead of Celery")
        parser.add_argument("--process-all", action="store_true", help="Re-check all books, not just unenriched")
        parser.add_argument(
            "--google-books-limit",
            type=int,
            default=self.GOOGLE_BOOKS_API_LIMIT,
            help=f"Max Google Books API calls, sync only (default: {self.GOOGLE_BOOKS_API_LIMIT})",
        )

    def _log(self, msg):
        self.stdout.write(msg)
        logger.info(f"enrich_books: {msg}")

    def _warn(self, msg):
        self.stdout.write(self.style.WARNING(msg))
        logger.warning(f"enrich_books: {msg}")

    def _get_queryset(self, process_all):
        if process_all:
            self._warn("--process-all flag set. Re-checking all books.")
            return Book.objects.all()
        return Book.objects.filter(
            Q(publish_year__isnull=True) | Q(genres__isnull=True) | Q(google_books_last_checked__isnull=True)
        ).distinct()

    def _show_stats(self, queryset):
        missing_year = Book.objects.filter(publish_year__isnull=True).count()
        missing_genres = Book.objects.filter(genres__isnull=True).count()
        missing_gb = Book.objects.filter(google_books_last_checked__isnull=True).count()
        self._log(f"Books missing publish_year: {missing_year}")
        self._log(f"Books missing genres: {missing_genres}")
        self._log(f"Books missing Google Books check: {missing_gb}")
        self._log(f"Total unique books needing enrichment: {queryset.count()}")

    def handle(self, *args, **options):
        queryset = self._get_queryset(options["process_all"])

        if options["limit"]:
            queryset = queryset[: options["limit"]]

        total = queryset.count() if not options["limit"] else min(options["limit"], queryset.count())
        if total == 0:
            self._log("No books found that need enrichment. All done!")
            return

        self._show_stats(queryset)

        if options["dry_run"]:
            self._log("Dry run — no tasks dispatched.")
            return

        if options["sync"]:
            self._sync_enrich(queryset, options["google_books_limit"])
        else:
            self._async_enrich(queryset)

    def _async_enrich(self, queryset):
        dispatched = 0
        for book in queryset.iterator():
            enrich_book_task.delay(book.pk)
            dispatched += 1
        self._log(f"Dispatched {dispatched} enrichment tasks to Celery.")

    def _sync_enrich(self, queryset, gb_limit):
        session = requests.Session()
        session.headers.update({"User-Agent": "BibliotypeApp/1.0"})
        gb_calls = 0
        ol_calls = 0
        processed = 0

        for book in queryset.iterator():
            if gb_calls >= gb_limit:
                self._warn(f"Google Books API request limit of {gb_limit} reached. Stopping.")
                break

            processed += 1
            self._log(f'  -> Processing: "{book.title}" ({processed}) | OL: {ol_calls} | GB: {gb_calls}')

            _, ol, gb = enrich_book_from_apis(book, session, slow_down=True)
            ol_calls += ol
            gb_calls += gb

        self._log(f"Finished. Processed {processed} books. OL calls: {ol_calls}, GB calls: {gb_calls}")
