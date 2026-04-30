---
title: StoryGraph Support PR — Handoff
date: 2026-04-30
pr: https://github.com/vanajmoorthy/bibliotype/pull/98
branch: feat/storygraph-support
status: ready-to-merge
---

# StoryGraph Support PR — Handoff

This is the handoff doc for PR #98 (`feat/storygraph-support`). It explains everything the PR does, every bug found and fixed, every test added, and the known follow-ups we want to address next.

## What this PR ships

### Core feature: StoryGraph CSV upload

Users can now upload **StoryGraph CSV exports** in addition to Goodreads. The pipeline auto-detects the format and normalizes everything to the internal Goodreads schema so downstream code is unchanged.

Specifically:
- Format detection by column header (`Exclusive Shelf` → goodreads, `Read Status` → storygraph)
- Column rename via `STORYGRAPH_TO_GOODREADS` (`Authors → Author`, `Star Rating → My Rating`, `Last Date Read → Date Read`, `ISBN/UID → ISBN13`, `Format → Binding`, `Read Status → Exclusive Shelf`, `Review → My Review`)
- Multi-author rows: take only the first author (StoryGraph uses "First Last", Goodreads uses "Last, First" — comma is the author separator)
- Round-half-up rating math (4.5→5, 3.5→4, 0.5→1) — preserves user intent
- ISBN validation: only keep 10/13-digit numeric values; StoryGraph internal UIDs become NaN
- Inline backfill for StoryGraph: query existing books by ISBN to fill page count, publish year, and average rating before stats are calculated
- Hero text on home: "Upload your Goodreads or StoryGraph data..."

### StoryGraph metadata extraction (pre-enrichment)

StoryGraph provides metadata Goodreads doesn't. The PR uses it to reduce API dependency and unlock new signals:

- **Tags → canonical genres**: `STORYGRAPH_TAG_TO_GENRE` maps user tags (sci-fi, fantasy, dystopian, classic, non-fiction, etc.) to canonical genres. Applied in `process_book_row` before enrichment dispatch — tagged books skip OL/GB genre lookups entirely
- **`dystopian` is now its own canonical genre** (was aliased to "science fiction"). Updated `GENRE_ALIASES`, `FICTION_GENRES`, both `genre_priority` lists in `book_enrichment_service.py`, and `Fantasy Fanatic` scoring (now sums `fantasy + science fiction + dystopian`)
- **Moods + pace preserved**: `mood_distribution` and `pace_distribution` computed from CSV and stored in `dna_data`. Top moods feed into the Gemini vibe prompt as extra context
- **`Read Count` for re-read detection**: `Comfort Rereader` reader type scores 3 pts per book with `Read Count > 1`. Goodreads fallback uses duplicate title detection
- **Reader type descriptions filled in**: added descriptions for `Rapacious Reader`, `Nature Nut Case`, `Self Help Scholar`, and the new `Comfort Rereader` (all had scoring logic but no descriptions)

### Enrichment speed optimizations

Pre-PR: each book made 4 sequential API calls (5–13s per book, ~4–5 min for a 62-book library).

- **Direct OL ISBN lookup**: when a book has an ISBN, hit `/isbn/{isbn}.json` directly — skips the OL search call. Falls back to title+author search on 404
- **Skip OL edition endpoint** when book already has page count + publisher + publish_year + isbn13
- **Skip Google Books when OL found genres** — GB is now only a fallback for books with no OL genres. Sets `google_books_last_checked` immediately so book is marked attempted
- **Celery worker concurrency raised to 2** (was default 1) in `docker-compose.local.yml`

### Enrichment UX

