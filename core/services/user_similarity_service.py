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


def _calculate_rating_pattern_similarity(user1_books_qs, user2_books_qs):
    """Compare rating distribution patterns, not just averages"""

    # Get rating distributions
    user1_ratings = list(user1_books_qs.filter(user_rating__isnull=False).values_list("user_rating", flat=True))
    user2_ratings = list(user2_books_qs.filter(user_rating__isnull=False).values_list("user_rating", flat=True))

    if not user1_ratings or not user2_ratings:
        return 0.5  # Neutral similarity

    # Distribution similarity (are they both harsh/generous?)
    user1_dist = Counter(user1_ratings)
    user2_dist = Counter(user2_ratings)

    total1 = sum(user1_dist.values())
    total2 = sum(user2_dist.values())

    # Compare proportions at each rating level
    pattern_diff = (
        sum(abs(user1_dist.get(r, 0) / total1 - user2_dist.get(r, 0) / total2) for r in range(1, 6)) / 2
    )  # Normalize to 0-1

    pattern_similarity = 1 - pattern_diff

    # Average difference
    avg1 = sum(user1_ratings) / len(user1_ratings)
    avg2 = sum(user2_ratings) / len(user2_ratings)
    avg_similarity = 1 - abs(avg1 - avg2) / 4.0

    # Combine: pattern more important than absolute average
    return pattern_similarity * 0.7 + avg_similarity * 0.3


def _calculate_shared_book_correlation(user1_books_qs, user2_books_qs):
    """
    Pearson correlation on books both users have rated.
    Returns (correlation, shared_count) tuple.
    Correlation is normalized to 0-1 range, or None if insufficient data.
    """

    # Get shared books with ratings
    user1_dict = {ub.book_id: ub.user_rating for ub in user1_books_qs.filter(user_rating__isnull=False)}
    user2_dict = {ub.book_id: ub.user_rating for ub in user2_books_qs.filter(user_rating__isnull=False)}

    shared_books = set(user1_dict.keys()) & set(user2_dict.keys())

    if len(shared_books) < 3:  # Need minimum overlap for correlation
        return None, len(shared_books)

    ratings1 = [user1_dict[book_id] for book_id in shared_books]
    ratings2 = [user2_dict[book_id] for book_id in shared_books]

    # Pearson correlation
    mean1 = np.mean(ratings1)
    mean2 = np.mean(ratings2)

    numerator = sum((r1 - mean1) * (r2 - mean2) for r1, r2 in zip(ratings1, ratings2))
    denom1 = sum((r1 - mean1) ** 2 for r1 in ratings1) ** 0.5
    denom2 = sum((r2 - mean2) ** 2 for r2 in ratings2) ** 0.5

    if denom1 == 0 or denom2 == 0:
        return None, len(shared_books)

    correlation = numerator / (denom1 * denom2)

    # Convert from -1,1 to 0,1 range (0 = opposite tastes, 0.5 = no correlation, 1 = identical tastes)
    normalized_correlation = (correlation + 1) / 2

    return normalized_correlation, len(shared_books)


