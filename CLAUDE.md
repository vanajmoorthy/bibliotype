# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

When committing, do not include the Co-Authored-By trailer. Do not mention Claude or AI assistance in PR descriptions.

## Project Overview

Bibliotype is a Django web application that analyzes reading data from Goodreads/StoryGraph CSV exports to generate personalized "Reading DNA" dashboards with AI-powered insights. It features a neobrutalist design aesthetic with bold borders, offset shadows, bright colors, and a retro monospace font (VT323).

## Package Management (Poetry)

**Poetry is the required package manager.** All Python dependencies are managed via `pyproject.toml` and `poetry.lock`. Never use `pip install` directly — always use Poetry commands.

```bash
poetry install                     # Install all dependencies (reads poetry.lock)
poetry add <package>               # Add a new dependency
poetry add --group dev <package>   # Add a dev-only dependency
poetry run <command>               # Run a command inside the Poetry virtualenv
poetry shell                       # Activate the virtualenv in your shell
```

- **Build system:** `poetry-core>=2.0.0` (defined in `[build-system]`)
- **Virtual environment:** Created in-project (`.venv/`) via `POETRY_VIRTUALENVS_IN_PROJECT=true`
- All `python manage.py` commands should be run via `poetry run python manage.py ...` unless the virtualenv is already activated
- The `Procfile` (used by `honcho start`) already uses `poetry run` for Django and Celery processes

## Development Commands

### Running the Application
```bash
# Start both Django server and Tailwind watcher (recommended)
honcho start

# Or run separately:
poetry run python manage.py runserver     # Django backend
pnpm run dev                              # Tailwind CSS watch mode
```

### Database
```bash
poetry run python manage.py migrate
poetry run python manage.py loaddata core/fixtures/initial_data.json
poetry run python manage.py createsuperuser
```

### Testing
```bash
poetry run python manage.py test                              # Run all tests
poetry run python manage.py test core.tests.test_views_e2e    # Run specific test module
poetry run python manage.py test core.tests.test_views_e2e.TestClassName.test_method  # Single test
```

### Docker

#### Local Development (`docker-compose.local.yml`)
```bash
docker-compose -f docker-compose.local.yml up --build -d      # Build and start all services
docker-compose -f docker-compose.local.yml exec web poetry run python manage.py migrate
docker-compose -f docker-compose.local.yml exec web poetry run python manage.py test
docker-compose -f docker-compose.local.yml logs -f web         # Tail web logs
docker-compose -f docker-compose.local.yml down                # Stop all services
docker-compose -f docker-compose.local.yml down -v             # Stop and delete volumes (resets DB)
```

#### Production (`docker-compose.prod.yml`)
```bash
docker compose -f docker-compose.prod.yml up -d --force-recreate   # Deploy/update
docker compose -f docker-compose.prod.yml logs -f web worker       # Tail logs
```

#### Docker Architecture

**Services (4 containers):**
| Service | Image | Purpose |
|---|---|---|
| `db` | `postgres:15` | PostgreSQL database with named volume `postgres_data` for persistence |
| `redis` | `redis:7-alpine` | Celery broker (db 0), result backend (db 0), Django cache (db 1) |
| `web` | Built from `Dockerfile` | Django app server |
| `worker` | Built from `Dockerfile` | Celery worker for async tasks |

**Dockerfile build stages** (single-stage, `python:3.13-slim-bookworm`):
1. Install system deps: `postgresql-client`, `dos2unix`, `curl`
2. Install Node.js 20 (via nodesource) + pnpm (via npm)
3. Set Poetry env vars (`POETRY_VIRTUALENVS_IN_PROJECT=true`, `POETRY_NO_INTERACTION=1`)
4. Copy `pyproject.toml` + `poetry.lock` → `pip install poetry` → `poetry install --no-root`
5. Copy `package.json` + `pnpm-lock.yaml` + `static/` + `tailwind.config.js` → `pnpm install --frozen-lockfile` → `pnpm run build`
6. Copy rest of app, fix line endings (`dos2unix`), make scripts executable
7. Entrypoint: `/app/docker-entrypoint.sh`

