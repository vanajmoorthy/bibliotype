"""
Tests for the number line visualization component.
Covers the data pipeline (_enrich_dna_for_display), template rendering,
2-marker vs 3-marker modes, and edge cases.
"""

from datetime import date

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from core.models import AggregateAnalytics
from core.views import _enrich_dna_for_display


def _make_dna_data(**overrides):
    """Build a minimal but complete dna_data dict for dashboard rendering."""
    dna = {
        "reader_type": "Fantasy Fanatic",
        "reader_type_explanation": "You love fantasy books.",
        "top_reader_types": [
            {"type": "Fantasy Fanatic", "score": 10},
            {"type": "Novella Navigator", "score": 5},
            {"type": "Classic Collector", "score": 3},
        ],
        "reader_type_scores": {"Fantasy Fanatic": 10, "Novella Navigator": 5, "Classic Collector": 3},
        "user_stats": {
            "total_books_read": 80,
            "total_pages_read": 24000,
            "avg_book_length": 300,
            "avg_publish_year": 2010,
            "avg_books_per_year": 16.0,
            "num_reading_years": 5,
        },
        "bibliotype_percentiles": {
            "avg_book_length": 65.0,
            "avg_publish_year": 40.0,
            "total_books_read": 72.0,
            "avg_books_per_year": 80.0,
        },
        "top_genres": [("Fantasy", 30), ("Science Fiction", 20), ("Horror", 10)],
        "top_authors": [("Brandon Sanderson", 8), ("Terry Pratchett", 5)],
        "average_rating_overall": 3.8,
        "ratings_distribution": {"1": 2, "2": 5, "3": 20, "4": 35, "5": 18},
        "top_controversial_books": [
            {
                "Title": "Controversial Book",
                "Author": "Some Author",
                "my_rating": 5.0,
                "average_rating": 2.5,
                "rating_difference": 2.5,
            },
            {
                "Title": "Another Book",
                "Author": "Other Author",
                "my_rating": 1.0,
                "average_rating": 4.0,
                "rating_difference": 3.0,
            },
        ],
        "most_positive_review": {"Title": "Great Book", "Author": "Good Author", "my_review": "Amazing!", "sentiment": 0.9},
        "most_negative_review": {"Title": "Bad Book", "Author": "Bad Author", "my_review": "Terrible.", "sentiment": -0.8},
        "stats_by_year": [
            {"year": 2020, "count": 15, "avg_rating": 3.5},
            {"year": 2021, "count": 18, "avg_rating": 3.7},
            {"year": 2022, "count": 20, "avg_rating": 3.9},
            {"year": 2023, "count": 14, "avg_rating": 4.0},
            {"year": 2024, "count": 13, "avg_rating": 3.6},
        ],
        "mainstream_score_percent": 45,
        "reading_vibe": ["Daydreaming in libraries", "Epic quest seeker", "Magic system analyst", "Worldbuilder at heart"],
        "vibe_data_hash": "abc123",
        "most_niche_book": {"title": "Niche Book", "author": "Niche Author", "read_count": 3},
    }
    dna.update(overrides)
    return dna


def _seed_aggregate_analytics():
    """Seed AggregateAnalytics so community averages are computable."""
    analytics = AggregateAnalytics.get_instance()
    analytics.total_profiles_counted = 50
    analytics.avg_book_length_dist = {"250-299": 15, "300-349": 20, "350-399": 10, "400-449": 5}
    analytics.avg_publish_year_dist = {"2000-2009": 10, "2010-2019": 25, "2020-2029": 15}
    analytics.total_books_read_dist = {"25-49": 10, "50-74": 20, "75-99": 15, "100-124": 5}
    analytics.avg_books_per_year_dist = {"5-9": 15, "10-14": 20, "15-19": 10, "20-24": 5}
    analytics.save()


