from django.db.models import F

from .models import AggregateAnalytics


def get_bucket(value, bucket_size):
    """Calculates the histogram bucket for a given value."""
    if value is None:
        return "Unknown"
    lower_bound = int(value // bucket_size * bucket_size)
    upper_bound = lower_bound + bucket_size - 1
    return f"{lower_bound}-{upper_bound}"


def update_analytics_from_stats(user_stats):
    """
    Takes a user's calculated stats and updates the central AggregateAnalytics model.
    """
    analytics = AggregateAnalytics.get_instance()

    distributions = {
        "avg_book_length": ("avg_book_length_dist", 50),
        "avg_publish_year": ("avg_publish_year_dist", 10),
        "total_books_read": ("total_books_read_dist", 25),
    }

    # Atomically update the total count first
    AggregateAnalytics.objects.filter(pk=1).update(total_profiles_counted=F("total_profiles_counted") + 1)
    analytics.refresh_from_db()

    # Read the current JSON, update it in memory, then save it back.
    for stat_key, (dist_field, bucket_size) in distributions.items():
        value = user_stats.get(stat_key)
        if value is not None:
            bucket = get_bucket(value, bucket_size)
            current_dist = getattr(analytics, dist_field, {})
            current_dist[bucket] = current_dist.get(bucket, 0) + 1
            setattr(analytics, dist_field, current_dist)

    analytics.save()
    print("   [Analytics] Updated global aggregate statistics.")


# --- THIS IS THE CORRECTED FUNCTION ---
def calculate_percentiles_from_aggregates(user_stats):
    """
    Calculates where a user's stats fall within the global distribution.
    This version is corrected to cap the percentile at 100% to handle
    extreme outlier values gracefully.
    """
    analytics = AggregateAnalytics.get_instance()
    total_other_users = max(0, analytics.total_profiles_counted - 1)

    if total_other_users < 10:
        print("   [Analytics] Not enough data for percentiles. Skipping.")
        return {}

    percentiles = {}

    # --- 1. Avg Book Length ---
    length_dist = analytics.avg_book_length_dist
    user_length = user_stats.get("avg_book_length", 0)
    bucket_size_len = 50
    user_length_bucket_start = user_length // bucket_size_len * bucket_size_len
    user_length_bucket_key = f"{user_length_bucket_start}-{user_length_bucket_start + bucket_size_len - 1}"

    lower_buckets_count_len = sum(
        count for bucket, count in length_dist.items() if int(bucket.split("-")[0]) < user_length_bucket_start
    )
    same_bucket_count_len = length_dist.get(user_length_bucket_key, 0)
    better_than_count_length = lower_buckets_count_len + (same_bucket_count_len / 2)

    # FIX: Cap the final percentile at 100.0
    percentile_length = (better_than_count_length / total_other_users) * 100
    percentiles["avg_book_length"] = min(100.0, percentile_length)

    # --- 2. Avg Publish Year (lower year is "older" / higher percentile) ---
    year_dist = analytics.avg_publish_year_dist
    user_year = user_stats.get("avg_publish_year", 2025)
    bucket_size_year = 10
    user_year_bucket_start = user_year // bucket_size_year * bucket_size_year
    user_year_bucket_key = f"{user_year_bucket_start}-{user_year_bucket_start + bucket_size_year - 1}"

    higher_buckets_count_year = sum(
        count for bucket, count in year_dist.items() if int(bucket.split("-")[0]) > user_year_bucket_start
    )
    same_bucket_count_year = year_dist.get(user_year_bucket_key, 0)
    older_than_count = higher_buckets_count_year + (same_bucket_count_year / 2)

    # FIX: Cap the final percentile at 100.0
    percentile_year = (older_than_count / total_other_users) * 100
    percentiles["avg_publish_year"] = min(100.0, percentile_year)

    # --- 3. Total Books Read ---
    books_dist = analytics.total_books_read_dist
    user_books = user_stats.get("total_books_read", 0)
    bucket_size_books = 25
    user_books_bucket_start = user_books // bucket_size_books * bucket_size_books
    user_books_bucket_key = f"{user_books_bucket_start}-{user_books_bucket_start + bucket_size_books - 1}"

    lower_buckets_count_books = sum(
        count for bucket, count in books_dist.items() if int(bucket.split("-")[0]) < user_books_bucket_start
    )
    same_bucket_count_books = books_dist.get(user_books_bucket_key, 0)
    better_than_count_books = lower_buckets_count_books + (same_bucket_count_books / 2)

    # FIX: Cap the final percentile at 100.0
    percentile_books = (better_than_count_books / total_other_users) * 100
    percentiles["total_books_read"] = min(100.0, percentile_books)

    print("   [Analytics] Calculated percentiles against global data.")
    return percentiles
