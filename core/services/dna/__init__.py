"""DNA package: submodule helpers re-exported here for stable `core.services.dna.*` import paths.

`calculate_full_dna` (the orchestrator) lives in this module; CSV normalization, currently-reading,
book sync, stats, the reading vibe, reader-type scoring, persistence, and small utilities live in
the sibling submodules. Patch a name where it is looked up: e.g. `core.services.dna.vibe.generate_vibe_with_llm`.
"""

import hashlib
import logging
import random
from collections import Counter
from io import StringIO

import pandas as pd

from ...dna_constants import (  # noqa: F401 — STORYGRAPH_TAG_TO_GENRE re-exported for stable import paths
    ENRICHMENT_BULK_QUEUE,
    ENRICHMENT_QUEUE,
    GLOBAL_AVERAGES,
    NICHE_THRESHOLD,
    READER_TYPE_DESCRIPTIONS,
    STORYGRAPH_TAG_TO_GENRE,
)
from ..anonymization_service import _key_hash
from ..genre_classification import (
    canonicalize_genre_names,
    count_fiction_nonfiction,
    parse_shelf_signals,
    resolve_genre_labels,
)
from ..top_books_service import calculate_and_store_top_books
from .book_sync import backfill_from_db, sync_read_books, sync_read_counts_and_user_books
from .csv_parser import (  # noqa: F401 — re-exported for stable import paths
    STORYGRAPH_TO_GOODREADS,
    CSVValidationError,
    _coerce_columns,
    _detect_and_normalize_csv,
)
from .currently_reading import extract_currently_reading, sync_currently_reading_books
from .enrichment_budget import INLINE_ENRICHMENT_BUDGET_SECONDS  # noqa: F401 — re-exported for stable import paths
from .persistence import (  # noqa: F401 — re-exported for stable import paths
    _save_dna_to_profile,
    save_anonymous_session_data,
)
from .reader_type import (  # noqa: F401 — re-exported for stable import paths
    assign_reader_type,
    compute_books_per_year,
    compute_reread_count,
)
from .stats import (
    compute_base_stats,
    compute_book_extremes,
    compute_community_stats,
    compute_mainstream_score,
    compute_mood_pace,
    compute_rating_stats,
    compute_review_stats,
)
from .types import ReadingDNA
from .utils import (  # noqa: F401 — re-exported for stable import paths
    _build_cover_url,
    _cover_initial,
    _isbn_to_isbn13,
    _sanitize_review_text,
)
from .vibe import attach_reading_vibe

logger = logging.getLogger(__name__)


