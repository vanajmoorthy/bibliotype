"""
Tests for the percentile engine: update_analytics_from_stats, calculate_percentiles_from_aggregates,
and the deduplication logic for re-uploads.
"""

from django.test import TestCase

from core.models import AggregateAnalytics
from core.percentile_engine import (
    calculate_percentiles_from_aggregates,
    get_bucket,
    update_analytics_from_stats,
)


class GetBucketTests(TestCase):
    def test_basic_bucketing(self):
        self.assertEqual(get_bucket(290, 50), "250-299")
        self.assertEqual(get_bucket(300, 50), "300-349")
        self.assertEqual(get_bucket(7, 5), "5-9")
        self.assertEqual(get_bucket(0, 5), "0-4")

    def test_none_returns_unknown(self):
        self.assertEqual(get_bucket(None, 50), "Unknown")


class UpdateAnalyticsFirstUploadTests(TestCase):
    """Tests for update_analytics_from_stats when previous_stats is None (first upload)."""

    def test_first_upload_increments_total_profiles(self):
        stats = {"avg_book_length": 300, "avg_publish_year": 2010, "total_books_read": 80, "avg_books_per_year": 16.0}
        update_analytics_from_stats(stats)
        analytics = AggregateAnalytics.get_instance()
        self.assertEqual(analytics.total_profiles_counted, 1)

    def test_first_upload_adds_to_buckets(self):
        stats = {"avg_book_length": 300, "avg_publish_year": 2010, "total_books_read": 80, "avg_books_per_year": 16.0}
        update_analytics_from_stats(stats)
        analytics = AggregateAnalytics.get_instance()
        self.assertEqual(analytics.avg_book_length_dist.get("300-349"), 1)
        self.assertEqual(analytics.avg_publish_year_dist.get("2010-2019"), 1)
        self.assertEqual(analytics.total_books_read_dist.get("75-99"), 1)
        self.assertEqual(analytics.avg_books_per_year_dist.get("15-19"), 1)

    def test_multiple_first_uploads_accumulate(self):
        stats1 = {"avg_book_length": 300, "total_books_read": 80, "avg_books_per_year": 16.0}
        stats2 = {"avg_book_length": 300, "total_books_read": 50, "avg_books_per_year": 10.0}
        update_analytics_from_stats(stats1)
        update_analytics_from_stats(stats2)
        analytics = AggregateAnalytics.get_instance()
        self.assertEqual(analytics.total_profiles_counted, 2)
        self.assertEqual(analytics.avg_book_length_dist.get("300-349"), 2)


