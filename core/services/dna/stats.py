"""Reading statistics computed from the normalised CSV and the synced Book rows."""

import logging
from collections import Counter

from django.core.exceptions import ObjectDoesNotExist
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from ...dna_constants import NICHE_THRESHOLD, compute_contrariness
from ...percentile_engine import (
    calculate_community_means,
    calculate_percentiles_from_aggregates,
    update_analytics_from_stats,
)
from .utils import _build_cover_url, _cover_initial, _sanitize_review_text

logger = logging.getLogger(__name__)


def compute_base_stats(read_df):
    """Return (user_base_stats, stats_by_year_list)."""
    # Calculate stats_by_year early so we can derive avg_books_per_year
    stats_by_year_list = []
    avg_books_per_year = 0
    num_reading_years = 0
    books_with_dates = 0

    if "Date Read" in read_df.columns and not read_df["Date Read"].dropna().empty:
        yearly_df = read_df.dropna(subset=["Date Read"])
        yearly_df["year"] = yearly_df["Date Read"].dt.year

        yearly_stats = (
            yearly_df.groupby("year").agg(count=("Title", "size"), avg_rating=("My Rating", "mean")).reset_index()
        )

        yearly_stats["avg_rating"] = yearly_stats["avg_rating"].fillna(0).round(2)
        stats_by_year_list = yearly_stats.to_dict("records")

        for item in stats_by_year_list:
            item["year"] = int(item["year"])
            item["count"] = int(item["count"])
            item["avg_rating"] = float(item["avg_rating"])

        num_reading_years = len(stats_by_year_list)
        books_with_dates = int(yearly_df.shape[0])
        if num_reading_years > 0:
            avg_books_per_year = round(books_with_dates / num_reading_years, 1)

    user_base_stats = {
        "total_books_read": int(len(read_df)),
        "books_with_dates": books_with_dates,
        "total_pages_read": int(read_df["Number of Pages"].dropna().sum()),
        "avg_book_length": (
            int(round(read_df["Number of Pages"].dropna().mean()))
            if not read_df["Number of Pages"].dropna().empty
            else 0
        ),
        "avg_publish_year": (
            int(round(read_df["Original Publication Year"].dropna().mean()))
            if "Original Publication Year" in read_df.columns
            and not read_df["Original Publication Year"].dropna().empty
            else 0
        ),
        "avg_books_per_year": avg_books_per_year,
        "num_reading_years": num_reading_years,
    }
    return user_base_stats, stats_by_year_list


def compute_community_stats(user, previous_anon_session, user_base_stats):
    """Fold this upload into the community aggregates; return (percentiles, community_averages)."""
    logger.info("Calculating community stats...")

    previous_stats = None
    if user:
        try:
            existing_dna = user.userprofile.dna_data
            if existing_dna:
                previous_stats = existing_dna.get("user_stats")
        except ObjectDoesNotExist:
            pass
    elif previous_anon_session and previous_anon_session.dna_data:
        previous_stats = previous_anon_session.dna_data.get("user_stats")
    update_analytics_from_stats(user_base_stats, previous_stats=previous_stats)

    # Percentiles stored here serve as a fallback for contexts that bypass
    # _enrich_dna_for_display (API access, data exports). The view recalculates
    # fresh percentiles at display time so dashboard values are never stale.
    percentiles = calculate_percentiles_from_aggregates(user_base_stats)

    community_averages = calculate_community_means()
    return percentiles, community_averages


def compute_book_extremes(user_book_objects):
    """Return (most_niche_book, niche_books_count, longest_book, shortest_book, page_difference)."""
    most_niche_book = None

    niche_books_count = 0
    if user_book_objects:
        # min(), not an in-place sort: user_book_objects keeps CSV order.
        niche = min(user_book_objects, key=lambda b: b.global_read_count)
        most_niche_book = {
            "title": niche.title,
            "author": niche.author.name,
            "read_count": niche.global_read_count,
            "cover_url": niche.cover_url or _build_cover_url(niche.isbn13),
            "initial": _cover_initial(niche.title),
        }
        niche_books_count = sum(1 for b in user_book_objects if b.global_read_count <= NICHE_THRESHOLD)

    longest_book = None
    shortest_book = None
    page_difference = None
    books_with_pages = [b for b in user_book_objects if b.page_count]
    if len(books_with_pages) >= 2:
        books_with_pages.sort(key=lambda b: (-b.page_count, b.normalized_title))
        longest = books_with_pages[0]
        shortest = books_with_pages[-1]
        if longest.page_count != shortest.page_count:
            longest_book = {
                "title": longest.title,
                "author": longest.author.name,
                "page_count": longest.page_count,
                "cover_url": longest.cover_url or _build_cover_url(longest.isbn13),
                "initial": _cover_initial(longest.title),
            }
            shortest_book = {
                "title": shortest.title,
                "author": shortest.author.name,
                "page_count": shortest.page_count,
                "cover_url": shortest.cover_url or _build_cover_url(shortest.isbn13),
                "initial": _cover_initial(shortest.title),
            }
            page_difference = longest.page_count - shortest.page_count
    return most_niche_book, niche_books_count, longest_book, shortest_book, page_difference


