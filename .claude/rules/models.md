---
paths:
  - "core/models.py"
  - "core/services/**"
---

# Data Models

## Normalization Pattern

Author, Book, and Publisher auto-compute normalized fields in `save()` overrides. These are used for deduplication via unique constraints.

**Author._normalize(name):** lowercase → remove punctuation → remove whitespace
- "J.K. Rowling" → "jkrowling"

**Book._normalize_title(title):** lowercase → remove parenthetical/bracketed content → remove punctuation → remove whitespace
- "The Hobbit (1937 Edition)" → "thehobbit"

**Publisher:** Reuses `Author._normalize()` for its `normalized_name`.

Normalization is **lossy and one-way** — can cause false matches (rare) or miss edge cases.

## Signals

One `post_save` receiver on User: `create_user_profile` creates the UserProfile when a User is created. A User save never writes the profile: a full profile save there would write back stale `dna_data`/`pending_dna_task_id` over a DNA task's save. Save profiles with `update_fields`.

**Disconnected during fixture loading** (`load_fixture_data.py`) to prevent duplicate creation errors. Reconnected in `finally` block.

## Key Relationships

- **Book → Author:** CASCADE (author deleted = books deleted)
- **Book → Publisher:** SET_NULL (publisher deleted = book keeps existing, publisher becomes null)
- **Publisher → Publisher (parent):** SET_NULL, self-referential FK (related_name="subsidiaries")
- **UserBook:** unique_together("user", "book") — one record per user per book
- **Book:** unique_together("normalized_title", "author") — prevents duplicate books per author

## AggregateAnalytics (Singleton)

Forced `pk=1` in `save()`. Access via `AggregateAnalytics.get_instance()` (uses `get_or_create`).
Contains histogram distributions for percentile calculations: `avg_book_length_dist`, `avg_publish_year_dist`, `total_books_read_dist`, `avg_books_per_year_dist`.

## Three User Data Storage Models

1. **UserProfile** — Persistent authenticated user data (dna_data, recommendations, reader_type)
2. **AnonymousUserSession** — Temporary 7-day storage (session_key, dna_data, books_data, ratings). Indexed by (session_key, expires_at) for cleanup
3. **AnonymizedReadingProfile** — Permanent anonymized aggregates for community comparisons (no identifiers)

Flow: AnonymousUserSession → (expire) → AnonymizedReadingProfile (via `anonymize_expired_sessions_task`)

## JSONField Schemas (no validation)

**UserProfile.dna_data:** Complex nested dict with `user_stats`, `reader_type`, `top_genres`, `top_authors`, `ratings_distribution`, `reading_vibe`, `mainstream_score_percent`, `fiction_nonfiction_split`, and many more. Schema evolves without migrations.

**UserProfile.reading_vibe:** List of strings (LLM-generated). Regenerated only when `vibe_data_hash` (SHA256 of book fingerprints) changes.

**UserProfile.recommendations_data:** List of recommendation dicts with book info, confidence scores, and explanation components.

**AnonymousUserSession fields:**
- `books_data`: list of book IDs
- `top_books_data`: top 5 book IDs
- `genre_distribution` / `author_distribution`: {"name": count}
- `book_ratings`: {"book_id": rating_int}

## UserBook Top Books

- `is_top_book` (bool) + `top_book_position` (1-5 or null)
- Calculated by `calculate_and_store_top_books()` in top_books_service.py
- Scoring: rating weight (5★=100, 4★=80, else rating×15) + sentiment weight (±30)
- Resets ALL books to false, then marks top 5
- Compound indexes on (user, is_top_book) and (book, is_top_book)

## Gotchas

- `Book.isbn13` is unique but nullable — multiple NULL values allowed (PostgreSQL behavior)
- Publisher.parent allows cycles (no constraint preventing circular references)
- JSONField schemas are unvalidated — fields can be missing on older profiles (use `.get()` with defaults)
- Author/Book normalized fields can diverge from display fields after manual edits
