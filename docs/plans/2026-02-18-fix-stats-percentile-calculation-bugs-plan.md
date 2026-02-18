---
title: "fix: Correct avg books/year calculation, aggregate double-counting, and stale percentiles"
type: fix
status: completed
date: 2026-02-18
---

# Fix: Stats & Percentile Calculation Bugs

## Overview

Three interrelated bugs cause incorrect statistics on user dashboards:

1. **avg_books_per_year** divides only dated books by years, but the template displays total books — making the math appear wrong (e.g., "218 books across 4 years, averaging 7.0/year")
2. **Aggregate analytics double-count** on re-uploads — `total_profiles_counted` and histogram buckets inflate every time DNA is regenerated
3. **Stale percentiles** — percentiles are frozen at DNA generation time but community averages refresh on every page load, causing inconsistent comparisons across users

These must be deployed together. After deployment, run `rebuild_analytics` to clean historical corruption.

## Bug 1: avg_books_per_year Uses Wrong Numerator

### Root Cause

`core/services/dna_analyser.py:426-428`:
```python
books_with_dates = int(yearly_df.shape[0])    # Only books with "Date Read"
avg_books_per_year = round(books_with_dates / num_reading_years, 1)
```

But the template at `core/templates/core/partials/dna/comparative_analytics_card.html:44-48` shows `total_books_read` (ALL books) alongside this average.

### Fix

**File: `core/services/dna_analyser.py:428`** — Use `total_books` (line 217: `len(read_df)`) instead of `books_with_dates`:

```python
# Before
avg_books_per_year = round(books_with_dates / num_reading_years, 1)

# After
total_books = int(len(read_df))
avg_books_per_year = round(total_books / num_reading_years, 1)
```

Note: `total_books` is already computed as `len(read_df)` and stored in `user_base_stats["total_books_read"]` at line 431. We can reference that directly once `user_base_stats` is built, or compute it inline before. The cleanest approach: move the avg calculation after `user_base_stats` is constructed, using `user_base_stats["total_books_read"]`.

**File: `core/views.py:206-208`** — Backfill for old DNA:

```python
# Before
total_books_with_dates = sum(y.get("count", 0) for y in stats_by_year)
user_stats["avg_books_per_year"] = round(total_books_with_dates / num_years, 1)

# After
total_books = user_stats.get("total_books_read", 0)
user_stats["avg_books_per_year"] = round(total_books / num_years, 1) if num_years > 0 else 0
```

**File: `core/management/commands/rebuild_analytics.py:45-47`** — Same backfill fix:

```python
# Before
total_books_with_dates = sum(y.get("count", 0) for y in stats_by_year)
user_stats["avg_books_per_year"] = round(total_books_with_dates / num_years, 1)

# After
total_books = user_stats.get("total_books_read", 0)
user_stats["avg_books_per_year"] = round(total_books / num_years, 1) if num_years > 0 else 0
```

### Template Pluralization Fix

`core/templates/core/partials/dna/comparative_analytics_card.html:47` — fix "1 years" grammar:

```html
<!-- Before -->
across <strong>{{ dna.user_stats.num_reading_years }} years</strong>

<!-- After -->
across <strong>{{ dna.user_stats.num_reading_years }} year{{ dna.user_stats.num_reading_years|pluralize }}</strong>
```

---

## Bug 2: Aggregate Analytics Double-Counting on Re-Uploads

### Root Cause

`core/percentile_engine.py:19-41` — `update_analytics_from_stats()` always increments `total_profiles_counted` and adds to buckets, even on re-uploads. Called unconditionally at `dna_analyser.py:450`.

### Fix

**File: `core/percentile_engine.py`** — Add `previous_stats` parameter to `update_analytics_from_stats()`:

```python
def update_analytics_from_stats(user_stats, previous_stats=None):
    analytics = AggregateAnalytics.get_instance()

    distributions = {
        "avg_book_length": ("avg_book_length_dist", 50),
        "avg_publish_year": ("avg_publish_year_dist", 10),
        "total_books_read": ("total_books_read_dist", 25),
        "avg_books_per_year": ("avg_books_per_year_dist", 5),
    }

    # Only increment total count for genuinely new profiles
    if previous_stats is None:
        AggregateAnalytics.objects.filter(pk=1).update(
            total_profiles_counted=F("total_profiles_counted") + 1
        )
    analytics.refresh_from_db()

    for stat_key, (dist_field, bucket_size) in distributions.items():
        current_dist = getattr(analytics, dist_field, {})

        # Subtract old bucket if re-uploading
        if previous_stats is not None:
            old_value = previous_stats.get(stat_key)
            if old_value is not None:
                old_bucket = get_bucket(old_value, bucket_size)
                current_dist[old_bucket] = max(0, current_dist.get(old_bucket, 0) - 1)

        # Add new bucket
        new_value = user_stats.get(stat_key)
        if new_value is not None:
            new_bucket = get_bucket(new_value, bucket_size)
            current_dist[new_bucket] = current_dist.get(new_bucket, 0) + 1

        setattr(analytics, dist_field, current_dist)

    analytics.save()
```

Key details:
- `max(0, ...)` clamp prevents negative bucket counts from corrupted historical data
- When `previous_stats is None` (first upload or anonymous), behavior is identical to current code
- When `previous_stats` is provided, old bucket is decremented and new bucket is incremented, with no change to `total_profiles_counted`

