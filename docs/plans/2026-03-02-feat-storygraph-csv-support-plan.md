---
title: "feat: Add StoryGraph CSV Upload Support"
type: feat
status: active
date: 2026-03-10
---

# Add StoryGraph CSV Upload Support

## Overview

Bibliotype currently only supports Goodreads CSV exports. StoryGraph is a popular alternative book-tracking platform with a fundamentally different CSV schema (23 columns, different column names, float ratings, no page count/publication year/average rating/publisher). Users who track their reading on StoryGraph cannot use Bibliotype today.

This plan adds StoryGraph CSV support across multiple PRs: a **core PR** (format detection, normalization, bug fixes, tests), a **polish PR** (template copy, SEO, instructions modal, "Community Average" rename), and optional **follow-up PRs** (enrichment trigger expansion, duplicate book merge command).

## Problem Statement / Motivation

The README, ARCHITECTURE.md, and about page all claim StoryGraph support, but the codebase has **zero actual support**. Uploading a StoryGraph CSV today crashes with `KeyError` on `"Exclusive Shelf"` (a column that doesn't exist in StoryGraph exports), or at best produces `"No books found on the 'read' shelf"`. This is a documentation lie and a broken user experience.

StoryGraph has grown significantly as a Goodreads alternative. Supporting it expands the addressable audience and delivers on promises already made in user-facing copy.

## Key Technical Challenge: Missing Data

StoryGraph CSVs lack 4 fields that Goodreads provides:

| Missing Field | Impact |
|---|---|
| `Number of Pages` | "Tome Tussler" / "Novella Navigator" reader types never assigned; total pages = 0; avg book length = 0 |
| `Original Publication Year` | "Classic Collector" / "Modern Maverick" reader types never assigned; avg publish year = 0 |
| `Average Rating` | Controversial ratings section empty; contrariness label unavailable |
| `Publisher` | "Small Press Supporter" reader type never assigned; mainstream score inaccurate until enrichment |

The existing book enrichment service (`core/book_enrichment_service.py`) already fetches page count, publish year, and publisher from Open Library + Google Books APIs -- but only for newly created books or books without genres, and it runs **asynchronously after DNA generation**.

To avoid a sparse first dashboard, StoryGraph uploads get **inline enrichment** during the DNA task: first a DB backfill (pulling data from already-enriched books), then a quick Google Books API lookup (1 call per book) for remaining gaps. This fills page_count, publish_year, and average_rating before stats are calculated. Full enrichment (genres, publisher) still runs async.

## StoryGraph CSV Format

**23 columns:**
```
Title, Authors, Contributors, ISBN/UID, Format, Read Status,
Date Added, Last Date Read, Dates Read, Read Count, Moods, Pace,
Character- or Plot-Driven?, Strong Character Development?,
Loveable Characters?, Diverse Characters?, Flawed Characters?,
Star Rating, Review, Content Warnings, Content Warning Description, Tags, Owned?
```

**Column mapping to Goodreads equivalents:**

| StoryGraph Column | Goodreads Equivalent | Notes |
|---|---|---|
| `Title` | `Title` | Same |
| `Authors` | `Author` | Plural, comma-separated; take first author |
| `Read Status` | `Exclusive Shelf` | Values: `read`, `currently-reading`, `to-read`, `did-not-finish` |
| `Star Rating` | `My Rating` | Float 0-5 (half-star increments); round half-up to int |
| `Last Date Read` | `Date Read` | Format: `YYYY/MM/DD` |
| `Review` | `My Review` | Plain text (no HTML tags unlike Goodreads) |
| `ISBN/UID` | `ISBN13` | May contain non-ISBN UIDs; no `="..."` wrapping; **requires ISBN validation** |
| `Format` | `Binding` | e.g., "Paperback", "Kindle", "Audiobook" |
| _(missing)_ | `Number of Pages` | Not in StoryGraph export |
| _(missing)_ | `Original Publication Year` | Not in StoryGraph export |
| _(missing)_ | `Average Rating` | Not in StoryGraph export |
| _(extra)_ | -- | Moods, Pace, Character traits, Tags, Content Warnings -- unused for now |

---

## Technical Approach

### Architecture

The approach is a **normalization layer** inserted between CSV parsing and the existing processing pipeline. All downstream code continues to use Goodreads column names as the **internal canonical schema** (this is a deliberate architectural choice, not tech debt). This minimizes changes to `calculate_full_dna()` and keeps the diff focused.

```
CSV Upload → pd.read_csv()
  → _detect_and_normalize_csv(df)    ← NEW
    → Detect format (Goodreads vs StoryGraph vs unknown)
    → Rename StoryGraph columns → Goodreads equivalents
    → Transform data (multi-author split, float→int ratings, ISBN validation, add missing columns)
    → Return (normalized_df, csv_source)
  → Existing pipeline (unchanged except guards for missing data)
```

### Delivery Strategy: Multiple PRs

**PR 1 (Core):** Backend normalization, ISBN dedup, book_defaults fix, enrichment rate limiting, BOM handling, tests. ~7 files.
**PR 2 (Polish):** Template copy, SEO, instructions modal, "Community Average" rename, about/terms/privacy pages. ~6 files.
**PR 3 (Enrichment trigger expansion):** Expand enrichment trigger to cover books missing page_count/publish_year. 1-2 files.
**PR 4 (Merge duplicates command):** Management command to find and merge duplicate Book records. Build only when duplicate reports surface from real users.

PR 1 is deliberately slim -- it ships the core feature with minimal scope. Follow-up PRs are independent and can be shipped in any order.

---

### PR 1: Core CSV Support (Backend)

**Goal:** StoryGraph CSVs produce valid DNA data with graceful handling of missing fields.

**Files to modify:**

| File | Change |
|---|---|
| `core/services/dna_analyser.py` | Format detection, column normalization, vectorized ISBN validation, batched ISBN dedup, `book_defaults` fix, inline enrichment (DB backfill + Google Books quick lookup), persist `csv_source`, error messages |
| `core/views.py` | BOM handling (`utf-8-sig`), pass `enrichment_pending` to dashboard context |
| `core/tasks.py` | Add `rate_limit='5/m'` to `enrich_book_task` |
| `core/templates/core/dashboard.html` | "Still enriching" notice on genre/mainstream/reader-type tiles when `enrichment_pending` |
| `core/templates/core/home.html` | Hero text only: "Upload your Goodreads or StoryGraph data" |
| `core/tests/test_tasks_unit.py` | Unit tests for normalization |
| `core/tests/test_tasks_integration.py` | Integration tests for StoryGraph pipeline (with inline enrichment mock) + controversial guard verification |

---

**Step 1.1: Add `_detect_and_normalize_csv(df)` helper**

Location: Insert between `save_anonymous_session_data()` (line ~226) and `calculate_full_dna()` (line ~229), following the "helpers first, orchestrator last" convention.

```python
# Goodreads column names serve as the internal canonical schema.
# All CSV sources are normalized to these names so downstream code
# (calculate_full_dna, assign_reader_type, save_anonymous_session_data)
# works identically regardless of source. This is a deliberate
# architectural choice -- if a third source is added, extract this
# into a dedicated core/services/csv_normalizer.py module.
STORYGRAPH_TO_GOODREADS = {
    "Authors": "Author",
    "Read Status": "Exclusive Shelf",
    "Star Rating": "My Rating",
    "Last Date Read": "Date Read",
    "Review": "My Review",
    "ISBN/UID": "ISBN13",
    "Format": "Binding",
}

def _detect_and_normalize_csv(df):
    """Detect CSV source (Goodreads vs StoryGraph) and normalize columns to Goodreads schema."""
    if "Exclusive Shelf" in df.columns:
        return df, "goodreads"
    elif "Read Status" in df.columns:
        df = df.rename(columns=STORYGRAPH_TO_GOODREADS)
        # Multi-author: take first author from comma-separated list
        # StoryGraph uses "First Last" format (not "Last, First"), so comma = author separator
        df["Author"] = df["Author"].str.split(",").str[0].str.strip()
        # Float ratings → int using round-half-up (not banker's rounding)
        # 4.5 → 5, 3.5 → 4, 0.5 → 1 (preserves user intent for half-star ratings)
        ratings = pd.to_numeric(df["My Rating"], errors="coerce")
        df["My Rating"] = np.where(pd.notna(ratings), np.floor(ratings + 0.5).astype(int), pd.NA).astype("Int64")
        # Validate ISBN: only keep values that are all-digits and length 10 or 13
        # StoryGraph ISBN/UID may contain non-ISBN internal identifiers
        # Vectorized — avoids row-by-row .apply() which is a pandas anti-pattern
        if "ISBN13" in df.columns:
            isbn_col = df["ISBN13"].astype(str).str.strip().str.replace(r"[^0-9Xx]", "", regex=True)
            df["ISBN13"] = isbn_col.where(isbn_col.str.len().isin([10, 13]), other=pd.NA)
        # Add missing columns as NaN
        for col in ["Number of Pages", "Original Publication Year", "Average Rating"]:
            if col not in df.columns:
                df[col] = pd.NA
        return df, "storygraph"
    else:
        raise ValueError("Unrecognized CSV format. Please upload a Goodreads or StoryGraph export.")

```

**Step 1.2: Call normalization in `calculate_full_dna()`**

After `pd.read_csv()` (line ~231), before the `"Exclusive Shelf" == "read"` filter:

```python
df = pd.read_csv(StringIO(csv_file_content))
df, csv_source = _detect_and_normalize_csv(df)  # ← INSERT
read_df = df[df["Exclusive Shelf"] == "read"].copy()
```

Log `csv_source` for debugging: `logger.info(f"CSV source detected: {csv_source}")`. Persist `csv_source` in the DNA dict so the dashboard template can show "still enriching" notices on genre/mainstream tiles for StoryGraph uploads.

**Important:** During the book sync loop, store each book's PK back into the DataFrame so the inline enrichment step (Step 1.5) can reference it: `read_df.at[idx, "_book_pk"] = book.pk`. This column is internal (not persisted in the DNA dict).

**Step 1.3: ISBN-based book deduplication during upload**

The current book lookup uses `(normalized_title, author)` as the composite key. This fails for multi-author books across platforms (e.g., "Good Omens" listed under "Neil Gaiman" on Goodreads but "Terry Pratchett" on StoryGraph). These produce different composite keys and create duplicate Book records.

**Fix:** Batch-prefetch ISBN-matched books before the processing loop, then try ISBN lookup before title+author per row.

**Batch prefetch** (before the per-row loop in `calculate_full_dna()`):

```python
# Prefetch all ISBN-matched books in a single query to avoid N+1
csv_isbns = read_df["ISBN13"].dropna().unique().tolist()
isbn_book_map = {}
if csv_isbns:
    isbn_book_map = {b.isbn13: b for b in Book.objects.filter(isbn13__in=csv_isbns)}
```

**Per-row dedup** in `process_book_row()`, after computing `isbn13_value` and before `update_or_create`:

```python
# ISBN-based deduplication: if we have a valid ISBN, check if the book already exists
# under a different author (common for multi-author books across platforms)
existing_book_by_isbn = isbn_book_map.get(isbn13_value) if isbn13_value else None

if existing_book_by_isbn:
    # Book exists -- reuse it, only fill in null fields (never overwrite title or enriched data)
    book = existing_book_by_isbn
    created = False
    for key, value in book_defaults.items():
        if key == "title":
            continue  # Don't overwrite title (cosmetic regression risk)
        if value is not None and getattr(book, key, None) is None:
            setattr(book, key, value)
    try:
        book.save()
    except IntegrityError:
        logger.warning(f"IntegrityError saving ISBN-matched book {isbn13_value}, skipping update")
else:
    # Standard title+author lookup (existing behavior)
    try:
        book, created = Book.objects.update_or_create(
            normalized_title=normalized_book_title,
            author=author,
            defaults=book_defaults,
        )
    except IntegrityError:
        book_defaults.pop("isbn13", None)
        book, created = Book.objects.update_or_create(
            normalized_title=normalized_book_title,
            author=author,
            defaults=book_defaults,
        )
```

This means if a user uploads "Good Omens" from Goodreads (ISBN `9780060853983`, author "Neil Gaiman") and then from StoryGraph (same ISBN, author "Terry Pratchett"), the second upload reuses the existing Book record instead of creating a duplicate. The book stays under the original author, which is acceptable since both are valid. The batch prefetch eliminates the N+1 query cost (a single `WHERE isbn13 IN (...)` instead of one `GET` per row).

**Step 1.4: Fix `book_defaults` overwrite bug**

`core/services/dna_analyser.py` lines ~301-305.

**Two bugs in the current code:**

1. `page_count` and `average_rating` are always included in defaults (even as `None`), so `update_or_create` overwrites existing enriched data with `None` on StoryGraph uploads.
2. `title` is always in defaults, so every re-upload overwrites the stored title with whatever the CSV provides. A StoryGraph upload could shorten a previously-stored title. This is a pre-existing issue that StoryGraph support makes worse.

Current:
```python
book_defaults = {
    "title": title_from_csv,
    "page_count": int(p) if pd.notna(p := original_row.get("Number of Pages")) else None,
    "average_rating": float(r) if pd.notna(r := original_row.get("Average Rating")) else None,
}
```

Fix -- conditional inclusion, and only set title on creation (not on update):
```python
book_defaults = {}
if pd.notna(p := original_row.get("Number of Pages")) and int(p) > 0:
    book_defaults["page_count"] = int(p)
if pd.notna(r := original_row.get("Average Rating")) and float(r) > 0:
    book_defaults["average_rating"] = float(r)
```

For the `update_or_create` call, pass `title` via `create_defaults` (Django 5.x) so it's only set on creation, not on subsequent updates:
```python
book, created = Book.objects.update_or_create(
    normalized_title=normalized_book_title,
    author=author,
    defaults=book_defaults,
    create_defaults={"title": title_from_csv, **book_defaults},
)
```

This aligns `page_count`/`average_rating` with the existing conditional pattern already used for `publish_year` and `isbn13` (lines ~306-309). Benefits both StoryGraph uploads (where these are always missing) and sparse Goodreads CSVs.

**Known limitation:** Author names with non-separator commas (e.g., "Robert Downey, Jr.") will be truncated by the multi-author comma split. This is uncommon for book authors and documented as a known edge case.

**Step 1.5: Inline enrichment for StoryGraph uploads (DB backfill + quick Google Books lookup)**

StoryGraph CSVs lack page_count, publish_year, and average_rating. Without these, the first dashboard is sparse (0 total pages, no controversial books, reader types like "Tome Tussler" impossible). Instead of showing a degraded dashboard and waiting for async enrichment, we enrich inline during the DNA task -- but do it smartly.

**Two-phase approach:**

**Phase A: DB backfill (instant, no API calls)**

After the book sync loop, many books will already have enriched data in the DB (from other users' Goodreads uploads or previous enrichment runs). Pull that data back into the DataFrame with a single batch query:

```python
if csv_source == "storygraph":
    # Phase A: Backfill from DB -- single batch query
    book_pks = read_df["_book_pk"].dropna().astype(int).tolist()
    enriched_books = {
        b["pk"]: b for b in Book.objects.filter(pk__in=book_pks).values(
            "pk", "page_count", "publish_year", "average_rating"
        )
    }
    backfilled = 0
    for idx, row in read_df.iterrows():
        book_data = enriched_books.get(row["_book_pk"])
        if book_data:
            if pd.isna(row.get("Number of Pages")) and book_data["page_count"]:
                read_df.at[idx, "Number of Pages"] = book_data["page_count"]
                backfilled += 1
            if pd.isna(row.get("Original Publication Year")) and book_data["publish_year"]:
                read_df.at[idx, "Original Publication Year"] = book_data["publish_year"]
            if pd.isna(row.get("Average Rating")) and book_data["average_rating"]:
                read_df.at[idx, "Average Rating"] = book_data["average_rating"]
    logger.info(f"DB backfill: enriched {backfilled}/{len(book_pks)} books from existing data")
```

**Phase B: Quick Google Books lookup for remaining gaps**

For books still missing critical data (page_count or publish_year), hit Google Books API -- **1 call per book** instead of 3-4, because `volumeInfo` includes `pageCount`, `publishedDate`, AND `averageRating` in a single response.

```python
    # Phase B: Quick enrichment via Google Books for remaining gaps
    needs_enrichment_idxs = read_df[
        read_df["Number of Pages"].isna() | read_df["Original Publication Year"].isna()
    ].index.tolist()

    if needs_enrichment_idxs and GOOGLE_BOOKS_API_KEY:
        progress_cb(current, total, "Enriching your library")

        # Shared session with connection pooling across threads
        gb_session = requests.Session()

        def _quick_enrich(idx):
            """Single Google Books API call to get pageCount + publishedDate + averageRating."""
            row = read_df.loc[idx]
            book = Book.objects.get(pk=row["_book_pk"])
            if book.isbn13:
                query = f"isbn:{book.isbn13}"
            else:
                query = f"intitle:{requests.utils.quote(book.title)}+inauthor:{requests.utils.quote(str(row['Author']))}"
            url = f"https://www.googleapis.com/books/v1/volumes?q={query}&key={GOOGLE_BOOKS_API_KEY}"
            try:
                res = gb_session.get(url, timeout=10)
                res.raise_for_status()
                data = res.json()
                if data.get("totalItems", 0) == 0:
                    return idx, {}
                volume_info = data["items"][0].get("volumeInfo", {})
                result = {}
                if pc := volume_info.get("pageCount"):
                    result["page_count"] = int(pc)
                if pd_str := volume_info.get("publishedDate"):
                    if match := re.search(r"\d{4}", str(pd_str)):
                        result["publish_year"] = int(match.group())
                if ar := volume_info.get("averageRating"):
                    result["average_rating"] = float(ar)
                return idx, result
            except Exception:
                return idx, {}

        from concurrent.futures import ThreadPoolExecutor, as_completed
        enriched_count = 0
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {executor.submit(_quick_enrich, idx): idx for idx in needs_enrichment_idxs}
            for future in as_completed(futures):
                idx, result = future.result()
                if result:
                    if "page_count" in result:
                        read_df.at[idx, "Number of Pages"] = result["page_count"]
                        # Also update the Book record for future uploads
                        Book.objects.filter(pk=read_df.at[idx, "_book_pk"]).update(page_count=result["page_count"])
                    if "publish_year" in result:
                        read_df.at[idx, "Original Publication Year"] = result["publish_year"]
                        Book.objects.filter(pk=read_df.at[idx, "_book_pk"]).update(publish_year=result["publish_year"])
                    if "average_rating" in result:
                        read_df.at[idx, "Average Rating"] = result["average_rating"]
                        Book.objects.filter(pk=read_df.at[idx, "_book_pk"]).update(average_rating=result["average_rating"])
                    enriched_count += 1
                # Update progress per-book
                progress_cb(enriched_count, len(needs_enrichment_idxs), "Enriching your library")

        logger.info(f"Quick enrichment: enriched {enriched_count}/{len(needs_enrichment_idxs)} books via Google Books")
```

**Performance characteristics:**
- Phase A (DB backfill): ~10ms for 200 books (single batch query)
- Phase B (Google Books): ~1s per book / 4 workers = ~12s for 50 remaining books, ~50s for 200
- Full enrichment (genres, publisher via Open Library) still runs async via `enrich_book_task`

**Progress tracking:** New progress stage `"Enriching your library"` with per-book updates, displayed between "Syncing books" and "Crunching stats". Frontend loading screen should show longer time estimate when this stage is active.

**Graceful degradation:** If `GOOGLE_BOOKS_API_KEY` is not set, Phase B is skipped entirely. Dashboard shows with whatever data Phase A backfilled. If both phases produce no data, dashboard still renders with 0s (same as current behavior without inline enrichment).

**Step 1.6: Add rate limit to enrichment tasks**

Add `rate_limit='5/m'` to `enrich_book_task` in `core/tasks.py`:

```python
@shared_task(bind=True, max_retries=3, rate_limit='5/m')
def enrich_book_task(self, book_id: int):
```

This limits async enrichment (genres, publisher, full metadata) to 5 tasks per minute per worker. The inline quick enrichment (Step 1.5) handles page_count/publish_year/average_rating synchronously; this rate limit only applies to the follow-up full enrichment.

Note: `rate_limit` is per worker process. With default concurrency, the effective rate scales with CPU count.

**Enrichment trigger expansion is deferred to PR 3** -- the `(not book.page_count or not book.publish_year) and not book.google_books_last_checked` trigger benefits ALL books, not just StoryGraph uploads.

**Step 1.7: Update error message for empty shelf**

Line ~239: Change `"No books found on the 'read' shelf in your CSV."` to `"No books found with 'read' status in your CSV. Make sure your export includes books you've finished reading."`

**Step 1.8: Handle BOM encoding**

In `core/views.py` line ~541, change `decode("utf-8")` to `decode("utf-8-sig")` to transparently handle UTF-8-BOM CSVs that some exports produce. Add a comment noting that any new CSV entry point (API endpoint, management command) must also use `utf-8-sig`.

**Step 1.9: "Still enriching" notice on dashboard for StoryGraph uploads**

For StoryGraph uploads, the inline enrichment (Step 1.5) populates page counts, publish years, and average ratings -- but genres, publisher, and mainstream status still come from async Open Library enrichment. Show a subtle notice on affected dashboard tiles so users know this data may improve.

In `core/views.py` `display_dna_view`, pass `enrichment_pending` to the template context. Check **live DB state** (not the stale DNA dict) so the notice disappears on browser refresh once async enrichment has added genres to the user's books:
```python
csv_source = dna_data.get("csv_source", "goodreads")
if csv_source == "storygraph" and request.user.is_authenticated:
    total_books = UserBook.objects.filter(user=request.user).count()
    books_with_genres = Book.objects.filter(
        userbook__user=request.user, genres__isnull=False
    ).distinct().count()
    context["enrichment_pending"] = total_books > 0 and (books_with_genres / total_books) < 0.5
else:
    context["enrichment_pending"] = csv_source == "storygraph"  # Anonymous: always show for SG
```

Two cheap indexed queries per dashboard view. Once async enrichment has added genres to 50%+ of the user's books, a simple page refresh hides the notice -- no re-upload required.

In the dashboard template, show a notice above tiles that depend on async data:
```html
{% if enrichment_pending %}
<p class="border-brand-text border-2 bg-brand-yellow/30 px-3 py-2 text-sm">
    Still fetching genre and mainstream data — refresh the page in a few minutes to see updates.
</p>
{% endif %}
```

**Affected tiles:**
- Top Genres -- genres come from Open Library (async)
- Reader Type -- partially depends on genre counts
- Mainstream Score -- depends on publisher hierarchy + author mainstream status
- Reading Vibe -- LLM prompt includes genre data

**Not affected (accurate from inline enrichment):**
- User Stats (total pages, avg book length, avg publish year)
- Ratings Distribution
- Controversial Books (uses Google Books average_rating)
- Top Authors
- Stats by Year

The notice uses `enrichment_pending` (not `csv_source` directly) so the template logic is decoupled from the source detection. The live DB check means the notice auto-clears on page refresh once async enrichment populates genres -- no re-upload needed.

---

### PR 1 Tests

**Test constants and helpers** (add to relevant test files, following `test_subtitle_data.py` pattern):
```python
SG_CSV_HEADER = (
    "Title,Authors,Contributors,ISBN/UID,Format,Read Status,"
    "Date Added,Last Date Read,Dates Read,Read Count,Moods,Pace,"
    "Character- or Plot-Driven?,Strong Character Development?,"
    "Loveable Characters?,Diverse Characters?,Flawed Characters?,"
    "Star Rating,Review,Content Warnings,Content Warning Description,Tags,Owned?"
)

def _sg_csv(*rows):
    """Join header + data rows into a single StoryGraph CSV string."""
    return "\n".join([SG_CSV_HEADER] + list(rows))
```

**Unit tests (`core/tests/test_tasks_unit.py`):**

| Test | Description |
|---|---|
| `test_detect_and_normalize_storygraph_csv` | Verify: columns renamed, multi-author split (both multi and single author), ratings rounded half-up (4.5→5, 3.5→4, 0.5→1), missing columns added as NaN, non-ISBN UIDs filtered out |
| `test_detect_and_normalize_goodreads_csv` | Verify Goodreads CSV passes through unchanged |
| `test_detect_unrecognized_csv_raises_error` | Unknown CSV format raises ValueError with clear message |

**Integration tests (`core/tests/test_tasks_integration.py`):**

| Test | Description |
|---|---|
| `test_generate_dna_for_authenticated_user_storygraph` | Full pipeline with mocked Google Books API: DNA generated, reader type assigned, user stats populated with inline-enriched values (page counts and publish years from mock). Verifies controversial books guard works correctly when Average Rating is backfilled from Google Books. Asserts `csv_source == "storygraph"` in DNA dict |
| `test_book_defaults_no_overwrite_with_none` | Existing enriched book's page_count/average_rating preserved after StoryGraph upload |

---

### PR 2: Template & Copy Polish

**Goal:** All user-facing text mentions StoryGraph alongside Goodreads. Rename "Goodreads Average" to "Community Average".

**Files to modify:**

| File | Change |
|---|---|
| `core/templates/core/home.html` | Instructions modal: Add StoryGraph section below Goodreads instructions, SEO/structured data updates |
| `core/templates/core/about.html` | Copy, links, SEO |
| `core/templates/core/base.html` | Default meta descriptions/keywords, JSON-LD |
| `core/templates/core/terms.html` | "from Goodreads" → "from Goodreads or StoryGraph" |
| `core/templates/core/privacy.html` | SEO keyword |
| `core/templates/core/partials/dna/controversial_ratings_card.html` | "Goodreads Average" → "Community Average" |
| `core/tests/test_number_line.py` | Update `"Goodreads Average"` → `"Community Average"` assertion |

**"Community Average" rename:** Even for Goodreads uploads, the average rating stored in the DB may come from Google Books enrichment, not Goodreads. "Community Average" is accurate regardless of source.

**Instructions modal:** Add a "For StoryGraph" section below the existing Goodreads instructions (plain text, no tabs). If the section feels cluttered, upgrade to Alpine.js tabs in a follow-up.

StoryGraph export instructions:
1. Go to app.thestorygraph.com
2. Click your profile picture → Settings → Manage Account
3. Scroll to "Export Your Data" → click "Generate Export"
4. Download the CSV and upload here

**SEO updates:** Add "StoryGraph" to meta descriptions, keywords, FAQ schema, HowTo schema across `home.html`, `base.html`, `about.html`.

**Copy updates:** Update references in `terms.html` and `privacy.html`.

---

### PR 3: Enrichment Trigger Expansion

**Goal:** Enrich books missing critical metadata (page_count, publish_year) that were previously skipped by the enrichment trigger.

**Scope:** Expand the enrichment trigger in `core/services/dna_analyser.py` to also enrich books where `(not book.page_count or not book.publish_year) and not book.google_books_last_checked`. The `google_books_last_checked` guard prevents fruitlessly re-enriching books where the API simply didn't have data. This benefits ALL books (not just StoryGraph uploads), which is why it's a separate PR.

**Files:** `core/services/dna_analyser.py` (1 file, ~5 lines changed).

---

### PR 4: Merge Duplicate Books Command (Deferred)

**Goal:** Management command to find and merge duplicate Book records that share the same ISBN or normalized title.

**When to build:** When duplicate book reports surface from real users. The ISBN dedup in PR 1 prevents most future duplicates. This command is for retroactively fixing duplicates created before the dedup logic was added, or for books without ISBNs that slipped through.

**Scope:** `core/management/commands/merge_duplicate_books.py` with `--dry-run`, `--by-isbn`, `--by-title` flags. Detection via ISBN duplicates and normalized title heuristics. Merge logic transfers UserBook records, genres, and fills metadata gaps. ~80 LOC + 3 tests.

**Not included in PR 1** because it's YAGNI -- it solves a problem that doesn't exist yet.

---

## Design Decisions

### Decision 1: Normalize to Goodreads schema (not a neutral schema)

**Chosen:** Rename StoryGraph columns to match Goodreads column names.
**Why:** Minimizes changes to the 700-line `calculate_full_dna()` function. All downstream code (reader type assignment, stats calculation, book syncing, UserBook creation, anonymous session saving) continues working unchanged. The normalization function is the only place that knows about StoryGraph's column names.
**Trade-off:** Goodreads is treated as the "canonical" format. If a third CSV source is added, extract normalization into a dedicated `core/services/csv_normalizer.py` module.

### Decision 2: Round-half-up ratings (not banker's rounding, not truncation)

**Chosen:** `int(x + 0.5)` -- standard round-half-up.
**Why:** Python's `round()` uses banker's rounding where `round(4.5) = 4` and `round(0.5) = 0`. This is counterintuitive for star ratings -- a user who gives 4.5 stars expects it to round to 5, not 4. And `round(0.5) = 0` would discard a real half-star rating as "unrated" (the code treats 0 as unrated). Round-half-up avoids both problems: `4.5→5`, `3.5→4`, `0.5→1`.
**Alternative rejected:** `math.ceil()` -- too aggressive: `3.1→4` misrepresents the user's rating.

### Decision 3: Validate ISBNs, discard StoryGraph UIDs

**Chosen:** Only store values in `isbn13` that are all-digits with length 10 or 13.
**Why:** StoryGraph's `ISBN/UID` column contains internal identifiers for some books. Storing non-ISBN UIDs in the `isbn13` field would pollute the unique constraint, break API-based enrichment lookups (searching Open Library for `isbn:sg_abc123`), and potentially cause `IntegrityError` collisions. The existing `len(cleaned) >= 10` check is too permissive.

### Decision 4: Discard StoryGraph-exclusive data (Moods, Pace, etc.)

**Chosen:** Silently drop extra columns during normalization.
**Why:** Using this data would require schema changes, new reader types, and significant UI work. This is a separate feature.
**Future opportunity:** Moods/Pace/Character traits could enable unique reader insights not possible from Goodreads data.

### Decision 5: "Community Average" label (not dynamic per-source)

**Chosen:** Universal rename from "Goodreads Average" to "Community Average".
**Why:** Even for Goodreads uploads, the average rating stored in the DB may come from Google Books enrichment, not Goodreads. "Community Average" is accurate regardless of source.

### Decision 6: Rate-limit enrichment tasks (not deferred)

**Chosen:** Add `rate_limit='5/m'` to `enrich_book_task` immediately.
**Why:** A 200-book StoryGraph upload dispatches ~200 enrichment tasks, generating ~600 Open Library API requests. Open Library's rate limit is ~100 requests/5 minutes. Without rate limiting, this triggers HTTP 429s within the first minute and risks IP blocking. The `google_books_last_checked` guard prevents re-enriching already-processed books on subsequent uploads. At 5/min per worker, 200 books complete in ~40 minutes -- acceptable for background work.

### Decision 7: Persist `csv_source` in DNA dict

**Chosen:** Store `csv_source` ("goodreads" or "storygraph") in the DNA dict.
**Why:** The dashboard needs to know the upload source to show "still enriching" notices on tiles that depend on async data (genres, mainstream score). Without this, the template has no way to distinguish a StoryGraph upload (where genres are expected to be sparse initially) from a Goodreads upload (where sparse genres means the user genuinely reads few genres). Also useful for PostHog analytics.
**Revised from:** Originally deferred as YAGNI, but inline enrichment (Step 1.5) created a real consumer.

---

## Known Limitations

### Multi-author book duplication across platforms (partially mitigated)

When the same multi-author book is uploaded from Goodreads and StoryGraph, it could create two separate Book records. Example: Goodreads lists `Author="Neil Gaiman"` for "Good Omens", while StoryGraph lists `Authors="Terry Pratchett, Neil Gaiman"` → first author = "Terry Pratchett". These produce different `(normalized_title, author)` composite keys.

**Mitigation:** ISBN-based dedup during upload (Step 1.3). If the book has an ISBN, the system checks for an existing Book with the same ISBN before creating a new one. This prevents duplication for books that have ISBNs on both platforms.

**Remaining limitation:** Books without ISBNs on either platform and with different primary authors will still create duplicates. This is uncommon in practice (most published books have ISBNs). A `merge_duplicate_books` management command (PR 4) can retroactively fix duplicates if needed.

### Book fingerprint hash instability across formats

The vibe cache hash uses raw `Title` + `Author` from the CSV. Different author formatting between Goodreads and StoryGraph for the same book produces different hashes, causing unnecessary vibe regeneration (extra Gemini API call). **Impact:** Performance only, not data corruption. **Mitigation:** Could use normalized author names instead of raw CSV values in a follow-up.

---

## Acceptance Criteria

### Functional Requirements

- [x] StoryGraph CSV uploads produce valid DNA dashboards
- [x] Goodreads CSV uploads continue to work identically (no regressions)
- [x] Unknown CSV formats produce a clear error message
- [x] StoryGraph multi-author books use the first listed author
- [x] StoryGraph float ratings are rounded half-up to integers (4.5→5, 3.5→4, 0.5→1)
- [x] StoryGraph `did-not-finish` books are excluded from analysis
- [x] StoryGraph non-ISBN UIDs are discarded (not stored in `isbn13`)
- [x] Books with matching ISBNs across platforms are deduplicated (not duplicated)
- [x] Uploading a StoryGraph CSV does NOT overwrite existing enriched book data with None
- [x] Title is not overwritten on existing books during re-upload (only set on creation)
- [x] StoryGraph uploads enrich books inline (DB backfill + Google Books) before stats calculation
- [x] Dashboard sections depending on missing data (pages, controversial ratings) degrade gracefully when enrichment data unavailable
- [x] Enrichment tasks rate-limited to prevent API abuse
- [x] Home page hero text mentions StoryGraph

### Non-Functional Requirements

- [x] No performance regression for Goodreads uploads
- [x] UTF-8-BOM encoded CSVs handled transparently
- [x] Enrichment rate limited to 5/min per worker

### Quality Gates

- [x] All existing tests pass (no regressions)
- [x] 3 unit tests for normalization function (format detection, column rename, rating rounding, ISBN validation)
- [x] 2 integration tests for full StoryGraph pipeline (with controversial guard verification) and book_defaults no-overwrite
- [x] `poetry run python manage.py test` passes clean

---

## Implementation Order

### PR 1 (Core) -- 11 steps

1. `_detect_and_normalize_csv()` with vectorized ISBN validation -- format detection, column normalization
2. Call normalization in `calculate_full_dna()` + batch ISBN prefetch, persist `csv_source` in DNA dict, store `_book_pk` in DataFrame during book sync
3. ISBN-based book deduplication in `process_book_row()` -- try batched ISBN lookup before title+author, null-only field updates, skip title overwrite
4. Fix `book_defaults` overwrite bug -- conditional inclusion of `page_count`/`average_rating`, title only on creation via `create_defaults`
5. Inline enrichment for StoryGraph uploads -- DB backfill (batch query) + Google Books quick lookup (1 API call per book, shared session, ThreadPoolExecutor(4)), new progress stage "Enriching your library"
6. Add `rate_limit='5/m'` to `enrich_book_task` in `tasks.py`
7. Update error messages to be platform-agnostic
8. BOM handling in `views.py` (`utf-8-sig`)
9. "Still enriching" notice on dashboard -- pass `enrichment_pending` from view (checks live DB genre count, disappears on refresh after async enrichment), show notice above genre/mainstream/reader-type tiles for StoryGraph uploads
10. Home page hero text change
11. Tests

**Tests:**
- 3 unit tests -- normalization (StoryGraph, Goodreads passthrough, unknown format)
- 2 integration tests -- full StoryGraph pipeline (with inline enrichment mocked, controversial guard verification), book_defaults no-overwrite
- Full test suite -- `poetry run python manage.py test`

**Removed from PR 1 (per review):**
- `merge_duplicate_books` command → deferred to PR 4 (YAGNI)
- Enrichment trigger expansion → deferred to PR 3 (not StoryGraph-specific)
- `assign_reader_type` explicit guard → cut (pandas NaN comparison semantics already correct)
- Separate controversial guard verification step → folded into integration test
- "Community Average" label change → moved to PR 2

### PR 2 (Polish)

1. "Goodreads Average" → "Community Average" label + test assertion update
2. Instructions modal -- StoryGraph export steps (plain text section)
3. SEO/structured data updates across `home.html`, `base.html`
4. Copy updates in `about.html`, `terms.html`, `privacy.html`

### PR 3 (Enrichment Trigger Expansion)

1. Expand enrichment trigger in `dna_analyser.py` to cover books missing page_count/publish_year
2. Add `google_books_last_checked` guard to prevent re-enriching already-processed books

### PR 4 (Merge Duplicate Books -- deferred)

Build when duplicate book reports surface from real users. See PR 4 section above for spec.

---

## Files to Modify

### PR 1 (Core) -- 7 files

| File | Change |
|---|---|
| `core/services/dna_analyser.py` | Format detection, column normalization, vectorized ISBN validation, batched ISBN dedup, `book_defaults` fix, inline enrichment (DB backfill + Google Books quick lookup), persist `csv_source`, error messages |
| `core/views.py` | BOM handling (`utf-8-sig`), pass `enrichment_pending` to dashboard context |
| `core/tasks.py` | Add `rate_limit='5/m'` to `enrich_book_task` |
| `core/templates/core/dashboard.html` | "Still enriching" notice on affected tiles |
| `core/templates/core/home.html` | Hero text only |
| `core/tests/test_tasks_unit.py` | 3 unit tests for normalization |
| `core/tests/test_tasks_integration.py` | 2 integration tests (pipeline with enrichment mock + no-overwrite) |

### PR 2 (Polish) -- 7 files

| File | Change |
|---|---|
| `core/templates/core/home.html` | Instructions modal (StoryGraph section), SEO/structured data |
| `core/templates/core/about.html` | Copy, links, SEO |
| `core/templates/core/base.html` | Meta descriptions/keywords, JSON-LD |
| `core/templates/core/terms.html` | Service description |
| `core/templates/core/privacy.html` | SEO keyword |
| `core/templates/core/partials/dna/controversial_ratings_card.html` | "Goodreads Average" → "Community Average" |
| `core/tests/test_number_line.py` | Update "Community Average" assertion |

### PR 3 (Enrichment Trigger) -- 1 file

| File | Change |
|---|---|
| `core/services/dna_analyser.py` | Expand `needs_enrichment` condition + `google_books_last_checked` guard |

### PR 4 (Merge Duplicates) -- deferred, build when needed

| File | Change |
|---|---|
| `core/management/commands/merge_duplicate_books.py` | **NEW** -- Find and merge duplicate Book records |
| `core/tests/test_merge_duplicate_books.py` | **NEW** -- 3 tests (dry run, UserBook transfer, metadata merge) |

---

## Known Gaps (Deferred)

| Gap | Reason for Deferral |
|---|---|
| Use StoryGraph Moods/Pace/Character traits | Requires schema changes, new reader types, UI work -- separate feature |
| Use StoryGraph `Read Count` for weighting | Nice-to-have, not essential for MVP |
| Store StoryGraph `Owned?` flag | Would need new model field |
| Re-calculate DNA after async enrichment completes | Partially addressed by inline enrichment for StoryGraph. Full recalculation after async Open Library enrichment (genres, publisher) still deferred |
| Management command StoryGraph CSV generation | Dev tooling, not user-facing |
| `Dates Read` (multiple read dates) column | Re-read tracking is a separate feature |
| Conditional hiding of page/year stats on dashboard | Follow-up polish pass |
| Books without ISBNs with different primary authors | Rare edge case; merge command (PR 4) can fix post-hoc |
| `csv_source` in PostHog events | `csv_source` is now in the DNA dict; adding it to PostHog events is a one-liner but not essential for MVP |
| Normalized author names in vibe cache hash | Performance optimization; follow-up |
| `global_read_count` double-counting on task retry | Pre-existing bug, not StoryGraph-specific; compounded by ISBN dedup |
| Multi-author comma-splitting edge case | Author names with non-separator commas (e.g., "Robert Downey, Jr.") would be truncated; uncommon for book authors |
| Enrichment trigger expansion for missing metadata | Deferred to PR 3; benefits all books, not StoryGraph-specific |
| `merge_duplicate_books` management command | Deferred to PR 4; YAGNI until duplicate reports surface |

---

## Verification

```bash
# Unit tests
poetry run python manage.py test core.tests.test_tasks_unit

# Integration tests
poetry run python manage.py test core.tests.test_tasks_integration

# Full suite (no regressions)
poetry run python manage.py test

# Manual: upload a real StoryGraph CSV through the running app
# Verify dashboard renders with inline-enriched data (page counts, publish years from Google Books)
# Verify "still enriching" notice appears on genre/mainstream tiles
# Re-upload after async enrichment completes → notice should disappear, genres populated
```

---

## References

### Internal References

- CSV parser: `core/services/dna_analyser.py:229-719` (`calculate_full_dna()`)
- Reader type assignment: `core/services/dna_analyser.py:48-116` (`assign_reader_type()`)
- Book enrichment: `core/book_enrichment_service.py`
- Enrichment task: `core/tasks.py:69-91` (`enrich_book_task`)
- Upload view: `core/views.py:525-572`
- Home template: `core/templates/core/home.html:172-249` (instructions modal)
- Test patterns: `core/tests/test_subtitle_data.py:25-33` (CSV helper pattern)
- Controversial card: `core/templates/core/partials/dna/controversial_ratings_card.html:9,15`
- Book defaults: `core/services/dna_analyser.py:301-309` (overwrite bug)

### External References

- StoryGraph export: app.thestorygraph.com (Settings → Manage Account → Export Your Data)
- Previous plan: `~/.claude/plans/wild-toasting-flamingo.md`