def _calculate_reading_era_similarity(user1_books_qs, user2_books_qs):
    """Compare publication year preferences using decade distributions"""

    user1_years = []
    user2_years = []

    for ub in user1_books_qs:
        if ub.book.publish_year:
            weight = ub.user_rating if ub.user_rating else 3  # Default neutral weight
            user1_years.extend([ub.book.publish_year] * int(weight))

    for ub in user2_books_qs:
        if ub.book.publish_year:
            weight = ub.user_rating if ub.user_rating else 3
            user2_years.extend([ub.book.publish_year] * int(weight))

    if not user1_years or not user2_years:
        return 0.5  # Neutral similarity

    # Compare distributions using decade bins
    def get_decade_distribution(years):
        decades = Counter([(y // 10) * 10 for y in years])
        total = sum(decades.values())
        return {k: v / total for k, v in decades.items()}

    user1_decades = get_decade_distribution(user1_years)
    user2_decades = get_decade_distribution(user2_years)

    # Calculate distribution similarity
    all_decades = set(user1_decades.keys()) | set(user2_decades.keys())
    similarity = 1 - sum(abs(user1_decades.get(d, 0) - user2_decades.get(d, 0)) for d in all_decades) / 2

    return similarity


def calculate_user_similarity(user1, user2):
    """
    Enhanced similarity calculation between two registered users.
    Uses adaptive weighting based on data availability.
    """

    # Get data with optimized queries (prefetch genres to avoid N+1)
    user1_books_qs = (
        UserBook.objects.filter(user=user1).select_related("book", "book__author").prefetch_related("book__genres")
    )

    user2_books_qs = (
        UserBook.objects.filter(user=user2).select_related("book", "book__author").prefetch_related("book__genres")
    )

    user1_books = set(user1_books_qs.values_list("book_id", flat=True))
    user2_books = set(user2_books_qs.values_list("book_id", flat=True))

    # Initialize components and weights
    components = {}
    weights = {}

    # 1. Shared book correlation (MOST IMPORTANT when available)
    correlation, shared_rated_count = _calculate_shared_book_correlation(user1_books_qs, user2_books_qs)
    if correlation is not None and shared_rated_count >= 5:
        components["shared_correlation"] = correlation
        # Weight increases with more shared books (up to 20 books for full confidence)
        confidence = min(shared_rated_count / 20, 1.0)
        weights["shared_correlation"] = 0.35 * confidence
    else:
        weights["shared_correlation"] = 0
        shared_rated_count = shared_rated_count if correlation is not None else 0

    # 2. Jaccard (book overlap) - less important if we have correlation data
    intersection = user1_books & user2_books
    union = user1_books | user2_books
    components["jaccard"] = len(intersection) / len(union) if union else 0
    weights["jaccard"] = 0.15 if correlation is not None else 0.25

    # 3. Top books overlap (people who love the same books)
    user1_top = set(UserBook.objects.filter(user=user1, is_top_book=True).values_list("book_id", flat=True))
    user2_top = set(UserBook.objects.filter(user=user2, is_top_book=True).values_list("book_id", flat=True))
    components["top_overlap"] = (
        len(user1_top & user2_top) / max(len(user1_top), len(user2_top), 1) if (user1_top or user2_top) else 0
    )
    weights["top_overlap"] = 0.20

    # 4. Genre similarity (weighted by ratings)
    user1_genres = Counter()
    user2_genres = Counter()

    for ub in user1_books_qs:
        weight = ub.user_rating if ub.user_rating else 3  # Default to neutral rating
        for genre in ub.book.genres.all():
            user1_genres[genre.name] += weight

    for ub in user2_books_qs:
        weight = ub.user_rating if ub.user_rating else 3
        for genre in ub.book.genres.all():
            user2_genres[genre.name] += weight

    components["genre_similarity"] = _calculate_cosine_similarity(user1_genres, user2_genres)
    weights["genre_similarity"] = 0.15

    # 5. Author similarity (weighted by ratings)
    user1_authors = Counter()
    user2_authors = Counter()

    for ub in user1_books_qs:
        weight = ub.user_rating if ub.user_rating else 3
        user1_authors[ub.book.author.normalized_name] += weight

    for ub in user2_books_qs:
        weight = ub.user_rating if ub.user_rating else 3
        user2_authors[ub.book.author.normalized_name] += weight

    components["author_similarity"] = _calculate_cosine_similarity(user1_authors, user2_authors)
    weights["author_similarity"] = 0.15

    # 6. Rating pattern similarity (harsh vs generous raters)
    components["rating_pattern"] = _calculate_rating_pattern_similarity(user1_books_qs, user2_books_qs)
    weights["rating_pattern"] = 0.08

    # 7. Publication era similarity (do they read similar time periods?)
    components["era_similarity"] = _calculate_reading_era_similarity(user1_books_qs, user2_books_qs)
    weights["era_similarity"] = 0.07

    # Normalize weights to sum to 1
    total_weight = sum(weights.values())
    if total_weight > 0:
        weights = {k: v / total_weight for k, v in weights.items()}

    # Calculate final score
    final_similarity = sum(components[key] * weights[key] for key in components if key in weights and weights[key] > 0)

    return {
        "similarity_score": final_similarity,
        "components": components,
        "weights_used": weights,
        "shared_books_count": len(intersection),
        "shared_rated_count": shared_rated_count,
        "total_books_user1": len(user1_books),
        "total_books_user2": len(user2_books),
    }


def find_similar_users(user, top_n=20, min_similarity=0.2):
    """
    Find registered users similar to the given user.
    Returns list of (user, similarity_data) tuples sorted by similarity.
    Optimized to limit database queries and use bulk operations.
    """
    from django.core.cache import cache
    
    # Cache key for this user's similar users
    cache_key = f"similar_users_{user.id}_{top_n}_{min_similarity}"
    cached_result = cache.get(cache_key)
    if cached_result is not None:
        return cached_result

    # Get user's book IDs for filtering
    user_book_ids = set(UserBook.objects.filter(user=user).values_list("book_id", flat=True))
    
    if not user_book_ids:
        # User has no books, return empty
        return []
    
    users_with_shared_books = (
        User.objects
        .exclude(id=user.id)
        .select_related("userprofile")
        .filter(
            userprofile__dna_data__isnull=False,
            userprofile__visible_in_recommendations=True,
            user_books__book_id__in=user_book_ids
        )
        .distinct()[:500]
    )
    
    # If we don't have enough users with shared books, also consider users with similar genres/authors
    # This is a fallback to ensure we have enough candidates
    if users_with_shared_books.count() < top_n * 2:
        additional_users = (
            User.objects
            .exclude(id=user.id)
            .exclude(id__in=[u.id for u in users_with_shared_books])
            .select_related("userprofile")
            .filter(
                userprofile__dna_data__isnull=False,
                userprofile__visible_in_recommendations=True
            )[:200]  # Get up to 200 more users
        )
        all_users = list(users_with_shared_books) + list(additional_users)
    else:
        all_users = list(users_with_shared_books)

    similarities = []
    for other_user in all_users:
        similarity_data = calculate_user_similarity(user, other_user)

        if similarity_data["similarity_score"] >= min_similarity:
            similarities.append((other_user, similarity_data))

    # Sort by similarity score (highest first)
    similarities.sort(key=lambda x: x[1]["similarity_score"], reverse=True)
    result = similarities[:top_n]
    
    # Cache for 30 minutes
    cache.set(cache_key, result, 1800)
    return result


def calculate_anonymous_similarity(anonymous_session, user):
    """
    Calculate similarity between anonymous session and registered user.
    Uses stored distribution data from anonymous session.
    Enhanced to match calculate_user_similarity when rating data is available.
    """

    anon_books = set(anonymous_session.books_data or [])
    anon_top_books = set(anonymous_session.top_books_data or [])
    anon_genres = Counter(anonymous_session.genre_distribution or {})
    anon_authors = Counter(anonymous_session.author_distribution or {})
    # Use getattr for backwards compatibility if migration hasn't run yet
    anon_ratings = getattr(anonymous_session, 'book_ratings', None) or {}  # Get stored ratings

    # Get user data with optimized queries
    user_books_qs = (
        UserBook.objects.filter(user=user).select_related("book", "book__author").prefetch_related("book__genres")
    )

    user_books = set(user_books_qs.values_list("book_id", flat=True))

    # Initialize components and weights (similar to calculate_user_similarity)
    components = {}
    weights = {}

    # 1. Shared book correlation (MOST IMPORTANT when available) - NEW!
    if anon_ratings:
        user_ratings_dict = {ub.book_id: ub.user_rating for ub in user_books_qs.filter(user_rating__isnull=False)}
        shared_rated_books = set(anon_ratings.keys()) & set(user_ratings_dict.keys())
        
        if len(shared_rated_books) >= 3:  # Need minimum overlap for correlation
            anon_ratings_list = [anon_ratings[book_id] for book_id in shared_rated_books]
            user_ratings_list = [user_ratings_dict[book_id] for book_id in shared_rated_books]
            
            # Calculate Pearson correlation
            mean_anon = np.mean(anon_ratings_list)
            mean_user = np.mean(user_ratings_list)
            
            numerator = sum((r1 - mean_anon) * (r2 - mean_user) for r1, r2 in zip(anon_ratings_list, user_ratings_list))
            denom_anon = sum((r1 - mean_anon) ** 2 for r1 in anon_ratings_list) ** 0.5
            denom_user = sum((r2 - mean_user) ** 2 for r2 in user_ratings_list) ** 0.5
            
            if denom_anon > 0 and denom_user > 0:
                correlation = numerator / (denom_anon * denom_user)
                # Convert from -1,1 to 0,1 range
                normalized_correlation = (correlation + 1) / 2
                components["shared_correlation"] = normalized_correlation
                confidence = min(len(shared_rated_books) / 20, 1.0)
                weights["shared_correlation"] = 0.35 * confidence
            else:
                weights["shared_correlation"] = 0
        else:
            weights["shared_correlation"] = 0
            shared_rated_books = set()
    else:
        weights["shared_correlation"] = 0
        shared_rated_books = set()

    # 2. Jaccard similarity on books
    intersection = anon_books & user_books
    union = anon_books | user_books
    components["jaccard"] = len(intersection) / len(union) if union else 0
    weights["jaccard"] = 0.15 if weights.get("shared_correlation", 0) > 0 else 0.25

    # 3. Top books overlap
    user_top = set(UserBook.objects.filter(user=user, is_top_book=True).values_list("book_id", flat=True))
    components["top_overlap"] = (
        len(anon_top_books & user_top) / max(len(anon_top_books), len(user_top), 1)
        if (anon_top_books or user_top)
        else 0
    )
    weights["top_overlap"] = 0.20

    # 4. Genre similarity
    user_genres = Counter()
    for ub in user_books_qs:
        weight = ub.user_rating if ub.user_rating else 3
        for genre in ub.book.genres.all():
            user_genres[genre.name] += weight

    components["genre_similarity"] = _calculate_cosine_similarity(anon_genres, user_genres)
    weights["genre_similarity"] = 0.15

    # 5. Author similarity
    user_authors = Counter()
    for ub in user_books_qs:
        weight = ub.user_rating if ub.user_rating else 3
        user_authors[ub.book.author.normalized_name] += weight

    components["author_similarity"] = _calculate_cosine_similarity(anon_authors, user_authors)
    weights["author_similarity"] = 0.15

    # Weighted combination (similar to calculate_user_similarity)
    final_similarity = sum(components.get(key, 0) * weights.get(key, 0) for key in set(components.keys()) | set(weights.keys()))
    
    # Normalize if weights don't sum to 1
    total_weight = sum(weights.values())
    if total_weight > 0:
        final_similarity = final_similarity / total_weight

    return {
        "similarity_score": final_similarity,
        "jaccard": components.get("jaccard", 0),
        "top_overlap": components.get("top_overlap", 0),
        "genre_similarity": components.get("genre_similarity", 0),
        "author_similarity": components.get("author_similarity", 0),
        "shared_correlation": components.get("shared_correlation"),
        "shared_books_count": len(intersection),
        "shared_rated_count": len(shared_rated_books),
    }


def calculate_similarity_with_anonymized(profile_data, anon_profile):
    """
    Calculate similarity with anonymized profile.
    Enhanced to match quality of user-to-user comparisons.

    Args:
        profile_data: Either a User object or dict with anonymous session data
        anon_profile: AnonymizedReadingProfile object
    """

    # Extract user data based on type
    if isinstance(profile_data, User):
        user = profile_data
        dna = user.userprofile.dna_data

        user_genres = Counter()
        user_authors = Counter()
        user_top_books = set(UserBook.objects.filter(user=user, is_top_book=True).values_list("book_id", flat=True))

        # Extract from DNA data
        for genre, count in dna.get("top_genres", []):
            user_genres[genre] = count

        for author, count in dna.get("top_authors", []):
            normalized = Author._normalize(author)
            user_authors[normalized] = count

        # Get rating distribution from DNA
        user_rating_dist = Counter()
        for rating_str, count in dna.get("ratings_distribution", {}).items():
            user_rating_dist[int(rating_str)] = count

    else:
        # Anonymous session data
        user_genres = Counter(profile_data.get("genre_distribution", {}))
        user_authors = Counter(profile_data.get("author_distribution", {}))
        user_top_books = set(profile_data.get("top_books_data", []))
        user_rating_dist = Counter(profile_data.get("rating_distribution", {}))

    # Extract anonymized profile data
    anon_genres = Counter(anon_profile.genre_distribution or {})
    anon_authors = Counter(anon_profile.author_distribution or {})
    anon_top_books = set(anon_profile.top_book_ids or [])
    anon_rating_dist = Counter(anon_profile.rating_distribution or {})

    # Calculate similarity components
    genre_similarity = _calculate_cosine_similarity(user_genres, anon_genres)
    author_similarity = _calculate_cosine_similarity(user_authors, anon_authors)

    top_overlap = (
        len(user_top_books & anon_top_books) / max(len(user_top_books), len(anon_top_books), 1)
        if user_top_books or anon_top_books
        else 0
    )

    # Rating distribution similarity
    rating_similarity = _calculate_cosine_similarity(user_rating_dist, anon_rating_dist)

    # Weighted combination
    final_similarity = (
        genre_similarity * 0.30 + author_similarity * 0.25 + top_overlap * 0.25 + rating_similarity * 0.20
    )

    return {
        "similarity_score": final_similarity,
        "genre_similarity": genre_similarity,
        "author_similarity": author_similarity,
        "top_overlap": top_overlap,
        "rating_similarity": rating_similarity,
    }


def get_match_quality_label(similarity_score):
    """
    Convert similarity score (0-1) to human-readable quality label.
    Useful for displaying results to users.
    """
    if similarity_score >= 0.80:
        return "Literary twin"
    elif similarity_score >= 0.65:
        return "Kindred reader"
    elif similarity_score >= 0.50:
        return "Some shared tastes"
    elif similarity_score >= 0.35:
        return "Some overlap"
    elif similarity_score >= 0.20:
        return "Different preferences"
    else:
        return "Opposite tastes"


# Add this new function at the end of the file
def debug_user_similarity(user1, user2):
    """
    A wrapper for calculate_user_similarity that prints a detailed
    breakdown of the calculation for debugging purposes.
    """
    print("=" * 60)
    print(f"DEBUGGING SIMILARITY BETWEEN: {user1.username} AND {user2.username}")
    print("=" * 60)

    # 1. Get the raw data from the database
    user1_books_qs = UserBook.objects.filter(user=user1).select_related("book", "book__author")
    user2_books_qs = UserBook.objects.filter(user=user2).select_related("book", "book__author")

    user1_books = {ub.book.title: ub for ub in user1_books_qs}
    user2_books = {ub.book.title: ub for ub in user2_books_qs}

    print(f"\n--- INPUT DATA ---")
    print(f"{user1.username} has {len(user1_books)} books.")
    print(f"{user2.username} has {len(user2_books)} books.")

    shared_titles = set(user1_books.keys()) & set(user2_books.keys())
    print(f"Found {len(shared_titles)} shared book titles between them.")
    if shared_titles:
        print(f"Shared titles: {list(shared_titles)[:5]}...")  # Print first 5

    # 2. Call the real calculation function
    similarity_data = calculate_user_similarity(user1, user2)

    # 3. Print the detailed report
    print("\n--- CALCULATION BREAKDOWN ---")

    components = similarity_data.get("components", {})
    weights = similarity_data.get("weights_used", {})

    print("\n[Raw Component Scores]:")
    for comp, score in components.items():
        print(f"  - {comp:<20}: {score:.4f}")

    print("\n[Weights Applied]: (How much each component matters)")
    for comp, weight in weights.items():
        if weight > 0:
            print(f"  - {comp:<20}: {weight:.4f} ({weight*100:.1f}%)")

    print("\n[Final Calculation]:")
    final_score = 0
    for comp, score in components.items():
        weight = weights.get(comp, 0)
        if weight > 0:
            contribution = score * weight
            final_score += contribution
            print(f"  - {comp:<20}: {score:.4f} (score) * {weight:.4f} (weight) = {contribution:.4f}")

    print("-" * 30)
    print(
        f"FINAL SIMILARITY SCORE: {similarity_data['similarity_score']:.4f} ({similarity_data['similarity_score']*100:.2f}%)"
    )
    print("=" * 60)

    # Add a specific check for the most likely culprit: Normalization
    print("\n--- NORMALIZATION CHECK ---")
    print("Checking if titles that *look* the same are being mismatched...")
    for title1, ub1 in user1_books.items():
        for title2, ub2 in user2_books.items():
            # If titles are very similar but not identical
            if title1.lower() != title2.lower() and title1.lower().strip() == title2.lower().strip():
                norm1 = Book._normalize_title(title1)
                norm2 = Book._normalize_title(title2)
                if norm1 == norm2:
                    print(f"  - SUCCESSFUL MATCH: '{title1}' ==> '{norm1}'")
                else:
                    print(f"  - !!! MISMATCH FOUND !!!")
                    print(f"    '{title1}' normalized to '{norm1}'")
                    print(f"    '{title2}' normalized to '{norm2}'")
