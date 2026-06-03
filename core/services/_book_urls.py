"""Single source of truth for Open Library cover URL construction.

Both endpoints return the same Open Library Covers API image; the difference
is the lookup key. Use `cover_url_from_isbn` when an ISBN13 is available, and
`cover_url_from_olid` when only an Open Library cover id (the integer returned
in `cover_i` from the search API, or `cover_id` from the works/editions API)
is available.
"""

OPEN_LIBRARY_ISBN_COVER_URL = "https://covers.openlibrary.org/b/isbn/{isbn}-M.jpg"
OPEN_LIBRARY_OLID_COVER_URL = "https://covers.openlibrary.org/b/id/{olid}-M.jpg"


def cover_url_from_isbn(isbn):
    """Return an Open Library cover URL from an ISBN13, or None for invalid input.

    Accepts Goodreads-style ="..." wrapping and surrounding whitespace.
    Returns None for inputs shorter than 10 characters after cleaning.
    """
    if not isbn:
        return None
    cleaned = str(isbn).strip().strip('="')
    if not cleaned or len(cleaned) < 10:
        return None
    return OPEN_LIBRARY_ISBN_COVER_URL.format(isbn=cleaned[:13])


def cover_url_from_olid(olid):
    """Return an Open Library cover URL from a numeric cover id, or None."""
    if olid is None or olid == "":
        return None
    return OPEN_LIBRARY_OLID_COVER_URL.format(olid=olid)