- **Live polling**: `/api/enrichment-status/` returns updated `total_pages_read`, `avg_book_length`, `mainstream_score_percent`, `top_genres`, `fiction_nonfiction_split`. Dashboard JS updates `#stat-pages`, `#stat-avg-length`, `#mainstream-score` every 5 s. On `pending: false`, page reloads automatically
- **Completion detection**: replaced 50% genre threshold with `google_books_last_checked` check. Banners disappear when *all* books have been attempted, regardless of how many got genres
- **Banner positioning fix**: comparative-analytics sub-tiles use flow-based positioning instead of absolute (fixes text overlap). Banner text is `text-xs` (was `text-[11px]`)
- **Skeleton placeholders**: `book_extremes_skeleton.html`, `fiction_nonfiction_skeleton.html`, `top_genres_skeleton.html` — all use the `cover-crosshatch` pattern + `animate-pulse` for visual consistency. Shown when partial data is missing during enrichment
- **Dim partial data**: tiles with the "Still enriching" banner dim their content to opacity 60% when enrichment is pending (genre list/chart, fiction/nonfiction pie + legend, mainstream gauge body, key-stats pages tile, comparative-analytics sub-tiles for book length + book age). Banners themselves stay at full opacity to remain readable. Description copy adds "(so far)" qualifier where appropriate
- **Anonymous user fix**: removed fake enrichment context for anonymous users (no async enrichment to poll), preventing 302 redirect errors on `/api/enrichment-status/`

### Re-upload cancellation

