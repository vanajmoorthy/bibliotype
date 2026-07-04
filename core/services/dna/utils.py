import re

import pandas as pd

from core.services._book_urls import cover_url_from_isbn

# Backwards-compatible re-export: tests and earlier call sites import
# `_build_cover_url` from this module. The implementation now lives in
# `core/services/_book_urls.py` as `cover_url_from_isbn`.
_build_cover_url = cover_url_from_isbn

_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _sanitize_review_text(text):
    """Strip HTML tags from review text, preserving <br> variants as newlines."""
    if not text or not isinstance(text, str):
        return text
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = _HTML_TAG_RE.sub("", text)
    return text.strip()


_ARTICLE_PREFIXES = ("the ", "a ", "an ")


def _cover_initial(title):
    """Return the first letter for a book cover fallback, skipping leading articles."""
    lower = title.lower()
    for prefix in _ARTICLE_PREFIXES:
        if lower.startswith(prefix):
            rest = title[len(prefix):].strip()
            if rest:
                return rest[0].upper()
    return title[0].upper() if title else "?"


def _isbn_to_isbn13(raw):
    """Normalize an ISBN-10 or ISBN-13 to its 13-digit form.

    Without this, Goodreads ISBN-10 and StoryGraph ISBN-13 for the same physical
    book don't match in our DB, so cross-platform dedup fails.

    Returns None for invalid input. Accepts the ISBN-10 X check digit (X = 10),
    Goodreads-style ="..." wrapping, and inputs containing whitespace or other
    junk characters. Idempotent for valid ISBN-13 inputs.
    """
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None
    s = str(raw).strip().strip('="').upper()
    cleaned = re.sub(r"[^0-9X]", "", s)
    if len(cleaned) == 13 and cleaned.isdigit():
        return cleaned
    if len(cleaned) == 10 and cleaned[:9].isdigit() and (cleaned[9].isdigit() or cleaned[9] == "X"):
        prefix = "978" + cleaned[:9]
        total = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(prefix))
        check = (10 - (total % 10)) % 10
        return prefix + str(check)
    return None
