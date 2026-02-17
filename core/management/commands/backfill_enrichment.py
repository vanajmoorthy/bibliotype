from django.core.management.base import BaseCommand
from django.db.models import Q

from core.models import Book
from core.tasks import enrich_book_task


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

    def handle(self, *args, **options):
        queryset = Book.objects.filter(
            Q(publish_year__isnull=True) | Q(genres__isnull=True) | Q(google_books_last_checked__isnull=True)
        ).distinct()

        total = queryset.count()

        if total == 0:
            self.stdout.write(self.style.SUCCESS("All books are already enriched. Nothing to do."))
            return

        # Show breakdown
        missing_year = Book.objects.filter(publish_year__isnull=True).count()
        missing_genres = Book.objects.filter(genres__isnull=True).count()
        missing_gb = Book.objects.filter(google_books_last_checked__isnull=True).count()

        self.stdout.write(f"Books missing publish_year: {missing_year}")
        self.stdout.write(f"Books missing genres: {missing_genres}")
        self.stdout.write(f"Books missing Google Books check: {missing_gb}")
        self.stdout.write(f"Total unique books needing enrichment: {total}")

        if options["limit"]:
            queryset = queryset[: options["limit"]]
            self.stdout.write(self.style.NOTICE(f"Limiting to {options['limit']} books."))

        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("Dry run — no tasks dispatched."))
            return

        dispatched = 0
        for book in queryset.iterator():
            enrich_book_task.delay(book.pk)
            dispatched += 1

        self.stdout.write(self.style.SUCCESS(f"Dispatched {dispatched} enrichment tasks to Celery."))
