import logging

from django.core.management.base import BaseCommand

from core.dna_constants import CANONICAL_GENRE_MAP, NICHE_THRESHOLD, compute_contrariness
from core.models import UserProfile, UserBook

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Backfill subtitle data (unique counts, contrariness stats, review counts, niche counts) "
        "into existing dna_data for users who generated DNA before these fields were added."
    )

    def _log(self, msg):
        self.stdout.write(msg)
        logger.info(f"backfill_subtitle_data: {msg}")

    def _warn(self, msg):
        self.stdout.write(self.style.WARNING(msg))
        logger.warning(f"backfill_subtitle_data: {msg}")

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would change without saving.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            help="Limit the number of profiles to process.",
        )
        parser.add_argument(
            "--username",
            type=str,
            help="Backfill for a single user by username.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Overwrite existing subtitle fields even if already present.",
        )

    def handle(self, *args, **options):
        profiles = UserProfile.objects.filter(dna_data__isnull=False).select_related("user")

        if options["username"]:
            profiles = profiles.filter(user__username=options["username"])

        if options["limit"]:
            profiles = profiles[: options["limit"]]

        profiles = list(profiles)

        if not profiles:
            self._log("No profiles with DNA data found.")
            return

        self._log(f"Found {len(profiles)} profiles to process.")
        updated = 0
        skipped = 0

        for profile in profiles:
            user = profile.user
            dna = profile.dna_data

            # Check if already backfilled (has any of the new fields)
            already_has = "contrariness_label" in dna and "unique_authors_count" in dna
            if already_has and not options["force"]:
                skipped += 1
                continue

            user_books = (
                UserBook.objects.filter(user=user)
                .select_related("book", "book__author", "book__publisher")
                .prefetch_related("book__genres")
            )

            if not user_books.exists():
                self._log(f"  {user.username}: no UserBook records, skipping.")
                skipped += 1
                continue

            # --- Unique authors count ---
            unique_authors = set()
            for ub in user_books:
                unique_authors.add(ub.book.author.name)
            unique_authors_count = len(unique_authors)

            # --- Unique genres count ---
            all_genres = []
            for ub in user_books:
                for genre in ub.book.genres.all():
                    all_genres.append(genre.name)
            mapped_genres = [CANONICAL_GENRE_MAP.get(g, g) for g in all_genres]
            unique_genres_count = len(set(mapped_genres))

            # --- Contrariness stats ---
            controversial_books_count = 0
            total_diff = 0.0
            for ub in user_books:
                if ub.user_rating and ub.user_rating > 0 and ub.book.average_rating:
                    controversial_books_count += 1
                    total_diff += abs(ub.user_rating - ub.book.average_rating)

            avg_rating_difference = round(total_diff / controversial_books_count, 2) if controversial_books_count > 0 else 0.0
            contrariness_label, contrariness_color = compute_contrariness(avg_rating_difference)

            # --- Review sentiment counts ---
            # We need VADER for sentiment, but reviews are stored in UserBook.user_review
            total_reviews_count = 0
            positive_reviews_count = 0
            negative_reviews_count = 0
            try:
                from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

                analyzer = SentimentIntensityAnalyzer()
                for ub in user_books:
                    review = ub.user_review
                    if review and len(review.strip()) > 15 and ub.user_rating and ub.user_rating > 0:
                        total_reviews_count += 1
                        sentiment = analyzer.polarity_scores(review)["compound"]
                        if sentiment > 0:
                            positive_reviews_count += 1
                        elif sentiment < 0:
                            negative_reviews_count += 1
            except ImportError:
                self._warn(f"  {user.username}: vaderSentiment not available, skipping review counts.")

            # --- Niche books count ---
            niche_books_count = sum(1 for ub in user_books if ub.book.global_read_count <= NICHE_THRESHOLD)

            changes = {
                "unique_authors_count": unique_authors_count,
                "unique_genres_count": unique_genres_count,
                "controversial_books_count": controversial_books_count,
                "avg_rating_difference": avg_rating_difference,
                "contrariness_label": contrariness_label,
                "contrariness_color": contrariness_color,
                "total_reviews_count": total_reviews_count,
                "positive_reviews_count": positive_reviews_count,
                "negative_reviews_count": negative_reviews_count,
                "niche_books_count": niche_books_count,
                "niche_threshold": NICHE_THRESHOLD,
            }

            self._log(
                f"  {user.username}: {unique_authors_count} authors, {unique_genres_count} genres, "
                f"contrariness={avg_rating_difference} ({contrariness_label}), "
                f"{total_reviews_count} reviews ({positive_reviews_count}+/{negative_reviews_count}-), "
                f"{niche_books_count} niche books"
            )

            if not options["dry_run"]:
                new_dna = dna.copy()
                new_dna.update(changes)
                profile.dna_data = new_dna
                profile.save(update_fields=["dna_data"])
                updated += 1

        if options["dry_run"]:
            self._warn(f"Dry run complete. Would update {len(profiles) - skipped} profiles. No changes saved.")
        else:
            self._log(f"Updated {updated} profiles, skipped {skipped}.")