**Entrypoint (`docker-entrypoint.sh`):**
1. Waits for PostgreSQL to be ready (`wait-for-postgres.sh db`)
2. Runs `poetry run python manage.py migrate`
3. If `DJANGO_ENV=production`: runs `collectstatic --noinput`, sets file ownership to `www-data` (UID 33) on `/app/staticfiles`
4. Executes the `command` from docker-compose via `exec "$@"`

**Local vs Production differences:**
| Aspect | Local | Production |
|---|---|---|
| **Web command** | `pnpm run dev & poetry run python manage.py runserver 0.0.0.0:8000` | `poetry run gunicorn bibliotype.wsgi:application --bind 0.0.0.0:8000` |
| **Volumes** | Source code mounted (`.:/app`) for live reload; `.venv` and `node_modules` excluded via anonymous volumes | Only `./staticfiles:/app/staticfiles` for Nginx to serve |
| **Ports** | `8000:8000` (all interfaces), `5432:5432`, `6379:6379` exposed | `127.0.0.1:8000:8000` (localhost only — Nginx proxies), no DB/Redis ports exposed |
| **Image tag** | Built locally | `${DOCKERHUB_USERNAME}/bibliotype:${IMAGE_TAG:-latest}` |
| **DJANGO_ENV** | `development` | `production` |

**Environment variables** are passed from `.env` file via `${VAR}` interpolation in compose files. Both compose files share the same `.env` file.

### Code Formatting
```bash
# Python
black --line-length 120 .
isort --profile black --line-length 120 .

# HTML/Templates (Prettier with jinja-template and tailwindcss plugins)
npx prettier --write "**/*.html"
```

## Architecture

### Data Flow
```
CSV Upload → Validation (10MB limit, .csv ext) → Celery Task (async) → DNA Analysis
→ Book/Author DB Sync → API Enrichment (Open Library, Google Books)
→ AI Vibe Generation (Gemini) → Results stored in UserProfile/AnonymousUserSession
→ Recommendation Generation (async) → Cache
```

### Key Directories
- `bibliotype/` - Django project config (settings.py, urls.py, celery.py, wsgi.py, runner.py)
- `core/` - Main Django app
  - `services/` - Business logic layer (9 service modules)
  - `templates/core/` - HTML templates (base.html + page templates + partials/)
  - `tests/` - Test suite (unit, integration, e2e, feature)
  - `management/commands/` - 25 custom Django management commands
  - `analytics/` - PostHog event tracking (posthog_client.py, events.py, middleware.py)
  - `forms.py` - CustomUserCreationForm, UpdateDisplayNameForm
  - `utils.py` - calculate_mainstream_score()
  - `dna_constants.py` - Reader types, genre mappings, publisher hierarchies (~40KB)
- `static/` - Static assets
  - `src/input.css` - Tailwind CSS source with custom @theme variables
  - `dist/output.css` - Compiled Tailwind CSS

