import random
import logging
from collections import Counter

from django.core.management.base import BaseCommand
from django.contrib.auth.models import User

from core.models import UserProfile, UserBook
from core.dna_constants import CANONICAL_GENRE_MAP, READER_TYPE_DESCRIPTIONS

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Regenerate genre-dependent DNA fields (top_genres, reader_type, mainstream_score) "
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
                "Fantasy Fanatic", "Non-Fiction Ninja", "Philosophical Philomath",
                "Nature Nut Case", "Social Savant", "Self Help Scholar",
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
                1 for ub in user_books
                if ub.book.author.is_mainstream or (ub.book.publisher and ub.book.publisher.is_mainstream)
            )
            new_mainstream_score = round((mainstream_count / total) * 100) if total > 0 else 0

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
                profile.dna_data = dna
                profile.reader_type = new_reader_type
                profile.save(update_fields=["dna_data", "reader_type"])
                updated += 1

        if options["dry_run"]:
            self._warn("Dry run complete. No changes saved.")
        else:
            self._log(f"Updated {updated} profiles.")
