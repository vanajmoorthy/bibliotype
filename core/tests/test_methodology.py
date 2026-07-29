"""Tests for the /methodology/ page and GLOBAL_AVERAGES_SOURCES constant."""

from django.test import TestCase
from django.urls import reverse

from core.dna_constants import GLOBAL_AVERAGES, GLOBAL_AVERAGES_SOURCES

# The documented schema for each GLOBAL_AVERAGES_SOURCES entry.
SOURCE_SCHEMA_KEYS = {"url", "archived_url", "accessed", "note"}


class MethodologyPageTests(TestCase):
    """Verify that the methodology page loads and contains the expected content."""

    def setUp(self):
        self.url = reverse("core:methodology")

    def test_methodology_page_returns_200(self):
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_methodology_page_contains_heading(self):
        self.assertIn("Comparative Analytics", self.client.get(self.url).content.decode())

    def test_methodology_page_contains_all_section_anchors(self):
        """The comparative card deep-links to these anchors; they must exist."""
        content = self.client.get(self.url).content.decode()
        for anchor in ('id="book-length"', 'id="book-age"', 'id="books-per-year"'):
            self.assertIn(anchor, content, msg=f"Missing section anchor {anchor}.")

    def test_methodology_page_distinguishes_live_data_from_estimates(self):
        """The honesty distinction — real community data vs literature-derived constants — must be stated."""
        content = self.client.get(self.url).content.decode().lower()
        self.assertIn("community percentiles are real data", content)
        self.assertIn("global averages are rough estimates", content)

    def test_methodology_page_renders_every_source_note(self):
        """Every source's plain-language note must be rendered on the page."""
        content = self.client.get(self.url).content.decode()
        for key, source in GLOBAL_AVERAGES_SOURCES.items():
            fragment = source["note"].split(".")[0].strip()
            self.assertIn(fragment, content, msg=f"Note for '{key}' was not rendered.")

    def test_methodology_page_links_only_sources_that_have_a_url(self):
        """Live URLs are linked; sources without one render no link and never leak 'None'."""
        content = self.client.get(self.url).content.decode()
        sources_with_url = [s for s in GLOBAL_AVERAGES_SOURCES.values() if s["url"]]
        for source in sources_with_url:
            self.assertIn(source["url"], content, msg="A live source url was not rendered.")
        self.assertEqual(
            content.count("Further reading"),
            len(sources_with_url),
            msg="'Further reading' link count must match the number of sources with a live url.",
        )

    def test_methodology_page_renders_global_average_values(self):
        content = self.client.get(self.url).content.decode()
        for key, value in GLOBAL_AVERAGES.items():
            self.assertIn(str(value), content, msg=f"Global average value for '{key}' not rendered.")

    def test_methodology_page_links_to_home(self):
        """The CTA link back to the home page must be present."""
        content = self.client.get(self.url).content.decode()
        self.assertIn(reverse("core:home"), content)


class GlobalAveragesSourcesConsistencyTests(TestCase):
    """GLOBAL_AVERAGES_SOURCES must honestly document every GLOBAL_AVERAGES key."""

    def test_every_global_average_key_has_a_source_entry(self):
        for key in GLOBAL_AVERAGES:
            self.assertIn(key, GLOBAL_AVERAGES_SOURCES, msg=f"'{key}' has no source entry.")

    def test_no_orphan_source_entries(self):
        for key in GLOBAL_AVERAGES_SOURCES:
            self.assertIn(key, GLOBAL_AVERAGES, msg=f"Source '{key}' has no matching GLOBAL_AVERAGES key.")

    def test_every_source_matches_the_documented_schema(self):
        for key, source in GLOBAL_AVERAGES_SOURCES.items():
            self.assertEqual(
                set(source.keys()),
                SOURCE_SCHEMA_KEYS,
                msg=f"Source '{key}' keys {set(source.keys())} do not match schema {SOURCE_SCHEMA_KEYS}.",
            )

    def test_every_source_has_an_accessed_date_and_a_note(self):
        """A note is the minimum honest documentation; the url may legitimately be None."""
        for key, source in GLOBAL_AVERAGES_SOURCES.items():
            self.assertTrue(str(source["accessed"]).strip(), msg=f"'{key}' has no accessed date.")
            self.assertTrue(str(source["note"]).strip(), msg=f"'{key}' has no note.")

    def test_source_urls_are_null_or_wellformed(self):
        for key, source in GLOBAL_AVERAGES_SOURCES.items():
            for field in ("url", "archived_url"):
                value = source[field]
                if value is not None:
                    self.assertTrue(
                        value.startswith(("https://", "http://")),
                        msg=f"{field} for '{key}' is not a URL: {value!r}",
                    )

    def test_books_per_year_is_a_survey_median_not_a_skewed_mean(self):
        """avg_books_per_year is the median across reading surveys (which cluster at 2-5),
        not the heavy-reader-skewed mean (~12). Guards against accidental reversion, and
        against re-adopting the old unsourced 7."""
        value = GLOBAL_AVERAGES["avg_books_per_year"]
        self.assertGreaterEqual(value, 2, msg="books-per-year is below every reported survey median.")
        self.assertLessEqual(value, 6, msg="books-per-year looks like a skewed mean, not a typical-reader median.")
        self.assertIn(
            "pewresearch.org",
            GLOBAL_AVERAGES_SOURCES["avg_books_per_year"]["url"],
            msg="books-per-year should cite a named survey (Pew) for its median.",
        )
