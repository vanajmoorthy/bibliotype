---
paths:
  - "core/cache_utils.py"
  - "core/services/**"
  - "core/views.py"
---

# Caching Architecture

## Safe Cache Wrappers (`core/cache_utils.py`)

All Redis operations MUST go through these wrappers — never call `cache.get/set/delete` directly:

- `safe_cache_get(key, default=None)` — returns default on any exception
- `safe_cache_set(key, value, timeout=None)` — silently continues on failure
- `safe_cache_delete(key)` — silently continues on failure

All three catch bare `Exception`, log a warning, and track via `track_redis_cache_error()` (production only). The app continues without cache — queries just run slower.

## Redis Configuration

- **Cache:** Redis DB 1 (`redis://localhost:6379/1` via `REDIS_CACHE_URL`)
- **Celery broker/backend:** Redis DB 0 (`redis://localhost:6379/0`)
- **Dev fallback:** Set `REDIS_CACHE_URL=locmem://` for in-memory cache
- **Tests:** Use `@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})`

## Cache Key Registry

| Key | TTL | Set By | Read By |
|-----|-----|--------|---------|
| `user_recommendations_{user_id}` | 15min | recommendation_service | recommendation_service |
| `anon_recommendations_{session_key}` | 15min | recommendation_service | recommendation_service |
| `similar_users_{user_id}` | 30min | user_similarity_service | user_similarity_service |
| `anon_profiles_sample` | 1hr | recommendation_service | recommendation_service |
| `public_users_for_recs_sample` | 30min | recommendation_service | recommendation_service |
| `dna_result_{task_id}` | 1hr | tasks.py (anonymous only) | tasks.py, views.py |
| `session_key_{task_id}` | 1hr | tasks.py (anonymous only) | tasks.py |
| `community_means` | 10min | views.py | views.py |
| `fresh_pct_{bl}_{br}_{bpy}_{py}` | 10min | views.py | views.py |

## Invalidation Strategy

**Explicit invalidation (on DNA regeneration in `_save_dna_to_profile`):**
- Deletes `similar_users_{user_id}` and `user_recommendations_{user_id}`
- Clears `profile.recommendations_data` (triggers async regeneration)

**On recommendation task completion:**
- Deletes `user_recommendations_{user_id}`

**Everything else is timeout-based** — no event-driven invalidation.

## Database-Level Caching

These `UserProfile` JSONFields act as persistent caches:
- `dna_data` — overwritten on re-upload
- `reading_vibe` + `vibe_data_hash` — regenerated only when book fingerprint SHA256 changes
- `recommendations_data` + `recommendations_generated_at` — cleared on DNA save, regenerated async

## Gotchas

- Cache failures are **silent** — callers must handle `None` returns and compute fresh data
- Cache keys are manually composed strings — check for typos
- `dna_result_{task_id}` expires after 1 hour — anonymous users who wait too long to claim lose their cached result (falls back to Celery result backend)
- Percentile cache is keyed by exact user stats — any stat change generates a new cache entry
- No retry logic on cache failures — fail-fast approach
