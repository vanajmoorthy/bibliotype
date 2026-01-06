import numpy as np
from collections import Counter, defaultdict
from django.db.models import Avg
from ..models import UserBook, User, Book, Author, AnonymousUserSession, AnonymizedReadingProfile
import logging

logger = logging.getLogger(__name__)


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


def _build_user_context_for_similarity(user):
    """
    Pre-build all user data needed for similarity calculations.
    This should be called ONCE per user and reused for all comparisons.
    Returns a dict with all pre-computed data structures.
    """
    # Single query with all needed relations
    user_books_qs = (
        UserBook.objects.filter(user=user)
        .select_related("book", "book__author")
        .prefetch_related("book__genres")
    )
    
    # Convert to list ONCE to avoid multiple evaluations
    user_books_list = list(user_books_qs)
    
    # Pre-compute all data in a single pass
    book_ids = set()
    top_book_ids = set()
    book_ratings = {}  # book_id -> rating
    genre_weights = Counter()
    author_weights = Counter()
    ratings_list = []
    years_weighted = []
    
    for ub in user_books_list:
        book_id = ub.book_id
        book_ids.add(book_id)
        
        if ub.is_top_book:
            top_book_ids.add(book_id)
        
        rating = ub.user_rating
        if rating:
            book_ratings[book_id] = rating
            ratings_list.append(rating)
        
        weight = rating if rating else 3
        
        # Genres (prefetched, no query)
        for genre in ub.book.genres.all():
            genre_weights[genre.name] += weight
        
        # Authors (select_related, no query)
        author_weights[ub.book.author.normalized_name] += weight
        
        # Publication years
        if ub.book.publish_year:
            years_weighted.extend([ub.book.publish_year] * int(weight))
    
    # Rating distribution
    rating_dist = Counter(ratings_list)
    
    return {
        "user_id": user.id,
        "book_ids": book_ids,
        "top_book_ids": top_book_ids,
        "book_ratings": book_ratings,
        "genre_weights": genre_weights,
        "author_weights": author_weights,
        "rating_dist": rating_dist,
        "years_weighted": years_weighted,
        "total_books": len(book_ids),
    }


def _calculate_rating_pattern_similarity_from_context(ctx1, ctx2):
    """Compare rating distribution patterns using pre-built contexts"""
    user1_dist = ctx1["rating_dist"]
    user2_dist = ctx2["rating_dist"]

    if not user1_dist or not user2_dist:
        return 0.5  # Neutral similarity

    total1 = sum(user1_dist.values())
    total2 = sum(user2_dist.values())

    # Compare proportions at each rating level
    pattern_diff = (
        sum(abs(user1_dist.get(r, 0) / total1 - user2_dist.get(r, 0) / total2) for r in range(1, 6)) / 2
    )

    pattern_similarity = 1 - pattern_diff

    # Average difference
    ratings1 = [r for r, count in user1_dist.items() for _ in range(count)]
    ratings2 = [r for r, count in user2_dist.items() for _ in range(count)]
    
    if ratings1 and ratings2:
        avg1 = sum(ratings1) / len(ratings1)
        avg2 = sum(ratings2) / len(ratings2)
        avg_similarity = 1 - abs(avg1 - avg2) / 4.0
    else:
        avg_similarity = 0.5

    return pattern_similarity * 0.7 + avg_similarity * 0.3


def _calculate_rating_pattern_similarity(user1_books_qs, user2_books_qs):
    """Compare rating distribution patterns, not just averages (legacy, for backwards compat)"""

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


def _calculate_shared_book_correlation_from_context(ctx1, ctx2):
    """
    Pearson correlation using pre-built contexts.
    Returns (correlation, shared_count) tuple.
    """
    ratings1 = ctx1["book_ratings"]
    ratings2 = ctx2["book_ratings"]

    shared_books = set(ratings1.keys()) & set(ratings2.keys())

    if len(shared_books) < 3:
        return None, len(shared_books)

    r1_list = [ratings1[book_id] for book_id in shared_books]
    r2_list = [ratings2[book_id] for book_id in shared_books]

    mean1 = np.mean(r1_list)
    mean2 = np.mean(r2_list)

    numerator = sum((r1 - mean1) * (r2 - mean2) for r1, r2 in zip(r1_list, r2_list))
    denom1 = sum((r1 - mean1) ** 2 for r1 in r1_list) ** 0.5
    denom2 = sum((r2 - mean2) ** 2 for r2 in r2_list) ** 0.5

    if denom1 == 0 or denom2 == 0:
        return None, len(shared_books)

    correlation = numerator / (denom1 * denom2)
    normalized_correlation = (correlation + 1) / 2

    return normalized_correlation, len(shared_books)


