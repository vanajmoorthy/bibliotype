# In core/management/commands/review_publishers.py

from django.core.management.base import BaseCommand
from django.db.models import Count
from core.models import Publisher


class Command(BaseCommand):
    help = "Lists non-mainstream publishers ordered by popularity to help with admin review."

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("🔎 Finding popular, non-mainstream publishers to review..."))

        # Find all non-mainstream publishers, count their books, and order by that count
        publishers_to_review = (
            Publisher.objects.filter(is_mainstream=False)
            .annotate(book_count=Count("books"))
            .filter(book_count__gt=0)
            .order_by("-book_count")
        )

        if not publishers_to_review.exists():
            self.stdout.write(self.style.SUCCESS("✅ No new publishers need reviewing at this time."))
            return

        self.stdout.write(
            self.style.WARNING(
                "The following publishers are popular in your database but are not flagged as mainstream:"
            )
        )
        self.stdout.write("Consider reviewing them in the Django Admin and ticking 'is_mainstream' if appropriate.")

        for pub in publishers_to_review:
            self.stdout.write(f"  - {pub.name} ({pub.book_count} books)")
