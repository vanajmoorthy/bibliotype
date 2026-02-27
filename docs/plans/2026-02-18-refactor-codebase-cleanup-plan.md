---
title: Codebase Cleanup - Dead Code Removal, Import Cleanup, and Consolidation
type: refactor
status: active
date: 2026-02-18
branch: refactor/codebase-cleanup
---

# Codebase Cleanup - Dead Code Removal, Import Cleanup, and Consolidation

## Overview

Systematic cleanup of ~700 lines of dead code, broken commands, unused imports, and duplicate logic identified in `CLEANUP_REPORT.md`. Work is organized into 10 phases, ordered from safest/most isolated changes to those requiring more care. Each phase should be a separate commit for easy review and rollback.

---

## Phase 1: Delete Broken Management Commands and Dead Files

**Risk: None** - These files crash at runtime and are never called.

### Files to DELETE entirely:

| File | Why it's dead |
|---|---|
| `core/management/commands/update_scores.py` | References removed Book fields (`awards_won`, `shortlists`, `canon_lists`, `nyt_bestseller_weeks`, `ratings_count`, `mainstream_score`, `score_breakdown`). Crashes immediately. |
| `core/management/commands/scrape_awards.py` | References removed fields (`awards_won`, `shortlists`). |
| `core/management/commands/scrape_goodreads_choice.py` | References removed fields (`awards_won`, `lookup_key`) and non-existent `Book.generate_lookup_key()`. |
| `core/management/commands/scrape_lists.py` | References removed field (`canon_lists`). |
| `core/management/commands/scrape_nyt.py` | References removed field (`nyt_bestseller_weeks`). |
| `core/management/commands/merge_duplicates.py` | References removed field (`nyt_bestseller_weeks`). |
| `core/management/commands/score_config.py` | Data dict only imported by `update_scores.py` and `utils.py` (both dead). Confirmed: no other references in codebase. |
| `core/utils.py` | `calculate_mainstream_score()` references removed fields, is never imported anywhere. Import of `score_config` is also broken (wrong relative path). Mainstream scoring is now handled via `author_service.py` and `publisher_service.py`. |
| `core/management/commands/upload_test_data.py` | Redundant — hardcoded to `test_reader*` users. `create_users_from_csvs.py` does everything this does with more flexibility (glob patterns, `--skip-existing`, `--password`). |

**How this functionality was replaced:**
- Mainstream scoring: Now computed via `author_service.py` (Open Library work count + Wikipedia pageviews) and `publisher_service.py` (Wikipedia + Gemini LLM analysis for Big 5 hierarchy). The old `calculate_mainstream_score()` used Book-level fields that were removed in migration 0017.
- Scraping commands (awards, NYT, lists, Goodreads Choice): Feature was abandoned. Book enrichment now goes through `book_enrichment_service.py` (Open Library + Google Books APIs).
- `upload_test_data.py`: Replaced by `create_users_from_csvs.py` which supports `--csv-dir`, `--password`, `--skip-existing`, and `--pattern` flags.

### Verification:
```bash
# Confirm no imports reference these files
grep -r "update_scores\|scrape_awards\|scrape_goodreads_choice\|scrape_lists\|scrape_nyt\|merge_duplicates\|score_config\|upload_test_data" core/ --include="*.py" -l
# Should only show the files themselves and CLEANUP_REPORT.md
```

---

## Phase 2: Delete Dead Functions

**Risk: None** - All confirmed never called via grep.

### `core/tasks.py`
- Delete `normalize_and_filter_genres()` (lines 211-228)
- Delete `analyze_and_print_genres()` (lines 231-265)

### `core/services/dna_analyser.py`
- Delete `normalize_and_filter_genres()` (lines 658-670)
- Delete `analyze_and_print_genres()` (lines 672-701)

