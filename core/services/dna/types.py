"""Shape of the DNA dict that calculate_full_dna builds and UserProfile/AnonymousUserSession.dna_data store."""

from typing import Any, TypedDict


class UserStats(TypedDict, total=False):
    total_books_read: int
    books_with_dates: int
    total_pages_read: int
    avg_book_length: int
    avg_publish_year: int
    avg_books_per_year: float
    num_reading_years: int


# total=False: stored DNA predates some keys, and views add display-only ones
# (comparative_text, number_line_ranges, enrichment_finalized) at read time.
class ReadingDNA(TypedDict, total=False):
    user_stats: UserStats
    bibliotype_percentiles: dict[str, Any]
    global_averages: dict[str, Any]
    community_averages: dict[str, Any]
    most_niche_book: dict[str, Any] | None
    reader_type: str
    reader_type_explanation: str
    top_reader_types: list[Any]
    reader_type_scores: dict[str, float]
    reader_type_scores_version: int
    reader_type_csv_context: dict[str, Any]
    top_genres: list[tuple[str, int]]
    top_authors: list[tuple[str, int]]
    average_rating_overall: float
    ratings_distribution: dict[str, int]
    top_controversial_books: list[dict[str, Any]]
    controversial_books_count: int
    avg_rating_difference: float
    contrariness_label: str
    contrariness_color: str
    most_positive_review: dict[str, Any] | None
    most_negative_review: dict[str, Any] | None
    total_reviews_count: int
    positive_reviews_count: int
    negative_reviews_count: int
    stats_by_year: list[dict[str, Any]]
    mainstream_score_percent: int
    unique_authors_count: int
    unique_genres_count: int
    niche_books_count: int
    niche_threshold: int
    currently_reading_books: list[dict[str, Any]]
    currently_reading_count: int
    custom_shelf_count: int
    fiction_nonfiction_split: dict[str, int] | None
    shelf_signals: dict[str, Any]
    enrichment_cohort_ids: list[int]
    longest_book: dict[str, Any] | None
    shortest_book: dict[str, Any] | None
    page_difference: int | None
    csv_source: str
    mood_distribution: dict[str, Any]
    pace_distribution: dict[str, Any]
    reading_vibe: list[str]
    vibe_data_hash: str | None
    reading_vibe_color: str
