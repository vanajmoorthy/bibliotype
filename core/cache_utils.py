import logging

from django.core.cache import cache

from .analytics.events import track_redis_cache_error

logger = logging.getLogger(__name__)

# TTL for the DNA task-flow caches (dna_result_/session_key_/upload_nonce_/task_owner_ keys)
DNA_CACHE_TTL = 3600

# How long a user's DNA upload counts as "in flight" (dna_task_inflight_{user_id}):
# blocks a second upload and keeps the dashboard polling. Set at dispatch and
# re-armed when the task starts, so it must cover the queue wait, then the task's
# CELERY_TASK_TIME_LIMIT (900s), with margin. If a worker dies mid-task the marker
# simply expires, so the user is never locked out for longer than this.
DNA_TASK_INFLIGHT_TTL = 1800  # 30 minutes


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
