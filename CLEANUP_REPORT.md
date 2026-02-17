# Bibliotype Codebase Cleanup Report

A comprehensive audit of the Bibliotype codebase identifying dead code, unused imports, broken management commands, logic issues, and cleanup opportunities.

---

## 1. Broken Management Commands (Runtime Crashes)

Six management commands and `core/utils.py` reference Book model fields that **do not exist**. These commands will crash immediately when run.

The Book model's actual fields are: `title`, `author`, `normalized_title`, `average_rating`, `page_count`, `publish_year`, `publisher`, `genres`, `global_read_count`, `isbn13`, `google_books_average_rating`, `google_books_ratings_count`, `google_books_last_checked`.

The non-existent fields referenced are: `awards_won`, `shortlists`, `canon_lists`, `nyt_bestseller_weeks`, `ratings_count`, `mainstream_score`, `score_breakdown`, `lookup_key`.

Old migrations confirm these fields once existed (likely on a removed `PopularBook` model or an earlier version of `Book`) but were removed without updating the commands.

| Command | Non-existent fields used | Lines |
|---|---|---|
| `update_scores.py` | `awards_won`, `shortlists`, `canon_lists`, `nyt_bestseller_weeks`, `ratings_count`, `mainstream_score`, `score_breakdown` | 23-46 |
| `scrape_awards.py` | `awards_won`, `shortlists` | 88-95 |
| `scrape_goodreads_choice.py` | `awards_won`, `lookup_key`, `Book.generate_lookup_key()` | 54-62 |
| `scrape_lists.py` | `canon_lists` | 78-79 |
| `scrape_nyt.py` | `nyt_bestseller_weeks` | 77 |
| `merge_duplicates.py` | `nyt_bestseller_weeks` | 48 |

Additionally, `update_scores.py:56` does `Author.objects.get_or_create(name=pop_book.author)` where `pop_book.author` is already an Author ForeignKey object, not a string.

**`core/utils.py`** — the entire file is dead code. The `calculate_mainstream_score()` function references the same non-existent fields and is **never imported by anything**. The file can be deleted.

### Proposed fix

Either add the missing fields to the Book model (if the scraping/scoring pipeline is intended to be used), or delete these commands and `core/utils.py` if the feature was abandoned. `score_config.py` (a config data file misplaced inside `management/commands/`) should also be relocated to `core/` if the scoring pipeline is kept.

---

## 2. Broken Error Display (Processing Screen)

**`core/templates/core/dashboard.html:77`**

```html
<div x-show="status === 'FAILURE'" ...>
    Sorry, something went wrong while processing your file...
</div>
```

The variable `status` is **never defined** anywhere. The processing screen (lines 85-156) uses plain JavaScript (`checkStatus()`, `updateTargetsFromServer()`, `tweenProgress()`) — not Alpine.js state. If DNA generation fails, the `.catch()` handler on line 146 just silently retries. Users will **never** see the failure message.

### Proposed fix

Either wire the `status` variable into an Alpine.js `x-data` scope and update it from the `checkStatus()` response, or handle FAILURE explicitly in the plain JS `checkStatus()` function by showing the error div via `document.getElementById()`.

---

## 3. Dead Functions

| Function | File | Lines | Why it's dead |
|---|---|---|---|
| `normalize_and_filter_genres()` | `core/tasks.py` | 211-228 | Never called. Duplicate also exists in `dna_analyser.py:658-670`, also never called. |
| `analyze_and_print_genres()` | `core/tasks.py` | 231-265 | Never called. Duplicate also exists in `dna_analyser.py:672-701`, also never called. |
| `normalize_and_filter_genres()` | `core/services/dna_analyser.py` | 658-670 | Never called. Legacy enrichment helper. |
| `analyze_and_print_genres()` | `core/services/dna_analyser.py` | 672-701 | Never called. Legacy enrichment helper. |
| `catch_all_404_view()` | `core/views.py` | 917-928 | Near-identical duplicate of `handler404()` (line 903). Never registered in URL conf. |
| `get_match_badge_class()` | `core/views.py` | 253-262 | Defined inside `display_dna_view` but never called. Returns CSS classes never used in templates. |
| `debug_user_similarity()` | `core/services/user_similarity_service.py` | 725-798 | Debug-only function, never called. Uses `print()` instead of logger. |
| `_calculate_rating_pattern_similarity()` | `core/services/user_similarity_service.py` | 127-157 | Legacy function replaced by `_calculate_rating_pattern_similarity_from_context()`. Never called. |
| `_calculate_shared_book_correlation()` | `core/services/user_similarity_service.py` | 192-227 | Legacy function replaced by `_calculate_shared_book_correlation_from_context()`. Never called. |
| `_calculate_reading_era_similarity()` | `core/services/user_similarity_service.py` | 252-284 | Legacy function replaced by `_calculate_reading_era_similarity_from_context()`. Never called. |
| `calculate_anonymous_similarity()` | `core/services/user_similarity_service.py` | 607-614 | Wrapper function imported in `recommendation_service.py` but never actually called — all callers use `calculate_anonymous_similarity_with_context()` directly. |
| `calculate_mainstream_score()` | `core/utils.py` | 9-40 | Never imported. References non-existent model fields. |

