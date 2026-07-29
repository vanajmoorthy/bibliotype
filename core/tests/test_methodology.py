"""Tests for the /methodology/ page and GLOBAL_AVERAGES_SOURCES constant."""

from django.test import TestCase
from django.urls import reverse

from core.dna_constants import GLOBAL_AVERAGES, GLOBAL_AVERAGES_SOURCES


class MethodologyPageTests(TestCase):
    """Verify that the methodology page loads and contains expected content."""

    def test_methodology_page_returns_200(self):
        url = reverse("core:methodology")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_methodology_page_contains_heading(self):
        url = reverse("core:methodology")
        response = self.client.get(url)
        content = response.content.decode()
        self.assertIn("Comparative Analytics", content)

    def test_methodology_page_contains_book_length_section(self):
        url = reverse("core:methodology")
        response = self.client.get(url)
        content = response.content.decode()
        # Anchor id present
        self.assertIn('id="book-length"', content)
        # Human-readable mention
        self.assertIn("book length", content.lower())

    def test_methodology_page_contains_book_age_section(self):
        url = reverse("core:methodology")
        response = self.client.get(url)
        content = response.content.decode()
        self.assertIn('id="book-age"', content)

    def test_methodology_page_contains_books_per_year_section(self):
        url = reverse("core:methodology")
        response = self.client.get(url)
        content = response.content.decode()
        self.assertIn('id="books-per-year"', content)
        self.assertIn("books a year", content.lower())

    def test_methodology_page_renders_external_source_urls(self):
        """All three external source URLs must appear in the rendered page."""
        url = reverse("core:methodology")
        response = self.client.get(url)
        content = response.content.decode()

        for key, source in GLOBAL_AVERAGES_SOURCES.items():
            self.assertIn(
                source["url"],
                content,
                msg=f"Source URL for '{key}' not found in methodology page.",
            )

    def test_methodology_page_renders_global_average_values(self):
        """The numeric global average values must appear in the rendered page."""
        url = reverse("core:methodology")
        response = self.client.get(url)
        content = response.content.decode()

        self.assertIn(str(GLOBAL_AVERAGES["avg_book_length_pages"]), content)
        self.assertIn(str(GLOBAL_AVERAGES["avg_publish_year"]), content)
        self.assertIn(str(GLOBAL_AVERAGES["avg_books_per_year"]), content)

    def test_methodology_page_links_to_home(self):
        """The CTA link back to the home page must be present."""
        url = reverse("core:methodology")
        response = self.client.get(url)
        content = response.content.decode()
        home_url = reverse("core:home")
        self.assertIn(home_url, content)


class GlobalAveragesSourcesConsistencyTests(TestCase):
    """GLOBAL_AVERAGES_SOURCES must have an entry for every key in GLOBAL_AVERAGES."""

    def test_all_global_averages_keys_have_sources(self):
        for key in GLOBAL_AVERAGES:
            self.assertIn(
                key,
                GLOBAL_AVERAGES_SOURCES,
                msg=f"GLOBAL_AVERAGES key '{key}' has no entry in GLOBAL_AVERAGES_SOURCES.",
            )

    def test_all_sources_have_non_empty_url(self):
        for key, source in GLOBAL_AVERAGES_SOURCES.items():
            self.assertIn("url", source, msg=f"Source for '{key}' is missing 'url' key.")
            self.assertTrue(source["url"], msg=f"Source url for '{key}' is empty.")

    def test_all_sources_have_non_empty_title(self):
        for key, source in GLOBAL_AVERAGES_SOURCES.items():
            self.assertIn("title", source, msg=f"Source for '{key}' is missing 'title' key.")
            self.assertTrue(source["title"], msg=f"Source title for '{key}' is empty.")

    def test_no_extra_sources_keys_without_global_averages(self):
        """GLOBAL_AVERAGES_SOURCES should not have keys absent from GLOBAL_AVERAGES."""
        for key in GLOBAL_AVERAGES_SOURCES:
            self.assertIn(
                key,
                GLOBAL_AVERAGES,
                msg=f"GLOBAL_AVERAGES_SOURCES key '{key}' has no matching entry in GLOBAL_AVERAGES.",
            )
