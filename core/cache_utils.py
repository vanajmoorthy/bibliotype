import logging

from django.core.cache import cache

from .analytics.events import track_redis_cache_error

logger = logging.getLogger(__name__)


def safe_cache_get(key, default=None):
    """Safely get a value from cache, handling Redis connection errors gracefully."""
    try:
        return cache.get(key, default)
    except Exception as e:
        logger.warning(f"Cache get failed for key '{key}': {e}. Continuing without cache.")
        track_redis_cache_error(operation="get", key=key, error_type=type(e).__name__, error_message=str(e))
        return default


def safe_cache_set(key, value, timeout=None):
    """Safely set a value in cache, handling Redis connection errors gracefully."""
    try:
        cache.set(key, value, timeout)
    except Exception as e:
        logger.warning(f"Cache set failed for key '{key}': {e}. Continuing without cache.")
        track_redis_cache_error(operation="set", key=key, error_type=type(e).__name__, error_message=str(e))


def safe_cache_delete(key):
    """Safely delete a cache key, handling Redis connection errors gracefully."""
    try:
        cache.delete(key)
    except Exception as e:
        logger.warning(f"Cache delete failed for key '{key}': {e}. Continuing without cache.")
        track_redis_cache_error(operation="delete", key=key, error_type=type(e).__name__, error_message=str(e))
