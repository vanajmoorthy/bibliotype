import logging
from collections import Counter

import pandas as pd

from ...dna_constants import CANONICAL_GENRE_MAP

logger = logging.getLogger(__name__)

# Diversity bonus: readers spanning at least this many unique canonical genres
# get a flat "Versatile Valedictorian" score bump.
DIVERSITY_THRESHOLD = 10
DIVERSITY_BONUS = 15


def assign_reader_type(read_df, enriched_data, all_genres):
    """
    Calculates scores for reader traits using the final, equitable bonus-based logic.
    """
    scores = Counter()
    total_books = len(read_df)
    if total_books == 0:
        return "Not enough data", Counter()

    # --- HEURISTICS & SCORE CALCULATION ---
    if "Date Read" in read_df.columns:
        books_per_year = read_df.dropna(subset=["Date Read"])["Date Read"].dt.year.value_counts().mean()
        if total_books > 75 and books_per_year > 40:
            scores["Rapacious Reader"] = 100

    if "Number of Pages" in read_df.columns:
        long_books = read_df[read_df["Number of Pages"] > 490].shape[0]
        short_books = read_df[read_df["Number of Pages"] < 200].shape[0]
        scores["Tome Tussler"] += long_books * 2
        scores["Novella Navigator"] += short_books

    # Use the CANONICAL_GENRE_MAP to group aliases into their canonical form
    mapped_genres = [CANONICAL_GENRE_MAP.get(g, g) for g in all_genres]
    genre_counts = Counter(mapped_genres)
    logger.debug(f"Genre counts: {genre_counts}")

    # These scores are now based on CLEAN, CANONICAL genre counts.
    # Mystery, literary fiction, and poetry get no dedicated type — they
    # contribute to genre diversity (Versatile Valedictorian) only.
    scores["Fantasy Fanatic"] += (
        genre_counts.get("fantasy", 0)
        + genre_counts.get("science fiction", 0)
        + genre_counts.get("dystopian", 0)
        + genre_counts.get("adventure", 0)
    )
    scores["Non-Fiction Ninja"] += (
        genre_counts.get("non-fiction", 0)
        + genre_counts.get("memoir", 0)
        + genre_counts.get("true crime", 0)
        + genre_counts.get("essays", 0)
        + genre_counts.get("classic nonfiction", 0)
    )
    scores["Philosophical Philomath"] += genre_counts.get("philosophy", 0)
    scores["Nature Nut Case"] += genre_counts.get("nature", 0)
    scores["Social Savant"] += genre_counts.get("social science", 0)
    scores["Self Help Scholar"] += genre_counts.get("self-help", 0)

    for index, book in read_df.iterrows():
        details = enriched_data.get(book["Title"])
        if not details:
            continue

        if details.get("publish_year"):
            if details["publish_year"] < 1970:
                scores["Classic Collector"] += 1
            elif details["publish_year"] > 2018:
                scores["Modern Maverick"] += 1

        publisher = details.get("publisher")

        if publisher:
            if not publisher.is_mainstream:
                logger.debug(f"Found non-major publisher: {publisher}")
                scores["Small Press Supporter"] += 1

    # Re-read detection: StoryGraph provides Read Count, Goodreads uses duplicate titles.
    # Goodreads: a title appearing N times = N-1 rereads. value_counts().sub(1).clip(lower=0)
    # avoids the //2 trick (which under-counts: 3 reads → 1 reread instead of 2).
    if "Read Count" in read_df.columns:
        reread_count = int((pd.to_numeric(read_df["Read Count"], errors="coerce").fillna(1) > 1).sum())
    else:
        reread_count = int(read_df["Title"].value_counts().sub(1).clip(lower=0).sum())
    # Award 3 points per reread — a reader with 5+ rereads out of 30 books gets a meaningful score
    if reread_count > 0:
        scores["Comfort Rereader"] += reread_count * 3

    unique_canonical_genres = len(genre_counts)

    logger.debug(f"Found {unique_canonical_genres} unique CANONICAL genres")

    if unique_canonical_genres >= DIVERSITY_THRESHOLD:
        scores["Versatile Valedictorian"] += DIVERSITY_BONUS
    if not scores or all(s == 0 for s in scores.values()):

        return "Eclectic Reader", scores

    if scores.get("Rapacious Reader", 0) > 0:
        return "Rapacious Reader", scores

    primary_type = scores.most_common(1)[0][0]
    return primary_type, scores
