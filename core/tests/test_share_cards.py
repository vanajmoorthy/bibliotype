"""
Tests for the share-card partials and the timeline_bars filter they use.
"""

from django.template.loader import render_to_string
from django.test import SimpleTestCase

from core.templatetags.dna_extras import timeline_bars

DNA = {
    "reader_type": "Versatile Valedictorian",
    "reader_type_scores_version": 3,
    "reading_vibe": ["plotting elaborate murders in your head"],
    "top_reader_types": [
        {"type": "Versatile Valedictorian", "score": 100},
        {"type": "Small Press Supporter", "score": 75},
    ],
    "user_stats": {"total_books_read": 425, "total_pages_read": 161303, "avg_book_length": 382},
    "average_rating_overall": None,
    "top_genres": [("fantasy", 40), ("mystery", 10)],
    "top_authors": [("Ursula K. Le Guin", 9)],
    "fiction_nonfiction_split": {"fiction_count": 3, "nonfiction_count": 1, "defaulted_count": 0},
    "most_niche_book": {"title": "A Very Obscure Book", "author": "Someone", "read_count": 1},
    "mainstream_score_percent": 42,
    "stats_by_year": [{"year": 2020, "count": 5}, {"year": 2021, "count": 80}, {"year": 2022, "count": 40}],
}


def render_card(name, dna=DNA):
    return render_to_string(f"core/partials/share/{name}.html", {"dna": dna, "share_url": "bibliotype.app/u/reader"})


class ShareCardCopyTests(SimpleTestCase):
    CARDS = ("card_identity", "card_stats", "card_taste", "card_mainstream", "card_full")

    def test_wordmark_and_url_are_not_letter_spaced_capitals(self):
        for name in self.CARDS:
            with self.subTest(card=name):
                html = render_card(name)
                self.assertIn('<p class="text-center text-base font-bold">Bibliotype</p>', html)
                self.assertNotIn("tracking-[0.3em]", html)
                self.assertIn("bibliotype.app/u/reader", html)
                self.assertNotIn("tracking-wider uppercase opacity-60", html)

    def test_reader_type_lead_in_is_sentence_case(self):
        for name in ("card_identity", "card_full"):
            with self.subTest(card=name):
                html = render_card(name)
                self.assertIn("My reader type is...", html)
                self.assertNotIn("My Reader Type is", html)

    def test_trait_scores_are_percentages_like_the_dashboard(self):
        html = render_card("card_identity")
        self.assertIn("100%", html)
        self.assertIn("75%", html)

    def test_missing_average_rating_shows_na(self):
        for name in ("card_stats", "card_full"):
            with self.subTest(card=name):
                html = render_card(name)
                self.assertIn("N/A", html)
                self.assertNotIn("None", html)

    def test_taste_card_shows_fiction_split_instead_of_duplicate_niche_read(self):
        html = render_card("card_taste")
        self.assertIn("Fiction vs Nonfiction", html)
        self.assertIn("Fiction 75%", html)
        self.assertIn("Nonfiction 25%", html)
        # Most Niche Read lives on the mainstream card; it was duplicated here.
        self.assertNotIn("Most Niche Read", html)

    def test_taste_card_without_split_omits_it(self):
        html = render_card("card_taste", {**DNA, "fiction_nonfiction_split": None})
        self.assertNotIn("Fiction vs Nonfiction", html)


class TimelineBarsTests(SimpleTestCase):
    def test_busiest_year_is_full_height_and_others_scale(self):
        bars = timeline_bars(DNA["stats_by_year"])
        self.assertEqual([b["year"] for b in bars], [2020, 2021, 2022])
        self.assertEqual([b["height"] for b in bars], [2, 16, 8])

    def test_quiet_years_keep_a_visible_minimum(self):
        bars = timeline_bars([{"year": 2020, "count": 1}, {"year": 2021, "count": 500}])
        self.assertEqual(bars[0]["height"], 2)

    def test_empty_or_missing_data(self):
        self.assertEqual(timeline_bars([]), [])
        self.assertEqual(timeline_bars(None), [])
        self.assertEqual(timeline_bars([{"year": 2020, "count": 0}]), [])

    def test_bars_no_longer_all_max_out(self):
        html = render_card("card_stats")
        self.assertIn("height: 16px", html)
        self.assertIn("height: 8px", html)
        self.assertNotIn("max-height: 16px", html)


class ShareModalScrollLockTests(SimpleTestCase):
    def test_modal_pins_the_body_instead_of_toggling_overflow_hidden(self):
        # overflow-hidden on body snaps the page to the top; the settings modal's
        # position:fixed lock keeps the reader where they were.
        html = render_to_string("core/partials/share/share_modal.html", {"dna": DNA, "share_url": "bibliotype.app"})
        self.assertNotIn("classList.toggle('overflow-hidden'", html)
        self.assertIn("document.body.style.position = 'fixed'", html)
        self.assertIn("window.scrollTo(0, y)", html)