### Service Layer (`core/services/`)
- `dna_analyser.py` - Core analysis engine: CSV parsing, book syncing, reader type assignment, statistics, vibe generation orchestration. Entry point: `calculate_full_dna()`
- `llm_service.py` - Gemini 2.5 Flash integration with few-shot prompting. Returns JSON `{"vibe_phrases": [...]}`. No internal caching (handled by dna_analyser hash comparison)
- `recommendation_service.py` - Class-based `RecommendationEngine` with multi-source candidate collection (similar users, anonymized profiles, fallback), scoring with diminishing returns (sqrt), diversity filtering, and explanation generation. Has `safe_cache_get/set()` wrappers for graceful Redis failure
- `user_similarity_service.py` - Multi-component similarity: shared book correlation (35%), Jaccard overlap (15-25%), top books overlap (20%), genre cosine similarity (15%), author cosine similarity (15%), rating pattern (8%), reading era (7%). Bulk-optimized with `_bulk_build_user_contexts()`
- `book_enrichment_service.py` - Two-stage: Open Library API (genres, metadata) then Google Books API (ratings). Genre canonicalization against `CANONICAL_GENRE_MAP`
- `author_service.py` - Mainstream detection via Open Library (work count >= 10) + Wikipedia (monthly pageviews >= 2,000 or cultural icon >= 50,000)
- `publisher_service.py` - Wikipedia + Gemini LLM analysis for Big 5 publisher hierarchy
- `top_books_service.py` - Top book calculation with VADER sentiment scoring
- `anonymization_service.py` - Batch anonymization of expired anonymous sessions

Services are standalone functions (except `RecommendationEngine` class). Internal helpers use `_underscore` prefix. Data is passed as dicts and Counters. No dependency injection — services query the ORM directly.

### Core Models (`core/models.py`)
- `Genre` - name (unique, indexed)
- `Publisher` - name, normalized_name (auto-computed in save()), is_mainstream, parent (self-referential FK)
- `Author` - name (unique, indexed), normalized_name (auto-computed), popularity_score, is_mainstream. Static `_normalize()` method for string normalization
- `Book` - title, author (FK CASCADE), normalized_title (auto-computed), genres (M2M), publisher (FK SET_NULL), isbn13 (unique), page_count, publish_year, average_rating, Google Books fields. unique_together on (normalized_title, author)
- `UserBook` - user (FK CASCADE), book (FK CASCADE), user_rating (1-5), date_read, is_top_book, top_book_position. unique_together on (user, book)
- `UserProfile` - user (OneToOne CASCADE), dna_data (JSONField), reader_type, reading_vibe (JSONField), vibe_data_hash, pending_dna_task_id, recommendations_data (JSONField), is_public, visible_in_recommendations
- `AnonymousUserSession` - Temporary storage with session_key, dna_data, books_data, book_ratings (JSONField), expires_at
- `AnonymizedReadingProfile` - Permanent anonymized profiles for community comparisons
- `AggregateAnalytics` - Singleton (forced pk=1) with community distribution stats. Access via `get_instance()`

**Signals:** Two `post_save` receivers on User auto-create/save UserProfile. Disconnected during fixture loading (`load_fixture_data.py`).

**Normalization pattern:** Author, Book, and Publisher models auto-compute normalized fields in `save()` overrides using `_normalize()` / `_normalize_title()`.

### Views (`core/views.py`)
All function-based views (no CBVs). Key decorators: `@login_required`, `@require_POST` (often stacked). Authentication is email-based (case-insensitive lookup), not username-based.

Authenticated and anonymous users have separate flows — anonymous users get session-based temporary storage, authenticated users get persistent UserProfile storage. AJAX endpoints return `JsonResponse`, page views use `render()` with context dicts.

### URL Conventions (`core/urls.py`)
- App namespace: `core` (reversed as `core:view_name`)
- URL paths use kebab-case: `update-privacy/`, `update-recommendation-visibility/`
- API endpoints prefixed with `api/`: `api/update-username/`, `api/task-result/<str:task_id>/`
- Public profiles at `u/<str:username>/`

### Tech Stack
- **Backend:** Django 5.2.5, Python 3.13+, Poetry 2.0+, Celery 5.5 + Redis 7
- **Frontend:** Tailwind CSS 4.x (with custom @theme), Alpine.js 3.x (CDN), Chart.js (CDN)
- **Database:** PostgreSQL 15 (prod), SQLite (dev fallback via dj-database-url)
- **AI:** Google Generative AI — Gemini 2.5 Flash (`genai.GenerativeModel("gemini-2.5-flash")`)
- **Analytics:** PostHog (EU instance, `https://eu.i.posthog.com`)
- **Profiling:** Django Silk (dev-only)

