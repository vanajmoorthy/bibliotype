import logging
import math
from collections import Counter

from django.db.models import Q
from django.utils import timezone

from ..cache_utils import safe_cache_delete, safe_cache_get, safe_cache_set  # noqa: F401 - re-exported
from ..models import AnonymizedReadingProfile, AnonymousUserSession, Author, Book, Genre, User, UserBook
from .user_similarity_service import (
    _build_user_context_for_similarity,
    _bulk_build_user_contexts,
    calculate_anonymous_similarity_with_context,
    calculate_similarity_with_anonymized,
    find_similar_users,
    get_match_quality_label,
)

logger = logging.getLogger(__name__)


class RecommendationEngine:
    """
    Enhanced recommendation engine with diversity, quality filtering, and smart scoring.
    """

    def __init__(self, min_similarity=0.15, diversity_factor=0.3, quality_threshold=3.5):
        self.min_similarity = min_similarity
        self.diversity_factor = diversity_factor  # How much to promote genre diversity
        self.quality_threshold = quality_threshold  # Minimum average rating

    def get_recommendations_for_user(self, user, limit=10, include_explanations=True):
        """
        Get personalized recommendations for a registered user.

        Returns list of dicts with: book, score, confidence, explanation, sources
        """
        # Cache key based on user ID and limit
        cache_key = f"user_recommendations_{user.id}"
        cached_result = safe_cache_get(cache_key)
        if cached_result is not None:
            return cached_result

        # Get user's reading history and preferences
        user_context = self._build_user_context(user)

        # Collect candidate books from multiple sources
        candidates = self._collect_candidates_for_user(user, user_context, limit)

        # Score and rank candidates
        ranked = self._score_and_rank_candidates(candidates, user_context)

        # Apply diversity and filtering
        final_recommendations = self._apply_diversity_filter(ranked, user_context, limit)

        # Add explanations if requested
        if include_explanations:
            final_recommendations = self._add_explanations(final_recommendations, user_context)

        result = final_recommendations[:limit]

        # Cache for 15 minutes (recommendations can change as users add books)
        safe_cache_set(cache_key, result, 900)
        return result

    def get_recommendations_for_anonymous(self, session_key, limit=10, include_explanations=True):
        """
        Get recommendations for an anonymous user.
        """
        try:
            anon_session = AnonymousUserSession.objects.get(session_key=session_key)
        except AnonymousUserSession.DoesNotExist:
            logger.warning(f"AnonymousUserSession not found for session_key: {session_key}")
            return []

        # Build anonymous context
        anon_context = self._build_anonymous_context(anon_session)

        # Collect candidates
        candidates = self._collect_candidates_for_anonymous(anon_session, anon_context)

        # If we don't have enough candidates, always use fallback
        if len(candidates) < limit:
            fallback_candidates = self._get_fallback_candidates(anon_context, limit=limit * 2)
            for book_id, candidate_data in fallback_candidates.items():
                if book_id not in candidates and book_id not in anon_context["read_book_ids"]:
                    candidates[book_id] = candidate_data

        # Score and rank
        ranked = self._score_and_rank_candidates(candidates, anon_context)

        # Apply diversity
        final_recommendations = self._apply_diversity_filter(ranked, anon_context, limit)

        # Add explanations
        if include_explanations:
            final_recommendations = self._add_explanations(final_recommendations, anon_context)

        return final_recommendations[:limit]

    def _build_user_context(self, user):
        """Build comprehensive user context for filtering and scoring."""
        user_books_qs = (
            UserBook.objects.filter(user=user).select_related("book", "book__author").prefetch_related("book__genres")
        )

        # Convert to list to avoid multiple queryset evaluations
        user_books = list(user_books_qs)

        # Initialize all data structures
        read_book_ids = set()
        disliked_book_ids = set()
        top_books = set()
        series_counter = Counter()
        genre_weights = Counter()
        author_weights = Counter()
        author_count = Counter()

        # Single pass through all user books
        for ub in user_books:
            book_id = ub.book.id
            read_book_ids.add(book_id)

            # Disliked books
            if ub.user_rating and ub.user_rating <= 2:
                disliked_book_ids.add(book_id)

            # Top books
            if ub.is_top_book:
                top_books.add(book_id)

            # Calculate weight for this book
            weight = 3
            if ub.user_rating:
                weight = ub.user_rating
            if ub.is_top_book:
                weight = 5  # Treat a "top book" like a 5-star rating

            # Genre preferences (weighted by rating)
            for genre in ub.book.genres.all():
                genre_weights[genre.name] += weight

            # Author preferences (weighted by rating)
            author_id = ub.book.author.id
            author_weights[author_id] += weight
            author_count[author_id] += 1

            # Series information
            series_key = self._get_series_key(ub.book.title)
            if series_key:
                series_counter[series_key] += 1

        # Extract oversaturated series
        oversaturated_series = {series for series, count in series_counter.items() if count >= 3}

        # Normalize genre weights
        total_weight = sum(genre_weights.values())
        genre_preferences = (
            {genre: weight / total_weight for genre, weight in genre_weights.items()} if total_weight > 0 else {}
        )

        # Authors user has read extensively (3+ books)
        author_saturation = {author_id: count for author_id, count in author_count.items() if count >= 3}

        # Get DNA data for additional context
        dna = user.userprofile.dna_data if hasattr(user, "userprofile") else {}

        # Extract currently-reading genres/authors for recommendation boosting
        currently_reading_genres = set()
        currently_reading_authors = set()
        if dna and dna.get("currently_reading_books"):
            for cr_book in dna["currently_reading_books"]:
                cr_author = cr_book.get("author", "")
                cr_title = cr_book.get("title", "")
                if cr_author:
                    normalized = Author._normalize(cr_author)
                    try:
                        author_obj = Author.objects.get(normalized_name=normalized)
                        currently_reading_authors.add(author_obj.id)
                    except Author.DoesNotExist:
                        pass
                if cr_title and cr_author:
                    normalized_title = Book._normalize_title(cr_title)
                    normalized_author = Author._normalize(cr_author)
                    try:
                        book_obj = Book.objects.get(
                            normalized_title=normalized_title, author__normalized_name=normalized_author
                        )
                        for genre in book_obj.genres.all():
                            currently_reading_genres.add(genre.name)
                    except Book.DoesNotExist:
                        pass

        return {
            "user": user,
            "read_book_ids": read_book_ids,
            "disliked_book_ids": disliked_book_ids,
            "top_books": top_books,
            "oversaturated_series": oversaturated_series,
            "genre_preferences": genre_preferences,
            "author_weights": author_weights,
            "author_saturation": author_saturation,
            "dna": dna,
            "total_books_read": len(read_book_ids),
            "currently_reading_genres": currently_reading_genres,
            "currently_reading_authors": currently_reading_authors,
        }

    def _build_anonymous_context(self, anon_session):
        """Build context for anonymous user"""
        read_book_ids = set(anon_session.books_data or [])
        top_books = set(anon_session.top_books_data or [])

        # Use getattr for backwards compatibility if migration hasn't run yet
        book_ratings = getattr(anon_session, "book_ratings", None) or {}

        # Get disliked books (rated 1-2 stars) - NEW!
        disliked_book_ids = {book_id for book_id, rating in book_ratings.items() if rating <= 2}

        # Get genre preferences from stored distribution
        genre_distribution = anon_session.genre_distribution or {}
        total_genre_weight = sum(genre_distribution.values())
        genre_preferences = (
            {genre: weight / total_genre_weight for genre, weight in genre_distribution.items()}
            if total_genre_weight > 0
            else {}
        )

        # Get series info from read books
        if read_book_ids:
            read_books = list(Book.objects.filter(id__in=read_book_ids).select_related("author"))
            series_counter = self._extract_series_info(read_books, is_queryset=False)
            oversaturated_series = {series for series, count in series_counter.items() if count >= 3}
        else:
            read_books = []
            oversaturated_series = set()

        # Build author_weights from author_distribution (needed for fallback)
        # Weight by ratings if available
        author_dist = anon_session.author_distribution or {}
        author_weights = Counter()

        # Convert normalized author names to author IDs for fallback
        from ..models import Author

        # Optimize: get all authors in one query
        normalized_names = list(author_dist.keys())
        authors_dict = {
            author.normalized_name: author for author in Author.objects.filter(normalized_name__in=normalized_names)
        }

        # Get all read books with authors in one query (reuse read_books if available)
        if read_book_ids:
            if read_books:  # Reuse if we already fetched them
                read_books_with_authors = {book.id: book.author_id for book in read_books}
            else:
                read_books_with_authors = {
                    book.id: book.author_id
                    for book in Book.objects.filter(id__in=read_book_ids).select_related("author")
                }
        else:
            read_books_with_authors = {}

        for normalized_name, count in author_dist.items():
            author = authors_dict.get(normalized_name)
            if not author:
                continue
            # If we have ratings, weight by average rating for this author
            author_book_ids = [
                book_id for book_id, author_id in read_books_with_authors.items() if author_id == author.id
            ]
            if author_book_ids and book_ratings:
                author_ratings = [book_ratings.get(bid, 3) for bid in author_book_ids if bid in book_ratings]
                if author_ratings:
                    avg_rating = sum(author_ratings) / len(author_ratings)
                    author_weights[author.id] = count * (avg_rating / 5.0)  # Weight by rating
                else:
                    author_weights[author.id] = count
            else:
                author_weights[author.id] = count
        # Extract currently-reading genres/authors for recommendation boosting
        currently_reading_genres = set()
        currently_reading_authors = set()
        anon_dna = anon_session.dna_data or {}
        if anon_dna.get("currently_reading_books"):
            for cr_book in anon_dna["currently_reading_books"]:
                cr_author = cr_book.get("author", "")
                cr_title = cr_book.get("title", "")
                if cr_author:
                    normalized = Author._normalize(cr_author)
                    author = authors_dict.get(normalized)
                    if author:
                        currently_reading_authors.add(author.id)
                if cr_title and cr_author:
                    normalized_title = Book._normalize_title(cr_title)
                    normalized_author = Author._normalize(cr_author)
                    try:
                        book_obj = Book.objects.get(
                            normalized_title=normalized_title, author__normalized_name=normalized_author
                        )
                        for genre in book_obj.genres.all():
                            currently_reading_genres.add(genre.name)
                    except Book.DoesNotExist:
                        pass

        context = {
            "session": anon_session,
            "read_book_ids": read_book_ids,
            "disliked_book_ids": disliked_book_ids,  # Now includes disliked books
            "top_books": top_books,
            "oversaturated_series": oversaturated_series,
            "genre_preferences": genre_preferences,
            "author_weights": author_weights,
            "author_saturation": {},  # Could compute from books if needed
            "dna": anon_dna,
            "total_books_read": len(read_book_ids),
            "currently_reading_genres": currently_reading_genres,
            "currently_reading_authors": currently_reading_authors,
        }
        return context

    def _extract_series_info(self, books_data, is_queryset=True):
        """
        Extract series information from book titles.
        Books with similar title prefixes are likely part of a series.
        """
        series_counter = Counter()

        if is_queryset:
            # UserBook queryset
            for ub in books_data:
                series_key = self._get_series_key(ub.book.title)
                if series_key:
                    series_counter[series_key] += 1
        else:
            # List of Book objects
            for book in books_data:
                series_key = self._get_series_key(book.title)
                if series_key:
                    series_counter[series_key] += 1

        return series_counter

    def _get_series_key(self, title):
        """
        Extract series identifier from book title.
        E.g., "Harry Potter and the..." -> "harry potter"
        """
        if not title:
            return None

        # Clean title
        title_lower = title.lower()

        # Remove common series indicators
        for indicator in [" book ", " vol ", " volume ", "#", ":", " - "]:
            if indicator in title_lower:
                title_lower = title_lower.split(indicator)[0]
                break

        # Get first 2-3 significant words
        words = [w for w in title_lower.split() if len(w) > 3]
        if len(words) >= 2:
            return " ".join(words[:2])
        elif len(words) == 1:
            return words[0]

        return None

    def _collect_candidates_from_similar_users(self, similar_users, read_book_ids, candidates):
        """
        Process similar users into recommendation candidates.
        Shared by both authenticated and anonymous flows.
        """
        if not similar_users:
            return

        similar_user_ids = [su[0].id for su in similar_users]
        all_similar_user_books = (
            UserBook.objects.filter(Q(user_id__in=similar_user_ids) & (Q(is_top_book=True) | Q(user_rating__gte=4)))
            .select_related("book", "book__author")
            .prefetch_related("book__genres")
        )

        # Group by user_id for efficient lookup
        books_by_user = {}
        for ub in all_similar_user_books:
            if ub.user_id not in books_by_user:
                books_by_user[ub.user_id] = []
            books_by_user[ub.user_id].append(ub)

        for similar_user, similarity_data in similar_users:
            for ub in books_by_user.get(similar_user.id, []):
                book_id = ub.book.id
                if book_id in read_book_ids:
                    continue

                if book_id not in candidates:
                    candidates[book_id] = {
                        "book": ub.book,
                        "sources": [],
                        "max_similarity": 0,
                        "recommender_count": 0,
                        "total_weight": 0,
                    }

                candidates[book_id]["sources"].append(
                    {
                        "type": "similar_user",
                        "username": similar_user.username,
                        "user_id": similar_user.id,
                        "similarity_score": similarity_data["similarity_score"],
                        "is_top_book": ub.is_top_book,
                        "user_rating": ub.user_rating,
                        "match_quality": get_match_quality_label(similarity_data["similarity_score"]),
                        "shared_books": similarity_data.get("shared_books_count", 0),
                    }
                )

                candidates[book_id]["max_similarity"] = max(
                    candidates[book_id]["max_similarity"], similarity_data["similarity_score"]
                )
                candidates[book_id]["recommender_count"] += 1
                weight = similarity_data["similarity_score"] * (1.5 if ub.is_top_book else 1.0)
                candidates[book_id]["total_weight"] += weight

    def _collect_candidates_from_anonymized_profiles(self, matching_profiles, read_book_ids, candidates):
        """
        Process matching anonymized profiles into recommendation candidates.
        Shared by both authenticated and anonymous flows.
        """
        candidate_book_ids = set()
        for anon_profile, _ in matching_profiles:
            for book_id in anon_profile.top_book_ids[:5]:
                if book_id not in read_book_ids:
                    candidate_book_ids.add(book_id)

        if not candidate_book_ids:
            return

        books_dict = {
            book.id: book
            for book in Book.objects.filter(id__in=candidate_book_ids)
            .select_related("author")
            .prefetch_related("genres")
        }

        for anon_profile, similarity_data in matching_profiles:
            for book_id in anon_profile.top_book_ids[:5]:
                if book_id in read_book_ids:
                    continue
                book = books_dict.get(book_id)
                if not book:
                    continue

                if book_id not in candidates:
                    candidates[book_id] = {
                        "book": book,
                        "sources": [],
                        "max_similarity": 0,
                        "recommender_count": 0,
                        "total_weight": 0,
                    }

                candidates[book_id]["sources"].append(
                    {
                        "type": "anonymized_profile",
                        "similarity_score": similarity_data["similarity_score"],
                    }
                )
                candidates[book_id]["max_similarity"] = max(
                    candidates[book_id]["max_similarity"], similarity_data["similarity_score"]
                )
                candidates[book_id]["recommender_count"] += 1
                candidates[book_id]["total_weight"] += similarity_data["similarity_score"] * 0.8

    def _collect_candidates_for_user(self, user, user_context, limit=10):
        """Collect candidate books from similar users, anonymized profiles, and fallbacks."""
        candidates = {}
        read_book_ids = user_context["read_book_ids"]

        # Source 1: Similar registered users (highest quality)
        similar_users = find_similar_users(user, min_similarity=self.min_similarity)
        self._collect_candidates_from_similar_users(similar_users, read_book_ids, candidates)

        # Source 2: Anonymized profiles (medium quality)
        cache_key = "anon_profiles_sample"
        anonymized_profiles = safe_cache_get(cache_key)
        if anonymized_profiles is None:
            anonymized_profiles = list(AnonymizedReadingProfile.objects.order_by("?")[:100])
            safe_cache_set(cache_key, anonymized_profiles, 3600)

        user_ctx = _build_user_context_for_similarity(user)
        matching_profiles = []
        for anon_profile in anonymized_profiles:
            similarity_data = calculate_similarity_with_anonymized(user, anon_profile, user_ctx=user_ctx)
            if similarity_data["similarity_score"] >= self.min_similarity:
                matching_profiles.append((anon_profile, similarity_data))

        self._collect_candidates_from_anonymized_profiles(matching_profiles, read_book_ids, candidates)

        # Source 3: Fallback candidates for discovery
        fallback_candidates = self._get_fallback_candidates(user_context, limit=10)
        for book_id, candidate_data in fallback_candidates.items():
            if book_id not in candidates and book_id not in read_book_ids:
                candidates[book_id] = candidate_data

        return candidates

    def _collect_candidates_for_anonymous(self, anon_session, anon_context):
        """Collect candidates for anonymous user from similar users and anonymized profiles."""
        candidates = {}
        read_book_ids = anon_context["read_book_ids"]

        # Source 1: Find similar users via bulk context comparison
        cache_key = "public_users_for_recs_sample"
        all_users = safe_cache_get(cache_key)
        if all_users is None:
            all_users = list(
                User.objects.select_related("userprofile").filter(
                    userprofile__dna_data__isnull=False,
                    userprofile__is_public=True,
                    userprofile__visible_in_recommendations=True,
                )[:500]
            )
            safe_cache_set(cache_key, all_users, 1800)

        user_ids = [u.id for u in all_users]
        user_lookup = {u.id: u for u in all_users}
        all_user_contexts = _bulk_build_user_contexts(user_ids)

        similarities = []
        for user_id, user_ctx in all_user_contexts.items():
            if not user_ctx["book_ids"]:
                continue
            similarity_data = calculate_anonymous_similarity_with_context(anon_session, user_ctx)
            if similarity_data["similarity_score"] >= self.min_similarity:
                similarities.append((user_lookup[user_id], similarity_data))

        similarities.sort(key=lambda x: x[1]["similarity_score"], reverse=True)
        similar_users = similarities[:30]

        self._collect_candidates_from_similar_users(similar_users, read_book_ids, candidates)

        # Source 2: Anonymized profiles (use global cached sample)
        cache_key = "anon_profiles_sample"
        anonymized_profiles = safe_cache_get(cache_key)
        if anonymized_profiles is None:
            anonymized_profiles = list(AnonymizedReadingProfile.objects.order_by("?")[:100])
            safe_cache_set(cache_key, anonymized_profiles, 3600)
        matching_profiles = []
        for anon_profile in anonymized_profiles:
            similarity_data = calculate_similarity_with_anonymized(anon_session, anon_profile)
            if similarity_data["similarity_score"] >= self.min_similarity:
                matching_profiles.append((anon_profile, similarity_data))

        self._collect_candidates_from_anonymized_profiles(matching_profiles, read_book_ids, candidates)

        # Note: Fallback is handled in get_recommendations_for_anonymous after candidate collection
        return candidates

    def _score_and_rank_candidates(self, candidates, context):
        """
        Score candidates using improved algorithm with diminishing returns.
        """
        scored_candidates = []

        for book_id, candidate_data in candidates.items():
            book = candidate_data["book"]

            # Skip if book fails quality checks
            if not self._passes_quality_filters(book, context):
                continue

            # Base score: Use square root to handle diminishing returns
            # Instead of linear accumulation, use: sqrt(sum of squared similarities)
            base_score = math.sqrt(candidate_data["total_weight"])

            # Popularity factor: More recommenders = more confidence (but diminishing)
            recommender_count = candidate_data["recommender_count"]
            popularity_boost = math.log(recommender_count + 1) * 0.1

            # Quality factor: Book's average rating
            quality_score = 0
            if book.average_rating and book.average_rating >= self.quality_threshold:
                quality_score = (book.average_rating - self.quality_threshold) * 0.15

            # Genre alignment: How well does this book match user's preferences?
            genre_alignment = self._calculate_genre_alignment(book, context)

            # Recency penalty: Slightly prefer newer books
            recency_factor = self._calculate_recency_factor(book)

            # Currently-reading alignment boost
            currently_reading_boost = self._calculate_currently_reading_boost(book, context)

            # Final score calculation
            final_score = (
                base_score * 1.0
                + popularity_boost
                + quality_score
                + genre_alignment * 0.3
                + recency_factor * 0.1
                + currently_reading_boost * 0.5
            )

            base_confidence = candidate_data["max_similarity"]

            # Add a small, diminishing boost for each additional recommender.
            # math.log provides this diminishing return (2 recommenders is a good boost, 10 is not much more than 9).
            recommender_boost = math.log(candidate_data["recommender_count"] + 1) * 0.1

            # Combine and cap at 100%
            confidence = min(base_confidence + recommender_boost, 1.0)

            scored_candidates.append(
                {
                    "book": book,
                    "score": final_score,
                    "confidence": confidence,
                    "max_similarity": candidate_data["max_similarity"],
                    "recommender_count": recommender_count,
                    "sources": candidate_data["sources"],
                    "genre_alignment": genre_alignment,
                }
            )

        # Sort by score descending
        scored_candidates.sort(key=lambda x: x["score"], reverse=True)

        return scored_candidates

    def _passes_quality_filters(self, book, context):
        """Check if book passes various quality filters"""

        # Already read
        if book.id in context["read_book_ids"]:
            return False

        # User disliked similar books
        if book.id in context["disliked_book_ids"]:
            return False

        # Series saturation check
        series_key = self._get_series_key(book.title)
        if series_key and series_key in context["oversaturated_series"]:
            return False

        # Author saturation check (read 3+ books from this author)
        if book.author.id in context["author_saturation"]:
            if context["author_saturation"][book.author.id] >= 4:
                # Only recommend if it's really highly rated
                if not book.average_rating or book.average_rating < 4.3:
                    return False

        # Quality threshold
        if book.average_rating and book.average_rating < self.quality_threshold:
            return False

        return True

    def _calculate_genre_alignment(self, book, context):
        """
        Calculate how well book's genres align with user preferences.
        Returns score 0-1.
        """
        if not context["genre_preferences"]:
            return 0.5  # Neutral if no preferences known

        # Use prefetched genres - this should not trigger a query if prefetch_related was used
        book_genres = set(genre.name for genre in book.genres.all())

        if not book_genres:
            return 0.3  # Slight penalty for books without genre data

        # Calculate weighted overlap
        alignment = sum(context["genre_preferences"].get(genre, 0) for genre in book_genres)

        # Normalize
        return min(alignment * 2, 1.0)  # Cap at 1.0

    def _calculate_recency_factor(self, book):
        """
        Slight boost for newer books to promote discovery.
        Returns score 0-0.2
        """
        if not book.publish_year:
            return 0

        current_year = timezone.now().year
        years_old = current_year - book.publish_year

        if years_old < 0:  # Future publication
            return 0.15
        elif years_old <= 3:
            return 0.15
        elif years_old <= 10:
            return 0.1
        elif years_old <= 20:
            return 0.05
        else:
            return 0

    def _calculate_currently_reading_boost(self, book, context):
        """Give a small boost to books matching genres/authors of currently-reading books. Returns 0-0.15."""
        boost = 0.0
        cr_genres = context.get("currently_reading_genres", set())
        cr_authors = context.get("currently_reading_authors", set())

        if book.author.id in cr_authors:
            boost += 0.10

        book_genres = {genre.name for genre in book.genres.all()}
        matching_genres = book_genres & cr_genres
        if matching_genres:
            boost += min(len(matching_genres) * 0.05, 0.10)

        return min(boost, 0.15)

    def _apply_diversity_filter(self, ranked_candidates, context, limit):
        """
        Apply diversity filtering to avoid recommending too many books from same genre/author.
        """
        final_recommendations = []
        genre_counts = Counter()
        author_counts = Counter()

        for candidate in ranked_candidates:
            if len(final_recommendations) >= limit:
                break

            book = candidate["book"]
            # Use prefetched genres - should not trigger query if prefetch_related was used
            book_genres = set(genre.name for genre in book.genres.all())

            # Check diversity constraints
            # Don't recommend more than 3 books from same primary genre
            primary_genre_violation = any(genre_counts[genre] >= 3 for genre in book_genres)

            # Don't recommend more than 2 books from same author
            author_violation = author_counts[book.author.id] >= 2

            # Apply diversity factor: skip if violates constraints
            # BUT allow if it's a very high score (top candidates bypass diversity)
            if len(final_recommendations) >= limit * 0.5:  # After first half
                if primary_genre_violation or author_violation:
                    # Skip unless score is exceptional
                    if candidate["score"] < ranked_candidates[0]["score"] * 0.8:
                        continue

            # Add to recommendations
            final_recommendations.append(candidate)

            # Update counters
            for genre in book_genres:
                genre_counts[genre] += 1
            author_counts[book.author.id] += 1

        return final_recommendations

    def _add_explanations(self, recommendations, context):
        """Add human-readable explanation components for why each book was recommended."""
        # Build possessive form of owner's display name for public profile text
        owner_user = context.get("user")
        if owner_user:
            owner_name = owner_user.first_name or owner_user.username
            owner_possessive = f"{owner_name}'" if owner_name.endswith("s") else f"{owner_name}'s"
        else:
            owner_name = None
            owner_possessive = None

        for rec in recommendations:
            # Use a dictionary to hold the separate parts of the explanation
            rec["explanation_components"] = {}
            sources = rec["sources"]

            # Find the best source to attribute the recommendation to
            best_source = max(sources, key=lambda s: s.get("similarity_score", 0))

            # --- Component 1: Shared Books ---
            if best_source["type"] == "similar_user":
                shared_count = best_source.get("shared_books", 0)
                recommender_name = best_source.get("username", "")
                if shared_count > 1:  # Only show if there's a meaningful overlap
                    rec["explanation_components"]["shared"] = f"You share {shared_count} books in common"
                    if owner_name and recommender_name:
                        rec["explanation_components"][
                            "shared_public"
                        ] = f"{owner_name} and {recommender_name} share {shared_count} books in common"
                    else:
                        rec["explanation_components"]["shared_public"] = f"They share {shared_count} books in common"

            # --- Component 2: Genre Match ---
            if rec.get("genre_alignment", 0) > 0.6:
                # Use prefetched genres - should not trigger query
                book_genres = [genre.name for genre in rec["book"].genres.all()[:2]]
                if book_genres:
                    genre_str = ", ".join(book_genres)
                    rec["explanation_components"]["genre"] = f"matches your interest in {genre_str}"
                    if owner_possessive:
                        rec["explanation_components"][
                            "genre_public"
                        ] = f"matches {owner_possessive} interest in {genre_str}"

            # --- Component 3: Popularity among similar readers ---
            if rec["recommender_count"] >= 3:
                rec["explanation_components"]["popularity"] = f"loved by {rec['recommender_count']} similar readers"
                rec["explanation_components"][
                    "popularity_public"
                ] = f"loved by {rec['recommender_count']} other similar readers"

            # --- Component 4: Quality indicator ---
            if rec["book"].average_rating and rec["book"].average_rating >= 4.2:
                rec["explanation_components"]["rating"] = f"highly rated ({rec['book'].average_rating:.1f}★)"

            # --- Component 5: Global popularity fallback ---
            if any(s.get("type") == "global_popularity" for s in sources):
                rec["explanation_components"]["discovery"] = "popular across the Bibliotype community"

            # --- Ensure at least one explanation always exists ---
            if not rec["explanation_components"]:
                if rec["book"].average_rating:
                    rec["explanation_components"]["rating"] = f"highly rated ({rec['book'].average_rating:.1f}★)"
                else:
                    rec["explanation_components"]["discovery"] = "popular across the Bibliotype community"

        return recommendations

    def _get_fallback_candidates(self, context, limit=20):
        """
        Get fallback candidates when not enough recommendations from similar users.
        Uses smart filtering based on user preferences.
        """
        candidates = {}

        # Strategy 1: Books by favorite authors (that aren't oversaturated)
        if context.get("author_weights"):
            top_authors = [
                author_id
                for author_id, weight in context["author_weights"].most_common(10)
                if context.get("author_saturation", {}).get(author_id, 0) < 3
            ]

            for author_id in top_authors[:5]:
                books = (
                    Book.objects.filter(author_id=author_id, average_rating__gte=self.quality_threshold)
                    .exclude(id__in=context["read_book_ids"])
                    .select_related("author")
                    .prefetch_related("genres")[:3]
                )

                for book in books:
                    if len(candidates) >= limit:
                        break

                    candidates[book.id] = {
                        "book": book,
                        "sources": [{"type": "fallback_author", "reason": f"From favorite author {book.author.name}"}],
                        "max_similarity": 0.4,  # Lower base similarity
                        "recommender_count": 1,
                        "total_weight": 0.4,
                    }

        # Strategy 2: Highly-rated books in favorite genres
        if len(candidates) < limit and context.get("genre_preferences"):
            top_genres = sorted(context["genre_preferences"].items(), key=lambda x: x[1], reverse=True)[:3]

            for genre_name, weight in top_genres:
                try:
                    genre = Genre.objects.get(name=genre_name)
                    books = (
                        Book.objects.filter(genres=genre, average_rating__gte=4.0)
                        .exclude(id__in=context["read_book_ids"])
                        .order_by("-average_rating")
                        .select_related("author")
                        .prefetch_related("genres")[:5]
                    )

                    for book in books:
                        if len(candidates) >= limit:
                            break

                        if book.id not in candidates:
                            candidates[book.id] = {
                                "book": book,
                                "sources": [{"type": "fallback_genre", "reason": f"Popular {genre_name} book"}],
                                "max_similarity": 0.3,
                                "recommender_count": 1,
                                "total_weight": 0.3,
                            }
                except Genre.DoesNotExist:
                    continue

        # Strategy 3: Globally popular books (last resort)
        if len(candidates) < limit:
            remaining = limit - len(candidates)
            popular_books = (
                Book.objects.filter(average_rating__gte=4.0)
                .exclude(id__in=context["read_book_ids"])
                .exclude(id__in=candidates.keys())
                .select_related("author")
                .prefetch_related("genres")
                .order_by("-google_books_ratings_count", "-average_rating")[: remaining * 3]
            )

            seen_authors = set()
            for book in popular_books:
                if len(candidates) >= limit:
                    break
                if book.author_id in seen_authors:
                    continue
                seen_authors.add(book.author_id)

                candidates[book.id] = {
                    "book": book,
                    "sources": [{"type": "global_popularity", "similarity_score": 0.2}],
                    "max_similarity": 0.2,
                    "recommender_count": 1,
                    "total_weight": 0.2,
                }

        return candidates


# Convenience functions for backward compatibility
def get_recommendations_for_user(user, limit=10):
    """Get recommendations for a registered user"""
    engine = RecommendationEngine()
    return engine.get_recommendations_for_user(user, limit=limit)


def get_recommendations_for_anonymous(session_key, limit=10):
    """Get recommendations for an anonymous user"""
    cache_key = f"anon_recommendations_{session_key}"
    cached_result = safe_cache_get(cache_key)
    if cached_result is not None:
        return cached_result

    engine = RecommendationEngine()
    result = engine.get_recommendations_for_anonymous(session_key, limit=limit)
    safe_cache_set(cache_key, result, 900)
    return result
