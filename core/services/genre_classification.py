"""Shared context-dependent fiction/nonfiction classification.

Ambiguous genres ("classic fiction", "young adult fiction", "children's fiction")
default to fiction in the Genre DB, but a book carrying ONLY ambiguous fiction
genres alongside nonfiction genres (e.g. "classic fiction" + "history" for
"A Brief History of Time") is really nonfiction. This module resolves that at
analysis time, where the full set of a book's genres is available.

Goodreads `Bookshelves` shelf names supply an additional WEAK signal: shelf
`"fiction"` / `"nonfiction"` booleans and canonical shelf genres act as
tiebreakers only — they never override clear API genre signals, so a user who
shelves "Sapiens" as "fiction" can't flip a book with real nonfiction genres.

Used by core/services/dna/__init__.py (calculate_full_dna) and
core/views/_helpers.py (_compute_enrichment_stats) so both counting paths share
one vocabulary and one resolution policy. The views helper has no CSV context
and passes no shelf data — the defaults keep its behaviour unchanged.
"""

from itertools import repeat

from ..dna_constants import AMBIGUOUS_FICTION_GENRES, CANONICAL_GENRE_MAP, FICTION_GENRES, NONFICTION_GENRES

_NO_SHELF_SIGNALS = (False, False, frozenset())


def canonicalize_genre_names(genre_names):
    """Map an iterable of raw genre names to a set of canonical genre names."""
    return {CANONICAL_GENRE_MAP.get(g, g) for g in genre_names}


def parse_shelf_signals(raw_bookshelves):
    """Parse a Goodreads `Bookshelves` cell into (shelf_fiction, shelf_nonfiction, shelf_genres).

    The column is comma-separated (e.g. "read, fiction, favorites"). `"fiction"`
    and `"nonfiction"`/`"non-fiction"` are special-cased as boolean signals —
    "fiction" sits in EXCLUDED_GENRES (API false-positive guard) and the
    nonfiction spellings must stay weak booleans rather than becoming canonical
    genres in the combined set. Other shelf names resolve through
    CANONICAL_GENRE_MAP; non-genre shelves ("read", "owned", ...) fall out.
    """
    shelf_fiction = False
    shelf_nonfiction = False
    shelf_genres = set()
    if not raw_bookshelves:
        return _NO_SHELF_SIGNALS
    for shelf in str(raw_bookshelves).split(","):
        shelf_clean = shelf.strip().lower()
        if shelf_clean == "fiction":
            shelf_fiction = True
        elif shelf_clean in ("nonfiction", "non-fiction"):
            shelf_nonfiction = True
        elif shelf_clean in CANONICAL_GENRE_MAP:
            shelf_genres.add(CANONICAL_GENRE_MAP[shelf_clean])
    return shelf_fiction, shelf_nonfiction, frozenset(shelf_genres)


def classify_genres(canonical, shelf_fiction=False, shelf_nonfiction=False, shelf_genres=frozenset()):
    """Classify a set of canonical genre names as "fiction", "nonfiction", or None.

    Resolution order (context-dependent, per the genre-accuracy plan; shelf
    signals are weak tiebreakers consulted ONLY when the genre sets provide no
    clear signal):
    1. ONLY ambiguous fiction genres + a nonfiction signal → nonfiction
       (e.g. {"classic fiction", "history"})
    2. Any unambiguous fiction genre → fiction
       (e.g. {"classic fiction", "history", "fantasy"} — historical fantasy)
    3. Any nonfiction genre → nonfiction
    4. Shelf "nonfiction" → nonfiction (no API signal, trust the shelf;
       also disambiguates ambiguous-only sets like {"classic fiction"})
    5. Shelf "fiction", or only ambiguous fiction genres → fiction
    6. Empty or unmatched set, no shelf signal → None (caller decides)
    """
    combined = canonical | shelf_genres
    ambiguous_fiction = combined & AMBIGUOUS_FICTION_GENRES
    unambiguous_fiction = combined & (FICTION_GENRES - AMBIGUOUS_FICTION_GENRES)
    has_nonfiction = bool(combined & NONFICTION_GENRES)

    if ambiguous_fiction and has_nonfiction and not unambiguous_fiction:
        return "nonfiction"
    if unambiguous_fiction:
        return "fiction"
    if has_nonfiction:
        return "nonfiction"
    if shelf_nonfiction:
        return "nonfiction"
    if shelf_fiction or ambiguous_fiction:
        return "fiction"
    return None


def count_fiction_nonfiction(genre_sets, shelf_signals=None):
    """Count fiction/nonfiction/defaulted books across an iterable of canonical genre sets.

    `shelf_signals`, when given, is an iterable aligned with `genre_sets` of
    (shelf_fiction, shelf_nonfiction, shelf_genres) triples as produced by
    `parse_shelf_signals`. Callers without shelf data (e.g. the views helper)
    omit it and get pure API-genre classification.

    Returns (fiction_count, nonfiction_count, defaulted_count). The three
    counters are independent — books with no classifiable genres land in
    defaulted_count and are NEVER added to fiction_count — and always sum to
    the number of sets processed.
    """
    if shelf_signals is None:
        shelf_signals = repeat(_NO_SHELF_SIGNALS)
    fiction_count = 0
    nonfiction_count = 0
    defaulted_count = 0
    for canonical, (shelf_fiction, shelf_nonfiction, shelf_genres) in zip(genre_sets, shelf_signals):
        classification = classify_genres(
            canonical, shelf_fiction=shelf_fiction, shelf_nonfiction=shelf_nonfiction, shelf_genres=shelf_genres
        )
        if classification == "fiction":
            fiction_count += 1
        elif classification == "nonfiction":
            nonfiction_count += 1
        else:
            defaulted_count += 1
    return fiction_count, nonfiction_count, defaulted_count
