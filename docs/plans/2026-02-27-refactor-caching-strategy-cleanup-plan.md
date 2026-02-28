---
title: "refactor: Clean up caching strategy"
type: refactor
status: active
date: 2026-02-27
---

# refactor: Clean up caching strategy

## Overview

Address caching inefficiencies across the recommendation engine and supporting services. Eight changes total: one prerequisite extraction, six concrete fixes, and one analysis of the anonymous triple-storage pattern.

## Problem Statement

1. **`safe_cache_get`/`safe_cache_set` live in `recommendation_service.py`** — They're general-purpose cache utilities that force cross-layer imports (e.g., views importing from a service). There's also no `safe_cache_delete`, so invalidation uses the fragile `safe_cache_set(key, None, 1)` pattern.
2. **Percentiles cached for only 60 seconds** — `AggregateAnalytics` is a singleton updated infrequently. A 60s TTL means nearly every page load recalculates from DB. Additionally, `calculate_community_means()` in the same function has **no caching at all**.
3. **No cache invalidation for similar users on re-upload** — `_save_dna_to_profile()` clears `recommendations_data` but not `similar_users_{user_id}_*`. Stale data persists for up to 30 minutes.
4. **`anon_profiles_sample_{user_id}` key is per-user but data is global** — The query is `AnonymizedReadingProfile.objects.all()[:100]` (same profiles for everyone). There's also a second uncached call site in `_collect_candidates_for_anonymous()`. The query always returns the 100 most recent profiles rather than a representative random sample.
5. **`similar_users` cache key includes params that never vary** — Always called with `top_n=30, min_similarity=0.15`, but the function defaults are `top_n=20, min_similarity=0.2` — a mismatch between signature and usage.
6. **`user_recommendations` cache key includes `limit` param** — The task invalidates `_6` but the convenience function defaults to `limit=10`, creating a potential stale-cache gap.
7. **Anonymous recommendations are uncached** — `get_recommendations_for_anonymous()` runs the full recommendation pipeline (500-user comparison, 100-profile comparison, scoring, ranking) on every single dashboard page load. This is the most expensive uncached path in the system.
8. **Anonymous flow stores data in 3 places simultaneously** — Session, Redis cache, and `AnonymousUserSession` DB model.

---

## Prerequisite: Extract cache utilities to `core/cache_utils.py`

**Current:** `safe_cache_get` and `safe_cache_set` live in `core/services/recommendation_service.py` (lines 22-56). Any module that wants safe cache access must import from the recommendation service — a layering violation.

**Change:** Create `core/cache_utils.py` with three functions:

```python
# core/cache_utils.py
from django.core.cache import cache
import logging

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
```

**Then update `recommendation_service.py`:** Replace the inline definitions with re-exports for backward compatibility:

```python
# core/services/recommendation_service.py (top of file)
from ..cache_utils import safe_cache_get, safe_cache_set, safe_cache_delete  # noqa: F401 - re-exported
```

This keeps all existing imports working (`from .services.recommendation_service import safe_cache_get`) while allowing new code to import from the proper location (`from .cache_utils import safe_cache_get`).

**All other modules in this plan** should import from `core.cache_utils` directly.

---

## Fix 1: Increase percentiles cache TTL + cache community means

**File:** `core/views.py` (lines 188-221 in `_enrich_dna_for_display`)

### 1a. Cache `calculate_community_means()` (currently uncached)

**Current** (line 189):
```python
raw_community = calculate_community_means()
```

This calls `AggregateAnalytics.get_instance()` (a DB read) on **every single page load** — both dashboard and public profile views. The data changes only when new DNA is generated or the daily anonymization task runs.

**Change:** Add a global cache key with 600s TTL:

```python
from .cache_utils import safe_cache_get, safe_cache_set

community_cache_key = "community_means"
raw_community = safe_cache_get(community_cache_key)
if raw_community is None:
    raw_community = calculate_community_means()
    safe_cache_set(community_cache_key, raw_community, 600)
```

### 1b. Increase percentiles cache TTL

**Current** (lines 217-221):
```python
cache_key = f"fresh_pct_{bl}_{br}_{bpy}_{py}"
fresh_percentiles = cache.get(cache_key)
if fresh_percentiles is None:
    fresh_percentiles = calculate_percentiles_from_aggregates(user_stats)
    cache.set(cache_key, fresh_percentiles or {}, 60)
```

