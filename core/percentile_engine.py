import logging

from django.db import connection, transaction
from django.db.models import F

from .models import AggregateAnalytics

logger = logging.getLogger(__name__)


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


def update_analytics_from_stats(user_stats, previous_stats=None):
    """Updates aggregate analytics histograms with a user's stats.

    Uses select_for_update to prevent concurrent Celery workers from losing
    histogram updates via interleaved read-modify-write cycles.

    Args:
        user_stats: Current stats to add to distributions.
        previous_stats: If provided (re-upload), old stats are subtracted before adding new ones.
            When None (first upload or anonymous), total_profiles_counted is incremented.
    """
    distributions = {
        "avg_book_length": ("avg_book_length_dist", 50),
        "avg_publish_year": ("avg_publish_year_dist", 10),
        "total_books_read": ("total_books_read_dist", 25),
        "avg_books_per_year": ("avg_books_per_year_dist", 5),
    }

    with transaction.atomic():
        # Lock the singleton row to prevent concurrent workers from interleaving reads/writes.
        # Gracefully skips locking on SQLite (dev/test) where select_for_update is unsupported.
        analytics, _ = AggregateAnalytics.objects.get_or_create(pk=1)
        if connection.features.has_select_for_update:
            analytics = AggregateAnalytics.objects.select_for_update().get(pk=1)

        if previous_stats is None:
            analytics.total_profiles_counted = F("total_profiles_counted") + 1

        for stat_key, (dist_field, bucket_size) in distributions.items():
            current_dist = getattr(analytics, dist_field)

            # Subtract old bucket on re-upload
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
    logger.debug("Updated global aggregate statistics")


def calculate_percentiles_from_aggregates(user_stats):
    analytics = AggregateAnalytics.get_instance()
    total_other_users = max(0, analytics.total_profiles_counted - 1)

    if total_other_users < 10:
        logger.debug("Not enough data for percentiles. Skipping.")
        return {}

    percentiles = {}
    length_dist = analytics.avg_book_length_dist
    user_length = user_stats.get("avg_book_length", 0)
    bucket_size_len = 50
    user_length_bucket_start = user_length // bucket_size_len * bucket_size_len
    user_length_bucket_key = f"{user_length_bucket_start}-{user_length_bucket_start + bucket_size_len - 1}"

    lower_buckets_count_len = sum(
        count for bucket, count in length_dist.items()
        if (bs := _parse_bucket_start(bucket)) is not None and bs < user_length_bucket_start
    )
    same_bucket_count_len = length_dist.get(user_length_bucket_key, 0)
    better_than_count_length = lower_buckets_count_len + (same_bucket_count_len / 2)

    percentile_length = (better_than_count_length / total_other_users) * 100
    percentiles["avg_book_length"] = min(100.0, percentile_length)
    year_dist = analytics.avg_publish_year_dist
    user_year = user_stats.get("avg_publish_year", 2025)
    bucket_size_year = 10
    user_year_bucket_start = user_year // bucket_size_year * bucket_size_year
    user_year_bucket_key = f"{user_year_bucket_start}-{user_year_bucket_start + bucket_size_year - 1}"

    higher_buckets_count_year = sum(
        count for bucket, count in year_dist.items()
        if (bs := _parse_bucket_start(bucket)) is not None and bs > user_year_bucket_start
    )
    same_bucket_count_year = year_dist.get(user_year_bucket_key, 0)
    older_than_count = higher_buckets_count_year + (same_bucket_count_year / 2)

    percentile_year = (older_than_count / total_other_users) * 100
    percentiles["avg_publish_year"] = min(100.0, percentile_year)
    books_dist = analytics.total_books_read_dist
    user_books = user_stats.get("total_books_read", 0)
    bucket_size_books = 25
    user_books_bucket_start = user_books // bucket_size_books * bucket_size_books
    user_books_bucket_key = f"{user_books_bucket_start}-{user_books_bucket_start + bucket_size_books - 1}"

    lower_buckets_count_books = sum(
        count for bucket, count in books_dist.items()
        if (bs := _parse_bucket_start(bucket)) is not None and bs < user_books_bucket_start
    )
    same_bucket_count_books = books_dist.get(user_books_bucket_key, 0)
    better_than_count_books = lower_buckets_count_books + (same_bucket_count_books / 2)

    percentile_books = (better_than_count_books / total_other_users) * 100
    percentiles["total_books_read"] = min(100.0, percentile_books)

    # Books per year percentile
    bpy_dist = analytics.avg_books_per_year_dist
    user_bpy = user_stats.get("avg_books_per_year", 0)
    bucket_size_bpy = 5
    user_bpy_bucket_start = int(user_bpy // bucket_size_bpy * bucket_size_bpy)
    user_bpy_bucket_key = f"{user_bpy_bucket_start}-{user_bpy_bucket_start + bucket_size_bpy - 1}"

    lower_buckets_count_bpy = sum(
        count for bucket, count in bpy_dist.items()
        if (bs := _parse_bucket_start(bucket)) is not None and bs < user_bpy_bucket_start
    )
    same_bucket_count_bpy = bpy_dist.get(user_bpy_bucket_key, 0)
    better_than_count_bpy = lower_buckets_count_bpy + (same_bucket_count_bpy / 2)

    percentile_bpy = (better_than_count_bpy / total_other_users) * 100
    percentiles["avg_books_per_year"] = min(100.0, percentile_bpy)

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
        "avg_book_length": _weighted_mean(analytics.avg_book_length_dist, 50),
        "avg_publish_year": _weighted_mean(analytics.avg_publish_year_dist, 10),
        "total_books_read": _weighted_mean(analytics.total_books_read_dist, 25),
        "avg_books_per_year": _weighted_mean(analytics.avg_books_per_year_dist, 5),
    }
