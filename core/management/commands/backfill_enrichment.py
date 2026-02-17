import logging

from django.core.management.base import BaseCommand
from django.db.models import Q

from core.models import Book
from core.tasks import enrich_book_task

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Dispatch background enrichment tasks for books missing publish_year, genres, or API check data."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show how many books would be queued without dispatching tasks.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            help="Limit the number of books to dispatch tasks for.",
        )

    def _log(self, msg):
        self.stdout.write(msg)
        logger.info(f"backfill_enrichment: {msg}")

    def handle(self, *args, **options):
        queryset = Book.objects.filter(
            Q(publish_year__isnull=True) | Q(genres__isnull=True) | Q(google_books_last_checked__isnull=True)
        ).distinct()

        total = queryset.count()

        if total == 0:
            self._log("All books are already enriched. Nothing to do.")
            return

        # Show breakdown
        missing_year = Book.objects.filter(publish_year__isnull=True).count()
        missing_genres = Book.objects.filter(genres__isnull=True).count()
        missing_gb = Book.objects.filter(google_books_last_checked__isnull=True).count()

        self._log(f"Books missing publish_year: {missing_year}")
        self._log(f"Books missing genres: {missing_genres}")
        self._log(f"Books missing Google Books check: {missing_gb}")
        self._log(f"Total unique books needing enrichment: {total}")

        if options["limit"]:
            queryset = queryset[: options["limit"]]
            self._log(f"Limiting to {options['limit']} books.")

        if options["dry_run"]:
            self._log("Dry run — no tasks dispatched.")
            return

        dispatched = 0
        for book in queryset.iterator():
            enrich_book_task.delay(book.pk)
            dispatched += 1

        self._log(f"Dispatched {dispatched} enrichment tasks to Celery.")
