import logging

from django.db import connection, transaction
from django.db.models import F

from .models import AggregateAnalytics

logger = logging.getLogger(__name__)

# Averages stored as 0 when a library has no page/year/date data. 0 means "missing", not a real value.
MISSING_WHEN_ZERO = {"avg_book_length", "avg_publish_year", "avg_books_per_year"}

# (user_stats key, AggregateAnalytics histogram field, bucket size) for each community metric.
METRICS = (
    ("avg_book_length", "avg_book_length_dist", 50),
    ("avg_publish_year", "avg_publish_year_dist", 10),
    ("total_books_read", "total_books_read_dist", 25),
    ("avg_books_per_year", "avg_books_per_year_dist", 5),
)


def _has_value(stats, key):
    value = stats.get(key)
    return value is not None and not (key in MISSING_WHEN_ZERO and value <= 0)


def get_bucket(value, bucket_size):
    """Calculates the histogram bucket for a given value."""
    if value is None:
        return "Unknown"
    lower_bound = int(value // bucket_size * bucket_size)
    upper_bound = lower_bound + bucket_size - 1
    return f"{lower_bound}-{upper_bound}"


def _parse_bucket_start(bucket_key):
    """Safely parse the lower bound from a bucket key like '250-299'. Returns None for malformed keys."""
    try:
        return int(bucket_key.split("-")[0])
    except (ValueError, IndexError):
        return None


def _bucket_percentile(dist, bucket_start, bucket_size, total_other_users, count_higher=False):
    """Share of other users in buckets below (or above) the user's, counting the user's own bucket as half."""
    bucket_key = f"{bucket_start}-{bucket_start + bucket_size - 1}"
    beyond_count = sum(
        count
        for bucket, count in dist.items()
        if (bs := _parse_bucket_start(bucket)) is not None
        and (bs > bucket_start if count_higher else bs < bucket_start)
    )
    better_than_count = beyond_count + (dist.get(bucket_key, 0) / 2)
    return min(100.0, (better_than_count / total_other_users) * 100)


def update_analytics_from_stats(user_stats, previous_stats=None):
    """Updates aggregate analytics histograms with a user's stats.

    Uses select_for_update to prevent concurrent Celery workers from losing
    histogram updates via interleaved read-modify-write cycles.

    Args:
        user_stats: Current stats to add to distributions.
        previous_stats: If provided (re-upload), old stats are subtracted before adding new ones.
            When None (first upload or anonymous), total_profiles_counted is incremented.
    """
    with transaction.atomic():
        # Lock the singleton row to prevent concurrent workers from interleaving reads/writes.
        # Gracefully skips locking on SQLite (dev/test) where select_for_update is unsupported.
        analytics, _ = AggregateAnalytics.objects.get_or_create(pk=1)
        if connection.features.has_select_for_update:
            analytics = AggregateAnalytics.objects.select_for_update().get(pk=1)

        if previous_stats is None:
            analytics.total_profiles_counted = F("total_profiles_counted") + 1

        for stat_key, dist_field, bucket_size in METRICS:
            current_dist = getattr(analytics, dist_field)

            # Subtract old bucket on re-upload
            if previous_stats is not None:
                if _has_value(previous_stats, stat_key):
                    old_bucket = get_bucket(previous_stats[stat_key], bucket_size)
                    current_dist[old_bucket] = max(0, current_dist.get(old_bucket, 0) - 1)

            # Add new bucket
            if _has_value(user_stats, stat_key):
                new_bucket = get_bucket(user_stats[stat_key], bucket_size)
                current_dist[new_bucket] = current_dist.get(new_bucket, 0) + 1

            setattr(analytics, dist_field, current_dist)

        analytics.save()
    logger.debug("Updated global aggregate statistics")


def calculate_percentiles_from_aggregates(user_stats):
    analytics = AggregateAnalytics.get_instance()
    total_other_users = max(0, analytics.total_profiles_counted - 1)

    if total_other_users < 10:
        logger.debug("Not enough data for percentiles. Skipping.")
        return {}

    percentiles = {}
    user_length = user_stats.get("avg_book_length", 0)
    percentiles["avg_book_length"] = _bucket_percentile(
        analytics.avg_book_length_dist, user_length // 50 * 50, 50, total_other_users
    )
    # Older reading scores higher, so count the buckets above the user's.
    user_year = user_stats.get("avg_publish_year", 2025)
    percentiles["avg_publish_year"] = _bucket_percentile(
        analytics.avg_publish_year_dist, user_year // 10 * 10, 10, total_other_users, count_higher=True
    )
    user_books = user_stats.get("total_books_read", 0)
    percentiles["total_books_read"] = _bucket_percentile(
        analytics.total_books_read_dist, user_books // 25 * 25, 25, total_other_users
    )
    user_bpy = user_stats.get("avg_books_per_year", 0)
    percentiles["avg_books_per_year"] = _bucket_percentile(
        analytics.avg_books_per_year_dist, int(user_bpy // 5 * 5), 5, total_other_users
    )

    for key in MISSING_WHEN_ZERO:
        if not _has_value(user_stats, key):
            percentiles.pop(key, None)

    logger.debug("Calculated percentiles against global data")
    return percentiles


def calculate_community_means():
    """Computes weighted means from histogram distributions for all metrics."""
    analytics = AggregateAnalytics.get_instance()

    def _weighted_mean(dist, bucket_size):
        total_count = 0
        weighted_sum = 0.0
        for bucket_key, count in dist.items():
            lower = _parse_bucket_start(bucket_key)
            if lower is None:
                continue
            midpoint = lower + bucket_size / 2
            weighted_sum += midpoint * count
            total_count += count
        if total_count == 0:
            return None
        return round(weighted_sum / total_count, 1)

    return {
        stat_key: _weighted_mean(getattr(analytics, dist_field), bucket_size)
        for stat_key, dist_field, bucket_size in METRICS
    }