## Coding Conventions

### Python
- **Line length:** 120 characters (Black + isort)
- **Formatting:** Black with `line-length = 120`; isort with `profile = "black"`, `multi_line_output = 3`, `include_trailing_comma = true`
- **Imports:** Grouped with blank-line separators — stdlib first, third-party second, local third. Multi-line imports use parentheses
- **Naming:** snake_case for functions/variables, PascalCase for classes, ALL_CAPS for constants, `_underscore` prefix for private functions
- **Strings:** f-strings exclusively — no `.format()` or `%` formatting anywhere
- **Type hints:** Used selectively, more prevalent in service functions (e.g., `author_name: str`, `user_id: int | None`). Not required on views or model methods
- **Docstrings:** Selective, Google-style. Short one-liners for simple functions, multi-line for complex public methods. Not required on every function
- **Logging:** Module-level `logger = logging.getLogger(__name__)` in every file. Use `logger.info/warning/error` with f-strings. Always pass `exc_info=True` on `logger.error` for tracebacks
- **Error handling:** Catch specific Django exceptions first (`Model.DoesNotExist`), generic `Exception` last with logging. Graceful degradation preferred (continue/return, not crash). No custom exception classes — uses Django built-ins
- **Constants:** Shared constants in `core/dna_constants.py`. Module-local constants defined inline near usage in ALL_CAPS

### HTML / Templates
- **Naming:** snake_case for all template files (e.g., `primary_button.html`, `reader_type_card.html`)
- **Indentation:** 4 spaces (Prettier configured: `tabWidth: 4`, `printWidth: 120`)
- **Parser:** Prettier with `jinja-template` parser for `.html` files and `tailwindcss` plugin for class sorting
- **Inheritance:** All pages extend `core/base.html` which defines blocks: `seo_title`, `seo_description`, `og_*`, `twitter_*`, `structured_data`, `content`
- **Partials:** Reusable components in `templates/core/partials/` organized by function (`buttons/`, `dna/`). Included with explicit context: `{% include 'core/partials/buttons/primary_button.html' with text="Click" %}`
- **No custom template tags or filters** — all logic via context variables and built-in Django template features

### Tailwind / CSS
- **Design system:** Neobrutalist — 2px borders (`border-brand-text border-2`), offset shadows (`shadow-neo`: 4px 4px, `shadow-neo-sm`: 2px 2px), bright flat colors
- **Custom theme** (in `static/src/input.css` @theme block): `brand-background`, `brand-text`, `brand-yellow/orange/pink/cyan/green/purple`, match colors, quality colors
- **Font:** VT323 (retro monospace) via `--font-sans`
- **Card pattern:** `class="border-brand-text shadow-neo border-2 bg-white p-6"`
- **Button pattern:** Background color + `shadow-neo border-brand-text border-2` + `hover:shadow-none active:translate-x-1 active:translate-y-1 transition-all duration-150 ease-in-out`
- **Responsive:** Mobile-first with `md:` / `sm:` breakpoint prefixes
- **Grid:** `grid grid-cols-1 gap-6 md:grid-cols-2` or `md:grid-cols-3`

### JavaScript
- **No separate JS files** — all JavaScript is inline in templates or `<script>` tags
- **Libraries:** Alpine.js for interactivity (drag-drop, toggles, animations), Chart.js for data visualization. Both loaded via CDN in base.html
- **Style:** Vanilla ES6+ with arrow functions, optional chaining (`?.`), template literals, array methods (`.map()`, `.forEach()`)
- **Alpine.js pattern:** `x-data` objects on container divs with methods and computed getters. Event handlers via `@click`, `@change`, `@submit.prevent`
- **Chart.js pattern:** Canvas elements with `getElementById()?.getContext("2d")`, inline config objects. Global defaults set: `Chart.defaults.font.family = "VT323"`
- **Chart colors:** `['#ffb4dd', '#40e7aa', '#ffa75e', '#8bbfff', '#FFE9CE', '#ff647c', '#ffe56c', '#A1CDF1', '#9af6d4', '#fe9393']`

