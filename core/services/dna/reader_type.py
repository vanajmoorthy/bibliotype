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


def assign_reader_type(read_df, enriched_data, book_genre_sets):
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
    if "Read Count" in read_df.columns:
        reread_count = int((pd.to_numeric(read_df["Read Count"], errors="coerce").fillna(1) > 1).sum())
    else:
        reread_count = int(read_df["Title"].value_counts().sub(1).clip(lower=0).sum())

    # --- Series detection (Goodreads title notation: "(Series Name, #N)") ---
    series_count = int(read_df["Title"].str.contains(_SERIES_RE, na=False).sum())

    # --- Rapacious Reader: books-per-year rate ---
    mean_books_per_year = 0.0
    dated_reads_count = 0
    distinct_years = 0
    if "Date Read" in read_df.columns:
        dated_df = read_df.dropna(subset=["Date Read"])
        dated_reads_count = len(dated_df)
        if dated_reads_count >= 24:
            year_counts = dated_df["Date Read"].dt.year.value_counts()
            distinct_years = len(year_counts)
            if distinct_years >= 2:
                mean_books_per_year = dated_reads_count / distinct_years

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

    nonfiction_share = genre_share({"non-fiction", "memoir", "true crime", "essays", "classic nonfiction"})
    scores["Non-Fiction Ninja"] = score_ramp(nonfiction_share, *READER_TYPE_THRESHOLDS["Non-Fiction Ninja"])

    philosophy_share = genre_share({"philosophy"})
    scores["Philosophical Philomath"] = score_ramp(philosophy_share, *READER_TYPE_THRESHOLDS["Philosophical Philomath"])

    nature_share = genre_share({"nature"})
    scores["Nature Nut Case"] = score_ramp(nature_share, *READER_TYPE_THRESHOLDS["Nature Nut Case"])

    social_share = genre_share({"social science"})
    scores["Social Savant"] = score_ramp(social_share, *READER_TYPE_THRESHOLDS["Social Savant"])

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

    # Reread type
    scores["Comfort Rereader"] = score_ramp(reread_count / L if L > 0 else 0.0, *READER_TYPE_THRESHOLDS["Comfort Rereader"])

    # Series type
    scores["Series Slayer"] = score_ramp(series_count / L if L > 0 else 0.0, *READER_TYPE_THRESHOLDS["Series Slayer"])

    # Rapacious Reader (books/year rate, not a fraction)
    scores["Rapacious Reader"] = score_ramp(mean_books_per_year, *READER_TYPE_THRESHOLDS["Rapacious Reader"])

    # Versatile Valedictorian (count of solid genres)
    scores["Versatile Valedictorian"] = score_ramp(float(solid_genre_count), *READER_TYPE_THRESHOLDS["Versatile Valedictorian"])

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
