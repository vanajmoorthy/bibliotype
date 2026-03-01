import random
import logging
from collections import Counter

from django.core.management.base import BaseCommand
from django.contrib.auth.models import User

from core.models import UserProfile, UserBook
from core.dna_constants import CANONICAL_GENRE_MAP, READER_TYPE_DESCRIPTIONS

logger = logging.getLogger(__name__)

NICHE_THRESHOLD = 5

CONTRARINESS_SCALE = [
    (1.5, "Wildly contrarian", "bg-brand-pink"),
    (1.0, "Very contrarian", "bg-brand-orange"),
    (0.6, "Moderately contrarian", "bg-brand-yellow"),
    (0.3, "Mildly contrarian", "bg-brand-cyan"),
    (0.0, "Aligned with consensus", "bg-brand-green"),
]


def _compute_contrariness(avg_diff):
    for threshold, label, color in CONTRARINESS_SCALE:
        if avg_diff >= threshold:
            return label, color
    return "Aligned with consensus", "bg-brand-green"


class Command(BaseCommand):
    help = (
        "Regenerate genre-dependent DNA fields (top_genres, reader_type, mainstream_score, subtitle stats) "
        "for users from their current Book data. Use after enrichment backfills."
    )

    def _log(self, msg):
        self.stdout.write(msg)
        logger.info(f"regenerate_dna: {msg}")

    def _warn(self, msg):
        self.stdout.write(self.style.WARNING(msg))
        logger.warning(f"regenerate_dna: {msg}")

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
            help="Regenerate for a single user by username.",
        )
        parser.add_argument(
            "--with-recommendations",
            action="store_true",
            help="Also regenerate recommendations after DNA update.",
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

        self._log(f"Found {len(profiles)} profiles to regenerate.")
        updated = 0
        updated_profiles = []

        for profile in profiles:
            user = profile.user
            user_books = (
                UserBook.objects.filter(user=user)
                .select_related("book", "book__author", "book__publisher")
                .prefetch_related("book__genres")
            )

            if not user_books.exists():
                self._log(f"  {user.username}: no UserBook records, skipping.")
                continue

            # Collect current genres from enriched books
            all_genres = []
            for ub in user_books:
                for genre in ub.book.genres.all():
                    all_genres.append(genre.name)

            # Canonicalize genres
            mapped_genres = [CANONICAL_GENRE_MAP.get(g, g) for g in all_genres]
            new_top_genres = Counter(mapped_genres).most_common(10)
            # Convert to list of lists for JSON serialization
            new_top_genres_serializable = [[g, c] for g, c in new_top_genres]

            # Recalculate reader type scores (simplified — uses genre counts only)
            # Full assign_reader_type requires a DataFrame, but genre scores are the main thing affected
            genre_counts = Counter(mapped_genres)
            old_scores = profile.dna_data.get("reader_type_scores", {})
            # Rebuild genre-based scores on top of existing scores
            scores = Counter(old_scores)
            # Zero out genre-based scores before recalculating
            genre_types = [
                "Fantasy Fanatic",
                "Non-Fiction Ninja",
                "Philosophical Philomath",
                "Nature Nut Case",
                "Social Savant",
                "Self Help Scholar",
            ]
            for gt in genre_types:
                scores[gt] = 0

            scores["Fantasy Fanatic"] += genre_counts.get("fantasy", 0) + genre_counts.get("science fiction", 0)
            scores["Non-Fiction Ninja"] += genre_counts.get("non-fiction", 0)
            scores["Philosophical Philomath"] += genre_counts.get("philosophy", 0)
            scores["Nature Nut Case"] += genre_counts.get("nature", 0)
            scores["Social Savant"] += genre_counts.get("social science", 0)
            scores["Self Help Scholar"] += genre_counts.get("self-help", 0)

            new_reader_type = scores.most_common(1)[0][0] if scores else profile.dna_data.get("reader_type", "")
            new_top_types = [{"type": t, "score": s} for t, s in scores.most_common(3) if s > 0]

            # Recalculate mainstream score
            total = user_books.count()
            mainstream_count = sum(
                1
                for ub in user_books
                if ub.book.author.is_mainstream or (ub.book.publisher and ub.book.publisher.is_mainstream)
            )
            new_mainstream_score = round((mainstream_count / total) * 100) if total > 0 else 0

            # --- Subtitle fields ---
            unique_authors = set()
            for ub in user_books:
                unique_authors.add(ub.book.author.name)
            new_unique_authors_count = len(unique_authors)
            new_unique_genres_count = len(set(mapped_genres))

            controversial_books_count = 0
            total_diff = 0.0
            for ub in user_books:
                if ub.user_rating and ub.user_rating > 0 and ub.book.average_rating:
                    controversial_books_count += 1
                    total_diff += abs(ub.user_rating - ub.book.average_rating)
            new_avg_rating_diff = round(total_diff / controversial_books_count, 2) if controversial_books_count > 0 else 0.0
            new_contrariness_label, new_contrariness_color = _compute_contrariness(new_avg_rating_diff)

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

            niche_books_count = sum(1 for ub in user_books if ub.book.global_read_count <= NICHE_THRESHOLD)

            old_type = profile.dna_data.get("reader_type", "")
            old_genres = profile.dna_data.get("top_genres", [])
            old_mainstream = profile.dna_data.get("mainstream_score_percent", 0)

            changes = []
            if old_type != new_reader_type:
                changes.append(f"reader_type: '{old_type}' -> '{new_reader_type}'")
            if old_genres != new_top_genres_serializable:
                changes.append(f"top_genres: {len(old_genres)} -> {len(new_top_genres_serializable)} entries")
            if old_mainstream != new_mainstream_score:
                changes.append(f"mainstream: {old_mainstream}% -> {new_mainstream_score}%")
            if profile.dna_data.get("unique_authors_count") != new_unique_authors_count:
                changes.append(f"unique_authors: {new_unique_authors_count}")
            if profile.dna_data.get("contrariness_label") != new_contrariness_label:
                changes.append(f"contrariness: {new_contrariness_label}")

            if not changes:
                self._log(f"  {user.username}: no changes needed.")
                continue

            self._log(f"  {user.username}: {', '.join(changes)}")

            if not options["dry_run"]:
                dna = profile.dna_data.copy()
                dna["top_genres"] = new_top_genres_serializable
                dna["reader_type"] = new_reader_type
                dna["reader_type_scores"] = dict(scores)
                dna["top_reader_types"] = new_top_types
                dna["reader_type_explanation"] = random.choice(
                    READER_TYPE_DESCRIPTIONS.get(new_reader_type, [dna.get("reader_type_explanation", "")])
                )
                dna["mainstream_score_percent"] = new_mainstream_score
                # Subtitle fields
                dna["unique_authors_count"] = new_unique_authors_count
                dna["unique_genres_count"] = new_unique_genres_count
                dna["controversial_books_count"] = controversial_books_count
                dna["avg_rating_difference"] = new_avg_rating_diff
                dna["contrariness_label"] = new_contrariness_label
                dna["contrariness_color"] = new_contrariness_color
                dna["total_reviews_count"] = total_reviews_count
                dna["positive_reviews_count"] = positive_reviews_count
                dna["negative_reviews_count"] = negative_reviews_count
                dna["niche_books_count"] = niche_books_count
                dna["niche_threshold"] = NICHE_THRESHOLD
                profile.dna_data = dna
                profile.reader_type = new_reader_type
                profile.save(update_fields=["dna_data", "reader_type"])
                updated += 1
                updated_profiles.append(profile)

        if options["dry_run"]:
            self._warn("Dry run complete. No changes saved.")
        else:
            self._log(f"Updated {updated} profiles.")

        if options["with_recommendations"] and not options["dry_run"] and updated_profiles:
            from core.tasks import generate_recommendations_task

            for profile in updated_profiles:
                generate_recommendations_task.delay(profile.user.id)
                self._log(f"  Dispatched recommendations for {profile.user.username}")
            self._log(f"Dispatched recommendation generation for {len(updated_profiles)} users.")
