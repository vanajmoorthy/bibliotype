import logging
from collections import Counter, defaultdict

import numpy as np
from django.db.models import Count

from ..dna_constants import CANONICAL_GENRE_MAP
from ..models import Book, User, UserBook
from .genre_classification import resolve_genre_names

logger = logging.getLogger(__name__)


def _canonicalize_genre_counter(counts):
    """Fold a {genre_name: weight} mapping onto canonical genre names.

    Stored genre distributions and Genre DB rows may predate genre renames
    (e.g. "classics" → "classic fiction"). Canonicalizing both sides before
    cosine similarity keeps old and new vocabularies comparable.
    """
    canonical = Counter()
    for name, weight in counts.items():
        canonical[CANONICAL_GENRE_MAP.get(name, name)] += weight
    return canonical


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


def _genres_by_book(book_ids):
    """{book_id: [genre names]} for book_ids (a list or a values("book_id") subquery)."""
    genres = defaultdict(list)
    for book_id, name in Book.genres.through.objects.filter(book_id__in=book_ids).values_list("book_id", "genre__name"):
        genres[book_id].append(name)
    return genres


def _context_from_rows(user_id, rows, genres_by_book):
    """
    Similarity context from (book_id, is_top_book, user_rating, author_normalized_name, publish_year) rows.
    Users and anonymous sessions both go through here, so they score with the same formula.
    """
    book_ids = set()
    top_book_ids = set()
    book_ratings = {}  # book_id -> rating
    genre_weights = Counter()
    author_weights = Counter()
    ratings_list = []
    years_weighted = []

    for book_id, is_top_book, rating, author_name, publish_year in rows:
        book_ids.add(book_id)

        if is_top_book:
            top_book_ids.add(book_id)

        if rating:
            book_ratings[book_id] = rating
            ratings_list.append(rating)

        weight = rating if rating else 3

        # Canonicalized + axis-resolved so old genre rows compare and nonfiction classics don't vector as fiction
        for genre_name in resolve_genre_names(genres_by_book.get(book_id, [])):
            genre_weights[genre_name] += weight

        author_weights[author_name] += weight

        if publish_year:
            years_weighted.extend([publish_year] * int(weight))

    return {
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


def _bulk_build_user_contexts(user_ids):
    """Build similarity contexts for many users in 2 queries. Returns {user_id: context}."""
    if not user_ids:
        return {}

    user_books = UserBook.objects.filter(user_id__in=user_ids)
    rows_by_user = defaultdict(list)
    for user_id, *row in user_books.values_list(
        "user_id", "book_id", "is_top_book", "user_rating", "book__author__normalized_name", "book__publish_year"
    ):
        rows_by_user[user_id].append(row)

    genres = _genres_by_book(user_books.values("book_id"))
    return {user_id: _context_from_rows(user_id, rows_by_user[user_id], genres) for user_id in user_ids}


def build_session_context(anonymous_session):
    """The same context shape as _bulk_build_user_contexts, for an AnonymousUserSession."""
    # JSONField round-trips dict keys as strings; book ids are ints.
    ratings = {int(k): v for k, v in (anonymous_session.book_ratings or {}).items()}
    top = set(anonymous_session.top_books_data or [])
    rows = [
        (book_id, book_id in top, ratings.get(book_id), author_name, publish_year)
        for book_id, author_name, publish_year in Book.objects.filter(
            id__in=anonymous_session.books_data or []
        ).values_list("id", "author__normalized_name", "publish_year")
    ]
    return _context_from_rows(None, rows, _genres_by_book([row[0] for row in rows]))


def _calculate_rating_pattern_similarity_from_context(ctx1, ctx2):
    """Compare rating distribution patterns using pre-built contexts"""
    user1_dist = ctx1["rating_dist"]
    user2_dist = ctx2["rating_dist"]

    if not user1_dist or not user2_dist:
        return 0.5  # Neutral similarity

    total1 = sum(user1_dist.values())
    total2 = sum(user2_dist.values())

    # Compare proportions at each rating level
    pattern_diff = sum(abs(user1_dist.get(r, 0) / total1 - user2_dist.get(r, 0) / total2) for r in range(1, 6)) / 2

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

    # 2. Jaccard (book overlap)
    intersection = ctx1["book_ids"] & ctx2["book_ids"]
    union = ctx1["book_ids"] | ctx2["book_ids"]
    components["jaccard"] = len(intersection) / len(union) if union else 0
    weights["jaccard"] = 0.15 if weights["shared_correlation"] > 0 else 0.25

    # 3. Top books overlap
    top1 = ctx1["top_book_ids"]
    top2 = ctx2["top_book_ids"]
    components["top_overlap"] = len(top1 & top2) / max(len(top1), len(top2), 1) if (top1 or top2) else 0
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
    Similarity between two registered users.
    NOTE: For bulk comparisons, use calculate_user_similarity_from_context instead.
    """
    contexts = _bulk_build_user_contexts([user1.id, user2.id])
    return calculate_user_similarity_from_context(contexts[user1.id], contexts[user2.id])


def find_similar_users(ctx, exclude_id=None, top_n=30, min_similarity=0.15):
    """
    Find registered users similar to a reader context (a user's, or an anonymous session's).
    exclude_id is the reader's own user id, None for an anonymous session; it also keys the cache.
    Returns list of (user, similarity_data) tuples sorted by similarity.
    """
    from ..cache_utils import safe_cache_get, safe_cache_set

    cache_key = f"similar_users_{exclude_id}" if exclude_id else None
    if cache_key:
        cached_result = safe_cache_get(cache_key)
        if cached_result is not None:
            return cached_result

    if not ctx["book_ids"]:
        return []

    eligible = User.objects.exclude(id=exclude_id).filter(
        userprofile__dna_data__isnull=False, userprofile__visible_in_recommendations=True
    )
    # Most shared books first, so the cap keeps the strongest candidates.
    users_with_shared_books = list(
        eligible.filter(user_books__book_id__in=ctx["book_ids"])
        .annotate(shared_count=Count("user_books"))
        .order_by("-shared_count", "id")
        .only("id", "username")[:500]
    )

    # Fallback if not enough candidates
    if len(users_with_shared_books) < top_n * 2:
        existing_ids = {u.id for u in users_with_shared_books}
        additional_users = list(eligible.exclude(id__in=existing_ids).only("id", "username")[:200])
        all_users = users_with_shared_books + additional_users
    else:
        all_users = users_with_shared_books

    if not all_users:
        return []

    user_lookup = {u.id: u for u in all_users}
    candidate_contexts = _bulk_build_user_contexts(list(user_lookup))

    similarities = []
    for user_id, other_ctx in candidate_contexts.items():
        if not other_ctx["book_ids"]:  # Skip users with no books
            continue

        similarity_data = calculate_user_similarity_from_context(ctx, other_ctx)

        if similarity_data["similarity_score"] >= min_similarity:
            similarities.append((user_lookup[user_id], similarity_data))

    similarities.sort(key=lambda x: x[1]["similarity_score"], reverse=True)
    result = similarities[:top_n]

    if cache_key:
        safe_cache_set(cache_key, result, 1800)  # 30 minutes
    return result


def get_recommendation_pool_size():
    """Count users eligible for similarity matching — same filter as find_similar_users."""
    from ..cache_utils import safe_cache_get, safe_cache_set

    cache_key = "recommendations_pool_count"
    cached = safe_cache_get(cache_key)
    if cached is not None:
        return cached

    count = User.objects.filter(
        userprofile__dna_data__isnull=False,
        userprofile__visible_in_recommendations=True,
    ).count()
    safe_cache_set(cache_key, count, 3600)
    return count


def calculate_similarity_with_anonymized(ctx, anon_profile):
    """
    Calculate similarity between a reader context (from _bulk_build_user_contexts
    or build_session_context) and an AnonymizedReadingProfile.
    """
    user_genres = ctx["genre_weights"]
    user_authors = ctx["author_weights"]
    user_top_books = ctx["top_book_ids"]
    user_rating_dist = ctx["rating_dist"]

    # Extract anonymized profile data
    anon_genres = _canonicalize_genre_counter(anon_profile.genre_distribution or {})
    anon_authors = Counter(anon_profile.author_distribution or {})
    anon_top_books = set(anon_profile.top_book_ids or [])
    anon_rating_dist = Counter(getattr(anon_profile, "rating_distribution", None) or {})

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


COMPARE_CACHE_TTL = 1800  # 30 min, matching similar_users_{id}


def compare_readers(users):
    """
    Pairwise similarity payload for the compare page (2-6 distinct users).

    Callers own privacy checks (is_public, dna_data present) — this only
    computes. Raises ValueError for fewer than 2 distinct users or for a
    user with an empty library, so callers can treat both as "not
    comparable". Scores come from calculate_user_similarity_from_context
    over bulk-built contexts, so the whole group costs one UserBook query.

    The payload carries user ids, not usernames, so cached entries survive
    renames; invalidation is timeout-only (a re-upload can serve scores up
    to 30 minutes stale, same class as the app's other timeout-based keys).
    """
    from ..cache_utils import safe_cache_get, safe_cache_set

    user_ids = sorted({u.id for u in users})
    if len(user_ids) < 2:
        raise ValueError("compare_readers needs at least 2 distinct users")

    cache_key = "compare_" + "_".join(str(i) for i in user_ids)
    cached = safe_cache_get(cache_key)
    if cached is not None:
        return cached

    contexts = _bulk_build_user_contexts(user_ids)
    if any(not contexts[user_id]["book_ids"] for user_id in user_ids):
        # A DNA-bearing profile can still have zero UserBook rows (failed
        # sync, wiped library); scoring it would fabricate a result from the
        # neutral 0.5 defaults, so refuse — find_similar_users skips these too.
        raise ValueError("compare_readers needs users with non-empty libraries")

    pairs = []
    for i, id_a in enumerate(user_ids):
        for id_b in user_ids[i + 1 :]:
            data = calculate_user_similarity_from_context(contexts[id_a], contexts[id_b])
            score = data["similarity_score"]
            pairs.append(
                {
                    "user_ids": [id_a, id_b],
                    "score": score,
                    "pct": int(round(score * 100)),
                    "label": get_match_quality_label(score),
                    "components_pct": {k: int(round(v * 100)) for k, v in data["components"].items()},
                    "shared_books_count": data["shared_books_count"],
                    "shared_rated_count": data["shared_rated_count"],
                }
            )

    mean_score = sum(p["score"] for p in pairs) / len(pairs)
    result = {
        "pairs": pairs,
        "mean_score": mean_score,
        "mean_pct": int(round(mean_score * 100)),
        "mean_label": get_match_quality_label(mean_score),
        "best_pair": max(pairs, key=lambda p: p["score"]),
    }

    safe_cache_set(cache_key, result, COMPARE_CACHE_TTL)
    return result