### `core/views.py`
- Delete `catch_all_404_view()` (lines 917-928) — near-identical duplicate of `handler404()` at line 903, never registered in URL conf
- Delete `get_match_badge_class()` (lines 253-262) — defined inside `display_dna_view` but never called
- Delete `quality_badge_class_map` dict (lines 265-270) — defined but never referenced

### `core/services/user_similarity_service.py`
- Delete `debug_user_similarity()` (lines 725-798) — debug-only, uses `print()`, never called
- Delete `_calculate_rating_pattern_similarity()` (lines 127-157) — replaced by `_calculate_rating_pattern_similarity_from_context()`
- Delete `_calculate_shared_book_correlation()` (lines 192-227) — replaced by `_calculate_shared_book_correlation_from_context()`
- Delete `_calculate_reading_era_similarity()` (lines 252-284) — replaced by `_calculate_reading_era_similarity_from_context()`
- Delete `calculate_anonymous_similarity()` (lines 607-614) — wrapper never called; all callers use `calculate_anonymous_similarity_with_context()` directly

### `core/models.py`
- Delete `UserProfile.get_top_books()` (lines 143-148) — never called; top books always queried via `UserBook.objects.filter(...)`
- Delete `AnonymousUserSession.is_expired()` (lines 177-179) — never called; expiry checked via queryset filter

### Verification:
```bash
# After deletion, run tests to confirm nothing breaks
poetry run python manage.py test
```

---

## Phase 3: Clean Up All Unused Imports

**Risk: None** - Removing unused imports has no behavioral effect.

### Module-level unused imports to remove:

| File | Import to remove |
|---|---|
| `core/views.py:7` | `import posthog` |
| `core/views.py:10` | `from django.core.cache import cache` |
| `core/tasks.py:9` | `from django.core.cache import cache` |
| `core/tasks.py:11` | `from collections import Counter` |
| `core/tasks.py:13-15` | `from .dna_constants import EXCLUDED_GENRES` (and `CANONICAL_GENRE_MAP` if only used by dead functions) |
| `core/services/dna_analyser.py:6` | `from django.core.cache import cache` |
| `core/services/dna_analyser.py:30` | `from ..book_enrichment_service import enrich_book_from_apis` |
| `core/services/recommendation_service.py:1` | Remove `defaultdict` from `from collections import Counter, defaultdict` |
| `core/services/recommendation_service.py:3` | Remove `Avg, Count` from `from django.db.models import Avg, Count, Q` (keep only `Q`) |
| `core/services/recommendation_service.py:10` | Remove `calculate_anonymous_similarity` from import |
| `core/admin.py:11` | `from django.utils.decorators import method_decorator` |
| `core/admin.py:12` | `from django.views.decorators.csrf import csrf_protect` |
| `core/analytics/events.py:8` | `from django.core.cache import cache` |
| `core/management/commands/seed_test_books.py:7` | Remove `Genre` from import |
| `core/tests/test_recommendations.py:9` | Remove `UserProfile` from import |

### Duplicate imports to remove in `core/services/dna_analyser.py`:

Delete the duplicate block at lines 16 and 31-37 (these are exact duplicates of lines 1-15):
- `import hashlib` (duplicate of line 1)
- `from django.db.models import F` (duplicate of line 5)
- `import random` (duplicate of line 7)
- `import time` (duplicate of line 8)
- `from collections import Counter` (duplicate of line 9)
- `from concurrent.futures import ThreadPoolExecutor` (duplicate of line 10)
- `from io import StringIO` (duplicate of line 11)
- `import requests` (duplicate of line 15)

### Redundant function-level re-imports to remove:

