import logging

import pandas as pd
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from ...models import Author
from ..top_books_service import MIN_REVIEW_LENGTH_FOR_SENTIMENT, compute_book_score

logger = logging.getLogger(__name__)


def _save_dna_to_profile(profile, dna_data):
    profile.dna_data = dna_data
    profile.reader_type = dna_data.get("reader_type")
    profile.total_books_read = dna_data.get("user_stats", {}).get("total_books_read")
    profile.reading_vibe = dna_data.get("reading_vibe")
    profile.vibe_data_hash = dna_data.get("vibe_data_hash")

    # Clear the pending task ID since we've completed the regeneration
    profile.pending_dna_task_id = None

    # Clear old recommendations - they'll be regenerated asynchronously
    profile.recommendations_data = None
    profile.recommendations_generated_at = None

    try:
        # Explicitly save all fields including pending_dna_task_id
        profile.save(
            update_fields=[
                "dna_data",
                "reader_type",
                "total_books_read",
                "reading_vibe",
                "vibe_data_hash",
                "pending_dna_task_id",
                "recommendations_data",
                "recommendations_generated_at",
            ]
        )

        # Invalidate stale caches for this user
        from ...cache_utils import safe_cache_add, safe_cache_delete

        safe_cache_delete(f"similar_users_{profile.user.id}")
        safe_cache_delete(f"user_recommendations_{profile.user.id}")

        # Sentinel-guard the dispatch (same guard as display_dna_view) so a
        # dashboard poll landing in the window before the task picks up can't
        # double-dispatch. The task's finally block clears the sentinel.
        if safe_cache_add(f"recs_dispatching_{profile.user.id}", 1, timeout=300):
            # Lazy import avoids the circular import between core.tasks and
            # the dna package (core.tasks imports calculate_full_dna).
            from ...tasks import generate_recommendations_task

            generate_recommendations_task.delay(profile.user.id)
            logger.info(f"Dispatched recommendations task for user {profile.user.username}")
        else:
            logger.info(
                f"Skipped duplicate recommendations dispatch for user {profile.user.username} (sentinel held)"
            )

    except Exception as e:
        logger.error(f"Error saving profile for user {profile.user.username}: {e}", exc_info=True)
        raise


def save_anonymous_session_data(session_key, dna_data, user_book_objects, read_df):
    """Save anonymous user data to temporary session storage"""
    from datetime import timedelta

    from django.utils import timezone

    from ...models import AnonymousUserSession

    # Extract books and ratings
    books_data = [book.id for book in user_book_objects if book]
    book_ratings = {}  # Store ratings for rating correlation

    # Calculate top books for anonymous users based on ratings and reviews
    book_scores = []
    analyzer = SentimentIntensityAnalyzer()

    for idx, row_dict in enumerate(read_df.to_dict("records")):
        if idx < len(user_book_objects) and user_book_objects[idx]:
            book = user_book_objects[idx]

            rating_int = None
            rating = row_dict.get("My Rating")
            if pd.notna(rating) and rating > 0:
                try:
                    rating_int = int(rating)
                    book_ratings[book.id] = rating_int  # Store rating for correlation
                except (ValueError, TypeError):
                    rating_int = None

            sentiment = None
            review = str(row_dict.get("My Review", "")).strip()
            if review and len(review) > MIN_REVIEW_LENGTH_FOR_SENTIMENT:
                sentiment = analyzer.polarity_scores(review)["compound"]

            book_scores.append((book.id, compute_book_score(rating_int, sentiment)))

    book_scores.sort(key=lambda x: x[1], reverse=True)
    top_books_data = [book_id for book_id, score in book_scores[:5]]

    # Extract distributions from DNA
    genre_dist = {}
    for genre, count in dna_data.get("top_genres", []):
        genre_dist[genre] = count

    author_dist = {}
    for author, count in dna_data.get("top_authors", [])[:20]:
        normalized = Author._normalize(author)
        author_dist[normalized] = count

    # Save or update session
    AnonymousUserSession.objects.update_or_create(
        session_key=session_key,
        defaults={
            "dna_data": dna_data,
            "books_data": books_data,
            "top_books_data": top_books_data,
            "genre_distribution": genre_dist,
            "author_distribution": author_dist,
            "book_ratings": book_ratings,  # Store ratings for correlation
            "expires_at": timezone.now() + timedelta(days=7),
        },
    )
