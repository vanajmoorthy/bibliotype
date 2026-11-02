"""
Tests for the comparative analytics card's "Still enriching" banners.
"""

import re

from django.template.loader import render_to_string
from django.test import SimpleTestCase

DNA = {
    "bibliotype_percentiles": {"avg_book_length": 38, "avg_publish_year": 60, "avg_books_per_year": 19},
    "user_stats": {"avg_book_length": 361, "avg_publish_year": 1998, "avg_books_per_year": 21.2},
    "community_averages": {"avg_book_length": 375, "avg_publish_year": 1995, "avg_books_per_year": 22.5},
    "global_averages": {"avg_book_length_pages": 375, "avg_publish_year": 2009, "avg_books_per_year": 4.0},
    "comparative_text": {},
}


def render(enrichment):
    return render_to_string("core/partials/dna/comparative_analytics_card.html", {"dna": DNA, "enrichment": enrichment})


class ComparativeEnrichingBannerTests(SimpleTestCase):
    def test_banner_is_an_overlay_not_in_the_flow(self):
        html = render({"pages_any_missing": True, "year_any_missing": True})
        banners = re.findall(r'<p\s+class="enrichment-banner[^"]*"[^>]*>', html)
        self.assertEqual(len(banners), 2)
        for banner in banners:
            self.assertIn("absolute inset-x-0 top-0", banner)
            # The old in-flow banner pulled itself up with negative margins.
            self.assertNotIn("-mt-4", banner)
        self.assertIn('x-show="$store.enrichment?.pages_any_missing"', html)
        self.assertIn('x-show="$store.enrichment?.year_any_missing"', html)

    def test_posters_are_positioned_and_share_the_same_top_padding(self):
        html = render({"pages_any_missing": False, "year_any_missing": True})
        posters = re.findall(r'class="(bg-brand-(?:pink|cyan|green) [^"]*)"', html)
        self.assertEqual(len(posters), 3)
        for classes in posters:
            self.assertIn("relative", classes.split())
            self.assertIn("pt-6", classes.split())

    def test_no_banner_when_nothing_is_missing(self):
        html = render({"pages_any_missing": False, "year_any_missing": False})
        self.assertNotIn("Still enriching", html)