`upload_view` writes `upload_nonce_{user_id}` to Redis cache **before** dispatching the DNA task (using `uuid.uuid4()` so it's known before the task ID is assigned). `enrich_book_task` accepts `user_id` + `upload_nonce` arguments and exits early if the cached nonce no longer matches — old enrichment tasks from a previous upload don't waste API calls when the user re-uploads.

## Bugs found and fixed

| # | Bug | Symptom | Root cause | Fix |
|---|-----|---------|------------|-----|
| 1 | Stuck-at-X% enrichment | Banner percent never reached 100% even after all tasks completed | `enrich_book_task` raised on persistent API failure → `MaxRetriesExceededError` → book never got `google_books_last_checked` set | Catch `MaxRetriesExceededError` and mark book as attempted via `Book.objects.filter(pk=book_id).update(google_books_last_checked=timezone.now())` |
| 2 | Upload nonce race | In Celery eager mode (tests) and under fast worker pickup, the task could read a stale/null nonce | Nonce was written *after* `.delay()` returned | Generate nonce with `uuid4()` and write to cache *before* dispatching the task |
| 3 | Crash on minimal Goodreads CSV | `KeyError: 'Number of Pages'` when CSV omitted optional columns | Code accessed `temp_df["Number of Pages"]` etc. without checking column existence | Defensive checks for `My Rating`, `Number of Pages`, `Average Rating`, `Date Read`, `My Review` |
| 4 | Alias collision | `"fairy tales"` mapped to `fairy tales & fables` only — silently overrode the `mythology & folklore` mapping | Same alias appeared under two canonical genres in `GENRE_ALIASES` | Removed `"fairy tales"` from `mythology & folklore`. Added a regression test to fail loudly on future collisions |
| 5 | Anonymous upload 302 errors | Console errors on `/api/enrichment-status/` for anonymous users | View was `@login_required` but anonymous dashboards still triggered polling | Removed fake enrichment context for anonymous users in `display_dna_view` |
| 6 | "dystopian" was an unmapped subject | Books tagged dystopian got no canonical genre | `"dystopian"` was in the unmapped subjects list AND `"dystopian fiction"` aliased to science fiction | Promoted "dystopian" to its own canonical genre with proper aliases |

## Test coverage added (320 tests total, 44 new)

### `core/tests/test_math_accuracy.py` — 32 tests
Reader type scoring formulas (Tome Tussler 2pts/book>490p, Novella Navigator 1pt/book<200p, Fantasy Fanatic = fantasy+sci-fi+dystopian, Comfort Rereader 3pts/reread, etc.), genre canonicalization invariants (no alias collisions, all canonical genres classified, dystopian as separate genre), `STORYGRAPH_TAG_TO_GENRE` validity, mood/pace aggregation math, CSV detection edge cases, all scored reader types have descriptions.

### `core/tests/test_storygraph_integration.py` — 12 tests
Full StoryGraph upload flow with `csv_source="storygraph"`, tag-to-genre mapping pre-enrichment (verified DB state), `mood_distribution` and `pace_distribution` populated in DNA, `Read Count` drives Comfort Rereader scoring, enrichment completion detection via `google_books_last_checked`, upload nonce write-before-dispatch + replacement on re-upload.

### Misc
- `test_enrich_book_task_marks_attempted_after_max_retries` in `test_integration.py` — verifies the stuck-at-50% fix
- Updated existing GB-skip tests to reflect new "OL found genres → skip GB" behavior

## Files changed in this PR

- `core/services/dna_analyser.py` — StoryGraph normalization, tag mapping, mood/pace, Read Count, defensive CSV column handling
- `core/book_enrichment_service.py` — direct ISBN lookup, edition skip, GB skip, helper extraction
- `core/services/llm_service.py` — feed moods into vibe prompt
- `core/dna_constants.py` — new "dystopian" canonical, fairy tales alias fix, new reader type descriptions
- `core/tasks.py` — upload nonce check, max retries fallback for stuck books
- `core/views.py` — `enrichment_status_view` AJAX endpoint, completion detection, upload nonce write, anonymous user fix
- `core/urls.py` — `/api/enrichment-status/` route
- `core/templates/core/dashboard.html` — polling JS, skeleton inclusion conditions
- `core/templates/core/partials/dna/*` — banner repositioning, skeleton templates, dimming, IDs for live updates
- `core/templates/core/home.html` — "Goodreads or StoryGraph" copy
- `docker-compose.local.yml` — `--concurrency=2`
- `Dockerfile` — `gnupg ca-certificates` for NodeSource setup, version verification
- `core/tests/*` — 44 new tests

## Known limitations & follow-ups

These didn't make it into this PR. Pick them up next.

### 1. Reader type and top reader traits don't update live during enrichment ⚠️

**Behavior today:** while enrichment is pending, the dashboard polling endpoint updates page counts, mainstream score, top genres, and fiction/nonfiction split — but NOT `reader_type` or `reader_type_scores`. Those are computed once at upload time inside `assign_reader_type()` and frozen in `dna_data` until either:
- The page is reloaded after enrichment completes (the auto-reload on `pending: false` covers this)
- The user re-uploads

This means: if your initial upload has 0 genres enriched, the reader type may incorrectly default to "Eclectic Reader" or whatever scored highest from page counts alone. As genres trickle in, the dashboard shows updated genre stats but the reader-type card doesn't change to reflect them. Only the auto-reload at the end of enrichment surfaces the final reader type.

**Why it's like this:** `_recalculate_enrichment_stats` in `core/views.py:165` recalculates pages/genres/mainstream from the DB but doesn't call `assign_reader_type` (which lives in `dna_analyser.py` and needs the read DataFrame, not just DB state). The DataFrame is reconstructed during the original Celery task and isn't persisted.

**Fix sketch:**
- Either: persist the read DataFrame (or the data needed by `assign_reader_type`) on the user profile after the Celery task, then re-run scoring in `_recalculate_enrichment_stats` using fresh DB-side genre/publisher data
- Or: rewrite `assign_reader_type` to work entirely from DB state (count books by page bucket, query publisher mainstream flag, etc.) — likely cleaner long-term
- Then add `reader_type`, `reader_type_explanation`, `top_reader_types`, `reader_type_scores` to the `updated_stats` payload in `enrichment_status_view`, and update the polling JS to swap the reader-type card content + top-traits list

### 2. Concurrent uploads cause "stuck at 50%" stalls 🐛

**Symptom (reported by the user during testing):** uploaded a CSV, then went back to home and uploaded again *before* the first enrichment finished. Second upload's progress bar stalled at 50%, then jumped to 100% once the first task fully completed.

**Root cause:** `upload_view` in `core/views.py:688-715` has no guard against simultaneous uploads from the same user. It just:
1. Overwrites `upload_nonce_{user_id}` in Redis (nonce_B replaces nonce_A)
2. Dispatches a new DNA task → task_B
3. Overwrites `pending_dna_task_id` on `UserProfile` (B replaces A)

**Task A keeps running.** The nonce mechanism (`core/tasks.py:78-84`) only protects against wasted enrichment API calls — it makes task A's pending `enrich_book_task` jobs exit early when they see the new nonce. It does **nothing** to stop task A's own `generate_reading_dna_task`, which keeps chugging.

The "Syncing books" stage covers ~0–70% of the progress bar. With local Celery `--concurrency=2`, both tasks run in parallel and contend on:
1. **Postgres row locks** during `get_or_create` on the same Book/Author rows — task B blocks on task A's open transaction
2. **`ThreadPoolExecutor(max_workers=1)` inside each task** means each is internally serial, so you get two slow serial pipelines fighting each other rather than one fast parallel one
3. **The dashboard polls task B's progress.** Task B looks frozen because it's blocked waiting for task A to release locks. Once A finishes, B's locks free up and it sprints to 100%

That perfectly matches "stuck at 50%, then finally finished."

**Will it happen in prod?** Yes, but presents differently:

| Env | Celery concurrency | Behavior |
|-----|-------------------|----------|
| Local | 2 (hardcoded) | Both run in parallel → contention → 50% stall, then catches up |
| Prod $6 (1 vCPU) | Default = 1 | Serial queue → second upload looks frozen at "Parsing your library" until the first task fully completes (no progress at all for several minutes) |
| Prod $12+ (2+ vCPU) | Default = N vCPU | Same contention as local |

**Fix sketch — two options:**

**Option A (reject)** — safer, one commit:
```python
# In core/views.py around line 688, before reading the file
if request.user.is_authenticated and request.user.userprofile.pending_dna_task_id:
    from celery.result import AsyncResult
    existing = AsyncResult(request.user.userprofile.pending_dna_task_id)
    if not existing.ready():
        messages.warning(request, "Your previous upload is still processing — please wait.")
        return redirect(reverse("core:display_dna") + "?processing=true")
```

**Option B (revoke)** — matches user intent better ("I clearly meant to replace that upload"), but needs care because terminating a task mid-DB-write can leave orphan rows:
```python
existing.revoke(terminate=True)
```

Recommend starting with Option A.

### 3. UI follow-ups

- **"Still enriching" yellow banner overlaps the top-genres donut chart** — the chart is absolutely positioned in the top-right of the genres tile, and the banner uses absolute positioning at top-0. They collide. Either move the banner below the heading, or push the chart down when the banner is visible
- **Skeleton/placeholder cohesion pass** — the three skeleton templates (`book_extremes_skeleton`, `fiction_nonfiction_skeleton`, `top_genres_skeleton`) all use `cover-crosshatch` + `animate-pulse` and look reasonably consistent, but a designer pass would help unify spacing, animation timing, and copy ("Still figuring out what's fiction and what's not..." vs "Still discovering genres..." vs "Still fetching page count data..." — these could share a more consistent voice)

### 4. Other smaller follow-ups (lower priority)

- Genre coverage is ~65% for some Goodreads libraries due to API subject mapping gaps. StoryGraph coverage is higher thanks to tag extraction. Improving `CANONICAL_GENRE_MAP` (case-insensitive matching, adding "fiction"/"literary fiction" as canonical aliases) is tracked separately
- The `// 2` approximation in Goodreads re-read detection (`duplicated().sum() // 2`) undercounts books read 3+ times. StoryGraph uses `Read Count` directly so this only affects Goodreads
- Re-upload enrichment completion may show as complete prematurely when most books from a previous upload are already enriched (the `all_attempted` check includes old books). Worth scoping `all_attempted` to books from the current upload session if it bites users in prod

## How to test this PR locally

```bash
# Reset goog user + delete test books for a clean slate
docker-compose -f docker-compose.local.yml exec web poetry run python manage.py shell <<'PYEOF'
import csv, glob
from django.contrib.auth.models import User
from core.models import Book, UserBook
titles = set()
for path in sorted(glob.glob('/app/csv/test-csvs/*.csv')):
    with open(path, encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            t = row.get('Title') or row.get('title')
            if t: titles.add(t.strip())
books = Book.objects.filter(title__in=titles)
UserBook.objects.filter(book__in=books).delete()
books.delete()
u = User.objects.get(username='goog')
UserBook.objects.filter(user=u).delete()
u.userprofile.dna_data = None
u.userprofile.pending_dna_task_id = None
u.userprofile.reading_vibe = None
u.userprofile.vibe_data_hash = None
u.userprofile.recommendations_data = None
u.userprofile.save()
PYEOF

# Run tests
docker-compose -f docker-compose.local.yml exec web poetry run python manage.py test
```

Then upload `csv/test-csvs/storygraph_all_alien.csv` or `goodreads_mixed.csv` and watch the dashboard. Observe the skeleton placeholders, dimming, and live-updating stats during enrichment.

## CI status

Last green build: https://github.com/vanajmoorthy/bibliotype/actions/runs/25184829685 (after the Dockerfile gnupg fix). PR is mergeable.