def compute_rating_stats(read_df):
    """Return the ratings distribution, most controversial books and contrariness stats."""
    ratings_df = read_df[read_df["My Rating"] > 0].dropna(subset=["My Rating"])
    average_rating_overall = float(round(ratings_df["My Rating"].mean(), 2)) if not ratings_df.empty else "N/A"
    ratings_dist = {str(k): int(v) for k, v in ratings_df["My Rating"].value_counts().sort_index().to_dict().items()}

    controversial_df = read_df.dropna(subset=["My Rating", "Average Rating"]).copy()
    controversial_df = controversial_df[controversial_df["My Rating"] > 0]

    top_controversial_list = []

    if not controversial_df.empty:
        controversial_df["Rating Difference"] = abs(controversial_df["My Rating"] - controversial_df["Average Rating"])
        top_books = controversial_df.sort_values(by="Rating Difference", ascending=False).head(3)

        top_books["my_rating"] = top_books["My Rating"].astype(float)
        top_books["average_rating"] = top_books["Average Rating"].astype(float)
        top_books["rating_difference"] = top_books["Rating Difference"].astype(float)

        top_controversial_list = top_books.rename(columns={"Title": "Title", "Author": "Author"})[
            ["Title", "Author", "my_rating", "average_rating", "rating_difference"]
        ].to_dict("records")

    # Calculate contrariness stats across ALL rated books with average ratings
    controversial_books_count = int(len(controversial_df)) if not controversial_df.empty else 0
    avg_rating_difference = 0.0
    contrariness_label = "Aligned with consensus"
    contrariness_color = "bg-brand-green"
    if not controversial_df.empty:
        avg_rating_difference = round(float(controversial_df["Rating Difference"].mean()), 2)
        contrariness_label, contrariness_color = compute_contrariness(avg_rating_difference)
    return (
        average_rating_overall,
        ratings_dist,
        top_controversial_list,
        controversial_books_count,
        avg_rating_difference,
        contrariness_label,
        contrariness_color,
    )


def compute_review_stats(df):
    """Return (most_positive_review, most_negative_review, total, positive, negative review counts)."""
    reviews_df = df[
        (df["My Review"].str.strip().ne(""))
        & (df["My Review"].str.len() > 15)
        & (df["My Rating"].notna())
        & (df["My Rating"] > 0)
    ].copy()

    most_positive_review, most_negative_review = None, None

    if not reviews_df.empty:
        analyzer = SentimentIntensityAnalyzer()

        reviews_df["sentiment"] = reviews_df["My Review"].apply(lambda r: analyzer.polarity_scores(r)["compound"])

        pos_candidate = (
            reviews_df[reviews_df["My Rating"] == 5]
            if not reviews_df[reviews_df["My Rating"] == 5].empty
            else reviews_df
        )

        pos_review_row = pos_candidate.loc[pos_candidate["sentiment"].idxmax()]

        neg_candidate = (
            reviews_df[reviews_df["My Rating"] == 1]
            if not reviews_df[reviews_df["My Rating"] == 1].empty
            else reviews_df
        )

        neg_review_row = neg_candidate.loc[neg_candidate["sentiment"].idxmin()]

        most_positive_review = pos_review_row.rename({"My Review": "my_review"})[
            ["Title", "Author", "my_review", "sentiment"]
        ].to_dict()

        most_positive_review["sentiment"] = float(most_positive_review["sentiment"])
        most_positive_review["my_review"] = _sanitize_review_text(most_positive_review.get("my_review", ""))

        most_negative_review = neg_review_row.rename({"My Review": "my_review"})[
            ["Title", "Author", "my_review", "sentiment"]
        ].to_dict()

        most_negative_review["sentiment"] = float(most_negative_review["sentiment"])
        most_negative_review["my_review"] = _sanitize_review_text(most_negative_review.get("my_review", ""))

    # Review sentiment counts
    total_reviews_count = int(len(reviews_df)) if not reviews_df.empty else 0
    positive_reviews_count = 0
    negative_reviews_count = 0
    if not reviews_df.empty and "sentiment" in reviews_df.columns:
        positive_reviews_count = int((reviews_df["sentiment"] > 0).sum())
        negative_reviews_count = int((reviews_df["sentiment"] < 0).sum())
    return (
        most_positive_review,
        most_negative_review,
        total_reviews_count,
        positive_reviews_count,
        negative_reviews_count,
    )


def compute_mainstream_score(user_book_objects):
    """Percentage of books by a mainstream author or publisher."""
    mainstream_score = 0
    if user_book_objects:
        mainstream_books_count = 0
        total_user_books = len(user_book_objects)

        for book in user_book_objects:
            if book.author.is_mainstream or (book.publisher and book.publisher.is_mainstream):
                mainstream_books_count += 1

        if total_user_books > 0:
            mainstream_score = round((mainstream_books_count / total_user_books) * 100)
    return mainstream_score


def compute_mood_pace(read_df):
    """StoryGraph mood and pace distributions (empty for Goodreads)."""
    mood_distribution = []
    pace_distribution = []
    if "Moods" in read_df.columns:
        all_moods = []
        for moods_str in read_df["Moods"].dropna():
            all_moods.extend([m.strip().lower() for m in str(moods_str).split(",") if m.strip()])
        if all_moods:
            mood_distribution = Counter(all_moods).most_common(10)
    if "Pace" in read_df.columns:
        pace_values = read_df["Pace"].dropna().str.strip().str.lower()
        if not pace_values.empty:
            pace_distribution = Counter(pace_values).most_common()
    return mood_distribution, pace_distribution