**File: `core/services/dna_analyser.py:448-450`** — Pass previous stats from existing profile:

```python
# Before
logger.info("Calculating community stats...")
update_analytics_from_stats(user_base_stats)

# After
logger.info("Calculating community stats...")
previous_stats = None
if user:
    try:
        existing_dna = user.userprofile.dna_data
        if existing_dna:
            previous_stats = existing_dna.get("user_stats")
    except Exception:
        pass
update_analytics_from_stats(user_base_stats, previous_stats=previous_stats)
```

Note: At line 450, the profile hasn't been saved yet with new DNA, so `user.userprofile.dna_data` still contains the OLD data. This is correct — we read the old stats before they get overwritten.

**File: `core/management/commands/rebuild_analytics.py`** — No change needed. It already deletes all analytics and rebuilds from scratch, so every call is a "first upload" (`previous_stats=None`).

### Anonymous Users

Anonymous users will continue to be counted without deduplication (no profile to read previous stats from). This is a known limitation. Running `rebuild_analytics` periodically corrects for this since it only processes `UserProfile` objects. Anonymous contributions are inherently temporary.

---

## Bug 3: Stale Percentiles at Display Time

### Root Cause

`core/views.py:215` — `_enrich_dna_for_display()` uses stored `bibliotype_percentiles` from DNA generation time, but community averages are freshly computed (line 195). Different users have percentiles computed against different aggregate snapshots.

### Fix

**File: `core/views.py`** — Add fresh percentile calculation in `_enrich_dna_for_display()`, BEFORE the comparative text computation (before line 215):

```python
from .percentile_engine import calculate_percentiles_from_aggregates

# ... after community_averages are set (line 199) ...

# Recalculate percentiles from current aggregate data
user_stats = dna_data.get("user_stats", {})
fresh_percentiles = calculate_percentiles_from_aggregates(user_stats)
if fresh_percentiles:  # Only replace if enough data (>10 users)
    dna_data["bibliotype_percentiles"] = fresh_percentiles

# existing code continues:
percentiles = dna_data.get("bibliotype_percentiles", {})
```

Performance: `calculate_percentiles_from_aggregates()` does one `AggregateAnalytics.get_instance()` query (same singleton row already fetched by `calculate_community_means()`) plus pure Python math on small dicts. Sub-millisecond overhead.

We continue writing percentiles at DNA generation time — they serve as a snapshot for debugging and don't harm anything.

---

## Seed Analytics Fix

**File: `core/management/commands/seed_analytics.py`** — Add `avg_books_per_year_dist` seeding (currently missing). Follow the same pattern as the other 3 distributions with realistic buckets (e.g., most users in 5-20 books/year range).

---

## Acceptance Criteria

- [x] `avg_books_per_year` = `total_books_read / num_reading_years` everywhere (dna_analyser, views backfill, rebuild_analytics backfill)
- [x] Template shows correct grammar: "1 year" not "1 years"
- [x] Re-uploading CSV does NOT increment `total_profiles_counted`
- [x] Re-uploading swaps old histogram bucket for new bucket (net zero on total count per distribution)
- [x] Old bucket counts never go negative (clamped to 0)
- [x] Dashboard always shows percentiles calculated from current aggregate data
- [x] Percentiles fall back to stored values when community has <10 users
- [x] `seed_analytics` includes `avg_books_per_year_dist`
- [x] All existing tests pass
- [x] New unit tests for `update_analytics_from_stats` with and without `previous_stats`
- [x] New test where `total_books_read != sum(stats_by_year counts)` to catch Bug 1 regression
- [x] New test for fresh percentile recalculation in `_enrich_dna_for_display`

## Files to Modify

| File | Change |
|---|---|
| `core/services/dna_analyser.py` | Fix avg calculation (line 428), pass previous_stats (line 450) |
| `core/percentile_engine.py` | Add `previous_stats` param, subtract old buckets, clamp to 0 |
| `core/views.py` | Fix backfill (line 206-208), add fresh percentile calc (before line 215) |
| `core/management/commands/rebuild_analytics.py` | Fix backfill (line 45-47) |
| `core/management/commands/seed_analytics.py` | Add `avg_books_per_year_dist` |
| `core/templates/core/partials/dna/comparative_analytics_card.html` | Pluralize "year(s)" (line 47) |
| `core/tests/test_percentile_engine.py` | **NEW** — unit tests for percentile engine functions |
| `core/tests/test_number_line.py` | Update/add tests for fresh percentile recalculation |

## Deployment

1. Deploy all three fixes simultaneously (they are interdependent)
2. Run `poetry run python manage.py rebuild_analytics` immediately after deployment
3. Verify dashboards show consistent percentiles across users

## References

- `core/services/dna_analyser.py:404-446` — avg_books_per_year calculation
- `core/percentile_engine.py:19-41` — update_analytics_from_stats
- `core/percentile_engine.py:44-113` — calculate_percentiles_from_aggregates
- `core/views.py:171-252` — _enrich_dna_for_display
- `core/templates/core/partials/dna/comparative_analytics_card.html:42-63` — display template
- `core/management/commands/rebuild_analytics.py:26-50` — rebuild command
- `core/management/commands/seed_analytics.py` — seed command
