from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
from ..models import UserBook

def calculate_and_store_top_books(user, limit=5):
    """Calculate and store user's top books based on rating and review sentiment"""
    
    user_books = UserBook.objects.filter(user=user).select_related('book', 'book__author')
    
    book_scores = []
    analyzer = SentimentIntensityAnalyzer()
    
    for user_book in user_books:
        score = 0
        
        # Rating weight (heavily weighted if present)
        if user_book.user_rating:
            if user_book.user_rating == 5:
                score += 100
            elif user_book.user_rating == 4:
                score += 80
            else:
                score += user_book.user_rating * 15
        
        # Review sentiment weight
        if user_book.user_review and len(user_book.user_review) > 15:
            sentiment = analyzer.polarity_scores(user_book.user_review)["compound"]
            score += sentiment * 30
        
        # Small boost for books without ratings (to include them if nothing else)
        if not user_book.user_rating and not user_book.user_review:
            score += 10
        
        book_scores.append((user_book, score))
    
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


