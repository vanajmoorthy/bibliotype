from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
from ..models import UserBook

# Reviews shorter than this carry too little signal for sentiment scoring
MIN_REVIEW_LENGTH_FOR_SENTIMENT = 15


def compute_book_score(rating, sentiment):
    """Canonical top-book score, shared by the authenticated and anonymous paths.

    rating: int 1-5 (or None/0 when unrated)
    sentiment: VADER compound score for the review, or None when there is no usable review
    """
    score = 0

    # Rating weight (heavily weighted if present)
    if rating:
        if rating == 5:
            score += 100
        elif rating == 4:
            score += 80
        else:
            score += rating * 15

    # Review sentiment weight
    if sentiment is not None:
        score += sentiment * 30

    # Small boost for books without ratings or reviews (to include them if nothing else)
    if not rating and sentiment is None:
        score += 10

    return score


def calculate_and_store_top_books(user, limit=5):
    """Calculate and store user's top books based on rating and review sentiment"""

    user_books = UserBook.objects.filter(user=user).select_related("book", "book__author")

    book_scores = []
    analyzer = SentimentIntensityAnalyzer()

    for user_book in user_books:
        sentiment = None
        if user_book.user_review and len(user_book.user_review) > MIN_REVIEW_LENGTH_FOR_SENTIMENT:
            sentiment = analyzer.polarity_scores(user_book.user_review)["compound"]

        book_scores.append((user_book, compute_book_score(user_book.user_rating, sentiment)))

    # Sort by score
    book_scores.sort(key=lambda x: x[1], reverse=True)
    top_book_objects = [ub for ub, score in book_scores[:limit]]

    # Reset all flags
    UserBook.objects.filter(user=user).update(is_top_book=False, top_book_position=None)

    # Mark top books
    for position, user_book in enumerate(top_book_objects, 1):
        user_book.is_top_book = True
        user_book.top_book_position = position
        user_book.save()

    return top_book_objects
