import random
import logging
from collections import Counter

from django.core.management.base import BaseCommand
from django.contrib.auth.models import User

from core.models import UserProfile, UserBook
from core.dna_constants import CANONICAL_GENRE_MAP, NICHE_THRESHOLD, READER_TYPE_DESCRIPTIONS, compute_contrariness

logger = logging.getLogger(__name__)


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

            # Recalculate reader type using the shared scoring function.
            # NOTE: Raw CSV signals (Title for series detection, Read Count for rereads)
            # are unavailable here — only enriched Book data and genres are accessible.
            # Series Slayer and Comfort Rereader scores are carried forward from the
            # stored reader_type_scores since their signals require the original CSV.
            # Full accuracy restores on the user's next re-upload.
            import pandas as _pd
            from core.services.dna.reader_type import assign_reader_type as _assign_reader_type
            from core.services.genre_classification import canonicalize_genre_names as _canonicalize
            from core.dna_constants import MIN_WINNING_SCORE as _MIN_WIN, READER_TYPE_TIEBREAK_ORDER as _TIEBREAK

            rows = []
            book_genre_sets_regen = []
            enriched_data_regen = {}
            for ub in user_books:
                b = ub.book
                rows.append({
                    "Title": b.title or "",
                    "Number of Pages": b.page_count,
                    "Date Read": ub.date_read,
                    "Author": b.author.name if b.author_id else None,
                })
                # Canonicalize like recompute_reader_type_from_db does — stored Genre
                # names are raw and genre_share only matches canonical tokens.
                book_genre_sets_regen.append(_canonicalize([g.name for g in b.genres.all()]))
                if b.title:
                    enriched_data_regen[b.title] = {
                        "publish_year": b.publish_year,
                        "publisher": b.publisher,
                    }

            regen_df = _pd.DataFrame(rows) if rows else _pd.DataFrame(columns=["Title", "Number of Pages", "Date Read"])
            if not regen_df.empty and "Date Read" in regen_df.columns:
                regen_df["Date Read"] = _pd.to_datetime(regen_df["Date Read"], errors="coerce")

            new_reader_type, new_scores_counter = _assign_reader_type(regen_df, enriched_data_regen, book_genre_sets_regen)

            # Carry forward CSV-only signals from stored scores
            old_scores = profile.dna_data.get("reader_type_scores", {})
            scores = Counter(new_scores_counter)
            for carry_type in ("Series Slayer", "Comfort Rereader"):
                if carry_type in old_scores and carry_type not in scores:
                    scores[carry_type] = old_scores[carry_type]

            # Re-run winner selection with carry-forward scores included
            if scores and scores.most_common(1)[0][1] >= _MIN_WIN:
                top_score = scores.most_common(1)[0][1]
                tied = [t for t, s in scores.items() if s == top_score]

                def _tiebreak_key(t):
                    try:
                        return _TIEBREAK.index(t)
                    except ValueError:
                        return len(_TIEBREAK)
                new_reader_type = min(tied, key=_tiebreak_key)
            else:
                new_reader_type = "Eclectic Reader"

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
            new_contrariness_label, new_contrariness_color = compute_contrariness(new_avg_rating_diff)

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
            if profile.dna_data.get("unique_genres_count") != new_unique_genres_count:
                changes.append(f"unique_genres: {new_unique_genres_count}")
            if profile.dna_data.get("contrariness_label") != new_contrariness_label:
                changes.append(f"contrariness: {new_contrariness_label}")
            if profile.dna_data.get("niche_books_count") != niche_books_count:
                changes.append(f"niche_books: {niche_books_count}")
            if profile.dna_data.get("total_reviews_count") != total_reviews_count:
                changes.append(f"reviews: {total_reviews_count} ({positive_reviews_count}+/{negative_reviews_count}-)")

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