### Proposed fix

Delete all of the above. For `calculate_anonymous_similarity`, also remove its import from `recommendation_service.py:10`.

---

## 4. Dead Variables and Dictionaries

| Variable | File | Lines | Why it's dead |
|---|---|---|---|
| `quality_badge_class_map` | `core/views.py` | 265-270 | Defined but never referenced. Maps to `border-quality-*`/`text-quality-*` CSS classes that no template uses. |
| `currentIndex` | `core/templates/core/dashboard.html` | 28 | Alpine.js property defined but never read. The `init()` function uses a local `let index` variable instead. |

### Proposed fix

Delete `quality_badge_class_map` from views.py. Either use `this.currentIndex` in the Alpine init or remove the property.

---

## 5. Unused Model Methods

| Method | File | Lines | Notes |
|---|---|---|---|
| `UserProfile.get_top_books()` | `core/models.py` | 143-148 | Never called. Top books always queried directly via `UserBook.objects.filter(...)`. |
| `AnonymousUserSession.is_expired()` | `core/models.py` | 177-179 | Never called. Expiry always checked via queryset `.filter(expires_at__lt=timezone.now())`. |

### Proposed fix

Delete both methods, or refactor existing direct queries to use them if you prefer the model-method pattern.

---

## 6. Unused Imports

### Module-level imports that are never used

| File | Line | Import | Reason unused |
|---|---|---|---|
| `core/views.py` | 7 | `import posthog` | Tracking goes through `analytics.events`, never calls `posthog.` directly |
| `core/views.py` | 10 | `from django.core.cache import cache` | `cache.` is never called in this file |
| `core/tasks.py` | 9 | `from django.core.cache import cache` | All cache ops use `safe_cache_get/set` |
| `core/tasks.py` | 11 | `from collections import Counter` | Only used by dead `analyze_and_print_genres` |
| `core/tasks.py` | 13-15 | `from .dna_constants import EXCLUDED_GENRES` | Only used by dead `normalize_and_filter_genres` |
| `core/services/dna_analyser.py` | 6 | `from django.core.cache import cache` | `cache.` is never called in this file |
| `core/services/dna_analyser.py` | 30 | `from ..book_enrichment_service import enrich_book_from_apis` | Only referenced in commented-out code (line 290) |
| `core/services/recommendation_service.py` | 3 | `Avg, Count` from `django.db.models` | Only `Q` is used from that import |
| `core/services/recommendation_service.py` | 10 | `calculate_anonymous_similarity` | Imported but never called (see dead functions above) |
| `core/admin.py` | 11 | `from django.utils.decorators import method_decorator` | Never referenced |
| `core/admin.py` | 12 | `from django.views.decorators.csrf import csrf_protect` | Never referenced |
| `core/analytics/events.py` | 8 | `from django.core.cache import cache` | Never referenced |

### Duplicate imports (same thing imported twice in one file)

**`core/services/dna_analyser.py`** — lines 31-37 are a complete duplicate of lines 1-15:

| Import | First | Duplicate |
|---|---|---|
| `import hashlib` | 1 | 31 |
| `from django.db.models import F` | 5 | 16 |
| `import random` | 7 | 32 |
| `import time` | 8 | 33 |
| `from collections import Counter` | 9 | 34 |
| `from concurrent.futures import ThreadPoolExecutor` | 10 | 35 |
| `from io import StringIO` | 11 | 36 |
| `import requests` | 15 | 37 |

### Redundant function-level re-imports

| File | Line | Import | Already at |
|---|---|---|---|
| `core/views.py` | 64 | `from django.urls import reverse` | Line 21 |
| `core/views.py` | 69 | `from django.contrib.auth.models import User` | Line 18 |
| `core/tasks.py` | 425 | `import time as _time` | Line 3 (`import time`) — just use `time.sleep(2)` |
| `core/tasks.py` | 485 | `from django.utils import timezone` | Line 18 |
| `core/services/recommendation_service.py` | 108-109, 228-229, 545-546 | `import logging` + `logger = logging.getLogger(...)` (3 times inside methods) | Line 6 and 20 (module-level) |

### Proposed fix

Remove all unused imports, delete lines 16 and 31-37 from `dna_analyser.py`, replace `_time.sleep(2)` with `time.sleep(2)` in tasks.py, and remove the 3 redundant logger redeclarations in `recommendation_service.py`.

---

## 7. Unused CSS Variables

**`static/src/input.css`** — 9 custom color variables whose corresponding Tailwind classes are never used in any template:

| Variable | Line | Corresponding dead code |
|---|---|---|
| `--color-match-100` | 22 | Dead function `get_match_badge_class()` |
| `--color-match-90` | 23 | " |
| `--color-match-80` | 24 | " |
| `--color-match-70` | 25 | " |
| `--color-match-low` | 26 | " |
| `--color-quality-twin` | 29 | Dead dict `quality_badge_class_map` |
| `--color-quality-kindred` | 30 | " |
| `--color-quality-tastes` | 31 | " |
| `--color-quality-overlap` | 32 | " |