**Change:** Increase TTL from `60` to `600` (10 minutes) and use safe wrappers:

```python
fresh_percentiles = safe_cache_get(cache_key)
if fresh_percentiles is None:
    fresh_percentiles = calculate_percentiles_from_aggregates(user_stats)
    safe_cache_set(cache_key, fresh_percentiles or {}, 600)
```

**Rationale:** `AggregateAnalytics` is a singleton row that changes only when a new user generates DNA or the daily anonymization task runs. Percentile shifts from a single new user are negligible. 10 minutes eliminates ~10x redundant DB reads while staying fresh enough for bursts of new signups.

**Note on per-user cache keys:** The `fresh_pct_{stats}` key is content-addressed — users with identical stats share the same entry. This is a good design. The cache primarily helps same-user page refreshes within the TTL window. A future improvement could cache the `AggregateAnalytics` histograms globally and compute percentiles in Python, reducing DB reads to O(1) per TTL regardless of unique users. Not needed at current scale.

**Combined impact:** Reduces `AggregateAnalytics` DB reads from 2 per page load to ~2 per 10 minutes.

---

## Fix 2: Invalidate similar users cache on DNA re-upload

**File:** `core/services/dna_analyser.py` (lines 117-155)

**Current:** `_save_dna_to_profile()` clears `recommendations_data` and triggers `generate_recommendations_task.delay()`, but does not touch the `similar_users` cache. The newly triggered recommendations task calls `find_similar_users()`, which returns stale cached data.

**Change:** Add explicit cache invalidation using `safe_cache_delete` after saving profile, before triggering the recommendations task:

```python
def _save_dna_to_profile(profile, dna_data):
    # ... existing code ...

    profile.save(update_fields=[...])

    # Invalidate stale caches for this user
    from ..cache_utils import safe_cache_delete
    safe_cache_delete(f"similar_users_{profile.user.id}")
    safe_cache_delete(f"user_recommendations_{profile.user.id}")

    # Trigger async recommendation generation
    from ..tasks import generate_recommendations_task
    generate_recommendations_task.delay(profile.user.id)
```

**Also update** the existing invalidation in `generate_recommendations_task` (`core/tasks.py:485-486`):

```python
# Before (fragile set-to-None pattern):
safe_cache_set(cache_key, None, 1)

# After (explicit delete):
from .cache_utils import safe_cache_delete
safe_cache_delete(f"user_recommendations_{user_id}")
```

**Note on cross-user staleness:** We only invalidate the *current user's* similar users entry. Other users who had *this user* in their cached similar list will pick up the change when their own 30-minute TTL expires. Building a reverse index to proactively invalidate all referencing users is not worth the complexity.

---

## Fix 3: Global anon_profiles_sample key + random sampling + second call site

**Files:** `core/services/recommendation_service.py` (lines 469-473 and line 528)

### 3a. Fix the cache key and add random sampling

**Current** (lines 469-473 in `_collect_candidates_for_user`):
```python
cache_key = f"anon_profiles_sample_{user.id}"
anonymized_profiles = safe_cache_get(cache_key)
if anonymized_profiles is None:
    anonymized_profiles = list(AnonymizedReadingProfile.objects.all()[:100])
    safe_cache_set(cache_key, anonymized_profiles, 3600)
```

**Problems:**
1. `user.id` in the key creates N redundant cache entries storing identical data
2. `AnonymizedReadingProfile` has `ordering = ["-created_at"]`, so `.all()[:100]` always returns the 100 most recent profiles — not a representative sample of the community
3. As the table grows, the recommendation engine only ever compares against the newest cohort

**Change:** Remove `user.id` from key and use random sampling:

```python
cache_key = "anon_profiles_sample"
anonymized_profiles = safe_cache_get(cache_key)
if anonymized_profiles is None:
    anonymized_profiles = list(AnonymizedReadingProfile.objects.order_by("?")[:100])
    safe_cache_set(cache_key, anonymized_profiles, 3600)
```

`order_by("?")` triggers `ORDER BY RANDOM()` in PostgreSQL. This is normally expensive on large tables (full sequential scan), but since the result is cached for 1 hour, it only runs on cache miss — at most once per hour for the entire application. Acceptable at any realistic table size for this app.

