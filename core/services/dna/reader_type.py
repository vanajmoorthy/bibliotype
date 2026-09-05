import logging
import re
from collections import Counter
from datetime import datetime

import pandas as pd

from ...dna_constants import (
    MIN_SIGNAL_BOOKS,
    MIN_WINNING_SCORE,
    READER_TYPE_THRESHOLDS,
    READER_TYPE_TIEBREAK_ORDER,
    score_ramp,
)

logger = logging.getLogger(__name__)

# Regex for detecting Goodreads series notation in book titles: "(Series Name, #N)"
_SERIES_RE = re.compile(r"\(.*#\d", re.IGNORECASE)

# Normalized author names that indicate "no real single author" — never count
# toward Author Loyalist (anthologies, folk tales, uncredited works).
_GENERIC_AUTHOR_NAMES = frozenset({"anonymous", "various", "variousauthors", "unknown"})


def compute_reread_count(read_df):
    """Return the number of books read more than once, derived purely from read_df.

    Uses the "Read Count" column when present (Goodreads); falls back to title
    duplicate counting (StoryGraph or any CSV without that column).
    """
    if "Read Count" in read_df.columns:
        return int((pd.to_numeric(read_df["Read Count"], errors="coerce").fillna(1) > 1).sum())
    return int(read_df["Title"].value_counts().sub(1).clip(lower=0).sum())


def compute_books_per_year(read_df):
    """Return the mean books-per-year rate, derived purely from read_df.

    Returns 0.0 when fewer than 24 dated reads span fewer than 2 distinct years
    (matches the Rapacious Reader gate in assign_reader_type).
    """
    if "Date Read" not in read_df.columns:
        return 0.0
    dated_df = read_df.dropna(subset=["Date Read"])
    dated_reads_count = len(dated_df)
    if dated_reads_count < 24:
        return 0.0
    year_counts = dated_df["Date Read"].dt.year.value_counts()
    distinct_years = len(year_counts)
    if distinct_years < 2:
        return 0.0
    return dated_reads_count / distinct_years