# ──────────────────────────────────────────────────────────────
# 1. Data pipeline tests — _enrich_dna_for_display()
# ──────────────────────────────────────────────────────────────


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "nl-test"}},
)
class EnrichDnaTests(TestCase):
    def setUp(self):
        _seed_aggregate_analytics()

    def test_enrich_dna_produces_community_averages(self):
        dna = _enrich_dna_for_display(_make_dna_data())
        ca = dna["community_averages"]
        self.assertIn("avg_book_length", ca)
        self.assertIn("avg_publish_year", ca)
        self.assertIn("avg_books_per_year", ca)
        for key in ("avg_book_length", "avg_publish_year", "avg_books_per_year"):
            self.assertIsInstance(ca[key], (int, float))

    def test_enrich_dna_produces_comparative_text(self):
        dna = _enrich_dna_for_display(_make_dna_data())
        ct = dna["comparative_text"]
        self.assertIn("length_direction", ct)
        self.assertIn("length_pct", ct)
        self.assertIn("age_direction", ct)
        self.assertIn("age_pct", ct)
        self.assertIn("bpy_direction", ct)
        self.assertIn("bpy_pct", ct)

    def test_enrich_dna_length_direction_longer(self):
        """User avg book length > community avg → 'longer'."""
        dna = _make_dna_data()
        # Community mean from seeded data will be around 312 pages.
        # Set user to 400 so clearly longer.
        dna["user_stats"]["avg_book_length"] = 400
        dna["bibliotype_percentiles"]["avg_book_length"] = 85.0
        result = _enrich_dna_for_display(dna)
        self.assertEqual(result["comparative_text"]["length_direction"], "longer")

    def test_enrich_dna_length_direction_shorter(self):
        """User avg book length < community avg → 'shorter'."""
        dna = _make_dna_data()
        dna["user_stats"]["avg_book_length"] = 200
        dna["bibliotype_percentiles"]["avg_book_length"] = 20.0
        result = _enrich_dna_for_display(dna)
        self.assertEqual(result["comparative_text"]["length_direction"], "shorter")

    def test_enrich_dna_age_direction_older(self):
        """User avg publish year < community avg → 'older'."""
        dna = _make_dna_data()
        # Community mean is around 2012. Set user to 1990.
        dna["user_stats"]["avg_publish_year"] = 1990
        dna["bibliotype_percentiles"]["avg_publish_year"] = 90.0
        result = _enrich_dna_for_display(dna)
        self.assertEqual(result["comparative_text"]["age_direction"], "older")

    def test_enrich_dna_age_direction_newer(self):
        """User avg publish year > community avg → 'newer'."""
        dna = _make_dna_data()
        dna["user_stats"]["avg_publish_year"] = 2023
        dna["bibliotype_percentiles"]["avg_publish_year"] = 10.0
        result = _enrich_dna_for_display(dna)
        self.assertEqual(result["comparative_text"]["age_direction"], "newer")

    def test_enrich_dna_bpy_direction_more(self):
        """User books per year >= community avg → 'more'."""
        dna = _make_dna_data()
        dna["user_stats"]["avg_books_per_year"] = 25.0
        dna["bibliotype_percentiles"]["avg_books_per_year"] = 90.0
        result = _enrich_dna_for_display(dna)
        self.assertEqual(result["comparative_text"]["bpy_direction"], "more")

    def test_enrich_dna_bpy_direction_fewer(self):
        """User books per year < community avg → 'fewer'."""
        dna = _make_dna_data()
        dna["user_stats"]["avg_books_per_year"] = 3.0
        dna["bibliotype_percentiles"]["avg_books_per_year"] = 10.0
        result = _enrich_dna_for_display(dna)
        self.assertEqual(result["comparative_text"]["bpy_direction"], "fewer")

    def test_enrich_dna_produces_number_line_ranges(self):
        """Number line ranges are computed with dynamic min/max based on data values."""
        dna = _enrich_dna_for_display(_make_dna_data())
        nlr = dna["number_line_ranges"]
        # Pages range should tightly contain user (300), community (~330), world (375)
        self.assertLessEqual(nlr["pages"]["min"], 300)
        self.assertGreaterEqual(nlr["pages"]["max"], 375)
        self.assertIn("pages", nlr["pages"]["min_label"])
        self.assertIn("pages", nlr["pages"]["max_label"])
        # Year range should go up to current year
        current_year = date.today().year
        self.assertEqual(nlr["year"]["max"], current_year)
        self.assertIn("CE", nlr["year"]["min_label"])
        self.assertIn("CE", nlr["year"]["max_label"])
        # BPY range should be reasonable (not 50)
        self.assertLessEqual(nlr["bpy"]["max"], 30)
        self.assertGreaterEqual(nlr["bpy"]["max"], 10)
        self.assertIn("per year", nlr["bpy"]["max_label"])

    def test_enrich_dna_year_range_expands_for_old_books(self):
        """When user reads very old books, the year range lower bound drops below 1980."""
        dna = _make_dna_data()
        dna["user_stats"]["avg_publish_year"] = 1950
        result = _enrich_dna_for_display(dna)
        self.assertLessEqual(result["number_line_ranges"]["year"]["min"], 1950)

    def test_enrich_dna_backfills_avg_books_per_year(self):
        """Old DNA without avg_books_per_year is backfilled from stats_by_year."""
        dna = _make_dna_data()
        del dna["user_stats"]["avg_books_per_year"]
        del dna["user_stats"]["num_reading_years"]
        result = _enrich_dna_for_display(dna)
        us = result["user_stats"]
        self.assertIn("avg_books_per_year", us)
        self.assertIn("num_reading_years", us)
        # 5 years of data with total 80 books → 16.0
        self.assertEqual(us["num_reading_years"], 5)
        self.assertEqual(us["avg_books_per_year"], 16.0)


