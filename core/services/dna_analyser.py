# Deprecated shim — import from core.services.dna instead. Remove after 2026-08-03.
from .dna import *  # noqa: F401,F403
from .dna import (  # noqa: F401 — `*` skips underscore names, re-export them explicitly
    _build_cover_url,
    _cover_initial,
    _detect_and_normalize_csv,
    _isbn_to_isbn13,
    _sanitize_review_text,
    _save_dna_to_profile,
)