## Testing Conventions

### Structure
- Files in `core/tests/` named `test_<feature>.py`
- Classes: `<Feature><Type>TestCase` or `<Feature>Tests` (e.g., `ViewE2E_Tests`, `TaskUnitTests`, `RecommendationTestCase`)
- Methods: `test_<description>` in snake_case

### Patterns
- **Base classes:** `django.test.TestCase` for most tests, `TransactionTestCase` for e2e tests requiring real transactions
- **Setup:** `setUp()` with direct ORM model creation. CSV data via `SimpleUploadedFile`
- **Mocking:** `unittest.mock` with stacked `@patch("core.services.module.function")` decorators. External services (LLM, APIs, Celery tasks) always mocked
- **Assertions:** Standard `self.assert*` methods — `assertEqual`, `assertContains`, `assertRedirects`, `assertGreater`, `assertIn`
- **Celery in tests:** `@override_settings(CELERY_TASK_ALWAYS_EAGER=True)` makes tasks run synchronously
- **Cache in tests:** `@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})` for in-memory cache
- **Custom runner:** `ForceDisconnectTestRunner` in `bibliotype/runner.py` handles PostgreSQL connection cleanup during teardown

## User Flows

### Authenticated User Flow
1. **Upload** (`upload_view`): User POSTs CSV → validated (10MB, .csv) → `request.session.pop("dna_data")` clears old session data → `generate_reading_dna_task.delay(csv_content, user.id)` queued → `pending_dna_task_id` saved on UserProfile → redirect to `/dashboard/?processing=true`
2. **Processing** (`display_dna_view`): Sees processing spinner → frontend polls `check_dna_status_view` via AJAX → checks `profile.pending_dna_task_id` → queries `AsyncResult` for PROGRESS state (current/total/stage/percent) → returns `{"status": "PENDING", "progress": {...}}` or `{"status": "SUCCESS"}`
3. **DNA Generation** (`generate_reading_dna_task` → `calculate_full_dna`):
   - Parses CSV with pandas, filters to "read" shelf
   - Generates SHA256 hash of book fingerprints (`title+author`) for cache invalidation
   - Syncs books to DB: `Author.get_or_create()` by normalized_name, `Book.update_or_create()` by (normalized_title, author). New authors trigger `check_author_mainstream_status_task.delay(author.id)`
   - Creates `UserBook` records with ratings/reviews via `update_or_create(user=user, book=book)`
   - Assigns reader type via scoring system (`Counter`-based, checks genres, page lengths, publication years, mainstream status)
   - Calculates stats: top genres/authors, ratings distribution, controversial books, sentiment analysis (VADER), yearly stats, mainstream score
   - **Vibe caching**: Compares `new_data_hash` against `profile.vibe_data_hash` — if unchanged, reuses cached `profile.reading_vibe`; if changed, calls `generate_vibe_with_llm(dna)` (Gemini API)
   - Calculates top 5 books via `calculate_and_store_top_books(user)`
   - Saves via `_save_dna_to_profile()` which clears `pending_dna_task_id`, clears old `recommendations_data`, and triggers `generate_recommendations_task.delay(user.id)`
   - Returns string `"DNA saved for user {id}"` (not the DNA dict)
4. **Recommendations** (`generate_recommendations_task`): Runs asynchronously after DNA save → `RecommendationEngine.get_recommendations_for_user()` → processes results into serializable dicts (book_id, title, author, confidence, sources, explanation_components) → stores in `profile.recommendations_data` (JSONField) → invalidates Redis cache
5. **Dashboard** (`display_dna_view`): Loads `profile.dna_data` + `profile.recommendations_data` → transforms stored recommendations to template-expected format (nested `rec["book"]` dict) → adds badge classes for display → renders `dashboard.html`
6. **Re-upload**: Same flow — clears session data, sets new `pending_dna_task_id`, old DNA remains visible until new one replaces it

