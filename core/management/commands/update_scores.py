# core/management/commands/update_scores.py

from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db.models import F

from core.models import Author, Book

from .score_config import SCORE_CONFIG


class Command(BaseCommand):
    help = "Calculates final scores for all Book entries and updates author popularity."

    def handle(self, *args, **options):
        self.stdout.write("🚀 Calculating final mainstream scores for all books...")

        for book in Book.objects.all():
            score_breakdown = defaultdict(int)
            total_score = 0

            for award in book.awards_won:
                score_breakdown[award] = SCORE_CONFIG.get(award, 0)
            for shortlist in book.shortlists:
                score_breakdown[shortlist] = SCORE_CONFIG.get(shortlist, 0)

            # --- NEW: Add score for canon lists ---
            if book.canon_lists:
                score_breakdown["CANON_LISTS"] = len(book.canon_lists) * SCORE_CONFIG["CANON_LIST"]

            if book.nyt_bestseller_weeks > 0:
                score_breakdown["NYT_BESTSELLER_WEEKS"] = (
                    book.nyt_bestseller_weeks * SCORE_CONFIG["NYT_BESTSELLER_WEEK"]
                )

            if book.ratings_count > 1000:  # Add a threshold
                score_breakdown["RATINGS_SCORE"] = min(book.ratings_count // 1000, 50)

            if book.average_rating and book.average_rating >= 4.1:
                score_breakdown["HIGH_RATING_BONUS"] = SCORE_CONFIG["HIGH_RATING_BONUS"]

            total_score = sum(score_breakdown.values())

            book.mainstream_score = total_score
            book.score_breakdown = dict(score_breakdown)
            book.save()

        self.stdout.write(self.style.SUCCESS("✅ All book scores updated."))
        self._update_author_popularity()

    def _update_author_popularity(self):
        self.stdout.write("👤 Calculating popular author scores...")
        Author.objects.all().update(popularity_score=0)
        for pop_book in Book.objects.all():
            author, _ = Author.objects.get_or_create(name=pop_book.author)
            Author.objects.filter(pk=author.pk).update(
                popularity_score=F("popularity_score") + pop_book.mainstream_score
            )
        self.stdout.write(self.style.SUCCESS("✅ Author scores updated."))