def calculate_full_dna(
    csv_file_content: str, user=None, session_key=None, progress_cb=None, bulk_enrichment=False, task_id=None
) -> ReadingDNA:
    """Run the full DNA pipeline for one CSV.

    bulk_enrichment=True (seeding runs, backfills) routes every enrichment
    dispatch to ENRICHMENT_BULK_QUEUE *and* skips inline enrichment entirely,
    so all external API traffic from bulk runs flows through the rate-limited
    Celery task instead of hammering Open Library/Google Books in-process.
    """
    # Routing for the three async enrichment dispatch sites below. Interactive
    # enrichment gets its own queue so a big upload's per-book fan-out can't sit
    # FIFO ahead of other users' DNA tasks on "celery".
    dispatch_opts = {"queue": ENRICHMENT_BULK_QUEUE if bulk_enrichment else ENRICHMENT_QUEUE}
    try:
        df = pd.read_csv(StringIO(csv_file_content))
        df, csv_source = _detect_and_normalize_csv(df)
        logger.info(f"CSV source detected: {csv_source}")
        read_df = df[df["Exclusive Shelf"] == "read"].copy()
        # Re-reads first: without a "Read Count" column they show up as repeated titles.
        reader_type_reread_count = compute_reread_count(read_df)
        # A book listed on several rows still counts once in the reader's own stats.
        dedupe_key = (
            read_df["Title"].astype(str).str.strip().str.lower()
            + "|"
            + read_df["Author"].astype(str).str.strip().str.lower()
        )
        read_df = read_df[~dedupe_key.duplicated()]

        # --- Extract currently-reading and custom shelf data ---
        currently_reading_books, currently_reading_count, custom_shelf_count = extract_currently_reading(df)

        total_books = int(len(read_df))
        if progress_cb:
            progress_cb(0, total_books, "Parsing your library")

        if read_df.empty:
            raise CSVValidationError(
                'We couldn\'t find any books on your "read" shelf. Mark some books as read and export again.'
            )

        book_fingerprint_list = sorted([f"{row['Title']}{row['Author']}" for _, row in read_df.iterrows()])
        fingerprint_string = "".join(book_fingerprint_list)
        new_data_hash = hashlib.sha256(fingerprint_string.encode()).hexdigest()

        logger.info(f"Found {len(read_df)} books marked as 'read' for statistical analysis")
        for temp_df in [df, read_df]:
            _coerce_columns(temp_df)

        logger.info("Syncing book data with the database...")

        (
            results,
            book_pks_by_idx,
            already_counted_book_ids,
            previous_anon_session,
            upload_user_id,
            upload_nonce,
        ) = sync_read_books(
            read_df, csv_source, user, session_key, progress_cb, total_books, bulk_enrichment, dispatch_opts
        )

        all_resolved_genres, user_book_objects = [], []
        # Goodreads `Bookshelves` (exact column name — NOT "Bookshelves with
        # positions", whose "(#N)" suffixes would break matching) carries
        # user-curated shelf names used as weak fiction/nonfiction tiebreakers.
        # StoryGraph exports have no such column, so this is Goodreads-only.
        has_shelf_column = "Bookshelves" in read_df.columns
        book_genre_sets = []
        resolved_genre_sets = []
        shelf_signal_list = []
        for book, genres, original_row in results:
            if book:
                user_book_objects.append(book)
                canonical_set = canonicalize_genre_names(genres)
                book_genre_sets.append(canonical_set)
                raw_shelves = original_row.get("Bookshelves") if has_shelf_column else None
                if raw_shelves is None or pd.isna(raw_shelves):
                    raw_shelves = ""
                shelf_fiction, shelf_nonfiction, shelf_genres = parse_shelf_signals(raw_shelves)
                shelf_signal_list.append((shelf_fiction, shelf_nonfiction, shelf_genres))
                # Axis-resolved genre labels: when this book classifies as
                # nonfiction (the same call the split below makes), ambiguous
                # fiction-default names swap to their nonfiction variants so
                # top_genres and reader types agree with the split about the
                # same book. Occurrence multiplicity is preserved for counting.
                resolved_list = resolve_genre_labels(
                    genres,
                    shelf_fiction=shelf_fiction,
                    shelf_nonfiction=shelf_nonfiction,
                    shelf_genres=shelf_genres,
                )
                all_resolved_genres.extend(resolved_list)
                resolved_genre_sets.append(set(resolved_list))

        # Context-dependent fiction/nonfiction classification. Books with no
        # classifiable genres are tracked in defaulted_count — never fiction.
        # Shelf signals only decide when API genres give no clear signal.
        fiction_count, nonfiction_count, defaulted_count = count_fiction_nonfiction(book_genre_sets, shelf_signal_list)

        # Persist sparse per-book shelf signals (keyed by Book id) so the
        # poll-time recompute in core/views/_enrichment_stats.py can reproduce this
        # shelf-informed split instead of degrading to API-genre-only
        # classification. Only books that actually carried a signal are stored.
        shelf_signals_map = {}
        for signal_book, (shelf_fic, shelf_nonfic, shelf_genres) in zip(user_book_objects, shelf_signal_list):
            if shelf_fic or shelf_nonfic or shelf_genres:
                shelf_signals_map[str(signal_book.id)] = [shelf_fic, shelf_nonfic, sorted(shelf_genres)]

        # Sync currently-reading books to DB (Book/Author only, no UserBook, no global_read_count)
        if currently_reading_books:
            sync_currently_reading_books(currently_reading_books, upload_user_id, upload_nonce, dispatch_opts)

        sync_read_counts_and_user_books(user, results, already_counted_book_ids)

        # Fill blank pages/year/rating from the shared Book table for every upload, so the
        # stats agree with Book Extremes (which reads Book.page_count) instead of going "–".
        if book_pks_by_idx:
            user_book_objects = backfill_from_db(read_df, book_pks_by_idx, user_book_objects)

        enriched_data_for_scoring = {
            book.title: {
                "publish_year": book.publish_year,
                "publisher": book.publisher,
            }
            for book in user_book_objects
            if book.title
        }

        reader_type_books_per_year = compute_books_per_year(read_df)
        reader_type, reader_type_scores = assign_reader_type(
            read_df,
            enriched_data_for_scoring,
            resolved_genre_sets,
            reread_count_override=reader_type_reread_count,
            books_per_year_override=reader_type_books_per_year,
        )
        explanation = random.choice(READER_TYPE_DESCRIPTIONS.get(reader_type, [""]))
        top_types_list = [{"type": t, "score": s} for t, s in reader_type_scores.most_common(3) if s > 0]
        top_genres = Counter(all_resolved_genres).most_common(10)

        if progress_cb:
            progress_cb(total_books, total_books, "Crunching stats")
        logger.info("Calculating base user statistics...")

        user_base_stats, stats_by_year_list = compute_base_stats(read_df)
        percentiles, community_averages = compute_community_stats(user, previous_anon_session, user_base_stats)
        most_niche_book, niche_books_count, longest_book, shortest_book, page_difference = compute_book_extremes(
            user_book_objects
        )

        top_authors = list(read_df["Author"].value_counts().head(10).items())
        unique_authors_count = int(read_df["Author"].nunique())
        unique_genres_count = len(set(all_resolved_genres))

        (
            average_rating_overall,
            ratings_dist,
            top_controversial_list,
            controversial_books_count,
            avg_rating_difference,
            contrariness_label,
            contrariness_color,
        ) = compute_rating_stats(read_df)
        (
            most_positive_review,
            most_negative_review,
            total_reviews_count,
            positive_reviews_count,
            negative_reviews_count,
        ) = compute_review_stats(df)
        mainstream_score = compute_mainstream_score(user_book_objects)

        # comparative_text is computed at render time by _enrich_dna_for_display
        # in core/views.py (single source of truth — values would be overwritten
        # anyway, so don't bother computing them here).

        # StoryGraph mood and pace distributions (empty for Goodreads)
        mood_distribution, pace_distribution = compute_mood_pace(read_df)

        dna: ReadingDNA = {
            "user_stats": user_base_stats,
            "bibliotype_percentiles": percentiles,
            "global_averages": GLOBAL_AVERAGES,
            "community_averages": community_averages,
            "most_niche_book": most_niche_book,
            "reader_type": reader_type,
            "reader_type_explanation": explanation,
            "top_reader_types": top_types_list,
            "reader_type_scores": dict(reader_type_scores),
            "reader_type_scores_version": 3,
            "reader_type_csv_context": {
                "reread_count": reader_type_reread_count,
                "books_per_year_avg": reader_type_books_per_year,
            },
            "top_genres": top_genres,
            "top_authors": top_authors,
            "average_rating_overall": average_rating_overall,
            "ratings_distribution": ratings_dist,
            "top_controversial_books": top_controversial_list,
            "controversial_books_count": controversial_books_count,
            "avg_rating_difference": avg_rating_difference,
            "contrariness_label": contrariness_label,
            "contrariness_color": contrariness_color,
            "most_positive_review": most_positive_review,
            "most_negative_review": most_negative_review,
            "total_reviews_count": total_reviews_count,
            "positive_reviews_count": positive_reviews_count,
            "negative_reviews_count": negative_reviews_count,
            "stats_by_year": stats_by_year_list,
            "mainstream_score_percent": mainstream_score,
            "unique_authors_count": unique_authors_count,
            "unique_genres_count": unique_genres_count,
            "niche_books_count": niche_books_count,
            "niche_threshold": NICHE_THRESHOLD,
            "currently_reading_books": currently_reading_books,
            "currently_reading_count": currently_reading_count,
            "custom_shelf_count": custom_shelf_count,
            "fiction_nonfiction_split": (
                {
                    "fiction_count": fiction_count,
                    "nonfiction_count": nonfiction_count,
                    "defaulted_count": defaulted_count,
                }
                if (fiction_count + nonfiction_count) > 0
                else None
            ),
            "shelf_signals": shelf_signals_map,
            # Book ids belonging to THIS upload cohort. Enrichment-progress
            # scoping (core/views/_enrichment_stats.py:_compute_enrichment_progress) uses
            # this so a re-upload that only adds a few new books doesn't read as
            # "complete" just because the user's previously-enriched books
            # already have google_books_last_checked set. Legacy profiles without
            # this key fall back to counting all of the user's books.
            "enrichment_cohort_ids": [b.id for b in user_book_objects],
            "longest_book": longest_book,
            "shortest_book": shortest_book,
            "page_difference": page_difference,
            "csv_source": csv_source,
            "mood_distribution": mood_distribution,
            "pace_distribution": pace_distribution,
        }
        logger.debug(f"DNA data generated")

        attach_reading_vibe(dna, user, new_data_hash)

        def clean_dict(d):
            if not isinstance(d, dict):
                return d
            return {k: v for k, v in d.items() if pd.notna(v)}

        dna["top_controversial_books"] = [clean_dict(b) for b in dna.get("top_controversial_books", [])]
        dna["most_positive_review"] = clean_dict(dna.get("most_positive_review"))
        dna["most_negative_review"] = clean_dict(dna.get("most_negative_review"))

        if progress_cb:
            # Final update to help smooth the last jump in the UI
            progress_cb(total_books, total_books, "Finishing up")

        if user:
            # Calculate and store top books
            calculate_and_store_top_books(user, limit=5)

            _save_dna_to_profile(user.userprofile, dna, dispatch_recommendations=not bulk_enrichment, task_id=task_id)
            logger.info(f"Saved DNA for user: {user.username}")
            return dna
        else:
            # Save anonymous session data
            if session_key:
                book_row_pairs = [(book, original_row) for book, _, original_row in results if book]
                save_anonymous_session_data(session_key, dna, book_row_pairs)
                logger.info(f"Saved anonymous session data for session: {_key_hash(session_key)}")
            logger.info("DNA generated for an anonymous user. Returning result for display")
            return dna

    except Exception as e:
        user_identifier = user.id if user else "Anonymous"
        logger.error(f"A critical error occurred in DNA calculation for user_id {user_identifier}: {e}", exc_info=True)
        raise