**Impact:** Single global cache entry instead of N per-user entries. Each hourly refresh gets a fresh random sample, improving recommendation diversity over time.

### 3b. Fix the second uncached call site

**Current** (line 528 in `_collect_candidates_for_anonymous`):
```python
anonymized_profiles = list(AnonymizedReadingProfile.objects.all()[:100])
```

This is completely uncached — hits the DB on every anonymous dashboard load.

**Change:** Use the same global cache key:

```python
cache_key = "anon_profiles_sample"
anonymized_profiles = safe_cache_get(cache_key)
if anonymized_profiles is None:
    anonymized_profiles = list(AnonymizedReadingProfile.objects.order_by("?")[:100])
    safe_cache_set(cache_key, anonymized_profiles, 3600)
```

---

## Fix 4: Simplify `similar_users` cache key + align function defaults

**File:** `core/services/user_similarity_service.py` (lines 323, 332-333)

### 4a. Simplify the cache key

**Current** (line 332):
```python
cache_key = f"similar_users_{user.id}_{top_n}_{min_similarity}"
```

**Change:**
```python
cache_key = f"similar_users_{user.id}"
```

**Rationale:** `find_similar_users()` is called in exactly one production location — `_collect_candidates_for_user()` at line 465 of `recommendation_service.py` — always with `top_n=30, min_similarity=0.15`. The parameterized key creates the illusion of flexibility that doesn't exist and would silently return stale data if someone later called with different params.

### 4b. Align function defaults with actual usage

**Current** (line 323):
```python
def find_similar_users(user, top_n=20, min_similarity=0.2):
```

**Change:**
```python
def find_similar_users(user, top_n=30, min_similarity=0.15):
```

The current defaults (`20`, `0.2`) are never used — the single production call site always overrides them. Aligning the defaults with reality makes the simplified cache key honest and prevents confusion if someone calls the function without explicit params.

---

## Fix 5: Simplify `user_recommendations` cache key

**Files:** `core/services/recommendation_service.py` (line 76), `core/tasks.py` (line 485)

**Current:**

In `recommendation_service.py:76`:
```python
cache_key = f"user_recommendations_{user.id}_{limit}"
```

In `tasks.py:485` (invalidation):
```python
cache_key = f"user_recommendations_{user_id}_6"
```

**Problem:** The task hardcodes `_6` (matching its `limit=6` call), but `get_recommendations_for_user()` defaults to `limit=10`. If the convenience function is ever called with the default limit, it creates a `_10` cache entry that the task's invalidation never clears.

**Change:** Remove `limit` from the cache key in both locations:

```python
# recommendation_service.py
cache_key = f"user_recommendations_{user.id}"

# tasks.py (already handled by Fix 2's safe_cache_delete)
safe_cache_delete(f"user_recommendations_{user_id}")
```

Same rationale as Fix 4: the parameterized key encodes flexibility that doesn't exist in practice and creates invalidation gaps.

---

## Fix 6: Cache anonymous recommendations

**File:** `core/services/recommendation_service.py` (lines 103-136)

**Current:** `get_recommendations_for_anonymous()` runs the full recommendation pipeline on every dashboard page load for anonymous users:
1. Queries `AnonymousUserSession` from DB
2. Builds anonymous context (DB query for books and authors)
3. Fetches up to 500 public users (cached for 30 min, good)
4. Bulk-builds user contexts for all 500 users (large DB query)
5. Computes similarity against all 500 users
6. Queries 100 anonymized profiles
7. Scores, ranks, and filters with diversity + explanations

For an anonymous user who refreshes their dashboard 5 times, this runs 5 times. This is the most expensive uncached path in the system.

**Change:** Add a session-key-based cache with 15-minute TTL (matching the authenticated user caching):

```python
def get_recommendations_for_anonymous(self, session_key, limit=10, include_explanations=True):
    """Get recommendations for an anonymous user."""
    # Check cache first
    cache_key = f"anon_recommendations_{session_key}"
    cached_result = safe_cache_get(cache_key)
    if cached_result is not None:
        return cached_result

    try:
        anon_session = AnonymousUserSession.objects.get(session_key=session_key)
    except AnonymousUserSession.DoesNotExist:
        logger.warning(f"AnonymousUserSession not found for session_key: {session_key}")
        return []

    # ... existing recommendation pipeline (unchanged) ...

    result = final_recommendations[:limit]

    # Cache for 15 minutes
    safe_cache_set(cache_key, result, 900)
    return result
```