| File | Line | Import | Already at |
|---|---|---|---|
| `core/views.py:64` | `from django.urls import reverse` | Line 21 |
| `core/views.py:69` | `from django.contrib.auth.models import User` | Line 18 |
| `core/views.py:390` | `from collections import Counter` | Never used even locally |
| `core/tasks.py:20` | Change `from django.db import models as models` to `from django.db.models import Q` (only usage) |
| `core/tasks.py:425` | `import time as _time` → replace `_time.sleep(2)` with `time.sleep(2)` (line 3 already imports `time`) |
| `core/tasks.py:485` | `from django.utils import timezone` | Line 18 |
| `core/services/recommendation_service.py:108-109, 228-229, 545-546` | Remove 3 redundant `import logging` + `logger = logging.getLogger(...)` inside methods (already at lines 6 and 20) |

### Fix PEP 8 import ordering in `core/views.py`:
- Move `logger = logging.getLogger(__name__)` (line 9) below all imports

### Fix import error in `core/management/commands/generate_test_data.py`:
- Line 8: Remove `User` from `from core.models import Book, Author, User` (User doesn't exist in core.models; it's correctly imported on line 9 as `DjangoUser`)

---

## Phase 4: Remove Unused CSS Variables

**Risk: None** - Variables are never referenced by any template.

### `static/src/input.css`

Remove these 9 variables from the `@theme` block:

```css
/* Match colors (only used by dead get_match_badge_class()) */
--color-match-100   /* line 22 */
--color-match-90    /* line 23 */
--color-match-80    /* line 24 */
--color-match-70    /* line 25 */
--color-match-low   /* line 26 */

/* Quality colors (only used by dead quality_badge_class_map) */
--color-quality-twin     /* line 29 */
--color-quality-kindred  /* line 30 */
--color-quality-tastes   /* line 31 */
--color-quality-overlap  /* line 32 */
```

**Note:** Keep `--color-badge-*` variables (lines 17-20) — these ARE actively used.

After removal, rebuild Tailwind:
```bash
pnpm run build
```

---

## Phase 5: Fix Unused Context Variables

**Risk: Low**

### `core/views.py` — `display_dna_view`

1. **Stop computing `rec_error`**: Remove all assignments to `rec_error` throughout the function (lines 440-441, 466). Remove `"rec_error": rec_error` from the context dict (line 500). The dashboard has a fallback for when recommendations are empty.

2. **Keep `title`**: It's used in `public_profile.html` via the same context pattern, so leave it for consistency.

### `core/views.py` — `public_profile_view`

3. **Remove `user_profile` from context** (line 865): The template only uses `profile_user`. Remove `"user_profile": user_profile` from the context dict.

---

## Phase 6: Fix Dead Alpine.js Variable

**Risk: Low**

### `core/templates/core/dashboard.html`

Remove `currentIndex: 0` (line 28) from the Alpine `x-data` object in the cycling message animation. The `init()` function uses a local `let index` variable instead and never reads `this.currentIndex`.

---

## Phase 7: Fix Logic Issues

**Risk: Medium** - Behavioral changes; each needs a test.

### 7a. Remove dead vibe assignments (`dna_analyser.py:615-616`)

Remove these two lines:
```python
profile.reading_vibe = reading_vibe    # line 615
profile.vibe_data_hash = new_data_hash  # line 616
```

These assign to the profile object but have no effect — `_save_dna_to_profile()` reads the vibe from the `dna` dict (lines 120-121), not from the profile object. The vibe is already written to the `dna` dict on lines 621-622.

**Test to add:** Verify that after `calculate_full_dna()`, the profile's `reading_vibe` and `vibe_data_hash` are set correctly from the DNA dict (not from stale profile attributes).

### 7b. Consistent return type from `calculate_full_dna()` (`dna_analyser.py`)

Change the authenticated user return (line 662) from:
```python
return f"DNA saved for user {user.id}"
```
to:
```python
return dna
```

**Impact analysis (confirmed safe):**
- Only caller: `generate_reading_dna_task()` in `tasks.py:309`
- The task uses `isinstance(result_data, dict)` at line 316 to extract `books_count` — this will now work for authenticated users too (improvement)
- The task branches on `if not user:` (line 320) for caching, not on return type — no change needed
- Simplify tasks.py:314-318 by removing the `isinstance` check since result is always a dict now

