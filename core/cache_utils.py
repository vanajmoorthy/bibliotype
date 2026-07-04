import logging

from django.core.cache import cache

from .analytics.events import track_redis_cache_error

logger = logging.getLogger(__name__)

# TTL for the DNA task-flow caches (dna_result_/session_key_/upload_nonce_/task_owner_ keys)
DNA_CACHE_TTL = 3600


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


def safe_cache_add(key, value, timeout=None):
    """Atomically set a cache key only if it doesn't already exist.

    Returns True if the key was set (caller "won" the race), False if it
    already existed (caller should skip the guarded work). On Redis failure
    we fail open and return True — duplicate work is preferable to silently
    dropping work when the cache is unavailable.
    """
    try:
        return cache.add(key, value, timeout)
    except Exception as e:
        logger.warning(f"Cache add failed for key '{key}': {e}. Continuing without cache.")
        track_redis_cache_error(operation="add", key=key, error_type=type(e).__name__, error_message=str(e))
        return True