Note: `--color-badge-*` variables (lines 17-20) **are** actively used via `badge_color_map` and the recommendations template.

### Proposed fix

Remove lines 22-26 and 29-32 from `input.css`, then rebuild Tailwind CSS.

---

## 8. Unused Context Variables

| View | Variable | Template | Issue |
|---|---|---|---|
| `display_dna_view` (views.py:500) | `rec_error` | `dashboard.html` | Passed in context but **never referenced** in the template. Users never see recommendation errors. |
| `display_dna_view` (views.py:501) | `title` | `dashboard.html` | Passed in context but **never referenced** in the dashboard template (it IS used in `public_profile.html`). |
| `public_profile_view` (views.py:865) | `user_profile` | `public_profile.html` | Passed in context but **never referenced** in the template — only `profile_user` is used. |

### Proposed fix

Either use `rec_error` in the dashboard template to display recommendation errors, or stop computing it. Remove `user_profile` from the public profile context. The `title` issue is minor.

---

## 9. Logic Issues and Code Smells

### 9a. Dead assignments in vibe generation (`dna_analyser.py:615-616`)

```python
profile.reading_vibe = reading_vibe    # line 615
profile.vibe_data_hash = new_data_hash  # line 616
```

These assignments to the profile object have **no effect**. They are overwritten shortly after by `_save_dna_to_profile()` (line 641), which reads the vibe from the `dna` dict (line 120-121) rather than from the profile object. The vibe caching **read** on line 609-611 works correctly; only the write-back on lines 615-616 is dead.

**Proposed fix:** Remove lines 615-616 (the vibe is already added to the `dna` dict on lines 621-622, which is what `_save_dna_to_profile` actually reads).

### 9b. Inconsistent return type from `calculate_full_dna()` (`dna_analyser.py:643, 650`)

- Authenticated users: returns **string** `f"DNA saved for user {user.id}"`
- Anonymous users: returns **dict** `dna`

The caller in `tasks.py:316` uses `isinstance(result_data, dict)` to handle this, which means `books_count` is always `None` for authenticated user tracking events. This works but is fragile and confusing.

**Proposed fix:** Consider returning the `dna` dict in both cases for consistency, or document the intentional asymmetry.

### 9c. `'shared_rated_books' in dir()` anti-pattern (`user_similarity_service.py:603`)

```python
"shared_rated_count": len(shared_rated_books) if 'shared_rated_books' in dir() else 0,
```

`dir()` is not the right way to check variable existence, and the variable is guaranteed to exist at this point in the control flow (set in all branches above).

**Proposed fix:** Replace with just `len(shared_rated_books)`.

### 9d. PEP 8 import ordering violation (`views.py:9`)

```python
import posthog        # line 7

logger = logging.getLogger(__name__)  # line 9 — code execution
from django.core.cache import cache   # line 10 — imports resume
```

Executable code appears between import statements.

**Proposed fix:** Move `logger = logging.getLogger(__name__)` below all imports.

### 9e. Broad exception silently swallowed (`views.py:105-106`)

```python
except Exception:
    continue
```

Inside the sitemap generation loop. Could silently mask programming errors, database issues, etc.

**Proposed fix:** Add `logger.warning(...)` inside the except block, or catch a more specific exception.

### 9f. Misplaced config file (`management/commands/score_config.py`)

This is a data dictionary, not a management command. It belongs in `core/` or `core/dna_constants.py`.

**Proposed fix:** Move to `core/score_config.py` and update the import in `update_scores.py`.

---

## 10. Potential Duplicate Logic

### 10a. `_collect_candidates_for_user()` vs `_collect_candidates_for_anonymous()` (`recommendation_service.py`)

Lines 358-506 vs 508-661 — these two methods are ~95% identical. They follow the same pattern (collect from similar users, collect from anonymized profiles, collect fallback candidates) with only minor differences in how the user context is sourced.

**Proposed fix:** Extract the shared logic into a common helper method that both call, reducing ~200 lines of duplication.

### 10b. Duplicate cycling message animation

`dashboard.html` (lines 19-44) and `task_status.html` (lines 9-32) both contain nearly identical Alpine.js cycling message animations with 4-second intervals.

**Proposed fix:** Extract into a shared Alpine component or partial.

---

## Summary

| Category | Count | Estimated lines removable |
|---|---|---|
| Broken management commands (crash at runtime) | 6 commands + utils.py | Needs model fields added or commands deleted |
| Broken error display | 1 template | ~5 lines to fix |
| Dead functions | 12 | ~280 lines |
| Dead variables/dicts | 2 | ~10 lines |
| Unused model methods | 2 | ~10 lines |
| Unused imports (module-level) | 12 | ~15 lines |
| Duplicate imports | 9 | ~10 lines |
| Redundant function-level re-imports | 8 | ~12 lines |
| Unused CSS variables | 9 | ~10 lines |
| Unused context variables | 3 | ~3 lines |
| Dead assignments / logic issues | 6 | ~5 lines |
| Duplicate logic (refactor opportunity) | 2 areas | ~200 lines reducible |
| **Total** | | **~560 lines of dead code + 200 lines refactorable** |
