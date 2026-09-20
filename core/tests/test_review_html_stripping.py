"""The review card must never render raw HTML from Goodreads review exports.

New DNA is sanitized at ingestion, but profiles computed before that fix
store raw tags in most_positive/negative_review — the strip_review_html
template filter cleans those at render time.
"""

from django.test import SimpleTestCase

from core.templatetags.dna_extras import strip_review_html


class StripReviewHtmlFilterTests(SimpleTestCase):
    def test_br_variants_become_newlines(self):
        text = "First paragraph.<br/><br/>Second paragraph.<br>Third.<BR />"
        cleaned = strip_review_html(text)
        self.assertNotIn("<", cleaned)
        self.assertEqual(cleaned, "First paragraph.\n\nSecond paragraph.\nThird.")

    def test_other_tags_are_stripped(self):
        text = 'A <b>bold</b> claim with a <a href="https://x.test">link</a>.'
        self.assertEqual(strip_review_html(text), "A bold claim with a link.")

    def test_plain_text_unchanged(self):
        self.assertEqual(strip_review_html("Loved it."), "Loved it.")

    def test_non_string_passthrough(self):
        self.assertIsNone(strip_review_html(None))
        self.assertEqual(strip_review_html(3), 3)
