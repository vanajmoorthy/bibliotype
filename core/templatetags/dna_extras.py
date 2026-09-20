"""
Custom template filters for the DNA dashboard.

reader_color — maps a reader type name to its brand colour token so templates
can look up per-type colours without hard-coding class names that Tailwind's
scanner can't detect from Python data.
"""

from django import template

from core.dna_constants import READER_TYPE_COLORS
from core.services.dna.utils import _sanitize_review_text

register = template.Library()


@register.filter
def reader_color(reader_type_name):
    """Return the brand colour token (e.g. "purple", "yellow") for a reader type.

    Falls back to "purple" for unknown or legacy type names so old stored
    profiles always get a valid class rather than a broken CSS variable.

    Legacy mapping:
      "Classic Collector" → "orange"  (same as the renamed "Classics Collector")
      "Poetry Pilgrim"    → "pink"    (predecessor to "Sonnet Slinger")
    Any other unknown name → "purple"

    Usage in templates:
        {% load dna_extras %}
        <div class="pixel-banner reader-banner--{{ dna.reader_type|reader_color }}">
    """
    if not reader_type_name:
        return "purple"
    return READER_TYPE_COLORS.get(reader_type_name, "purple")


@register.filter
def strip_review_html(text):
    """Strip HTML from review text (Goodreads exports embed <br/> etc.).

    New DNA is sanitized at ingestion (_sanitize_review_text), but DNA stored
    before that fix still carries raw tags — this cleans those at render time.
    <br> variants become newlines so |linebreaksbr can restore the paragraphs.
    """
    if not isinstance(text, str):
        return text
    return _sanitize_review_text(text)
