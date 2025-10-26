import numpy as np
from collections import Counter
from django.db.models import Avg
from ..models import UserBook, User, Book, Author, AnonymousUserSession, AnonymizedReadingProfile

def _calculate_cosine_similarity(counter1, counter2):
    """Calculate cosine similarity between two Counter objects"""
    all_keys = set(counter1.keys()) | set(counter2.keys())
    if not all_keys:
        return 0
    
    vec1 = np.array([counter1.get(k, 0) for k in all_keys])
    vec2 = np.array([counter2.get(k, 0) for k in all_keys])
    
    dot_product = np.dot(vec1, vec2)
    norm1 = np.linalg.norm(vec1)
    norm2 = np.linalg.norm(vec2)
    
    if norm1 == 0 or norm2 == 0:
        return 0
    
    return float(dot_product / (norm1 * norm2))


def calculate_user_similarity(user1, user2):
    """Calculate similarity between two registered users"""
    
    # Get books with ratings
    user1_books_qs = UserBook.objects.filter(user=user1).select_related('book', 'book__author')
    user2_books_qs = UserBook.objects.filter(user=user2).select_related('book', 'book__author')
    
    user1_books = set(user1_books_qs.values_list('book_id', flat=True))
    user2_books = set(user2_books_qs.values_list('book_id', flat=True))
    
    # Jaccard similarity for books
    intersection = user1_books & user2_books
    union = user1_books | user2_books
    jaccard = len(intersection) / len(union) if union else 0
    
    # Top books overlap
    user1_top = set(UserBook.objects.filter(user=user1, is_top_book=True).values_list('book_id', flat=True))
    user2_top = set(UserBook.objects.filter(user=user2, is_top_book=True).values_list('book_id', flat=True))
    top_overlap = len(user1_top & user2_top) / max(len(user1_top), len(user2_top), 1)
    
    # Genre similarity
    user1_genres = Counter()
    user2_genres = Counter()
    
    for user_book in user1_books_qs:
        weight = user_book.user_rating if user_book.user_rating else 1
        for genre in user_book.book.genres.all():
            user1_genres[genre.name] += weight
    
    for user_book in user2_books_qs:
        weight = user_book.user_rating if user_book.user_rating else 1
        for genre in user_book.book.genres.all():
            user2_genres[genre.name] += weight
    
    genre_similarity = _calculate_cosine_similarity(user1_genres, user2_genres)
    
    # Author similarity (NEW - weighted by rating)
    user1_authors = Counter()
    user2_authors = Counter()
    
    for user_book in user1_books_qs:
        weight = user_book.user_rating if user_book.user_rating else 1
        user1_authors[user_book.book.author.normalized_name] += weight
    
    for user_book in user2_books_qs:
        weight = user_book.user_rating if user_book.user_rating else 1
        user2_authors[user_book.book.author.normalized_name] += weight
    
    author_similarity = _calculate_cosine_similarity(user1_authors, user2_authors)
    
    # Rating pattern similarity
    user1_avg = UserBook.objects.filter(user=user1, user_rating__isnull=False).aggregate(avg=Avg('user_rating'))['avg'] or 0
    user2_avg = UserBook.objects.filter(user=user2, user_rating__isnull=False).aggregate(avg=Avg('user_rating'))['avg'] or 0
    rating_similarity = 1 - abs(user1_avg - user2_avg) / 5.0 if user1_avg and user2_avg else 0
    
    # Weighted combination
    final_similarity = (
        jaccard * 0.30 +
        top_overlap * 0.25 +
        genre_similarity * 0.15 +
        author_similarity * 0.15 +
        rating_similarity * 0.10 +
        0.05  # Publication era placeholder
    )
    
    return {
        'similarity_score': final_similarity,
        'jaccard': jaccard,
        'top_overlap': top_overlap,
        'genre_similarity': genre_similarity,
        'author_similarity': author_similarity,
        'rating_similarity': rating_similarity,
        'shared_books_count': len(intersection)
    }


