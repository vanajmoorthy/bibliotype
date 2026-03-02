---
title: Robust Book Cover Lookup
type: feat
status: completed
date: 2026-03-02
---

# Robust Book Cover Lookup

## Overview

Book covers currently rely on a single source: constructing an Open Library Covers API URL from the book's ISBN (`https://covers.openlibrary.org/b/isbn/{isbn}-M.jpg`). This has two failure modes:

1. **No ISBN** — many books lack an ISBN in both the CSV and the DB, so `_build_cover_url()` returns `None` and the letter-initial fallback is shown
2. **ISBN exists but Open Library has no cover** — OL returns a 1x1 transparent pixel; caught client-side by `naturalWidth < 10` check, but still wastes a request and shows the fallback

Two better cover sources are available but unused:
- **Open Library cover ID** (`cover_i`) — returned in OL search results, only present when OL actually has a cover image. URL: `https://covers.openlibrary.org/b/id/{cover_id}-M.jpg`. Works without ISBN and is a *verified* cover.
- **Google Books thumbnail** — returned in the `volumeInfo.imageLinks` field of the Google Books API response, which is *already being called* during enrichment but the thumbnail is discarded.

## Proposed Solution

Add a `cover_url` field to the `Book` model, populated during enrichment from the best available source. At DNA generation time, prefer `book.cover_url` over constructing from ISBN.

### Cover source priority (set during enrichment):
1. **Open Library by cover ID** — most reliable (only returned when cover exists)
2. **Open Library by ISBN** — constructed URL, may return 1x1 pixel
3. **Google Books thumbnail** — different source, good coverage (upgrade HTTP to HTTPS before storing)

### Step 1: Model migration

**File: `core/models.py`** (line 70, after `isbn13`)

Add to `Book`:
```python
cover_url = models.URLField(max_length=500, null=True, blank=True)
```

Uses `null=True, blank=True` following the existing pattern for optional fields (`isbn13`, `page_count`, `publish_year`).

Generate migration: `poetry run python manage.py makemigrations core`

### Step 2: Capture cover sources during enrichment

**File: `core/book_enrichment_service.py`**

**In `_fetch_from_open_library()` (~line 182):**

Capture `cover_i` from the search result (currently discarded) and add it to the `book_details` return dict:
```python
cover_id = search_result.get("cover_i")
# ...
book_details = {
    "genres": [], "publish_year": None, "publisher": None,
    "page_count": None, "isbn_13": None, "cover_id": cover_id,
}
```

**In `_fetch_ratings_and_categories_from_google_books()` (~line 273):**

Capture the thumbnail URL from `volumeInfo.imageLinks` (currently discarded):
```python
image_links = volume_info.get("imageLinks", {})
if thumbnail := image_links.get("thumbnail"):
    # Upgrade HTTP to HTTPS to avoid mixed-content warnings
    result["thumbnail_url"] = thumbnail.replace("http://", "https://")
```

**In `enrich_book_from_apis()` (top of function + after both API calls):**

First, initialize `gb_data = {}` at the top of the function alongside the existing counter variables. This is necessary because `gb_data` is only assigned inside the `if book.google_books_last_checked is None:` block — without initialization, referencing it in the cover_url logic below would raise `NameError` for already-enriched books:
```python
ol_api_calls = 0
gb_api_calls = 0
gb_data = {}  # May not be populated if GB enrichment already ran
is_updated = False
```