**Why 15 minutes?** Matches the TTL for authenticated user recommendations. Anonymous users can't re-upload without the page reloading (which clears context), so there's no invalidation concern — the cache naturally expires.

**Impact:** Reduces DB queries and CPU-intensive similarity computations from O(page_loads) to O(1) per 15 minutes per anonymous session.

---

## Item 7: Anonymous Triple-Storage Analysis

### What's happening today

When an anonymous user uploads a CSV, their data ends up in **three places**:

| Store | What | Set when | Read when | Lifetime |
|---|---|---|---|---|
| **Redis cache** | Full DNA dict (`dna_result_{task_id}`) + session key mapping (`session_key_{task_id}`) | Celery task completes | `get_task_result_view` polls for result; `claim_anonymous_dna_task` on signup | 1 hour TTL |
| **Django session** | `dna_data`, `book_ids`, `top_book_ids`, `book_ratings` | `get_task_result_view` on SUCCESS | `display_dna_view` renders dashboard | Until session expires |
| **AnonymousUserSession** (DB) | `dna_data`, `books_data`, `top_books_data`, `genre_distribution`, `author_distribution`, `book_ratings` | `save_anonymous_session_data()` during DNA generation | Recommendation engine (similarity comparisons); `get_task_result_view` (to populate session); `claim_anonymous_dna_task` (to create UserBooks); `display_dna_view` (to recreate if expired) | 7 days, then anonymized |

### Why each store exists

**Redis cache (`dna_result_{task_id}`)**: Bridges the async gap. The Celery task runs in a separate process, and the frontend polls via AJAX. Redis is the fast handoff mechanism — the task writes the result, the polling view reads it. Without this, you'd have to call `AsyncResult.get()` every poll, which is slower and depends on the Celery result backend configuration.

**Django session**: The dashboard view needs DNA data on every page load. Session is the natural place for request-scoped data for anonymous users (no UserProfile to persist to). It's also what survives across page navigations without requiring another DB query.

**AnonymousUserSession (DB)**: Serves three purposes that session and Redis cannot:
1. **Recommendation engine input** — `get_recommendations_for_anonymous()` needs `genre_distribution`, `author_distribution`, `book_ratings` for similarity calculations. These aren't in the DNA dict (DNA has `top_genres`/`top_authors` as lists, not the full distributions).
2. **Claim flow** — When a user signs up, `claim_anonymous_dna_task` needs to create `UserBook` records from `books_data`. The Redis cache may have expired by then (1h TTL vs. signup could be days later). The session is tied to the browser, not accessible from a Celery task.
3. **Anonymization pipeline** — After 7 days, expired sessions become `AnonymizedReadingProfile` records that feed the recommendation engine for other users.

### Can it be simplified?

**The Redis cache layer is the most redundant.** Its only purpose is fast polling during the ~30 seconds of DNA processing. Without it, `get_task_result_view` falls back to `AsyncResult(task_id)` which works fine — it's just a Redis read from the Celery result backend (db 0) instead of the cache backend (db 1). The code already has this fallback at `views.py:929-952`.

**Potential simplification: Remove `dna_result_{task_id}` and `session_key_{task_id}` from the cache layer entirely.** Rely on `AsyncResult` for the polling handoff, and `AnonymousUserSession` for the claim flow.

**Pros:**
- Eliminates one storage layer and the `safe_cache_set` calls in the task
- `claim_anonymous_dna_task` already queries `AnonymousUserSession` for `books_data` — it could also read `dna_data` from there instead of Redis cache
- Removes the confusing dual-path in `get_task_result_view` (check cache, then check AsyncResult)

**Cons:**
- `AsyncResult.get()` blocks if the task isn't done yet (but the view already handles PENDING state)
- The claim flow currently tries Redis first, then falls back to AsyncResult with retry. Removing Redis means it always goes to AsyncResult, which is fine but slightly slower on the happy path
- `session_key_{task_id}` mapping is used by `claim_anonymous_dna_task` to find the `AnonymousUserSession`. Without this, the claim task would need to receive the `session_key` directly (passed from the signup view instead of looked up from cache)

