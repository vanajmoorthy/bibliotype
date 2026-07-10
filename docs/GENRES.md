# How the Genre System Works

A book's "genre" in Bibliotype is never taken at face value. Raw genre-ish strings arrive from four messy sources — Open Library subjects, Google Books categories, StoryGraph user tags, and Goodreads shelves — and everything downstream (the fiction/nonfiction split, top-genres chart, reader types, similarity, recommendations) only ever sees a small **canonical vocabulary** after normalization. This doc walks the whole pipeline with code references.

Line numbers are anchors as of the genre-accuracy overhaul (PRs #120/#121, 2026-07); re-anchor with `grep -n "<symbol>"` if they drift.

## 1. The canonical vocabulary (`core/dna_constants.py`)

| Structure | Where | What it is |
|---|---|---|
| `GENRE_ALIASES` | `core/dna_constants.py:123` | **Source of truth.** ~40 canonical genres, each mapping to a set of alias strings ("whodunit" → `mystery`). |
| `CANONICAL_GENRE_MAP` | `core/dna_constants.py:1605` | Derived reverse index: alias → canonical. The single lookup primitive used everywhere. |
| `EXCLUDED_GENRES` | `core/dna_constants.py:580` | Junk subjects dropped before matching (awards, places, LoC codes, "literary periodicals"…). Checked *before* aliases, so a string in here can silently kill a genre — a regression test in `test_genre_canonicalization.py` asserts no canonical name or alias is ever excluded. |
| `GENRE_PRIORITY` | `core/dna_constants.py:1539` | Specific-to-generic ordering used to pick a book's top 5–6 genres at enrichment time. Covers every canonical genre (anything missing would sort last and get truncated away). |
| `FICTION_GENRES` / `NONFICTION_GENRES` | `core/dna_constants.py:1611` / `:1636` | Classification sets. A module-load assertion guarantees every canonical genre is in exactly one. |
| `AMBIGUOUS_FICTION_GENRES` | `core/dna_constants.py:1661` | `{classic fiction, young adult fiction, children's fiction}` — genres that *default* to fiction but get context-dependent resolution (§4). |
| `STORYGRAPH_TAG_TO_GENRE` | `core/dna_constants.py:1680` | StoryGraph user tags → canonical genres, applied at CSV-parse time. |

Backward compatibility: the pre-overhaul names `classics`, `young adult`, `children's literature` still exist as *aliases* pointing at the fiction sub-splits (`CANONICAL_GENRE_MAP["classics"] == "classic fiction"`), so old Genre rows in the DB never needed a migration.

## 2. Where raw genres come from

### Open Library + Google Books (enrichment)

`enrich_book_from_apis` in `core/services/book_enrichment_service.py` fetches:

- **Open Library subjects** → `_clean_and_canonicalize_genres` (`book_enrichment_service.py:45`): drop `EXCLUDED_GENRES`, then word-boundary regex match against all aliases (longest first), double-check the canonical result isn't excluded.
- **Google Books categories** ("Fiction / Mystery / Cozy") → `_canonicalize_google_books_categories` (`book_enrichment_service.py:81`): split on `/`, delegate to the same cleaner.

Since PR #120 the two sources **merge** (Google Books first — higher confidence — Open Library supplements) instead of GB replacing OL. The combined set is sorted by `GENRE_PRIORITY` and truncated to the top 5–6 before being written to the `Book.genres` M2M.

Enrichment runs in two modes:
- **Inline** during DNA calculation (since PR #121): new/genre-less books call `enrich_book_from_apis(book, session, quick_mode=True)` right inside `process_book_row` (`core/services/dna/__init__.py:290`) with reduced HTTP timeouts (`book_enrichment_service.py:175`), so your first dashboard render uses real genres. Governed by a 90-second wall-clock budget (`core/services/dna/enrichment_budget.py:16`, lazily started so already-enriched libraries pay nothing).
- **Async fallback** via `enrich_book_task` when the inline call fails or the budget runs out — identical behavior to the pre-overhaul world.

### StoryGraph tags (CSV parse)

StoryGraph exports carry user tags; `STORYGRAPH_TAG_TO_GENRE` maps common ones to canonical genres during CSV normalization (`core/services/dna/csv_parser.py`), letting tagged books skip API lookups entirely.

### Goodreads shelves (weak signals only)

The Goodreads `Bookshelves` column (exact name — *not* "Bookshelves with positions") is parsed in `calculate_full_dna` (`core/services/dna/__init__.py:344`). Shelf names matching `CANONICAL_GENRE_MAP` become shelf-genres; the literal shelves `fiction` / `nonfiction` become boolean signals. Crucially these are **tiebreakers only** — see §4. A user shelving "Sapiens" as `fiction` cannot override its API genres.

## 3. Canonicalization at analysis time

Everything the analyzer touches goes through `canonicalize_genre_names` (`core/services/genre_classification.py:27`) — a plain `CANONICAL_GENRE_MAP.get(name, name)` set-map. The same lookup guards genre vocabulary in `user_similarity_service.py` (e.g. `:19`, `:81`) so profiles stored under old names still compare correctly against new ones.

## 4. Fiction vs nonfiction: context-dependent classification

One shared module decides — `core/services/genre_classification.py` — used by **both** counting paths so they can't drift:
- upload-time: `calculate_full_dna` (`core/services/dna/__init__.py:364`)
- dashboard recompute: `_compute_enrichment_stats` (`core/views/_helpers.py:57`)

`classify_genres(canonical, shelf_fiction, shelf_nonfiction, shelf_genres)` (`genre_classification.py:58`) resolves in this order:

1. *Only* ambiguous fiction genres + a nonfiction signal → **nonfiction** ("A Brief History of Time" = `classic fiction` + `history`)
2. Any unambiguous fiction genre → **fiction** (historical fantasy stays fiction even with `history` present)
3. Any nonfiction genre → **nonfiction**
4. No API signal at all → shelf signals may decide (this is the *only* place shelves matter)
5. Ambiguous-only, no other signal → **fiction** (the safe default)
6. Nothing → **None**

`count_fiction_nonfiction` (`genre_classification.py:92`) turns per-book results into three *independent* counters: `fiction_count`, `nonfiction_count`, and `defaulted_count`. Unclassifiable books are **never** counted as fiction (pre-overhaul they were silently skipped); they're tracked and excluded from the pie chart. The three always sum to total books.

The stored shape in `dna_data`:

```python
"fiction_nonfiction_split": {"fiction_count": N, "nonfiction_count": N, "defaulted_count": N}
```

`fiction_nonfiction_card.html` uses `fiction_count + nonfiction_count` as its denominator and shows a "Based on X of Y books" note while enrichment is pending and defaulted books remain; `genre_coverage_pct` comes from `_compute_enrichment_progress` (`core/views/_helpers.py:164`).

## 5. Downstream consumers

- **Top genres** — a `Counter` over each book's canonicalized genres, top 10, stored in `dna_data["top_genres"]`, rendered in `top_genres_authors_row.html` + the donut chart in `charts_scripts.html`.
- **Reader types** — `assign_reader_type` (`core/services/dna/reader_type.py:16`) counts canonical genres into type scores (fantasy/sci-fi/dystopian/adventure → Fantasy Fanatic; non-fiction/memoir/true crime/essays/classic nonfiction → Non-Fiction Ninja; etc.). Genre *diversity* (≥10 unique canonical genres) feeds Versatile Valedictorian.
- **Similarity & recommendations** — genre distributions are one component of user-similarity cosine scoring (`user_similarity_service.py`) and of recommendation genre-alignment (`recommendation_service.py`), both canonicalized.

## 6. Keeping existing data fresh

Genres stored on `Book` rows only change when a book is re-enriched. After vocabulary changes deploy, run on the VPS (tmux — it's rate-limited and slow):

```bash
docker compose -f docker-compose.prod.yml exec web poetry run python manage.py enrich_books --process-all
```

`--process-all` also resets `google_books_last_checked` so Google Books data is re-fetched and merged (otherwise the GB fetch is skipped for previously-checked books).

## 7. Tests to know about

- `core/tests/test_genre_canonicalization.py` — alias mapping, exclusion regression guards, the classification matrix, shelf-tiebreaker matrix, counter independence, backward-compat aliases.
- `core/tests/test_fiction_book_extremes.py` — ambiguous-genre classification cases + set coverage/overlap assertions.
- `core/tests/test_integration.py` — OL+GB merge behavior, inline enrichment + budget fallback.

## 8. Known simplifications

- `poetry` is classified as fiction (some poetry is neither, in the traditional sense).
- The Genre DB always stores the *fiction default* for ambiguous genres ("classic fiction", never "classic nonfiction") — the nonfiction variant exists only as a classification label at analysis time.
- StoryGraph's `Tags` column isn't parsed for shelf-style signals (format unverified); only the tag→genre map applies.