# ──────────────────────────────────────────────────────────────
# 2. Template rendering tests
# ──────────────────────────────────────────────────────────────


@override_settings(
    CELERY_TASK_ALWAYS_EAGER=True,
    CELERY_TASK_STORE_EAGER_RESULT=True,
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "nl-render"}},
)
class NumberLineRenderTests(TestCase):
    """Render the dashboard with known DNA and assert number line HTML structure."""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="nltest", email="nl@test.com", password="testpass123")
        _seed_aggregate_analytics()

        dna = _make_dna_data()
        self.user.userprofile.dna_data = dna
        self.user.userprofile.save()
        self.client.login(username="nltest", password="testpass123")

    def _get_dashboard(self):
        return self.client.get(reverse("core:display_dna"))

    def test_comparative_analytics_renders_three_number_lines(self):
        """The dashboard should contain 5 number lines: 3 comparative + 2 controversial."""
        response = self._get_dashboard()
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        # 3 from comparative analytics + 2 from controversial ratings (test data has 2 books)
        self.assertEqual(content.count("number-line-wrap"), 5)

    def test_number_line_renders_user_marker(self):
        """Number line contains a user diamond marker with the correct color class."""
        response = self._get_dashboard()
        content = response.content.decode()
        # Default user color is bg-brand-pink, used in the track marker
        self.assertIn("bg-brand-pink", content)
        # Diamond marker: rotated square
        self.assertIn("rotate-45", content)

    def test_number_line_renders_compare_marker(self):
        """Number line contains the community square marker."""
        response = self._get_dashboard()
        content = response.content.decode()
        # Default compare color is bg-brand-cyan (comparative analytics)
        self.assertIn("bg-brand-cyan", content)

    def test_number_line_renders_third_marker(self):
        """Number line contains the world avg circle marker."""
        response = self._get_dashboard()
        content = response.content.decode()
        # Third marker uses bg-brand-green and rounded-full
        self.assertIn("bg-brand-green", content)
        self.assertIn("rounded-full", content)

    def test_number_line_renders_legend_items(self):
        """Legend row should contain labels for all three markers."""
        response = self._get_dashboard()
        content = response.content.decode()
        # Legend items contain the tag names
        self.assertContains(response, "You")
        self.assertContains(response, "Community")
        self.assertContains(response, "World Avg")

    def test_number_line_renders_endpoint_labels(self):
        """Min and max labels use dynamic ranges and new unit formats."""
        response = self._get_dashboard()
        current_year = date.today().year
        # Book length: dynamic range with "pages" unit
        self.assertContains(response, "pages")
        # Book age: dynamic range with "CE" suffix
        self.assertContains(response, "1980 CE")
        self.assertContains(response, f"{current_year} CE")
        # Books per year: "per year" label
        self.assertContains(response, "per year")

    def test_number_line_renders_hatching_bands(self):
        """The gradient diagonal hatching bands are present."""
        response = self._get_dashboard()
        content = response.content.decode()
        # Hatching uses bandStyle() which generates mask-image CSS
        self.assertIn("bandStyle", content)
        self.assertIn("mask-image", content)

    def test_number_line_two_marker_mode(self):
        """Controversial ratings number lines render without third marker (2-marker variant)."""
        response = self._get_dashboard()
        content = response.content.decode()
        # Controversial ratings uses bg-brand-cyan and bg-brand-orange
        self.assertIn("bg-brand-cyan", content)
        self.assertIn("bg-brand-orange", content)
        # The Goodreads Average tag appears in the controversial section
        self.assertContains(response, "Goodreads Average")

    def test_number_line_no_layout_js(self):
        """The new template should not contain the old layoutLabels JS."""
        response = self._get_dashboard()
        content = response.content.decode()
        self.assertNotIn("layoutLabels", content)
        self.assertNotIn("@resize.window", content)