### Anonymous User Flow
1. **Upload** (`upload_view`): User POSTs CSV → `generate_reading_dna_task.delay(csv_content, None, session_key)` → `session["anonymous_task_id"] = task_id` → redirect to `/task/<task_id>/`
2. **Processing** (`task_status_view` + `get_task_result_view`): Renders `task_status.html` (polling page) → frontend polls `get_task_result_view` via AJAX → checks Redis cache first (`dna_result_{task_id}`), then falls back to `AsyncResult` → returns PENDING/PROGRESS/SUCCESS/FAILURE
3. **DNA Generation** (`generate_reading_dna_task` → `calculate_full_dna`):
   - Same CSV parsing and book syncing as authenticated flow
   - **No `UserBook` records created** — anonymous users don't have user FK
   - Vibe always regenerated (no hash cache — anonymous users have no profile)
   - Calls `save_anonymous_session_data()` → creates `AnonymousUserSession` with: dna_data, books_data (book IDs), top_books_data (top 5 IDs by rating+sentiment), genre_distribution, author_distribution, book_ratings (for correlation), expires_at (+7 days)
   - Caches result: `safe_cache_set(f"dna_result_{task_id}", result_data, timeout=3600)` + `safe_cache_set(f"session_key_{task_id}", session_key, timeout=3600)`
   - Returns the full DNA dict (not a string)
4. **Result retrieval** (`get_task_result_view`): On SUCCESS → stores DNA in `request.session["dna_data"]` + stores `book_ids`, `top_book_ids`, `book_ratings` from `AnonymousUserSession` into session → returns `{"status": "SUCCESS", "redirect_url": "/dashboard/"}`
5. **Dashboard** (`display_dna_view`): Reads `request.session["dna_data"]` → generates recommendations on-the-fly via `get_recommendations_for_anonymous(session_key)` (not pre-stored) → if `AnonymousUserSession` expired but session data exists, recreates it from dna_data → renders `dashboard.html`
6. **Claiming DNA on signup** (`signup_view`): If `task_id` in query params → user signs up → `claim_anonymous_dna_task.delay(user.id, task_id)` → task checks Redis cache for `dna_result_{task_id}` → saves to UserProfile via `_save_dna_to_profile()` → creates `UserBook` records from `AnonymousUserSession` → sets top books. If task not ready yet, retries with 10s countdown (max 5 retries)
7. **Session data on login** (`login_view`): If `dna_data` in session → pops it and saves to profile via `_save_dna_to_profile()` (only if user has no existing DNA)

### Key Differences Between Flows
| Aspect | Authenticated | Anonymous |
|---|---|---|
| **DNA storage** | `UserProfile.dna_data` (persistent) | `request.session["dna_data"]` + `AnonymousUserSession` (7-day expiry) |
| **Book records** | `UserBook` rows created | No UserBook — book IDs stored in `AnonymousUserSession.books_data` |
| **Progress polling** | `check_dna_status_view` (checks `pending_dna_task_id` on profile) | `get_task_result_view` (checks Redis cache then `AsyncResult`) |
| **Recommendations** | Pre-generated async, stored in `profile.recommendations_data` | Generated on-the-fly in `display_dna_view` |
| **Vibe caching** | Hash-based — reuses if library unchanged | Always regenerated |
| **Task return value** | String `"DNA saved for user {id}"` | Full DNA dict (cached in Redis) |

## Celery Task Details (`core/tasks.py`)

