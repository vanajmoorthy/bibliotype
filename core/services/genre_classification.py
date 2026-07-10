"""Shared context-dependent fiction/nonfiction classification.

Ambiguous genres ("classic fiction", "young adult fiction", "children's fiction")
default to fiction in the Genre DB, but a book carrying ONLY ambiguous fiction
genres alongside nonfiction genres (e.g. "classic fiction" + "history" for
"A Brief History of Time") is really nonfiction. This module resolves that at
analysis time, where the full set of a book's genres is available.

Used by core/services/dna/__init__.py (calculate_full_dna) and
core/views/_helpers.py (_compute_enrichment_stats) so both counting paths share
one vocabulary and one resolution policy.
"""

from ..dna_constants import AMBIGUOUS_FICTION_GENRES, CANONICAL_GENRE_MAP, FICTION_GENRES, NONFICTION_GENRES


def canonicalize_genre_names(genre_names):
    """Map an iterable of raw genre names to a set of canonical genre names."""
    return {CANONICAL_GENRE_MAP.get(g, g) for g in genre_names}


def classify_genres(canonical):
    """Classify a set of canonical genre names as "fiction", "nonfiction", or None.

    Resolution order (context-dependent, per the genre-accuracy plan):
    1. ONLY ambiguous fiction genres + a nonfiction signal → nonfiction
       (e.g. {"classic fiction", "history"})
    2. Any unambiguous fiction genre → fiction
       (e.g. {"classic fiction", "history", "fantasy"} — historical fantasy)
    3. Any nonfiction genre → nonfiction
    4. Only ambiguous fiction genres, no other signal → fiction (the default)
    5. Empty or unmatched set → None (caller decides how to track it)
    """
    ambiguous_fiction = canonical & AMBIGUOUS_FICTION_GENRES
    unambiguous_fiction = canonical & (FICTION_GENRES - AMBIGUOUS_FICTION_GENRES)
    has_nonfiction = bool(canonical & NONFICTION_GENRES)

    if ambiguous_fiction and has_nonfiction and not unambiguous_fiction:
        return "nonfiction"
    if unambiguous_fiction:
        return "fiction"
    if has_nonfiction:
        return "nonfiction"
    if ambiguous_fiction:
        return "fiction"
    return None


def count_fiction_nonfiction(genre_sets):
    """Count fiction/nonfiction/defaulted books across an iterable of canonical genre sets.

    Returns (fiction_count, nonfiction_count, defaulted_count). The three
    counters are independent — books with no classifiable genres land in
    defaulted_count and are NEVER added to fiction_count — and always sum to
    the number of sets processed.
    """
    fiction_count = 0
    nonfiction_count = 0
    defaulted_count = 0
    for canonical in genre_sets:
        classification = classify_genres(canonical)
        if classification == "fiction":
            fiction_count += 1
        elif classification == "nonfiction":
            nonfiction_count += 1
        else:
            defaulted_count += 1
    return fiction_count, nonfiction_count, defaulted_count