class UpdateAnalyticsReuploadTests(TestCase):
    """Tests for update_analytics_from_stats with previous_stats (re-upload deduplication)."""

    def test_reupload_does_not_increment_total(self):
        old_stats = {"avg_book_length": 300, "total_books_read": 80, "avg_books_per_year": 16.0}
        new_stats = {"avg_book_length": 350, "total_books_read": 100, "avg_books_per_year": 20.0}

        # First upload
        update_analytics_from_stats(old_stats)
        analytics = AggregateAnalytics.get_instance()
        self.assertEqual(analytics.total_profiles_counted, 1)

        # Re-upload
        update_analytics_from_stats(new_stats, previous_stats=old_stats)
        analytics.refresh_from_db()
        self.assertEqual(analytics.total_profiles_counted, 1)

    def test_reupload_swaps_buckets(self):
        old_stats = {"avg_book_length": 290}  # bucket 250-299
        new_stats = {"avg_book_length": 310}  # bucket 300-349

        update_analytics_from_stats(old_stats)
        analytics = AggregateAnalytics.get_instance()
        self.assertEqual(analytics.avg_book_length_dist.get("250-299"), 1)

        update_analytics_from_stats(new_stats, previous_stats=old_stats)
        analytics.refresh_from_db()
        self.assertEqual(analytics.avg_book_length_dist.get("250-299", 0), 0)
        self.assertEqual(analytics.avg_book_length_dist.get("300-349"), 1)

    def test_reupload_same_bucket_no_net_change(self):
        stats = {"avg_book_length": 310}  # bucket 300-349 both times
        update_analytics_from_stats(stats)
        analytics = AggregateAnalytics.get_instance()
        self.assertEqual(analytics.avg_book_length_dist.get("300-349"), 1)

        update_analytics_from_stats(stats, previous_stats=stats)
        analytics.refresh_from_db()
        self.assertEqual(analytics.avg_book_length_dist.get("300-349"), 1)

    def test_reupload_old_bucket_never_goes_negative(self):
        """If old bucket count is already 0, subtraction clamps to 0."""
        old_stats = {"avg_book_length": 290}  # bucket 250-299
        new_stats = {"avg_book_length": 310}  # bucket 300-349

        # Set bucket to 0 artificially
        analytics = AggregateAnalytics.get_instance()
        analytics.avg_book_length_dist = {"250-299": 0}
        analytics.total_profiles_counted = 1
        analytics.save()

        update_analytics_from_stats(new_stats, previous_stats=old_stats)
        analytics.refresh_from_db()
        self.assertEqual(analytics.avg_book_length_dist.get("250-299", 0), 0)
        self.assertEqual(analytics.avg_book_length_dist.get("300-349"), 1)

    def test_reupload_with_missing_key_in_previous_stats(self):
        """Old DNA missing avg_books_per_year should not crash; only subtract keys that exist."""
        old_stats = {"avg_book_length": 300}  # no avg_books_per_year
        new_stats = {"avg_book_length": 350, "avg_books_per_year": 20.0}

        update_analytics_from_stats(old_stats)
        update_analytics_from_stats(new_stats, previous_stats=old_stats)

        analytics = AggregateAnalytics.get_instance()
        # Old bucket subtracted, new added
        self.assertEqual(analytics.avg_book_length_dist.get("300-349", 0), 0)
        self.assertEqual(analytics.avg_book_length_dist.get("350-399"), 1)
        # avg_books_per_year was added (no old to subtract)
        self.assertEqual(analytics.avg_books_per_year_dist.get("20-24"), 1)


class CalculatePercentilesTests(TestCase):
    """Tests for calculate_percentiles_from_aggregates."""

    def _seed_analytics(self):
        analytics = AggregateAnalytics.get_instance()
        analytics.total_profiles_counted = 50
        analytics.avg_book_length_dist = {"200-249": 10, "250-299": 15, "300-349": 15, "350-399": 10}
        analytics.avg_publish_year_dist = {"2000-2009": 10, "2010-2019": 25, "2020-2029": 15}
        analytics.total_books_read_dist = {"25-49": 10, "50-74": 20, "75-99": 15, "100-124": 5}
        analytics.avg_books_per_year_dist = {"5-9": 15, "10-14": 20, "15-19": 10, "20-24": 5}
        analytics.save()

    def test_returns_empty_when_too_few_users(self):
        analytics = AggregateAnalytics.get_instance()
        analytics.total_profiles_counted = 5
        analytics.save()
        result = calculate_percentiles_from_aggregates({"avg_book_length": 300})
        self.assertEqual(result, {})

    def test_higher_value_gets_higher_percentile(self):
        """A user with more books/year should have a higher percentile than one with fewer."""
        self._seed_analytics()

        low_stats = {"avg_books_per_year": 6}
        high_stats = {"avg_books_per_year": 22}

        low_pct = calculate_percentiles_from_aggregates(low_stats)
        high_pct = calculate_percentiles_from_aggregates(high_stats)

        self.assertGreater(
            high_pct.get("avg_books_per_year", 0),
            low_pct.get("avg_books_per_year", 0),
        )

    def test_percentiles_capped_at_100(self):
        self._seed_analytics()
        extreme_stats = {"avg_book_length": 5000, "total_books_read": 10000, "avg_books_per_year": 500}
        result = calculate_percentiles_from_aggregates(extreme_stats)
        for key in ("avg_book_length", "total_books_read", "avg_books_per_year"):
            if key in result:
                self.assertLessEqual(result[key], 100.0)