**Note on race conditions:** There's a subtle timing issue in the current flow — when `get_task_result_view` receives SUCCESS from Redis cache (lines 907-927), it simultaneously queries `AnonymousUserSession` to populate session data. If the Celery task has written to Redis but `save_anonymous_session_data()` hasn't committed yet, the `AnonymousUserSession.DoesNotExist` is silently caught and session data is incomplete. This is extremely unlikely under normal load and self-heals on the next page load.

**Recommendation:** Leave the anonymous triple-storage as-is for now. Each layer genuinely serves a different consumer (polling view, dashboard rendering, background tasks). The Redis cache layer adds ~4 lines of code and makes the polling path faster. The complexity cost is low and the fallback paths already work.

If we wanted to simplify in the future, the cleanest approach would be to pass `session_key` directly to `claim_anonymous_dna_task` from `signup_view` (instead of looking it up via `session_key_{task_id}` in Redis), which would eliminate the `session_key_{task_id}` cache entry.

---

## Acceptance Criteria

- [ ] `core/cache_utils.py` exists with `safe_cache_get`, `safe_cache_set`, `safe_cache_delete`
- [ ] `recommendation_service.py` re-exports from `cache_utils` (backward compat)
- [ ] All existing `safe_cache_set(key, None, 1)` invalidation calls replaced with `safe_cache_delete(key)`
- [ ] `_enrich_dna_for_display()` caches `calculate_community_means()` with 600s TTL
- [ ] Percentiles cache TTL increased to 600s and uses `safe_cache_get`/`safe_cache_set` from `cache_utils`
- [ ] `_save_dna_to_profile()` invalidates both `similar_users_{user_id}` and `user_recommendations_{user_id}` caches
- [ ] `anon_profiles_sample` uses a global cache key (no `user_id`)
- [ ] Both call sites (authenticated + anonymous candidate collection) use the cached global key
- [ ] Anonymized profile sample uses `order_by("?")` for random sampling
- [ ] `similar_users` cache key simplified to `similar_users_{user_id}`
- [ ] `find_similar_users()` function defaults updated to `top_n=30, min_similarity=0.15`
- [ ] `user_recommendations` cache key simplified to `user_recommendations_{user_id}`
- [ ] `get_recommendations_for_anonymous()` caches results with 15-minute TTL keyed by session key
- [ ] All existing tests pass

## Implementation Order

Fixes should be implemented in this order due to dependencies:

1. **Prerequisite** — Extract `cache_utils.py`, update imports
2. **Fix 1** — Percentiles + community means caching (uses new imports)
3. **Fix 3** — Global anon_profiles_sample + random sampling (independent)
4. **Fix 4** — Simplify similar_users key + align defaults (independent)
5. **Fix 5** — Simplify user_recommendations key (independent)
6. **Fix 2** — Similar users invalidation (depends on Fix 4 + Fix 5 key formats)
7. **Fix 6** — Cache anonymous recommendations (independent, biggest impact)

## Files to Modify

| File | Change |
|---|---|
| `core/cache_utils.py` | **New file** — `safe_cache_get`, `safe_cache_set`, `safe_cache_delete` |
| `core/services/recommendation_service.py` | Re-export from cache_utils; global anon_profiles_sample key (2 locations); random sampling; simplify user_recommendations key; cache anonymous recommendations |
| `core/views.py:188-221` | Cache community means; increase percentiles TTL; import from cache_utils |
| `core/services/dna_analyser.py:117-155` | Add similar_users + user_recommendations cache invalidation via safe_cache_delete |
| `core/services/user_similarity_service.py:323,332-333` | Simplify cache key; update function defaults |
| `core/tasks.py:485-486` | Replace `safe_cache_set(key, None, 1)` with `safe_cache_delete(key)` |

## References

- `core/services/recommendation_service.py` — RecommendationEngine class, safe_cache wrappers
- `core/services/user_similarity_service.py` — find_similar_users(), bulk context building
- `core/services/dna_analyser.py` — _save_dna_to_profile(), save_anonymous_session_data()
- `core/views.py` — _enrich_dna_for_display(), get_task_result_view(), display_dna_view()
- `core/tasks.py` — generate_reading_dna_task, claim_anonymous_dna_task, generate_recommendations_task
- `core/percentile_engine.py` — calculate_percentiles_from_aggregates(), calculate_community_means()
- `core/models.py` — AnonymizedReadingProfile (ordering: `-created_at`), AnonymousUserSession, AggregateAnalytics