### Task Registry
| Task | Decorator | Retry | Purpose |
|---|---|---|---|
| `generate_reading_dna_task` | `@shared_task(bind=True)` | No auto-retry (raises on failure) | Main DNA pipeline with `progress_cb` for UI updates |
| `claim_anonymous_dna_task` | `@shared_task(bind=True, max_retries=5)` | 10s countdown per retry | Transfer anonymous DNA to new user account |
| `generate_recommendations_task` | `@shared_task(bind=True, max_retries=3)` | Exponential backoff: `60 * 2^retries` (60s, 120s, 240s) | Generate and store recommendations after DNA creation |
| `check_author_mainstream_status_task` | `@shared_task` | No retry (raises on failure) | Check author via Open Library + Wikipedia APIs |
| `anonymize_expired_sessions_task` | `@shared_task` | No retry | Celery Beat: daily at 2:00 AM UTC, converts expired sessions to `AnonymizedReadingProfile` |

### Progress Tracking
`generate_reading_dna_task` reports progress via `self.update_state(state="PROGRESS", meta={"current": N, "total": M, "stage": "description"})`. Stages: "Parsing your library" → "Syncing books" → "Crunching stats" → "Finishing up". Frontend polls and displays percent = `round((current * 100) / total)`.

### Task Chain: DNA → Recommendations
```
upload_view → generate_reading_dna_task.delay()
                  └→ calculate_full_dna()
                       └→ _save_dna_to_profile()
                            ├→ profile.save() (clears recommendations_data)
                            └→ generate_recommendations_task.delay(user.id)
                                 └→ get_recommendations_for_user()
                                      └→ find_similar_users()
                                      └→ Score, rank, filter, explain
                                      └→ profile.recommendations_data = processed_recs
```

### Task Chain: Anonymous Upload → Signup → Claim
```
upload_view → generate_reading_dna_task.delay(csv, None, session_key)
                  └→ calculate_full_dna() → returns DNA dict
                  └→ safe_cache_set("dna_result_{task_id}", dna, 3600)
                  └→ safe_cache_set("session_key_{task_id}", session_key, 3600)
                  └→ save_anonymous_session_data() → AnonymousUserSession

signup_view → claim_anonymous_dna_task.delay(user.id, task_id)
                  └→ safe_cache_get("dna_result_{task_id}")
                  ├→ Cache hit: _save_dna_to_profile() + _create_userbooks_from_anonymous_session()
                  └→ Cache miss: AsyncResult(task_id).get() → _save_dna_to_profile()
                  └→ If task not ready: self.retry(countdown=10)
```

## Caching Architecture

### Redis Cache (django.core.cache)
| Cache Key Pattern | TTL | Set By | Read By |
|---|---|---|---|
| `dna_result_{task_id}` | 3600s (1 hr) | `generate_reading_dna_task` (anonymous only) | `get_task_result_view`, `claim_anonymous_dna_task` |
| `session_key_{task_id}` | 3600s (1 hr) | `generate_reading_dna_task` (anonymous only) | `claim_anonymous_dna_task` |
| `user_recommendations_{user_id}_{limit}` | 900s (15 min) | `RecommendationEngine` | `RecommendationEngine` (check before computing) |
| `similar_users_{user_id}_{top_n}_{min_similarity}` | 1800s (30 min) | `find_similar_users()` | `find_similar_users()` |
| `anon_profiles_sample_{user_id}` | 3600s (1 hr) | `RecommendationEngine` | `RecommendationEngine` (100-profile sample) |
| `public_users_for_recs_sample` | 1800s (30 min) | `RecommendationEngine` | `RecommendationEngine` |

### Database-Level Caching
| Field | Location | Invalidation |
|---|---|---|
| `dna_data` | `UserProfile` (JSONField) | Overwritten on re-upload |
| `reading_vibe` + `vibe_data_hash` | `UserProfile` | Hash comparison — regenerated only when book fingerprint changes |
| `recommendations_data` + `recommendations_generated_at` | `UserProfile` | Cleared by `_save_dna_to_profile()`, regenerated by `generate_recommendations_task` |
| `dna_data` + distributions | `AnonymousUserSession` | Expires after 7 days, then anonymized by `anonymize_expired_sessions_task` |

