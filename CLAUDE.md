# CLAUDE.md

Bibliotype is a Django app that analyzes Goodreads/StoryGraph CSV exports to generate "Reading DNA" dashboards with AI insights. Neobrutalist design (bold borders, offset shadows, VT323 font).

## Quick Start (Docker — recommended)

Local development uses `docker-compose.local.yml`, which runs all 4 services (PostgreSQL, Redis, Django, Celery worker) and mounts source code for live reload.

```bash
docker-compose -f docker-compose.local.yml up --build -d      # Build and start everything
docker-compose -f docker-compose.local.yml exec web poetry run python manage.py migrate
docker-compose -f docker-compose.local.yml exec web poetry run python manage.py loaddata core/fixtures/initial_data.json
docker-compose -f docker-compose.local.yml exec web poetry run python manage.py createsuperuser
# App at http://localhost:8000

docker-compose -f docker-compose.local.yml logs -f web         # Tail logs
docker-compose -f docker-compose.local.yml exec web poetry run python manage.py test  # Run tests
docker-compose -f docker-compose.local.yml down                # Stop
docker-compose -f docker-compose.local.yml down -v             # Stop and reset DB
```

### Without Docker (alternative)

Requires local PostgreSQL and Redis.

```bash
poetry install && pnpm install
poetry run python manage.py migrate
honcho start                    # Runs Django + Tailwind watcher via Procfile
```

## Package Management

**Poetry only** — never `pip install`. All commands via `poetry run` unless virtualenv is activated.

```bash
poetry install                          # Install deps
poetry add <pkg>                        # Add dependency
poetry add --group dev <pkg>            # Dev dependency
poetry run python manage.py <cmd>       # Run Django commands
```

## Testing

```bash
poetry run python manage.py test                                                     # All tests
poetry run python manage.py test core.tests.test_views_e2e                           # Module
poetry run python manage.py test core.tests.test_views_e2e.TestClassName.test_method # Single test
```

Key patterns:
- `django.test.TestCase` for most tests, `TransactionTestCase` for Celery task tests
- **Always mock external services** (Gemini, Open Library, Google Books) with `@patch`
- Celery sync in tests: `@override_settings(CELERY_TASK_ALWAYS_EAGER=True, CELERY_TASK_EAGER_PROPAGATES=True)`
- In-memory cache: `@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})`
- Test data created directly with ORM in `setUp()` — no factory libraries
- Custom runner `ForceDisconnectTestRunner` handles PostgreSQL connection cleanup

## Tailwind CSS Build

`static/dist/output.css` is a compiled artifact checked into git. After any Tailwind/CSS changes (including changes to HTML templates that add new utility classes), you must rebuild and commit the output:

```bash
pnpm run build          # Compiles static/src/input.css → static/dist/output.css (minified)
```

If Tailwind changes aren't showing up locally, make sure either `pnpm run dev` (watch mode) is running or run `pnpm run build` manually.

## Code Formatting

```bash
black --line-length 120 .
isort --profile black --line-length 120 .
npx prettier --write "**/*.html"
```

Config is in `pyproject.toml` (Black, isort) and `.prettierrc` (Prettier with jinja-template + tailwindcss plugins).

## Architecture

```
CSV Upload → Celery Task (async) → DNA Analysis → Book/Author DB Sync
→ API Enrichment (Open Library, Google Books) → AI Vibe (Gemini)
→ Results in UserProfile/AnonymousUserSession → Recommendations (async)
```

**Key directories:**
- `bibliotype/` — Django project config
- `core/services/` — Business logic (9 modules). Entry point: `dna_analyser.py:calculate_full_dna()`
- `core/analytics/` — PostHog event tracking
- `core/templates/core/partials/` — Reusable template components
- `core/dna_constants.py` — Reader types, genre mappings, publisher hierarchies (~40KB)

**Non-obvious patterns:**
- All views are function-based (no CBVs)
- Authentication is email-based (case-insensitive), not username-based
- Authenticated users get persistent `UserProfile` storage; anonymous users get session-based `AnonymousUserSession` (7-day expiry) that can be claimed on signup
- Services query the ORM directly — no dependency injection
- f-strings exclusively — no `.format()` or `%` formatting
- All Redis operations go through `safe_cache_get/set/delete()` in `core/cache_utils.py` for graceful degradation

## Deep-Dive Rules (`.claude/rules/`)

These load automatically when you work on matching files:

- `ui-and-styling.md` — Neobrutalist design system, Tailwind @theme, component patterns, Alpine.js, Chart.js
- `caching.md` — safe_cache wrappers, Redis key registry, invalidation strategy, graceful degradation
- `celery-tasks.md` — Task registry, retry/backoff, progress tracking, task chains, testing
- `user-flows.md` — Auth vs anonymous uploads, DNA claim on signup, session data, CAPTCHA, polling
- `posthog-analytics.md` — How to add events, naming conventions, middleware, event registry
- `models.md` — Normalization, signals, JSONField schemas, constraints, singleton pattern

## Tech Stack

- **Backend:** Django 5.2, Python 3.13+, Celery 5.5, Redis 7, PostgreSQL 15
- **Frontend:** Tailwind CSS 4 (custom @theme in `static/src/input.css`), Alpine.js 3, Chart.js (both CDN)
- **AI:** Gemini 2.5 Flash via `google-generativeai`
- **Analytics:** PostHog (EU instance)

## Environment Variables

Required: `SECRET_KEY`, `GEMINI_API_KEY`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` (or `DATABASE_URL`)

Optional: `REDIS_CACHE_URL`, `CELERY_BROKER_URL`, `POSTHOG_API_KEY`, `GOOGLE_BOOKS_API_KEY`, `NYT_API_KEY`, `DEBUG`, `ALLOWED_HOSTS`, `DJANGO_ENV`

See `.env.example` for the full list including email and Turnstile CAPTCHA config.

## CI/CD

- **Tests:** GitHub Actions on push to `main` and PRs — runs in Docker
- **Deploy:** Push to `main` → Docker Hub image → SSH deploy to DigitalOcean VPS
- **Production:** Nginx (SSL/Certbot) → Gunicorn → Django + Celery worker
- No pre-commit hooks configured

## Docker

4 containers: `db` (PostgreSQL 15), `redis` (Redis 7), `web` (Django), `worker` (Celery).

```bash
# Local
docker-compose -f docker-compose.local.yml up --build -d
docker-compose -f docker-compose.local.yml down -v          # Reset DB

# Production
docker compose -f docker-compose.prod.yml up -d --force-recreate
```

Local mounts source code for live reload. Production uses Gunicorn with staticfiles served by Nginx. Both share the same `.env` file.