### 7c. Fix `dir()` anti-pattern (`user_similarity_service.py:603`)

Replace:
```python
"shared_rated_count": len(shared_rated_books) if 'shared_rated_books' in dir() else 0,
```
with:
```python
"shared_rated_count": len(shared_rated_books),
```

The variable is guaranteed to exist — it's set in all branches above this line.

### 7d. Add logging to broad exception (`views.py:105-106`)

Change:
```python
except Exception:
    continue
```
to:
```python
except Exception:
    logger.warning(f"Error generating sitemap entry for user {user.username}", exc_info=True)
    continue
```

### 7e. Remove dead test boilerplate (`core/tests/test_recommendations.py:571-573`)

Delete:
```python
if __name__ == '__main__':
    # Run tests with: python manage.py test core.tests.test_recommendations
    pass
```

---

## Phase 8: Fix Broken Error Display (Processing Screen)

**Risk: Medium** - Template + view change; needs a test.

### Problem

The FAILURE div in `dashboard.html:77` references `status === 'FAILURE'` but `status` is never defined. The `checkStatus()` JavaScript function (lines 127-149) never handles FAILURE and polls forever. The backend `check_dna_status_view` (views.py:779-809) never returns FAILURE either — it catches all exceptions and returns PENDING.

### Fix — Backend (`core/views.py` — `check_dna_status_view`)

Add FAILURE state detection before the existing PENDING return:

```python
result = AsyncResult(profile.pending_dna_task_id)

# Check for failure FIRST
if result.state == "FAILURE":
    # Clear the pending task so user can retry
    profile.pending_dna_task_id = None
    profile.save(update_fields=["pending_dna_task_id"])
    return JsonResponse({"status": "FAILURE", "error": "An error occurred while processing your file."})

# Then check for progress/pending (existing code)
info = result.info or {}
# ... rest of existing logic
```

### Fix — Frontend (`core/templates/core/dashboard.html`)

Refactor the processing section to use Alpine.js state (matching the pattern in `task_status.html:69-155` which already works correctly):

1. Wrap the processing section in an Alpine `x-data` scope with `status: "PENDING"`
2. Update `checkStatus()` to set `this.status = data.status`
3. Handle FAILURE: set status, clear polling interval
4. The existing FAILURE div at line 77 will then work via `x-show="status === 'FAILURE'"`

### Test to add

```python
class CheckDnaStatusFailureTest(TestCase):
    def test_check_dna_status_returns_failure_when_task_failed(self):
        """Verify check_dna_status_view returns FAILURE status when Celery task fails."""
        user = User.objects.create_user(username="testuser", password="testpass")
        profile = user.userprofile
        profile.pending_dna_task_id = "fake-task-id"
        profile.save()

        self.client.login(username="testuser", password="testpass")

        with patch("core.views.AsyncResult") as mock_async:
            mock_result = MagicMock()
            mock_result.state = "FAILURE"
            mock_result.info = Exception("Something went wrong")
            mock_async.return_value = mock_result

            response = self.client.get(reverse("core:api_check_dna_status"))

        data = response.json()
        self.assertEqual(data["status"], "FAILURE")
        self.assertIn("error", data)

        # Verify pending task was cleared so user can retry
        profile.refresh_from_db()
        self.assertIsNone(profile.pending_dna_task_id)
```

---

## Phase 9: Fix Management Command Issues

**Risk: Low-Medium**

### 9a. Fix `generate_test_data.py` import error (line 8)

Remove `User` from `from core.models import Book, Author, User`. The `User` model is correctly imported on line 9 as `from django.contrib.auth.models import User as DjangoUser`.

### 9b. Fix `seed_test_books.py` Author lookup (line 44)

