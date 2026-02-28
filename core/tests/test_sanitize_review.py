from django.test import TestCase

from core.services.dna_analyser import _sanitize_review_text


class SanitizeReviewTextTests(TestCase):
    """Tests for _sanitize_review_text used to strip HTML from user reviews."""

    def test_plain_text_unchanged(self):
        self.assertEqual(_sanitize_review_text("A great book!"), "A great book!")

    def test_br_tags_become_newlines(self):
        self.assertEqual(
            _sanitize_review_text("First paragraph.<br/>Second paragraph."),
            "First paragraph.\nSecond paragraph.",
        )

    def test_br_tag_variants(self):
        self.assertEqual(_sanitize_review_text("a<br>b"), "a\nb")
        self.assertEqual(_sanitize_review_text("a<br/>b"), "a\nb")
        self.assertEqual(_sanitize_review_text("a<br />b"), "a\nb")
        self.assertEqual(_sanitize_review_text("a<BR>b"), "a\nb")
        self.assertEqual(_sanitize_review_text("a<BR/>b"), "a\nb")

    def test_strips_script_tags(self):
        self.assertEqual(
            _sanitize_review_text('<script>alert("xss")</script>Loved it'),
            'alert("xss")Loved it',
        )

    def test_strips_arbitrary_html(self):
        self.assertEqual(
            _sanitize_review_text("<b>Bold</b> and <i>italic</i>"),
            "Bold and italic",
        )

    def test_strips_nested_html(self):
        self.assertEqual(
            _sanitize_review_text('<div class="foo"><p>Hello</p></div>'),
            "Hello",
        )

    def test_none_returns_none(self):
        self.assertIsNone(_sanitize_review_text(None))

    def test_empty_string_returns_empty(self):
        self.assertEqual(_sanitize_review_text(""), "")

    def test_non_string_returns_unchanged(self):
        self.assertEqual(_sanitize_review_text(123), 123)

    def test_whitespace_stripped(self):
        self.assertEqual(_sanitize_review_text("  hello  "), "hello")

    def test_real_world_goodreads_review(self):
        review = (
            "After reading this book - I changed my rating.<br/><br/>"
            "The storyline felt very different.<br/><br/>"
            "I love the constant surprises."
        )
        expected = (
            "After reading this book - I changed my rating.\n\n"
            "The storyline felt very different.\n\n"
            "I love the constant surprises."
        )
        self.assertEqual(_sanitize_review_text(review), expected)
