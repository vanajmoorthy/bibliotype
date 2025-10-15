# ... (at the top of the file, with other imports)

from .models import Book
from .score_config import (
    SCORE_CONFIG,
)


def calculate_mainstream_score(book: Book) -> tuple[int, dict]:
    """
    Calculates the mainstream score and its breakdown for a given Book object.
    Returns a tuple of (total_score, score_breakdown_dict).
    """
    score_breakdown = {}

    for award in book.awards_won:
        score_breakdown[award] = SCORE_CONFIG.get(award, 0)
    for shortlist in book.shortlists:
        score_breakdown[shortlist] = SCORE_CONFIG.get(shortlist, 0)

    if book.canon_lists:
        # Give a score for each list the book appears on
        score_breakdown["CANON_LISTS"] = len(book.canon_lists) * SCORE_CONFIG.get("CANON_LIST", 75)

    if book.nyt_bestseller_weeks > 0:
        score_breakdown["NYT_BESTSELLER_WEEKS"] = book.nyt_bestseller_weeks * SCORE_CONFIG.get(
            "NYT_BESTSELLER_WEEK", 15
        )

    # Use the Google Books data for a ratings score
    if book.google_books_ratings_count > 1000:
        # Cap the bonus to avoid runaway scores
        score_breakdown["RATINGS_SCORE"] = min(book.google_books_ratings_count // 1000, 50)

    if book.google_books_average_rating and book.google_books_average_rating >= 4.1:
        score_breakdown["HIGH_RATING_BONUS"] = SCORE_CONFIG.get("HIGH_RATING_BONUS", 10)

    total_score = sum(score_breakdown.values())

    return total_score, score_breakdown