### Graceful Redis Failure
All Redis operations go through `safe_cache_get(key, default=None)` / `safe_cache_set(key, value, timeout)` in `recommendation_service.py`. These catch any `Exception`, log a warning, track the error via `track_redis_cache_error()` in PostHog, and return the default value. The app continues functioning without cache — queries just run slower.

### DNA Data Structure
The DNA dict (stored in `UserProfile.dna_data` JSONField) contains:
```python
{
    "user_stats": {"total_books_read", "total_pages_read", "avg_book_length", "avg_publish_year"},
    "bibliotype_percentiles": {...},           # Percentile ranks vs community
    "global_averages": {...},                  # From GLOBAL_AVERAGES constant
    "most_niche_book": {"title", "author", "read_count"},
    "reader_type": str,                        # e.g., "Fantasy Fanatic", "Tome Tussler"
    "reader_type_explanation": str,
    "top_reader_types": [{"type", "score"}],   # Top 3
    "reader_type_scores": {type: score},
    "top_genres": [(genre, count), ...],       # Top 10, canonicalized
    "top_authors": [(author, count), ...],     # Top 10
    "average_rating_overall": float,
    "ratings_distribution": {"1": N, "2": N, ...},
    "top_controversial_books": [{"Title", "Author", "my_rating", "average_rating", "rating_difference"}],
    "most_positive_review": {"Title", "Author", "my_review", "sentiment"},
    "most_negative_review": {"Title", "Author", "my_review", "sentiment"},
    "stats_by_year": [{"year", "count", "avg_rating"}],
    "mainstream_score_percent": int,           # 0-100
    "reading_vibe": [str, str, str, str],      # 4 LLM-generated phrases
    "vibe_data_hash": str,                     # SHA256 for cache invalidation
}
```

## Analytics (PostHog)
- Events use snake_case: `file_upload_started`, `dna_generation_completed`, `user_signed_up`, `recommendations_generated`
- Distinct IDs: `str(user.id)` for authenticated, `session.session_key` for anonymous, `"system"` for background tasks
- All events include `environment` property ("production" / "development")
- Error messages sanitized: truncated to 500 chars, API keys/passwords stripped via regex
- Two custom middleware classes: `PostHogPageviewMiddleware` (tracks `$pageview`, excludes /admin/, /static/, /api/, /silk/) and `PostHogExceptionMiddleware` (production-only exception tracking)

## Constants
`core/dna_constants.py` (~40KB) contains `READER_TYPE_DESCRIPTIONS`, `CANONICAL_GENRE_MAP`, `EXCLUDED_GENRES`, `MAINSTREAM_PUBLISHERS_HIERARCHY`, and `GLOBAL_AVERAGES`. Reference this when working with DNA analysis or genre logic.

## Environment Variables

Required:
- `SECRET_KEY`, `GEMINI_API_KEY`
- `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` (or `DATABASE_URL`)

Optional:
- `REDIS_CACHE_URL` (default: `redis://localhost:6379/1`), `CELERY_BROKER_URL` (default: `redis://localhost:6379/0`)
- `POSTHOG_API_KEY`, `NYT_API_KEY`, `GOOGLE_BOOKS_API_KEY`
- `DEBUG` (defaults True in dev), `ALLOWED_HOSTS`, `DJANGO_ENV` ("production" or "development")

## CI/CD

- **Tests:** GitHub Actions on push to `main` and PRs — builds Docker containers, runs `poetry run python manage.py test` inside container
- **Deploy:** On push to `main` — builds and pushes Docker image to Docker Hub (tagged with commit SHA), then SSH deploys to DigitalOcean VPS with `docker compose -f docker-compose.prod.yml up -d --force-recreate`
- **Production stack:** Nginx reverse proxy (SSL via Certbot) → Gunicorn (via `poetry run`) → Django, with separate Celery worker container
- **No pre-commit hooks configured**