Then, after both API calls and before `book.save()` (~line 471), set `book.cover_url` from the best available source. Only set if currently None (don't downgrade a verified cover_i URL on re-enrichment):
```python
if not book.cover_url:
    new_cover_url = None
    if ol_data.get("cover_id"):
        new_cover_url = f"https://covers.openlibrary.org/b/id/{ol_data['cover_id']}-M.jpg"
    elif book.isbn13:
        new_cover_url = f"https://covers.openlibrary.org/b/isbn/{book.isbn13}-M.jpg"
    elif ol_data.get("isbn_13"):
        new_cover_url = f"https://covers.openlibrary.org/b/isbn/{ol_data['isbn_13']}-M.jpg"

    if not new_cover_url and gb_data.get("thumbnail_url"):
        new_cover_url = gb_data["thumbnail_url"]

    if new_cover_url:
        book.cover_url = new_cover_url
        is_updated = True
```

**Note on `google_books_last_checked` gate:** Books already enriched (where `google_books_last_checked is not None`) will not re-run the GB enrichment, so their thumbnails can only be recovered via the backfill command's `--with-api` mode or `enrich_books --process-all`.

### Step 3: Use `book.cover_url` in DNA generation

**File: `core/services/dna_analyser.py`**

**No changes to `_build_cover_url()` signature.** It stays as-is for backward compatibility and for the currently-reading case (CSV-only data).

Instead, update the three call sites where we have a Book object to prefer `book.cover_url` (a verified, enrichment-derived URL) over the ISBN-constructed URL:

```python
# Most niche book (line 605):
"cover_url": user_book_objects[0].cover_url or _build_cover_url(user_book_objects[0].isbn13),

# Longest book (line 622):
"cover_url": longest.cover_url or _build_cover_url(longest.isbn13),

# Shortest book (line 628):
"cover_url": shortest.cover_url or _build_cover_url(shortest.isbn13),
```

**Why `book.cover_url` takes priority:** A cover_i-based URL is verified to have an actual image (OL only returns `cover_i` when it has a cover). The ISBN-constructed URL is a blind guess that may return a 1x1 pixel. So the enrichment-derived URL should always win when available.

**Currently-reading books — zero-cost upgrade from DB sync (lines 431-466):**

Currently-reading books are initially built from raw CSV data (line 271-280) before any DB sync. However, they ARE synced to the DB later (lines 431-466) via `Book.objects.update_or_create()` — but the return value is currently discarded. By capturing the returned Book object, we can upgrade `cover_url` from prior enrichment at zero additional query cost:

```python
# Current code discards the return value:
Book.objects.update_or_create(
    normalized_title=normalized_title, author=author, defaults=cr_book_defaults
)

# Proposed — capture the Book object (already returned, zero extra queries):
try:
    db_book, _ = Book.objects.update_or_create(
        normalized_title=normalized_title, author=author, defaults=cr_book_defaults
    )
except IntegrityError:
    cr_book_defaults.pop("isbn13", None)
    db_book, _ = Book.objects.update_or_create(
        normalized_title=normalized_title, author=author, defaults=cr_book_defaults
    )

# Upgrade cover from enrichment data (verified cover_i or GB thumbnail)
if db_book.cover_url:
    cr_book["cover_url"] = db_book.cover_url
```

This helps when:
- **Returning users re-upload** — their currently-reading books already exist in the DB with `cover_url` populated from prior enrichment
- **Books uploaded by other users** — the book already exists and was enriched
- **Brand-new books** — `cover_url` is still `None`, so the CSV ISBN-based URL stays (no change from current behavior)

**Timing limitation:** For brand-new books, `enrich_book_task` runs *after* `calculate_full_dna()` completes. So `book.cover_url` will be `None` during the first DNA generation — the same timing constraint that already exists for genres and page counts. Users see improved covers on re-upload after enrichment has populated the field.

### Step 4: Backfill view helper

**File: `core/views.py`** (`_enrich_dna_for_display()`)

Add backfill guards for old DNA records, same pattern as the existing `most_niche_book` guard at line 224:

```python
# Existing guard (line 224):
if niche_book and "cover_url" not in niche_book:
    niche_book["cover_url"] = None

# New guards:
longest_book = dna_data.get("longest_book")
if longest_book and "cover_url" not in longest_book:
    longest_book["cover_url"] = None

shortest_book = dna_data.get("shortest_book")
if shortest_book and "cover_url" not in shortest_book:
    shortest_book["cover_url"] = None
```

### Step 5: Backfill management command + command runner

**New file: `core/management/commands/backfill_covers.py`**

A lightweight command that populates `cover_url` for existing books.

**Fast mode (default, no API calls):**
- Query `Book.objects.filter(cover_url__isnull=True, isbn13__isnull=False)` — books with ISBN but no cover URL
- For each, set `cover_url = f"https://covers.openlibrary.org/b/isbn/{book.isbn13}-M.jpg"`
- Bulk update in batches of 500
- **Note:** This is a best-effort pass — the URL may still return a 1x1 pixel from OL. The client-side `naturalWidth < 10` check handles this gracefully. The value is having the URL pre-stored on the model.

**Full mode (`--with-api`, makes API calls):**
- After the fast pass, query `Book.objects.filter(cover_url__isnull=True)` — remaining books without ISBN
- For each, make targeted lightweight API calls (not full enrichment):
  - OL search (`/search.json`) to get `cover_i` → construct `https://covers.openlibrary.org/b/id/{cover_i}-M.jpg`
  - If no `cover_i`, call GB volumes API to get `imageLinks.thumbnail` (upgrade HTTP→HTTPS)
- Rate-limited with `time.sleep(1.2)` between API calls (same pattern as `re_enrich_all_books`)
- Does NOT modify `google_books_last_checked` or other enrichment state — only sets `cover_url`
- Skip books that already have a non-null `cover_url`

**Arguments:** `--dry-run`, `--limit`, `--with-api`

**File: `core/admin.py`**

Add to `ADMIN_COMMANDS` list (after `backfill_subtitle_data`):
```python
{
    "name": "backfill_covers",
    "description": "Populate cover URLs for books. Fast mode uses ISBN (no API calls). Use --with-api for books missing ISBN.",
    "arguments": [
        {"name": "--dry-run", "type": "flag", "label": "Dry run", "help": "Show what would be updated without saving"},
        {"name": "--limit", "type": "int", "label": "Limit", "help": "Max books to process"},
        {"name": "--with-api", "type": "flag", "label": "With API calls", "help": "Also fetch covers for books without ISBN via API"},
    ],
},
```

Also update `BookAdmin`:
- Add `cover_url` to the main fieldset (after `isbn13`)
- Optionally add a truncated version to `list_display` for visibility

### Step 6: Tests

**File: `core/tests/test_currently_reading.py`** (existing `_build_cover_url` tests)

No changes needed — `_build_cover_url` signature is unchanged. Existing tests remain valid.

Add new tests for the `book.cover_url or _build_cover_url(isbn13)` pattern:
- `test_cover_url_prefers_book_cover_url_over_isbn` — Book with `cover_url="https://covers.openlibrary.org/b/id/123-M.jpg"` and `isbn13="9780593099322"` → DNA dict gets the cover_id URL, not the ISBN URL
- `test_cover_url_falls_back_to_isbn_when_no_book_cover_url` — Book with `cover_url=None` and `isbn13="9780593099322"` → DNA dict gets the ISBN URL
- `test_cover_url_none_when_no_book_cover_url_and_no_isbn` — Book with `cover_url=None` and `isbn13=None` → DNA dict gets `None`
- `test_currently_reading_cover_upgraded_from_db` — Currently-reading book exists in DB with `cover_url` from prior enrichment → `currently_reading_books` list gets the DB cover URL
- `test_currently_reading_cover_keeps_csv_isbn_when_no_db_cover` — Currently-reading book in DB with `cover_url=None` → keeps the ISBN-constructed URL from CSV

**File: `core/tests/test_integration.py`** (existing enrichment tests)

Add tests that verify `cover_url` is populated during enrichment:
- `test_enrich_book_sets_cover_url_from_cover_id` — Mock OL search to return `cover_i: 12345`, assert `book.cover_url == "https://covers.openlibrary.org/b/id/12345-M.jpg"`
- `test_enrich_book_sets_cover_url_from_isbn_when_no_cover_id` — Mock OL search without `cover_i`, book has ISBN, assert `book.cover_url` uses ISBN URL
- `test_enrich_book_sets_cover_url_from_google_books_thumbnail` — Mock OL search without `cover_i`, no ISBN, mock GB with thumbnail, assert `book.cover_url` is the HTTPS thumbnail URL
- `test_enrich_book_preserves_existing_cover_url` — Book already has `cover_url` set, re-enrichment should not overwrite it

**File: `core/tests/test_backfill_covers.py`** (new)

- `test_fast_mode_sets_cover_url_from_isbn` — Books with ISBN get cover URL set
- `test_fast_mode_skips_books_without_isbn` — Books without ISBN are not touched
- `test_fast_mode_skips_books_with_existing_cover_url` — Books that already have cover_url are not overwritten
- `test_dry_run_makes_no_changes` — Dry run reports counts but doesn't update DB

## Acceptance Criteria

- [x] `Book.cover_url` field added with migration
- [x] Enrichment captures OL `cover_i` and GB `thumbnail`, stores best available URL
- [x] GB thumbnail URLs upgraded from HTTP to HTTPS before storage
- [x] DNA generation prefers `book.cover_url` over ISBN-constructed URL for niche/extremes cards
- [x] Currently-reading books upgraded from DB `cover_url` during sync (zero extra queries)
- [x] `_build_cover_url()` signature unchanged (no breaking changes)
- [x] View layer backfill guards handle old DNA records missing `cover_url` on extremes
- [x] `backfill_covers` management command works in fast mode (ISBN-only) and full mode (with API)
- [x] `backfill_covers` registered in admin command runner
- [x] `BookAdmin` shows `cover_url` field
- [x] Existing tests pass; new tests cover priority logic and enrichment
- [x] Re-enrichment does not downgrade an existing verified `cover_url`

## Files to modify

| File | Change |
|------|--------|
| `core/models.py` | Add `cover_url` field to `Book` |
| `core/book_enrichment_service.py` | Capture `cover_i` from OL, `thumbnail` from GB, set `book.cover_url` |
| `core/services/dna_analyser.py` | Use `book.cover_url or _build_cover_url(isbn13)` at 3 call sites; capture DB book during currently-reading sync for cover upgrade |
| `core/views.py` | Add backfill guards for `longest_book` and `shortest_book` cover_url |
| `core/admin.py` | Add `backfill_covers` to `ADMIN_COMMANDS`, add `cover_url` to `BookAdmin` |
| `core/management/commands/backfill_covers.py` | **New** — backfill command (fast + full mode) |
| `core/tests/test_currently_reading.py` | Add tests for cover_url priority pattern |
| `core/tests/test_integration.py` | Add tests for cover_url enrichment |
| `core/tests/test_backfill_covers.py` | **New** — backfill command tests |
| New migration file | `cover_url` field addition |

## Edge Cases

- **New book, no ISBN, first upload:** `book.cover_url` is `None` (enrichment hasn't run yet). Letter fallback shown. Improved on re-upload after enrichment.
- **Currently-reading, returning user:** Book exists in DB with `cover_url` from prior enrichment. The DB sync captures the Book object (zero extra queries) and upgrades the cover URL in the `currently_reading_books` list.
- **Currently-reading, first-ever upload:** Book is new, `cover_url` is `None`. Falls back to CSV ISBN-constructed URL (same as current behavior).
- **Book enriched before this feature:** `cover_url` is `None`. Fast-mode backfill populates from ISBN. Full-mode backfill finds `cover_i` or GB thumbnail via API.
- **Re-enrichment:** Existing `cover_url` preserved (not downgraded). Only overwritten if explicitly cleared.
- **Anonymous users:** Same behavior — covers frozen in `dna_data` at generation time, same timing constraints.
- **Public profiles:** Read from frozen `UserProfile.dna_data`, same as dashboard.

## Verification

1. `poetry run python manage.py makemigrations core` — generates migration
2. `poetry run python manage.py migrate` — applies migration
3. `poetry run python manage.py test` — all tests pass
4. `poetry run python manage.py backfill_covers --dry-run` — shows stats
5. `poetry run python manage.py backfill_covers` — fast backfill for books with ISBN
6. `poetry run python manage.py backfill_covers --with-api --limit 10` — test full mode on 10 books
7. Verify in admin: Books now show `cover_url` field
8. Verify command runner: `backfill_covers` appears in admin command runner

## Deferred

- **View-layer re-hydration:** Refreshing frozen `dna_data` cover URLs from the DB at render time would show updated covers without re-upload, but adds architectural complexity. Deferred.
- **Backfill frozen DNA:** Patching `cover_url` inside existing `UserProfile.dna_data` JSON blobs is possible but fragile. Users see updated covers on next re-upload instead.