def find_similar_users(user, top_n=20, min_similarity=0.2):
    """Find registered users similar to the given user"""
    
    # Only compare against public users
    all_users = User.objects.exclude(id=user.id).select_related('userprofile').filter(
        userprofile__dna_data__isnull=False,
        userprofile__is_public=True
    )
    
    similarities = []
    for other_user in all_users:
        similarity_data = calculate_user_similarity(user, other_user)
        
        if similarity_data['similarity_score'] >= min_similarity:
            similarities.append((other_user, similarity_data))
    
    similarities.sort(key=lambda x: x[1]['similarity_score'], reverse=True)
    return similarities[:top_n]


def calculate_anonymous_similarity(anonymous_session, user):
    """Calculate similarity between anonymous session and registered user"""
    
    anon_books = set(anonymous_session.books_data or [])
    anon_top_books = set(anonymous_session.top_books_data or [])
    anon_genres = Counter(anonymous_session.genre_distribution or {})
    anon_authors = Counter(anonymous_session.author_distribution or {})
    
    user_books_qs = UserBook.objects.filter(user=user).select_related('book', 'book__author')
    user_books = set(user_books_qs.values_list('book_id', flat=True))
    
    # Jaccard
    intersection = anon_books & user_books
    union = anon_books | user_books
    jaccard = len(intersection) / len(union) if union else 0
    
    # Top books
    user_top = set(UserBook.objects.filter(user=user, is_top_book=True).values_list('book_id', flat=True))
    top_overlap = len(anon_top_books & user_top) / max(len(anon_top_books), len(user_top), 1)
    
    # Genres
    user_genres = Counter()
    for user_book in user_books_qs:
        weight = user_book.user_rating if user_book.user_rating else 1
        for genre in user_book.book.genres.all():
            user_genres[genre.name] += weight
    
    genre_similarity = _calculate_cosine_similarity(anon_genres, user_genres)
    
    # Authors
    user_authors = Counter()
    for user_book in user_books_qs:
        weight = user_book.user_rating if user_book.user_rating else 1
        user_authors[user_book.book.author.normalized_name] += weight
    
    author_similarity = _calculate_cosine_similarity(anon_authors, user_authors)
    
    final_similarity = (
        jaccard * 0.30 +
        top_overlap * 0.25 +
        genre_similarity * 0.15 +
        author_similarity * 0.15 +
        0.15  # Other factors
    )
    
    return {
        'similarity_score': final_similarity,
        'jaccard': jaccard,
        'top_overlap': top_overlap,
        'genre_similarity': genre_similarity,
        'author_similarity': author_similarity,
        'shared_books_count': len(intersection)
    }


def calculate_similarity_with_anonymized(profile_data, anon_profile):
    """Calculate similarity with anonymized profile"""
    
    # Extract from profile_data (could be User or dict)
    if isinstance(profile_data, User):
        user = profile_data
        dna = user.userprofile.dna_data
        
        user_genres = Counter()
        user_authors = Counter()
        user_top_books = set(UserBook.objects.filter(user=user, is_top_book=True).values_list('book_id', flat=True))
        
        for genre, count in dna.get('top_genres', []):
            user_genres[genre] = count
        
        for author, count in dna.get('top_authors', []):
            normalized = Author._normalize(author)
            user_authors[normalized] = count
    else:
        # Anonymous session
        user_genres = Counter(profile_data.get('genre_distribution', {}))
        user_authors = Counter(profile_data.get('author_distribution', {}))
        user_top_books = set(profile_data.get('top_books_data', []))
    
    anon_genres = Counter(anon_profile.genre_distribution or {})
    anon_authors = Counter(anon_profile.author_distribution or {})
    anon_top_books = set(anon_profile.top_book_ids or [])
    
    genre_similarity = _calculate_cosine_similarity(user_genres, anon_genres)
    author_similarity = _calculate_cosine_similarity(user_authors, anon_authors)
    top_overlap = len(user_top_books & anon_top_books) / max(len(user_top_books), len(anon_top_books), 1) if user_top_books or anon_top_books else 0
    
    final_similarity = (
        genre_similarity * 0.30 +
        author_similarity * 0.25 +
        top_overlap * 0.20 +
        0.25  # Other factors
    )
    
    return {
        'similarity_score': final_similarity,
        'genre_similarity': genre_similarity,
        'author_similarity': author_similarity,
        'top_overlap': top_overlap,
    }