# ──────────────────────────────────────────────────────────────
# 3. Edge case tests
# ──────────────────────────────────────────────────────────────


@override_settings(
    CELERY_TASK_ALWAYS_EAGER=True,
    CELERY_TASK_STORE_EAGER_RESULT=True,
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "nl-edge"}},
)
class NumberLineEdgeCaseTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="nledge", email="edge@test.com", password="testpass123")
        _seed_aggregate_analytics()
        self.client.login(username="nledge", password="testpass123")

    def _render_with_dna(self, dna):
        self.user.userprofile.dna_data = dna
        self.user.userprofile.save()
        return self.client.get(reverse("core:display_dna"))

    def test_number_line_values_at_extremes(self):
        """Values at the min/max of the scale render without error."""
        dna = _make_dna_data()
        dna["user_stats"]["avg_book_length"] = 50  # exactly at min
        response = self._render_with_dna(dna)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "number-line-wrap")

    def test_number_line_extreme_value_expands_range(self):
        """Extreme values cause the dynamic range to expand — page still renders."""
        dna = _make_dna_data()
        dna["user_stats"]["avg_book_length"] = 1500  # very high value
        response = self._render_with_dna(dna)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "number-line-wrap")

    def test_comparative_analytics_hidden_without_percentiles(self):
        """When bibliotype_percentiles is missing, comparative number lines are hidden but controversial ones remain."""
        dna = _make_dna_data()
        del dna["bibliotype_percentiles"]
        response = self._render_with_dna(dna)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        # Only controversial number lines should render (2 books in test data)
        self.assertEqual(content.count("number-line-wrap"), 2)
        # Fallback text should be shown for comparative section
        self.assertContains(response, "community rankings and percentiles")

    def test_controversial_books_empty(self):
        """When there are no controversial books, no number lines render for that section."""
        dna = _make_dna_data()
        dna["top_controversial_books"] = []
        response = self._render_with_dna(dna)
        self.assertEqual(response.status_code, 200)
        # Only the 3 comparative analytics number lines should exist
        content = response.content.decode()
        self.assertEqual(content.count("number-line-wrap"), 3)