def assign_reader_type(
    read_df, enriched_data, book_genre_sets, reread_count_override=None, books_per_year_override=None
):
    """
    Assign a reader type using normalized 0-100 scoring.

    Parameters
    ----------
    read_df : pd.DataFrame
        Normalized read books DataFrame (Goodreads or StoryGraph schema).
    enriched_data : dict
        {book_title: {"publish_year": int|None, "publisher": Publisher|None}}
    book_genre_sets : list[set[str]]
        One canonical-genre set per successfully-synced book (from canonicalize_genre_names).
        This is the new interface replacing the old flat all_genres list.
        If the caller passes a flat list of genre strings (legacy/test compatibility),
        we detect it and convert to a single set.
    reread_count_override : int or None
        When not None, use this value instead of computing from read_df (for DB-based
        recompute where Read Count is unavailable but was persisted at generation time).
    books_per_year_override : float or None
        When not None, use this value instead of computing from read_df (for DB-based
        recompute where Date Read is unavailable but was persisted at generation time).

    Returns
    -------
    (reader_type: str, scores: Counter)
    """
    total_books = len(read_df)
    if total_books == 0:
        return "Not enough data", Counter()

    # --- Legacy compatibility: if caller passes a flat list of strings (old tests),
    # wrap it as a single set so the rest of the logic works.
    if book_genre_sets and isinstance(next(iter(book_genre_sets), None), str):
        book_genre_sets = [set(book_genre_sets)]

    # L = total library size (denominator for reread/series signals)
    L = total_books

    # --- Genre signal denominators ---
    # G = number of books with at least one canonical genre
    G = sum(1 for gs in book_genre_sets if gs)

    # --- Per-book genre intersection check (unique-book, not occurrence-count) ---
    def genre_share(genre_set_target):
        """Fraction of genre-enriched books whose canonical set intersects genre_set_target."""
        if G < MIN_SIGNAL_BOOKS:
            return 0.0
        count = sum(1 for gs in book_genre_sets if gs & genre_set_target)
        return count / G

    # --- Page signal denominator ---
    P = 0
    long_books = 0
    short_books = 0
    if "Number of Pages" in read_df.columns:
        pages_series = pd.to_numeric(read_df["Number of Pages"], errors="coerce")
        P = int(pages_series.notna().sum())
        long_books = int((pages_series > 490).sum())
        short_books = int((pages_series < 200).sum())

    # --- Year + publisher signals from enriched_data ---
    Y = 0
    Pub = 0
    pre_1970_count = 0
    recent_count = 0
    small_press_count = 0
    current_year = pd.Timestamp.now().year
    maverick_cutoff = current_year - 6

    for book_title, details in enriched_data.items():
        pub_year = details.get("publish_year")
        publisher = details.get("publisher")
        if pub_year is not None:
            Y += 1
            if pub_year < 1970:
                pre_1970_count += 1
            elif pub_year >= maverick_cutoff:
                recent_count += 1
        if publisher is not None:
            Pub += 1
            if not publisher.is_mainstream:
                logger.debug(f"Found non-major publisher: {publisher}")
                small_press_count += 1

    # --- Reread detection ---
    if reread_count_override is not None:
        reread_count = int(reread_count_override)
    else:
        reread_count = compute_reread_count(read_df)

    # --- Series detection (Goodreads title notation: "(Series Name, #N)") ---
    series_count = int(read_df["Title"].str.contains(_SERIES_RE, na=False).sum())

    # --- Rapacious Reader: books-per-year rate ---
    if books_per_year_override is not None:
        mean_books_per_year = float(books_per_year_override)
    else:
        mean_books_per_year = compute_books_per_year(read_df)

    # --- Versatile Valedictorian: count of "solid" canonical genres ---
    # A genre is solid if it covers >= 3% of G and >= 2 books
    solid_genre_count = 0
    if G >= MIN_SIGNAL_BOOKS:
        from collections import Counter as _Counter

        all_genres_flat = []
        for gs in book_genre_sets:
            all_genres_flat.extend(gs)
        genre_book_counts = _Counter(all_genres_flat)
        min_books_for_solid = max(2, round(G * 0.03))
        solid_genre_count = sum(1 for g, cnt in genre_book_counts.items() if cnt >= min_books_for_solid)

    # --- Compute all normalized 0-100 scores ---
    scores = Counter()

    # Genre-share types
    fantasy_share = genre_share({"fantasy", "science fiction", "dystopian", "adventure"})
    scores["Fantasy Fanatic"] = score_ramp(fantasy_share, *READER_TYPE_THRESHOLDS["Fantasy Fanatic"])

    mystery_share = genre_share({"thriller", "mystery", "true crime"})
    scores["Mystery Maven"] = score_ramp(mystery_share, *READER_TYPE_THRESHOLDS["Mystery Maven"])

    romance_share = genre_share({"romance"})
    scores["Romance Reveller"] = score_ramp(romance_share, *READER_TYPE_THRESHOLDS["Romance Reveller"])

    history_share = genre_share({"history", "historical fiction", "biography"})
    scores["History Hound"] = score_ramp(history_share, *READER_TYPE_THRESHOLDS["History Hound"])

    literary_share = genre_share({"literary fiction", "classic fiction"})
    scores["Literary Luminary"] = score_ramp(literary_share, *READER_TYPE_THRESHOLDS["Literary Luminary"])

    poetry_share = genre_share({"poetry"})
    scores["Sonnet Slinger"] = score_ramp(poetry_share, *READER_TYPE_THRESHOLDS["Sonnet Slinger"])

    # "nature" and "social science" fold into Non-Fiction Ninja — they belonged to the
    # retired Nature Nut Case / Social Savant types.
    nonfiction_share = genre_share(
        {"non-fiction", "memoir", "true crime", "essays", "classic nonfiction", "nature", "social science"}
    )
    scores["Non-Fiction Ninja"] = score_ramp(nonfiction_share, *READER_TYPE_THRESHOLDS["Non-Fiction Ninja"])

    philosophy_share = genre_share({"philosophy"})
    scores["Philosophical Philomath"] = score_ramp(philosophy_share, *READER_TYPE_THRESHOLDS["Philosophical Philomath"])

    selfhelp_share = genre_share({"self-help"})
    scores["Self Help Scholar"] = score_ramp(selfhelp_share, *READER_TYPE_THRESHOLDS["Self Help Scholar"])

    # Page-based types
    if P >= MIN_SIGNAL_BOOKS:
        scores["Tome Tussler"] = score_ramp(long_books / P, *READER_TYPE_THRESHOLDS["Tome Tussler"])
        scores["Novella Navigator"] = score_ramp(short_books / P, *READER_TYPE_THRESHOLDS["Novella Navigator"])

    # Year-based types
    if Y >= MIN_SIGNAL_BOOKS:
        scores["Classics Collector"] = score_ramp(pre_1970_count / Y, *READER_TYPE_THRESHOLDS["Classics Collector"])
        scores["Modern Maverick"] = score_ramp(recent_count / Y, *READER_TYPE_THRESHOLDS["Modern Maverick"])

    # Publisher-based type
    if Pub >= MIN_SIGNAL_BOOKS:
        scores["Small Press Supporter"] = score_ramp(
            small_press_count / Pub, *READER_TYPE_THRESHOLDS["Small Press Supporter"]
        )

    # Author loyalty type: largest single-author share among unique authored books.
    # Rows are deduped on (Title, Author) so rereads logged as duplicate rows don't
    # inflate the share, and names go through Author._normalize so spelling variants
    # ("J.K. Rowling" / "J. K. Rowling") count as one author — keeping the CSV path
    # consistent with the DB-recompute path, where authors arrive already merged by
    # that same normalization.
    if "Author" in read_df.columns:
        from ...models import Author as _Author

        authored = read_df.loc[read_df["Author"].notna(), ["Title", "Author"]].drop_duplicates()
        author_names = authored["Author"].astype(str).map(_Author._normalize)
        author_names = author_names[(author_names != "") & ~author_names.isin(_GENERIC_AUTHOR_NAMES)]
        A = len(author_names)
        if A >= MIN_SIGNAL_BOOKS:
            top_author_share = int(author_names.value_counts().iloc[0]) / A
            scores["Author Loyalist"] = score_ramp(top_author_share, *READER_TYPE_THRESHOLDS["Author Loyalist"])

    # Reread type
    scores["Comfort Rereader"] = score_ramp(
        reread_count / L if L > 0 else 0.0, *READER_TYPE_THRESHOLDS["Comfort Rereader"]
    )

    # Series type
    scores["Series Slayer"] = score_ramp(series_count / L if L > 0 else 0.0, *READER_TYPE_THRESHOLDS["Series Slayer"])

    # Rapacious Reader (books/year rate, not a fraction)
    scores["Rapacious Reader"] = score_ramp(mean_books_per_year, *READER_TYPE_THRESHOLDS["Rapacious Reader"])

    # Versatile Valedictorian (count of solid genres)
    scores["Versatile Valedictorian"] = score_ramp(
        float(solid_genre_count), *READER_TYPE_THRESHOLDS["Versatile Valedictorian"]
    )

    # Remove zero scores (keep Counter clean)
    scores = Counter({t: s for t, s in scores.items() if s > 0})

    logger.debug(f"Reader type scores: {scores}")

    # --- Winner selection ---
    if not scores or scores.most_common(1)[0][1] < MIN_WINNING_SCORE:
        return "Eclectic Reader", scores

    top_score = scores.most_common(1)[0][1]
    tied = [t for t, s in scores.items() if s == top_score]

    if len(tied) == 1:
        winner = tied[0]
    else:
        # Tiebreak: pick the type that appears earliest in READER_TYPE_TIEBREAK_ORDER
        def tiebreak_key(t):
            try:
                return READER_TYPE_TIEBREAK_ORDER.index(t)
            except ValueError:
                return len(READER_TYPE_TIEBREAK_ORDER)

        winner = min(tied, key=tiebreak_key)

    return winner, scores


