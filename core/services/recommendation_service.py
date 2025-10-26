from collections import Counter
from django.utils import timezone
from .user_similarity_service import (
    find_similar_users, 
    calculate_anonymous_similarity,
    calculate_similarity_with_anonymized
)
from ..models import UserBook, Book, User, AnonymousUserSession, AnonymizedReadingProfile

def get_recommendations_for_user(user, limit=10):
    """Get recommendations for a registered user"""
    
    # Get books user has already read
    user_books = set(UserBook.objects.filter(user=user).values_list('book_id', flat=True))
    
    recommendation_scores = Counter()
    source_info = {}  # Track where recommendations come from
    
    # 1. Compare against similar registered users
    similar_users = find_similar_users(user, top_n=20, min_similarity=0.2)
    
    for similar_user, similarity_data in similar_users:
        top_books = UserBook.objects.filter(
            user=similar_user,
            is_top_book=True
        ).values_list('book_id', flat=True)
        
        for book_id in top_books:
            if book_id not in user_books:
                score = similarity_data['similarity_score'] * 1.2  # Boost registered users
                recommendation_scores[book_id] += score
                
                if book_id not in source_info:
                    source_info[book_id] = []
                source_info[book_id].append({
                    'type': 'registered_user',
                    'username': similar_user.username,
                    'user_id': similar_user.id,
                    'similarity': similarity_data['similarity_score']
                })
    
    # 2. Compare against active anonymous sessions
    active_sessions = AnonymousUserSession.objects.filter(
        expires_at__gt=timezone.now()
    )
    
    for session in active_sessions:
        similarity_data = calculate_anonymous_similarity(session, user)
        
        if similarity_data['similarity_score'] >= 0.2:
            for book_id in session.top_books_data[:5]:
                if book_id not in user_books:
                    recommendation_scores[book_id] += similarity_data['similarity_score']
    
    # 3. Compare against anonymized corpus
    anonymized_profiles = AnonymizedReadingProfile.objects.all()[:200]  # Limit for performance
    
    for anon_profile in anonymized_profiles:
        similarity_data = calculate_similarity_with_anonymized(user, anon_profile)
        
        if similarity_data['similarity_score'] >= 0.2:
            for book_id in anon_profile.top_book_ids[:5]:
                if book_id not in user_books:
                    recommendation_scores[book_id] += similarity_data['similarity_score']
    
    # Build final recommendations
    top_book_ids = [book_id for book_id, score in recommendation_scores.most_common(limit)]
    
    recommendations = []
    for book_id in top_book_ids:
        try:
            book = Book.objects.select_related('author').get(pk=book_id)
            # Convert score to percentage (similarity scores are typically 0-1 range)
            score_percent = recommendation_scores[book_id] * 100
            recommendations.append({
                'book': book,
                'score': score_percent,
                'sources': source_info.get(book_id, [])
            })
        except Book.DoesNotExist:
            continue
    
    # If we don't have enough recommendations, use fallback logic
    if len(recommendations) < limit:
        recommendations.extend(_get_fallback_recommendations(user, user_books, limit - len(recommendations)))
    
    return recommendations[:limit]


def _get_fallback_recommendations(user, user_books, needed):
    """Get fallback recommendations based on user's favorite authors/genres"""
    from ..models import Genre
    
    # Get user's DNA to understand their preferences
    if not hasattr(user, 'userprofile') or not user.userprofile.dna_data:
        return []
    
    dna = user.userprofile.dna_data
    recommendations = []
    
    # Strategy 1: Favorite authors they haven't read
    favorite_authors = []
    for author_name, count in dna.get('top_authors', [])[:5]:
        from ..models import Author
        try:
            author = Author.objects.get(normalized_name=Author._normalize(author_name))
            favorite_authors.append(author)
        except:
            continue
    
    for author in favorite_authors:
        books = Book.objects.filter(author=author).exclude(id__in=user_books)[:needed]
        for book in books:
            if len(recommendations) >= needed:
                break
            recommendations.append({
                'book': book,
                'score': 50,  # Lower score for fallback (percentage)
                'sources': [{'type': 'favorite_author', 'reason': f'From {author.name}'}]
            })
        if len(recommendations) >= needed:
            break
    
    # Strategy 2: Favorite genres they haven't explored
    if len(recommendations) < needed:
        favorite_genres = [genre_name for genre_name, count in dna.get('top_genres', [])[:3]]
        
        for genre_name in favorite_genres:
            try:
                genre = Genre.objects.get(name=genre_name)
                books = Book.objects.filter(genres=genre).exclude(id__in=user_books).distinct()[:needed]
                for book in books:
                    if len(recommendations) >= needed:
                        break
                    if not any(r['book'].id == book.id for r in recommendations):  # Avoid dupes
                        recommendations.append({
                            'book': book,
                            'score': 30,  # Lower score for genre-based (percentage)
                            'sources': [{'type': 'favorite_genre', 'reason': f'{genre_name} genre'}]
                        })
                if len(recommendations) >= needed:
                    break
            except:
                continue
    
    return recommendations


def get_recommendations_for_anonymous(session_key, limit=10):
    """Get recommendations for an anonymous user"""
    
    try:
        anon_session = AnonymousUserSession.objects.get(session_key=session_key)
    except AnonymousUserSession.DoesNotExist:
        return []
    
    anon_books = set(anon_session.books_data or [])
    recommendation_scores = Counter()
    source_info = {}
    
    # 1. Compare against registered users
    all_users = User.objects.select_related('userprofile').filter(
        userprofile__dna_data__isnull=False,
        userprofile__is_public=True
    )
    
    for user in all_users:
        similarity_data = calculate_anonymous_similarity(anon_session, user)
        
        if similarity_data['similarity_score'] >= 0.2:
            top_books = UserBook.objects.filter(
                user=user,
                is_top_book=True
            ).values_list('book_id', flat=True)
            
            for book_id in top_books:
                if book_id not in anon_books:
                    score = similarity_data['similarity_score'] * 1.2
                    recommendation_scores[book_id] += score
                    
                    if book_id not in source_info:
                        source_info[book_id] = []
                    source_info[book_id].append({
                        'type': 'registered_user',
                        'username': user.username,
                        'user_id': user.id,
                        'similarity': similarity_data['similarity_score']
                    })
    
    # 2. Compare against anonymized corpus
    anonymized_profiles = AnonymizedReadingProfile.objects.all()[:200]
    
    for anon_profile in anonymized_profiles:
        similarity_data = calculate_similarity_with_anonymized(anon_session, anon_profile)
        
        if similarity_data['similarity_score'] >= 0.2:
            for book_id in anon_profile.top_book_ids[:5]:
                if book_id not in anon_books:
                    recommendation_scores[book_id] += similarity_data['similarity_score']
    
    # Build recommendations
    top_book_ids = [book_id for book_id, score in recommendation_scores.most_common(limit)]
    
    recommendations = []
    for book_id in top_book_ids:
        try:
            book = Book.objects.select_related('author').get(pk=book_id)
            # Convert score to percentage
            score_percent = recommendation_scores[book_id] * 100
            recommendations.append({
                'book': book,
                'score': score_percent,
                'sources': source_info.get(book_id, [])
            })
        except Book.DoesNotExist:
            continue
    
    return recommendations