def _calculate_shared_book_correlation(user1_books_qs, user2_books_qs):
    """
    Pearson correlation on books both users have rated (legacy).
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


def _calculate_reading_era_similarity_from_context(ctx1, ctx2):
    """Compare publication year preferences using pre-built contexts"""
    user1_years = ctx1["years_weighted"]
    user2_years = ctx2["years_weighted"]

    if not user1_years or not user2_years:
        return 0.5

    def get_decade_distribution(years):
        decades = Counter([(y // 10) * 10 for y in years])
        total = sum(decades.values())
        return {k: v / total for k, v in decades.items()}

    user1_decades = get_decade_distribution(user1_years)
    user2_decades = get_decade_distribution(user2_years)

    all_decades = set(user1_decades.keys()) | set(user2_decades.keys())
    similarity = 1 - sum(abs(user1_decades.get(d, 0) - user2_decades.get(d, 0)) for d in all_decades) / 2

    return similarity


def _calculate_reading_era_similarity(user1_books_qs, user2_books_qs):
    """Compare publication year preferences using decade distributions (legacy)"""

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


def calculate_user_similarity_from_context(ctx1, ctx2):
    """
    OPTIMIZED: Calculate similarity using pre-built contexts.
    This avoids N+1 queries by using pre-computed data.
    """
    components = {}
    weights = {}

    # 1. Shared book correlation
    correlation, shared_rated_count = _calculate_shared_book_correlation_from_context(ctx1, ctx2)
    if correlation is not None and shared_rated_count >= 5:
        components["shared_correlation"] = correlation
        confidence = min(shared_rated_count / 20, 1.0)
        weights["shared_correlation"] = 0.35 * confidence
    else:
        weights["shared_correlation"] = 0
        shared_rated_count = shared_rated_count if correlation is not None else 0

    # 2. Jaccard (book overlap)
    intersection = ctx1["book_ids"] & ctx2["book_ids"]
    union = ctx1["book_ids"] | ctx2["book_ids"]
    components["jaccard"] = len(intersection) / len(union) if union else 0
    weights["jaccard"] = 0.15 if correlation is not None else 0.25

    # 3. Top books overlap
    top1 = ctx1["top_book_ids"]
    top2 = ctx2["top_book_ids"]
    components["top_overlap"] = (
        len(top1 & top2) / max(len(top1), len(top2), 1) if (top1 or top2) else 0
    )
    weights["top_overlap"] = 0.20

    # 4. Genre similarity
    components["genre_similarity"] = _calculate_cosine_similarity(ctx1["genre_weights"], ctx2["genre_weights"])
    weights["genre_similarity"] = 0.15

    # 5. Author similarity
    components["author_similarity"] = _calculate_cosine_similarity(ctx1["author_weights"], ctx2["author_weights"])
    weights["author_similarity"] = 0.15

    # 6. Rating pattern similarity
    components["rating_pattern"] = _calculate_rating_pattern_similarity_from_context(ctx1, ctx2)
    weights["rating_pattern"] = 0.08

    # 7. Publication era similarity
    components["era_similarity"] = _calculate_reading_era_similarity_from_context(ctx1, ctx2)
    weights["era_similarity"] = 0.07

    # Normalize weights
    total_weight = sum(weights.values())
    if total_weight > 0:
        weights = {k: v / total_weight for k, v in weights.items()}

    final_similarity = sum(components[key] * weights[key] for key in components if key in weights and weights[key] > 0)

    return {
        "similarity_score": final_similarity,
        "components": components,
        "weights_used": weights,
        "shared_books_count": len(intersection),
        "shared_rated_count": shared_rated_count,
        "total_books_user1": ctx1["total_books"],
        "total_books_user2": ctx2["total_books"],
    }


def calculate_user_similarity(user1, user2):
    """
    Enhanced similarity calculation between two registered users.
    Uses adaptive weighting based on data availability.
    NOTE: For bulk comparisons, use calculate_user_similarity_from_context instead.
    """
    # Build contexts and use optimized function
    ctx1 = _build_user_context_for_similarity(user1)
    ctx2 = _build_user_context_for_similarity(user2)
    return calculate_user_similarity_from_context(ctx1, ctx2)


def _bulk_build_user_contexts(user_ids):
    """
    OPTIMIZED: Build contexts for multiple users with just 2 queries.
    Returns dict of {user_id: context}
    """
    if not user_ids:
        return {}
    
    # Single query to get all user books with relations
    all_user_books = (
        UserBook.objects
        .filter(user_id__in=user_ids)
        .select_related("book", "book__author")
        .prefetch_related("book__genres")
    )
    
    # Group by user_id
    books_by_user = defaultdict(list)
    for ub in all_user_books:
        books_by_user[ub.user_id].append(ub)
    
    # Build contexts for each user
    contexts = {}
    for user_id in user_ids:
        user_books_list = books_by_user.get(user_id, [])
        
        book_ids = set()
        top_book_ids = set()
        book_ratings = {}
        genre_weights = Counter()
        author_weights = Counter()
        ratings_list = []
        years_weighted = []
        
        for ub in user_books_list:
            book_id = ub.book_id
            book_ids.add(book_id)
            
            if ub.is_top_book:
                top_book_ids.add(book_id)
            
            rating = ub.user_rating
            if rating:
                book_ratings[book_id] = rating
                ratings_list.append(rating)
            
            weight = rating if rating else 3
            
            for genre in ub.book.genres.all():
                genre_weights[genre.name] += weight
            
            author_weights[ub.book.author.normalized_name] += weight
            
            if ub.book.publish_year:
                years_weighted.extend([ub.book.publish_year] * int(weight))
        
        contexts[user_id] = {
            "user_id": user_id,
            "book_ids": book_ids,
            "top_book_ids": top_book_ids,
            "book_ratings": book_ratings,
            "genre_weights": genre_weights,
            "author_weights": author_weights,
            "rating_dist": Counter(ratings_list),
            "years_weighted": years_weighted,
            "total_books": len(book_ids),
        }
    
    return contexts


def find_similar_users(user, top_n=20, min_similarity=0.2):
    """
    Find registered users similar to the given user.
    Returns list of (user, similarity_data) tuples sorted by similarity.
    OPTIMIZED: Uses bulk loading to avoid N+1 queries.
    """
    from .recommendation_service import safe_cache_get, safe_cache_set
    
    # Cache key for this user's similar users
    cache_key = f"similar_users_{user.id}_{top_n}_{min_similarity}"
    cached_result = safe_cache_get(cache_key)
    if cached_result is not None:
        return cached_result

    # Build current user's context ONCE
    current_user_ctx = _build_user_context_for_similarity(user)
    
    if not current_user_ctx["book_ids"]:
        return []
    
    # Find candidate users who share books with current user
    users_with_shared_books = list(
        User.objects
        .exclude(id=user.id)
        .select_related("userprofile")
        .filter(
            userprofile__dna_data__isnull=False,
            userprofile__visible_in_recommendations=True,
            user_books__book_id__in=current_user_ctx["book_ids"]
        )
        .distinct()[:500]
    )
    
    # Fallback if not enough candidates
    if len(users_with_shared_books) < top_n * 2:
        existing_ids = {u.id for u in users_with_shared_books}
        additional_users = list(
            User.objects
            .exclude(id=user.id)
            .exclude(id__in=existing_ids)
            .select_related("userprofile")
            .filter(
                userprofile__dna_data__isnull=False,
                userprofile__visible_in_recommendations=True
            )[:200]
        )
        all_users = users_with_shared_books + additional_users
    else:
        all_users = users_with_shared_books
    
    if not all_users:
        return []
    
    # BULK LOAD all candidate user contexts in ONE query
    candidate_user_ids = [u.id for u in all_users]
    candidate_contexts = _bulk_build_user_contexts(candidate_user_ids)
    
    # Create user lookup for results
    user_lookup = {u.id: u for u in all_users}
    
    # Calculate similarities using pre-built contexts (NO additional queries!)
    similarities = []
    for user_id, other_ctx in candidate_contexts.items():
        if not other_ctx["book_ids"]:  # Skip users with no books
            continue
            
        similarity_data = calculate_user_similarity_from_context(current_user_ctx, other_ctx)
        
        if similarity_data["similarity_score"] >= min_similarity:
            other_user = user_lookup[user_id]
            similarities.append((other_user, similarity_data))

    # Sort by similarity score (highest first)
    similarities.sort(key=lambda x: x[1]["similarity_score"], reverse=True)
    result = similarities[:top_n]
    
    # Cache for 30 minutes
    safe_cache_set(cache_key, result, 1800)
    return result


def calculate_anonymous_similarity_with_context(anonymous_session, user_ctx):
    """
    OPTIMIZED: Calculate similarity using pre-built user context.
    Avoids N+1 queries when comparing anonymous session to multiple users.
    """
    anon_books = set(anonymous_session.books_data or [])
    anon_top_books = set(anonymous_session.top_books_data or [])
    anon_genres = Counter(anonymous_session.genre_distribution or {})
    anon_authors = Counter(anonymous_session.author_distribution or {})
    anon_ratings = getattr(anonymous_session, 'book_ratings', None) or {}

    user_books = user_ctx["book_ids"]
    user_ratings = user_ctx["book_ratings"]
    user_top = user_ctx["top_book_ids"]
    user_genres = user_ctx["genre_weights"]
    user_authors = user_ctx["author_weights"]

    components = {}
    weights = {}

    # 1. Shared book correlation
    if anon_ratings and user_ratings:
        shared_rated_books = set(anon_ratings.keys()) & set(user_ratings.keys())
        
        if len(shared_rated_books) >= 3:
            anon_ratings_list = [anon_ratings[book_id] for book_id in shared_rated_books]
            user_ratings_list = [user_ratings[book_id] for book_id in shared_rated_books]
            
            mean_anon = np.mean(anon_ratings_list)
            mean_user = np.mean(user_ratings_list)
            
            numerator = sum((r1 - mean_anon) * (r2 - mean_user) for r1, r2 in zip(anon_ratings_list, user_ratings_list))
            denom_anon = sum((r1 - mean_anon) ** 2 for r1 in anon_ratings_list) ** 0.5
            denom_user = sum((r2 - mean_user) ** 2 for r2 in user_ratings_list) ** 0.5
            
            if denom_anon > 0 and denom_user > 0:
                correlation = numerator / (denom_anon * denom_user)
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

    # 2. Jaccard similarity
    intersection = anon_books & user_books
    union = anon_books | user_books
    components["jaccard"] = len(intersection) / len(union) if union else 0
    weights["jaccard"] = 0.15 if weights.get("shared_correlation", 0) > 0 else 0.25

    # 3. Top books overlap (using pre-built context)
    components["top_overlap"] = (
        len(anon_top_books & user_top) / max(len(anon_top_books), len(user_top), 1)
        if (anon_top_books or user_top)
        else 0
    )
    weights["top_overlap"] = 0.20

    # 4. Genre similarity (using pre-built context)
    components["genre_similarity"] = _calculate_cosine_similarity(anon_genres, user_genres)
    weights["genre_similarity"] = 0.15

    # 5. Author similarity (using pre-built context)
    components["author_similarity"] = _calculate_cosine_similarity(anon_authors, user_authors)
    weights["author_similarity"] = 0.15

    final_similarity = sum(components.get(key, 0) * weights.get(key, 0) for key in set(components.keys()) | set(weights.keys()))
    
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
        "shared_rated_count": len(shared_rated_books) if 'shared_rated_books' in dir() else 0,
    }


def calculate_anonymous_similarity(anonymous_session, user):
    """
    Calculate similarity between anonymous session and registered user.
    For bulk comparisons, use calculate_anonymous_similarity_with_context instead.
    """
    # Build user context and use optimized function
    user_ctx = _build_user_context_for_similarity(user)
    return calculate_anonymous_similarity_with_context(anonymous_session, user_ctx)


def calculate_similarity_with_anonymized(profile_data, anon_profile, user_ctx=None):
    """
    Calculate similarity with anonymized profile.
    Enhanced to match quality of user-to-user comparisons.

    Args:
        profile_data: Either a User object, AnonymousUserSession, or dict with session data
        anon_profile: AnonymizedReadingProfile object
        user_ctx: Optional pre-built user context (for optimization)
    """

    # Extract user data based on type
    if isinstance(profile_data, User):
        user = profile_data
        
        # Use pre-built context if provided (OPTIMIZATION)
        if user_ctx is not None:
            user_genres = user_ctx["genre_weights"]
            user_authors = user_ctx["author_weights"]
            user_top_books = user_ctx["top_book_ids"]
            user_rating_dist = user_ctx["rating_dist"]
        else:
            # Fallback: build from DNA data (no query needed)
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
    
    elif isinstance(profile_data, AnonymousUserSession):
        # AnonymousUserSession object
        user_genres = Counter(profile_data.genre_distribution or {})
        user_authors = Counter(profile_data.author_distribution or {})
        user_top_books = set(profile_data.top_books_data or [])
        user_rating_dist = Counter()  # Anonymous sessions may not have this
    else:
        # Dict with anonymous session data
        user_genres = Counter(profile_data.get("genre_distribution", {}))
        user_authors = Counter(profile_data.get("author_distribution", {}))
        user_top_books = set(profile_data.get("top_books_data", []))
        user_rating_dist = Counter(profile_data.get("rating_distribution", {}))

    # Extract anonymized profile data
    anon_genres = Counter(anon_profile.genre_distribution or {})
    anon_authors = Counter(anon_profile.author_distribution or {})
    anon_top_books = set(anon_profile.top_book_ids or [])
    anon_rating_dist = Counter(getattr(anon_profile, 'rating_distribution', None) or {})

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