def recompute_reader_type_from_db(
    user, csv_context, books=None, current_reader_type=None, current_explanation=None, shelf_signal_list=None
):
    """Recompute reader type entirely from DB books + persisted CSV context.

    Called during enrichment polling so the reader-type card updates live as
    genre/publisher/page data flows in, without re-reading the original CSV.

    Parameters
    ----------
    user : User
        The authenticated user whose books to query.
    csv_context : dict
        {"reread_count": int, "books_per_year_avg": float} — persisted at
        generation time from the CSV-only signals.
    books : list[Book] or None
        Pre-fetched Book queryset (with select_related author/publisher and
        prefetch_related genres). When provided, skips the DB query so the
        caller can share one queryset pass with _compute_enrichment_stats.
    current_reader_type / current_explanation : str or None
        The type/blurb currently stored for the user. When the recomputed type
        matches, the stored blurb is reused instead of re-rolling random.choice,
        so the explanation doesn't churn on every poll.
    shelf_signal_list : list[tuple] or None
        Per-book (shelf_fiction, shelf_nonfiction, shelf_genres) triples aligned
        with `books`, as built by _compute_enrichment_stats from the persisted
        shelf-signals map. Used to axis-resolve genre labels the same way
        generation does; None means no shelf context (API-genre-only).

    Returns
    -------
    dict with keys: reader_type, reader_type_explanation, top_reader_types,
                    reader_type_scores, reader_type_scores_version
    """
    import random

    from ...dna_constants import READER_TYPE_DESCRIPTIONS
    from ...services.genre_classification import resolve_genre_labels

    if books is None:
        from ...models import Book as _Book

        books = list(
            _Book.objects.filter(readers__user=user).select_related("author", "publisher").prefetch_related("genres")
        )

    if not books:
        return None

    # Build a minimal DataFrame with only Title, Number of Pages, and Author.
    # Date Read and Read Count come from csv_context via overrides — we don't
    # reconstruct them here because they lived only in the original CSV.
    titles = [b.title or "" for b in books]
    page_counts = [b.page_count for b in books]
    authors = [b.author.name if b.author_id else None for b in books]
    read_df = pd.DataFrame({"Title": titles, "Number of Pages": page_counts, "Author": authors})

    enriched_data = {b.title: {"publish_year": b.publish_year, "publisher": b.publisher} for b in books if b.title}
    # Axis-resolved to mirror generation: a nonfiction classic must count as
    # "classic nonfiction" (Non-Fiction Ninja), not "classic fiction" (Literary
    # Luminary), or the reader type flips between polls and the stored DNA.
    if shelf_signal_list is None:
        shelf_signal_list = [(False, False, frozenset())] * len(books)
    book_genre_sets = [
        set(
            resolve_genre_labels(
                [g.name for g in b.genres.all()], shelf_fiction=sf, shelf_nonfiction=snf, shelf_genres=sg
            )
        )
        for b, (sf, snf, sg) in zip(books, shelf_signal_list)
    ]

    reader_type, reader_type_scores = assign_reader_type(
        read_df,
        enriched_data,
        book_genre_sets,
        reread_count_override=csv_context.get("reread_count"),
        books_per_year_override=csv_context.get("books_per_year_avg"),
    )
    # Keep the existing blurb when the winning type is unchanged; only re-roll
    # when the type actually flips, so polling doesn't churn the explanation.
    if reader_type == current_reader_type and current_explanation:
        explanation = current_explanation
    else:
        explanation = random.choice(READER_TYPE_DESCRIPTIONS.get(reader_type, [""]))
    top_types_list = [{"type": t, "score": s} for t, s in reader_type_scores.most_common(3) if s > 0]

    return {
        "reader_type": reader_type,
        "reader_type_explanation": explanation,
        "top_reader_types": top_types_list,
        "reader_type_scores": dict(reader_type_scores),
        "reader_type_scores_version": 3,
    }
