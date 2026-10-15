import re
from pathlib import Path

from django.template.loader import render_to_string
from django.test import SimpleTestCase

# Rendered HTML captured from the templates before the share cards and button partials were merged.
SNAPSHOTS = Path(__file__).parent / "fixtures" / "partial_snapshots"

DNA = {
    "reader_type": "Fantasy Fanatic",
    "reading_vibe": ["cosy dragons", "late-night epics", "third vibe"],
    "user_stats": {"total_books_read": 120, "total_pages_read": 45210},
    "average_rating_overall": 3.9,
    "top_genres": [("fantasy", 40), ("science fiction", 20), ("mystery", 10), ("romance", 5)],
    "top_authors": [("Ursula K. Le Guin", 9), ("Terry Pratchett", 7), ("N. K. Jemisin", 5), ("Extra", 1)],
    "mainstream_score_percent": 42,
    "most_niche_book": {"title": "A Very Obscure Book With A Long Title Indeed"},
    "top_reader_types": [{"type": "Fantasy Fanatic"}, {"type": "Classic Connoisseur"}, {"type": "Page Turner"}],
}

SVG = '<svg class="h-6 w-6"><path d="M6 18L18 6"></path></svg>'
BUTTON_CASES = {
    "icon_pink": (
        "icon_button",
        {"color": "pink", "extra_classes": "cursor-pointer", "extra_attrs": "@click=x", "icon_svg": SVG},
    ),
    "link_green": ("link_button", {"href": "/", "text": "Home", "color": "green", "extra_classes": "mt-4"}),
    "nav_signup": ("nav_button", {"href": "/s", "text": "Sign Up", "is_signup": True}),
    "nav_pink": ("nav_button", {"href": "/d", "text": "Dashboard", "hover_color": "pink"}),
    "nav_yellow": ("nav_button", {"href": "/l", "text": "Login", "hover_color": "yellow"}),
    "secondary_pink": ("secondary_button", {"text": "Instructions", "color": "pink", "extra_attrs": "@click=x"}),
    "small_green": ("small_button", {"text": "Save", "color": "green", "type": "submit", "extra_classes": "text-xl"}),
    "small_link_green": (
        "small_link_button",
        {"href": "/", "text": "Upload", "color": "green", "extra_classes": "mt-6"},
    ),
    "small_link_purple": ("small_link_button", {"href": "/", "text": "Update", "color": "purple"}),
}


def normalise(html):
    return re.sub(r"\s+", " ", re.sub(r">\s+<", "><", html)).strip()


def full_cards_from_share_modal():
    html = render_to_string(
        "core/partials/share/share_modal.html", {"dna": DNA, "share_url": "bibliotype.app/u/reader"}
    )
    return dict(re.findall(r'x-ref="cardFull(\w+)">(.*?)</div>\s*<button', html, re.S))


class FullShareCardThemeTests(SimpleTestCase):
    def test_each_theme_renders_like_its_original_card(self):
        cards = full_cards_from_share_modal()
        self.assertEqual(set(cards), {"Sherbet", "NeonPicnic", "Miami"})
        for theme, html in cards.items():
            with self.subTest(theme=theme):
                expected = (SNAPSHOTS / f"card_full_{theme}.html").read_text()
                self.assertEqual(normalise(html), expected.strip())


class ButtonPartialTests(SimpleTestCase):
    def test_each_call_site_variant_renders_like_before(self):
        for name, (partial, context) in BUTTON_CASES.items():
            with self.subTest(case=name):
                html = render_to_string(f"core/partials/buttons/{partial}.html", context)
                self.assertEqual(normalise(html), (SNAPSHOTS / f"{name}.html").read_text().strip())
