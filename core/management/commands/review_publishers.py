import logging

from django.core.management.base import BaseCommand
from django.db.models import Count

from core.models import Publisher

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Lists non-mainstream publishers ordered by popularity to help with admin review."

    def _log(self, msg):
        self.stdout.write(msg)
        logger.info(f"review_publishers: {msg}")

    def _warn(self, msg):
        self.stdout.write(self.style.WARNING(msg))
        logger.warning(f"review_publishers: {msg}")

    def handle(self, *args, **options):
        self._log("Finding popular, non-mainstream publishers to review...")

        publishers_to_review = (
            Publisher.objects.filter(is_mainstream=False)
            .annotate(book_count=Count("books"))
            .filter(book_count__gt=0)
            .order_by("-book_count")
        )

        if not publishers_to_review.exists():
            self._log("No new publishers need reviewing at this time.")
            return

        self._warn("The following publishers are popular but not flagged as mainstream:")
        self._log("Consider reviewing them in the Django Admin and ticking 'is_mainstream' if appropriate.")

        for pub in publishers_to_review:
            self._log(f"  - {pub.name} ({pub.book_count} books)")
