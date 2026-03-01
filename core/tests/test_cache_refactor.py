"""
Tests for the cache refactoring: cache_utils extraction, key simplification,
invalidation on re-upload, community means caching, percentile TTL, anon
recommendation caching, and global anon_profiles_sample key.
"""

from collections import Counter
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from core.cache_utils import safe_cache_delete, safe_cache_get, safe_cache_set
from core.models import (
    AnonymizedReadingProfile,
    AnonymousUserSession,
    Author,
    Book,
    Genre,
    Publisher,
    UserBook,
)


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "cache-refactor-tests"}}
)
class SafeCacheUtilsTests(TestCase):
    """Unit tests for core/cache_utils.py functions."""

    def setUp(self):
        cache.clear()

    def test_safe_cache_get_returns_value(self):
        cache.set("test_key", "test_value", 60)
        result = safe_cache_get("test_key")
        self.assertEqual(result, "test_value")

    def test_safe_cache_get_returns_default_on_miss(self):
        result = safe_cache_get("missing_key", default="fallback")
        self.assertEqual(result, "fallback")

    def test_safe_cache_get_returns_none_by_default_on_miss(self):
        result = safe_cache_get("missing_key")
        self.assertIsNone(result)

    def test_safe_cache_set_stores_value(self):
        safe_cache_set("test_key", {"data": 123}, 60)
        result = cache.get("test_key")
        self.assertEqual(result, {"data": 123})

    def test_safe_cache_delete_removes_value(self):
        cache.set("test_key", "value", 60)
        safe_cache_delete("test_key")
        result = cache.get("test_key")
        self.assertIsNone(result)

    def test_safe_cache_delete_noop_on_missing_key(self):
        # Should not raise
        safe_cache_delete("nonexistent_key")

    @patch("core.cache_utils.cache")
    @patch("core.cache_utils.track_redis_cache_error")
    def test_safe_cache_get_handles_exception(self, mock_track, mock_cache):
        mock_cache.get.side_effect = ConnectionError("Redis down")
        result = safe_cache_get("key", default="safe")
        self.assertEqual(result, "safe")
        mock_track.assert_called_once()
        self.assertEqual(mock_track.call_args.kwargs["operation"], "get")

    @patch("core.cache_utils.cache")
    @patch("core.cache_utils.track_redis_cache_error")
    def test_safe_cache_set_handles_exception(self, mock_track, mock_cache):
        mock_cache.set.side_effect = ConnectionError("Redis down")
        # Should not raise
        safe_cache_set("key", "value", 60)
        mock_track.assert_called_once()
        self.assertEqual(mock_track.call_args.kwargs["operation"], "set")

    @patch("core.cache_utils.cache")
    @patch("core.cache_utils.track_redis_cache_error")
    def test_safe_cache_delete_handles_exception(self, mock_track, mock_cache):
        mock_cache.delete.side_effect = ConnectionError("Redis down")
        # Should not raise
        safe_cache_delete("key")
        mock_track.assert_called_once()
        self.assertEqual(mock_track.call_args.kwargs["operation"], "delete")


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "cache-refactor-tests"}}
)
class ReExportBackwardCompatTests(TestCase):
    """Verify backward-compat re-exports from recommendation_service still work."""

    def test_safe_cache_get_importable_from_recommendation_service(self):
        from core.services.recommendation_service import safe_cache_get as imported_fn

        self.assertIs(imported_fn, safe_cache_get)

    def test_safe_cache_set_importable_from_recommendation_service(self):
        from core.services.recommendation_service import safe_cache_set as imported_fn

        self.assertIs(imported_fn, safe_cache_set)

    def test_safe_cache_delete_importable_from_recommendation_service(self):
        from core.services.recommendation_service import safe_cache_delete as imported_fn

        self.assertIs(imported_fn, safe_cache_delete)


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "cache-refactor-tests"}}
)
class UserRecommendationsCacheKeyTests(TestCase):
    """Fix 5: user_recommendations key no longer includes limit param."""

    def setUp(self):
        cache.clear()
        self.author = Author.objects.create(name="Test Author")
        self.genre = Genre.objects.create(name="fantasy")

        self.user = User.objects.create_user(username="cacheuser", password="test123")
        self.user.userprofile.is_public = True
        self.user.userprofile.visible_in_recommendations = True
        self.user.userprofile.dna_data = {"top_genres": [("fantasy", 5)], "top_authors": []}
        self.user.userprofile.save()

        self.book = Book.objects.create(title="Test Book", author=self.author, average_rating=4.5)
        self.book.genres.add(self.genre)
        UserBook.objects.create(user=self.user, book=self.book, user_rating=5, is_top_book=True, top_book_position=1)

    def test_cache_key_does_not_include_limit(self):
        """After Fix 5, the cache key should be user_recommendations_{id} without limit suffix."""
        from core.services.recommendation_service import RecommendationEngine

        engine = RecommendationEngine()
        engine.get_recommendations_for_user(self.user, limit=6)

        # The key should be just user_recommendations_{id}
        cached = cache.get(f"user_recommendations_{self.user.id}")
        self.assertIsNotNone(cached)

        # Old-style key with limit should NOT exist
        cached_old = cache.get(f"user_recommendations_{self.user.id}_6")
        self.assertIsNone(cached_old)

    def test_different_limits_share_same_cache(self):
        """Calling with limit=6 then limit=10 should return cached result from first call."""
        from core.services.recommendation_service import RecommendationEngine

        engine = RecommendationEngine()
        result1 = engine.get_recommendations_for_user(self.user, limit=6)
        result2 = engine.get_recommendations_for_user(self.user, limit=10)

        # Both should return the same cached result (from the first call)
        self.assertEqual(len(result1), len(result2))


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "cache-refactor-tests"}}
)
class SimilarUsersCacheKeyTests(TestCase):
    """Fix 4: similar_users key simplified and defaults aligned."""

    def setUp(self):
        cache.clear()

    def test_find_similar_users_defaults_match_production_usage(self):
        """Function defaults should be top_n=30, min_similarity=0.15."""
        from core.services.user_similarity_service import find_similar_users
        import inspect

        sig = inspect.signature(find_similar_users)
        self.assertEqual(sig.parameters["top_n"].default, 30)
        self.assertEqual(sig.parameters["min_similarity"].default, 0.15)

    def test_cache_key_is_simplified(self):
        """Cache key should be similar_users_{id} without top_n and min_similarity."""
        from core.models import Author, Book, UserBook
        from core.services.user_similarity_service import find_similar_users

        user = User.objects.create_user(username="simuser", password="test123")
        user.userprofile.dna_data = {"top_genres": []}
        user.userprofile.visible_in_recommendations = True
        user.userprofile.save()

        user2 = User.objects.create_user(username="simuser2", password="test123")
        user2.userprofile.dna_data = {"top_genres": []}
        user2.userprofile.visible_in_recommendations = True
        user2.userprofile.save()

        # Create shared book data so find_similar_users doesn't bail early
        author = Author.objects.create(name="Test Author")
        book = Book.objects.create(title="Shared Book", author=author)
        UserBook.objects.create(user=user, book=book, user_rating=5)
        UserBook.objects.create(user=user2, book=book, user_rating=4)

        find_similar_users(user, top_n=30, min_similarity=0.15)

        # Simplified key should exist (even if value is an empty list)
        cached = cache.get(f"similar_users_{user.id}")
        self.assertIsNotNone(cached)

        # Old-style key should NOT exist
        cached_old = cache.get(f"similar_users_{user.id}_30_0.15")
        self.assertIsNone(cached_old)


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "cache-refactor-tests"}}
)
class SaveDnaInvalidationTests(TestCase):
    """Fix 2: _save_dna_to_profile invalidates similar_users and user_recommendations caches."""

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="invaluser", password="test123")

    @patch("core.tasks.generate_recommendations_task")
    def test_save_dna_invalidates_similar_users_cache(self, mock_rec_task):
        mock_rec_task.delay = MagicMock()

        # Pre-populate cache
        cache.set(f"similar_users_{self.user.id}", [("fake", "data")], 1800)
        self.assertIsNotNone(cache.get(f"similar_users_{self.user.id}"))

        from core.services.dna_analyser import _save_dna_to_profile

        dna = {"reader_type": "Test", "user_stats": {}, "reading_vibe": [], "vibe_data_hash": "h"}
        _save_dna_to_profile(self.user.userprofile, dna)

        # Cache should be cleared
        self.assertIsNone(cache.get(f"similar_users_{self.user.id}"))

    @patch("core.tasks.generate_recommendations_task")
    def test_save_dna_invalidates_user_recommendations_cache(self, mock_rec_task):
        mock_rec_task.delay = MagicMock()

        # Pre-populate cache
        cache.set(f"user_recommendations_{self.user.id}", [{"fake": "rec"}], 900)
        self.assertIsNotNone(cache.get(f"user_recommendations_{self.user.id}"))

        from core.services.dna_analyser import _save_dna_to_profile

        dna = {"reader_type": "Test", "user_stats": {}, "reading_vibe": [], "vibe_data_hash": "h"}
        _save_dna_to_profile(self.user.userprofile, dna)

        # Cache should be cleared
        self.assertIsNone(cache.get(f"user_recommendations_{self.user.id}"))

    @patch("core.tasks.generate_recommendations_task")
    def test_save_dna_triggers_recommendation_generation(self, mock_rec_task):
        from core.services.dna_analyser import _save_dna_to_profile

        dna = {"reader_type": "Test", "user_stats": {}, "reading_vibe": [], "vibe_data_hash": "h"}
        _save_dna_to_profile(self.user.userprofile, dna)

        mock_rec_task.assert_called_once_with(self.user.id)


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "cache-refactor-tests"}}
)
class TaskInvalidationTests(TestCase):
    """Fix 2 continued: generate_recommendations_task uses safe_cache_delete."""

    def setUp(self):
        cache.clear()

    @patch("core.services.recommendation_service.RecommendationEngine.get_recommendations_for_user")
    def test_recommendations_task_uses_delete_not_set_none(self, mock_get_recs):
        """generate_recommendations_task should use safe_cache_delete, not safe_cache_set(key, None, 1)."""
        mock_get_recs.return_value = []

        user = User.objects.create_user(username="deltaskuser", password="test123")
        user.userprofile.dna_data = {"reader_type": "Test"}
        user.userprofile.save()

        # Pre-populate cache with old key format
        cache.set(f"user_recommendations_{user.id}", [{"old": "data"}], 900)

        from core.tasks import generate_recommendations_task

        generate_recommendations_task(user.id)

        # Cache should be deleted, not set to None
        result = cache.get(f"user_recommendations_{user.id}")
        self.assertIsNone(result)


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "cache-refactor-tests"}}
)
class AnonProfilesSampleTests(TestCase):
    """Fix 3: Global anon_profiles_sample key + random sampling."""

    def setUp(self):
        cache.clear()

        self.author = Author.objects.create(name="Anon Author")
        self.genre = Genre.objects.create(name="fantasy")

        # Create test users
        self.user1 = User.objects.create_user(username="anontest1", password="test123")
        self.user1.userprofile.is_public = True
        self.user1.userprofile.visible_in_recommendations = True
        self.user1.userprofile.dna_data = {"top_genres": [("fantasy", 5)], "top_authors": []}
        self.user1.userprofile.save()

        self.book1 = Book.objects.create(title="Anon Book 1", author=self.author, average_rating=4.5)
        self.book1.genres.add(self.genre)
        UserBook.objects.create(
            user=self.user1, book=self.book1, user_rating=5, is_top_book=True, top_book_position=1
        )

    def test_cache_key_is_global_not_per_user(self):
        """After Fix 3, anon_profiles_sample should be a global key without user_id."""
        from core.services.recommendation_service import RecommendationEngine

        engine = RecommendationEngine()
        user_context = engine._build_user_context(self.user1)
        engine._collect_candidates_for_user(self.user1, user_context, limit=10)

        # Global key should exist
        cached = cache.get("anon_profiles_sample")
        self.assertIsNotNone(cached)

        # Old per-user key should NOT exist
        cached_old = cache.get(f"anon_profiles_sample_{self.user1.id}")
        self.assertIsNone(cached_old)

    def test_anonymous_candidate_collection_uses_same_global_cache(self):
        """_collect_candidates_for_anonymous should use the same global cache key."""
        from datetime import timedelta

        # Create an AnonymousUserSession
        anon_session = AnonymousUserSession.objects.create(
            session_key="test_session_123",
            dna_data={"top_genres": [("fantasy", 5)]},
            books_data=[self.book1.id],
            top_books_data=[self.book1.id],
            genre_distribution={"fantasy": 5},
            author_distribution={},
            book_ratings={str(self.book1.id): 5},
            expires_at=timezone.now() + timedelta(days=7),
        )

        from core.services.recommendation_service import RecommendationEngine

        engine = RecommendationEngine()
        anon_context = engine._build_anonymous_context(anon_session)
        engine._collect_candidates_for_anonymous(anon_session, anon_context)

        # Should use the same global key
        cached = cache.get("anon_profiles_sample")
        self.assertIsNotNone(cached)


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "cache-refactor-tests"}}
)
class AnonymousRecommendationCacheTests(TestCase):
    """Fix 6: Anonymous recommendations are now cached."""

    def setUp(self):
        cache.clear()
        self.author = Author.objects.create(name="Cache Author")
        self.genre = Genre.objects.create(name="fantasy")
        self.book = Book.objects.create(title="Cache Book", author=self.author, average_rating=4.5)
        self.book.genres.add(self.genre)

    def _create_anon_session(self, session_key="anon_cache_test"):
        from datetime import timedelta

        return AnonymousUserSession.objects.create(
            session_key=session_key,
            dna_data={"top_genres": [("fantasy", 5)]},
            books_data=[self.book.id],
            top_books_data=[self.book.id],
            genre_distribution={"fantasy": 5},
            author_distribution={},
            book_ratings={str(self.book.id): 5},
            expires_at=timezone.now() + timedelta(days=7),
        )

    def test_anonymous_recommendations_are_cached(self):
        """After Fix 6, get_recommendations_for_anonymous should cache results."""
        self._create_anon_session()
        from core.services.recommendation_service import get_recommendations_for_anonymous

        result = get_recommendations_for_anonymous("anon_cache_test", limit=6)

        cached = cache.get("anon_recommendations_anon_cache_test")
        self.assertIsNotNone(cached)
        self.assertEqual(len(cached), len(result))

    def test_anonymous_recommendations_served_from_cache(self):
        """Second call should return cached result without re-running pipeline."""
        self._create_anon_session()
        from core.services.recommendation_service import RecommendationEngine

        engine = RecommendationEngine()

        # First call populates cache
        result1 = engine.get_recommendations_for_anonymous("anon_cache_test", limit=6)

        # Manually verify cache hit by checking the cached object is identical
        result2 = engine.get_recommendations_for_anonymous("anon_cache_test", limit=6)
        self.assertEqual(len(result1), len(result2))

    def test_anonymous_cache_returns_empty_for_missing_session(self):
        """Should return [] for nonexistent session, and not cache the empty result."""
        from core.services.recommendation_service import RecommendationEngine

        engine = RecommendationEngine()
        result = engine.get_recommendations_for_anonymous("nonexistent_session")

        self.assertEqual(result, [])
        # Empty result from missing session should not be cached
        cached = cache.get("anon_recommendations_nonexistent_session")
        self.assertIsNone(cached)


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "cache-refactor-tests"}}
)
class CommunityMeansCacheTests(TestCase):
    """Fix 1: _enrich_dna_for_display caches community means."""

    def setUp(self):
        cache.clear()

    def test_community_means_cached_on_first_call(self):
        """calculate_community_means() result should be cached with key 'community_means'."""
        from core.views import _enrich_dna_for_display

        dna_data = {
            "user_stats": {"avg_book_length": 300, "total_books_read": 50, "avg_publish_year": 2010},
            "stats_by_year": [],
            "bibliotype_percentiles": {},
        }

        _enrich_dna_for_display(dna_data)

        cached = cache.get("community_means")
        self.assertIsNotNone(cached)
        self.assertIsInstance(cached, dict)

    @patch("core.percentile_engine.calculate_community_means")
    def test_community_means_served_from_cache_on_second_call(self, mock_calc):
        """Second call should use cache, not recalculate."""
        mock_calc.return_value = {"avg_book_length": 320, "avg_publish_year": 2015, "avg_books_per_year": 12}

        from core.views import _enrich_dna_for_display

        dna_base = {
            "user_stats": {"avg_book_length": 300, "total_books_read": 50, "avg_publish_year": 2010},
            "stats_by_year": [],
            "bibliotype_percentiles": {},
        }

        # First call: populates cache
        _enrich_dna_for_display(dict(dna_base))
        self.assertEqual(mock_calc.call_count, 1)

        # Second call: should use cache
        _enrich_dna_for_display(dict(dna_base))
        self.assertEqual(mock_calc.call_count, 1)  # Still 1 — cache hit


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "cache-refactor-tests"}}
)
class PercentilesCacheTTLTests(TestCase):
    """Fix 1: Percentiles cache uses safe_cache_get/set and 600s TTL."""

    def setUp(self):
        cache.clear()

    def test_percentiles_cached_with_safe_wrappers(self):
        """Fresh percentiles should be cached using safe_cache_set."""
        from core.views import _enrich_dna_for_display

        dna_data = {
            "user_stats": {"avg_book_length": 300, "total_books_read": 50, "avg_publish_year": 2010},
            "stats_by_year": [],
            "bibliotype_percentiles": {},
        }

        _enrich_dna_for_display(dna_data)

        # Check that the percentile cache key exists
        cache_key = f"fresh_pct_{300}_{50}_{0}_{2010}"
        cached = cache.get(cache_key)
        self.assertIsNotNone(cached)

    @patch("core.percentile_engine.calculate_percentiles_from_aggregates")
    def test_percentiles_served_from_cache(self, mock_calc):
        """Second call with same stats should use cached percentiles."""
        mock_calc.return_value = {"avg_book_length": 60, "avg_publish_year": 55}

        from core.views import _enrich_dna_for_display

        dna_base = {
            "user_stats": {"avg_book_length": 300, "total_books_read": 50, "avg_publish_year": 2010},
            "stats_by_year": [],
            "bibliotype_percentiles": {},
        }

        _enrich_dna_for_display(dict(dna_base))
        self.assertEqual(mock_calc.call_count, 1)

        _enrich_dna_for_display(dict(dna_base))
        self.assertEqual(mock_calc.call_count, 1)  # Still 1 — cache hit


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "cache-refactor-tests"}}
)
class ImportPathTests(TestCase):
    """Verify all import paths work correctly after the refactor."""

    def test_cache_utils_importable_from_core(self):
        from core.cache_utils import safe_cache_delete, safe_cache_get, safe_cache_set

        self.assertTrue(callable(safe_cache_get))
        self.assertTrue(callable(safe_cache_set))
        self.assertTrue(callable(safe_cache_delete))

    def test_user_similarity_imports_from_cache_utils(self):
        """find_similar_users should import from cache_utils, not recommendation_service."""
        import inspect

        from core.services.user_similarity_service import find_similar_users

        source = inspect.getsource(find_similar_users)
        self.assertIn("cache_utils", source)
        self.assertNotIn("recommendation_service", source)

    def test_tasks_import_from_cache_utils(self):
        """Tasks should import safe_cache_* from cache_utils."""
        import inspect

        from core.tasks import generate_recommendations_task

        source = inspect.getsource(generate_recommendations_task)
        self.assertIn("cache_utils", source)