Change:
```python
author, created = Author.objects.get_or_create(name=author_name)
```
to:
```python
author, created = Author.objects.get_or_create(
    normalized_name=Author._normalize(author_name),
    defaults={"name": author_name},
)
```

### 9c. Fix `seed_test_books.py` Publisher lookup (line 69)

Change:
```python
publisher, _ = Publisher.objects.get_or_create(name=pub_name)
```
to:
```python
normalized = pub_name.strip().lower()
publisher, _ = Publisher.objects.get_or_create(
    normalized_name=normalized,
    defaults={"name": pub_name},
)
```

### 9d. Fix `re_enrich_all_books.py` contradictory timestamp (lines 78-85)

Remove lines 78 and 85 (save/restore of `original_google_check`). The timestamp is cleared to force re-fetch but then restored — defeating the purpose. After fix, the enrichment service will naturally update the timestamp.

### 9e. Fix `populate_test_dna.py` hash function (lines 186, 333)

Replace `hash(str(...))` with `hashlib.sha256(str(...).encode()).hexdigest()` to match production behavior in `dna_analyser.py`. Add `import hashlib` at top of file.

---

## Phase 10: Consolidate Duplicate Code

**Risk: Medium** - Structural changes; need thorough testing.

### 10a. Consolidate `backfill_enrichment.py` and `enrich_books.py`

Keep `enrich_books.py` as the single enrichment command. Merge in async Celery dispatch from `backfill_enrichment.py`. Delete `backfill_enrichment.py`.

**New `enrich_books.py` design:**

```python
class Command(BaseCommand):
    help = "Enrich books missing metadata. Default: async Celery tasks. Use --sync for direct API calls."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Show counts without processing")
        parser.add_argument("--limit", type=int, help="Limit number of books to process")
        parser.add_argument("--sync", action="store_true", help="Run synchronously via APIs instead of Celery")
        parser.add_argument("--process-all", action="store_true", help="Re-check all books, not just unenriched")
        parser.add_argument("--google-books-limit", type=int, default=950, help="Max Google Books API calls (sync only)")

    def handle(self, *args, **options):
        queryset = self._get_books_queryset(options["process_all"])
        if options["limit"]:
            queryset = queryset[:options["limit"]]

        if options["dry_run"]:
            self._show_stats(queryset)
            return

        if options["sync"]:
            self._sync_enrich(queryset, options["google_books_limit"])
        else:
            self._async_enrich(queryset)
```

### 10b. Consolidate `_collect_candidates_for_user()` and `_collect_candidates_for_anonymous()` in `recommendation_service.py`

These two methods (lines 358-506 vs 508-661) are ~90% identical. Extract two shared helpers:

1. **`_collect_candidates_from_similar_users(similar_users_with_scores, context)`**
   - Takes pre-computed list of `(user, similarity_data)` tuples
   - Handles the shared book extraction loop, candidate dict building, source metadata, weighting
   - The caller is responsible for finding similar users (different logic per auth type)
   - Minor difference: anonymous uses `.get("shared_books_count", 0)` defensively — parameterize with `is_anonymous` flag

2. **`_collect_candidates_from_anonymized_profiles(entity, context, min_similarity, user_ctx=None)`**
   - Takes either User or AnonymousUserSession
   - Handles anonymized profile iteration, similarity calculation, book fetching
   - `user_ctx` param: pre-built for authenticated (optimization), None for anonymous

After extraction, both `_collect_candidates_for_user()` and `_collect_candidates_for_anonymous()` become thin wrappers (~30 lines each) that:
1. Find similar users (different logic)
2. Call `_collect_candidates_from_similar_users()`
3. Call `_collect_candidates_from_anonymized_profiles()`
4. Add fallback candidates (authenticated only — anonymous handles fallback in caller)

**Estimated reduction:** ~200 lines.

### 10c. Extract username editor partial from `dashboard.html`

Create `core/templates/core/partials/username_editor.html` from the duplicate component.

