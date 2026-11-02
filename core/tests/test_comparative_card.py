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

    def _poster_classes(self, html):
        posters = re.findall(r'class="(bg-brand-(?:pink|cyan|green) [^"]*)"', html)
        self.assertEqual(len(posters), 3)
        return [classes.split() for classes in posters]

    def test_all_posters_get_banner_room_while_any_banner_shows(self):
        # Only the age poster is enriching, but all three get pt-6 so labels stay level.
        html = render({"pages_any_missing": False, "year_any_missing": True})
        for classes in self._poster_classes(html):
            self.assertIn("relative", classes)
            self.assertIn("pt-6", classes)
        self.assertEqual(html.count("$store.enrichment?.pages_any_missing || $store.enrichment?.year_any_missing"), 3)

    def test_no_extra_top_padding_when_nothing_is_enriching(self):
        html = render({"pages_any_missing": False, "year_any_missing": False})
        for classes in self._poster_classes(html):
            self.assertIn("p-4", classes)
            self.assertNotIn("pt-6", classes)

    def test_no_banner_when_nothing_is_missing(self):
        html = render({"pages_any_missing": False, "year_any_missing": False})
        self.assertNotIn("Still enriching", html)