The two instances differ in validation:
- First (lines 162-220): Has `isNewNameValid` computed getter, `maxlength="15"`, disabled button state, length error message
- Second (lines 323-383): Missing all validation

**Decision:** Use the first (full validation) version as the canonical partial — the second was simply missing validation by accident. Both should validate.

The partial needs no extra context variables — it uses `{{ user.username }}` and `{% url 'core:api_update_username' %}` which are available in both locations.

### 10d. Extract cycling message partial

Create `core/templates/core/partials/cycling_messages.html` from the duplicate Alpine.js animation in `dashboard.html` (lines 21-45) and `task_status.html` (lines 9-25).

Pass messages as a template variable or use a single canonical message list. The only difference is one message string ("Generating Your Bibliotype..." vs "Discovering Your Literary DNA...").

### 10e. Remove dead template variables

In `partials/dna/top_genres_authors_row.html`:
- Replace `{{ genre_color_class|default:'bg-brand-yellow' }}` with `bg-brand-yellow`
- Replace `{{ author_color_class|default:'bg-brand-cyan' }}` with `bg-brand-cyan`

In `partials/dna/recommendations_grid.html`:
- Replace `{{ rec_colors_js|default:'[...]' }}` with the default color array directly

---

## Acceptance Criteria

- [ ] All 9 broken management commands and dead files deleted (Phase 1)
- [ ] All 12+ dead functions removed (Phase 2)
- [ ] All 17+ unused imports cleaned up (Phase 3)
- [ ] 9 unused CSS variables removed and Tailwind rebuilt (Phase 4)
- [ ] `rec_error` computation removed, `user_profile` removed from public profile context (Phase 5)
- [ ] Dead `currentIndex` Alpine variable removed (Phase 6)
- [ ] Dead vibe assignments removed with regression test (Phase 7a)
- [ ] `calculate_full_dna()` returns dict for both paths with simplified caller (Phase 7b)
- [ ] `dir()` anti-pattern fixed (Phase 7c)
- [ ] Sitemap exception logged (Phase 7d)
- [ ] Dead test boilerplate removed (Phase 7e)
- [ ] Dashboard FAILURE display works end-to-end with test (Phase 8)
- [ ] `generate_test_data.py` import fixed (Phase 9a)
- [ ] `seed_test_books.py` uses normalized lookups (Phase 9b, 9c)
- [ ] `re_enrich_all_books.py` timestamp logic fixed (Phase 9d)
- [ ] `populate_test_dna.py` uses SHA256 (Phase 9e)
- [ ] Enrichment commands consolidated into one (Phase 10a)
- [ ] Recommendation candidate collection deduplicated (Phase 10b)
- [ ] Username editor extracted to partial (Phase 10c)
- [ ] Cycling messages extracted to partial (Phase 10d)
- [ ] Dead template variables replaced with defaults (Phase 10e)
- [ ] All existing tests pass after every phase
- [ ] Code formatted with `black --line-length 120` and `isort --profile black --line-length 120`

## Test Plan

- [ ] Run full test suite after each phase: `poetry run python manage.py test`
- [ ] New test: FAILURE status returned by `check_dna_status_view` when task fails (Phase 8)
- [ ] New test: `calculate_full_dna()` returns dict for authenticated users (Phase 7b)
- [ ] New test: Vibe data persists correctly through `_save_dna_to_profile()` without dead assignments (Phase 7a)
- [ ] Verify Tailwind build succeeds after CSS variable removal: `pnpm run build`
- [ ] Manual smoke test: Upload CSV, verify processing screen, verify dashboard renders

## References

- `CLEANUP_REPORT.md` — Full audit with line numbers
- `core/services/dna_analyser.py` — Main DNA pipeline
- `core/tasks.py` — Celery task definitions
- `core/views.py` — View functions
- `core/services/recommendation_service.py` — Recommendation engine
- `core/services/user_similarity_service.py` — User similarity calculations
