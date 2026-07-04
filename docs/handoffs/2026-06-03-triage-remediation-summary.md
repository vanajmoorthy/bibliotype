---
title: Bibliotype triage remediation — session summary
date: 2026-06-03
branches:
  - triage/codebase-fixes (active, 11 commits ahead of main)
  - main (merged Phases 1–5)
prd: tasks/prd-codebase-triage-fixes.md
scripts: scripts/ralph/prd.json, scripts/ralph/RALPH_PROMPT.md
status: mid-Phase-7
---

# Bibliotype triage remediation — where we are

## The project

Bibliotype (`bibliotype.app`, on a DigitalOcean VPS) is a Django app that ingests a Goodreads/StoryGraph CSV, computes a "Reading DNA", enriches book/author data via Open Library + Google Books, generates Gemini-authored "reading vibes" and reader-similar recommendations, and shows a neobrutalist dashboard. Stack: Django 5.2 + Postgres 15 + Redis 7 + Celery 5.5 + Tailwind 4 + Alpine.js + Chart.js. Deploys via GitHub Actions → Docker Hub → SSH `docker compose up -d --force-recreate` (both `web` and `worker` restart together).

Full architecture write-up: **`docs/ARCHITECTURE.md`** (long, comprehensive — read this first).

Read next:
- **`CLAUDE.md`** — project baseline (Docker dev stack, testing patterns, code formatting)
- **`AGENTS.md`** — durable conventions: function-based views only, `safe_cache_*` wrappers, f-strings only, email auth, settings invariants, Redis key registry
- **`.claude/rules/`** — path-scoped deep-dive rules: `caching.md`, `celery-tasks.md`, `models.md`, `posthog-analytics.md`, `ui-and-styling.md`, `user-flows.md`
- **`docs/SCALING.md`** + **`docs/scaling-implementation-plan.md`** — capacity per VPS tier + concrete changes
- **`README.md`** — user-facing overview

## The triage effort — this session's work

A parallel multi-agent review of the codebase in early May flagged debt across six dimensions: 3 Critical security issues, high-impact performance footguns, ~200+ LOC of dead code, ~150 LOC of pure-deletion simplifications, architectural drift, and pattern drift. The full remediation was captured as **69 user stories in 8 phases** in `tasks/prd-codebase-triage-fixes.md`. Ralph (autonomous single-story-per-iteration loop, see `scripts/ralph/RALPH_PROMPT.md`) drove each iteration.

### Shipped (merged into `main`)

| PR | Title | Phase | Stories |
|---|---|---|---|
| #106 | Phase 1: critical security — bind anon DNA tasks, fix DEBUG default, lock task whitelist | Phase 1 | US-001..US-005 |
| #107 | Phase 2+3: dead-code sweep + HSTS, login + username rate-limits, signup email-enumeration defence, CSV schema cap, `\|safe` removal | Phase 2 + Phase 3 | US-006..US-020, + US-021 which snuck in |
| #108 | Phase 4+5: perf + DB indexes + `dna_analyser` dedup | Phase 4 + Phase 5 + US-030 | US-022..US-030 (excl. US-030 which was a Phase-6 story) |

**Phases 1–5 fully done; US-021 and US-030 landed in earlier PRs by accident.**

### In-flight — `triage/codebase-fixes` (11 commits ahead of `main`)

Ralph is between iterations. Landed but not yet PR'd:

- `0e78d61` — chore: mark `regenerate_dna` run in production (unblocks US-033)
- `90a15ef` — **US-031a**: `RecommendationEngine` methods → module-level functions
- `df85146` — **US-031b**: remove unused recommendation parameters
- `5be4830` — **US-032**: bake `rec["book"]` dict into stored recommendations (dual-shape compat)
- `84aafe3` — **US-033**: delete legacy DNA-data backfill block (needed prod `regenerate_dna` run first)
- `2a5559c` — **US-035**: collapse duplicated cache-hit / result-hit branches in `claim_anonymous_dna_task`
- `c343bfb` — **US-035b**: dedupe `_canonicalize_google_books_categories` with `_clean_and_canonicalize_genres`
- `7224167` — **US-036**: `datetime.utcnow()` → `timezone.now()` in `services/author_service.py`
- `0fbb9c7` — **US-037**: reconcile Gemini model versions (single source of truth via `core/services/_gemini.py` + `GEMINI_MODEL` env var; default `gemini-2.5-flash`)
- `2e3339d` — **US-038**: centralize Open Library cover URL helper (new `core/services/_book_urls.py`)
- `2931b14` — **US-039**: hoist `genre_priority` list into `dna_constants.py`

Pass count in `scripts/ralph/prd.json`: **46 / 69** stories done.

### Not yet done — **23 stories remaining** across Phases 6, 7, 8

**Phase 6 tail (Simplifications) — 1 story:**
- **US-040** — Reconcile top-book scoring formulas (auth vs anonymous)

**Phase 7 (Pattern normalization) — 2 stories:**
- **US-041** — Promote magic numbers to named constants
- **US-042** — Logger severity drift + missing `exc_info=True`

**Phase 8 (Architecture refactors — file splits) — 18 stories, largest phase:**
- US-043: Move `core/book_enrichment_service.py` → `core/services/`
- US-044: Create `core/views/` package skeleton
- US-044b: Extract `_compute_enrichment_*` + `_enrich_dna_for_display` → `core/views/_helpers.py`
- US-045 → US-049: Split `core/views.py` (1296 LOC) into per-domain modules (`seo.py`, `auth.py`, `upload.py`, `dashboard.py`, `pages.py`, `profile.py`)
- US-050 → US-055: Split `core/services/dna_analyser.py` (1129 LOC) into `core/services/dna/` package (skeleton + `csv_parser.py` + `book_sync.py` + `reader_type.py` + `persistence.py`, then drop the shim)
- US-056 → US-059: Split `core/tasks.py` into `core/tasks/` package (skeleton + `dna.py` + `enrichment.py` + `recommendations.py` + `maintenance.py`)
- US-060: Drop the `core/services/recommendation_service.py` `safe_cache_*` re-export shim

**Phase 8 hard rules** (`tasks/prd-codebase-triage-fixes.md:50`):
- Every file-move story MUST be revertible with a single `git revert`.
- File moves and content edits must NOT be combined in the same commit — split them.

### Next PR cut

Per PRD §12, the next boundary is after **US-042** (title suggestion: `chore + refactor: simplifications + pattern normalization`). ~3 stories: US-040, US-041, US-042. Then Phase 8's 18 stories cut PR-by-PR.

## How to continue

### Resume Ralph after a merge

The recipe (also in `scripts/handoff/HANDOFF.md`):
```bash
cd /Users/vanajmoorthy/Desktop/github/bibliotype
git checkout main && git pull
git branch -D triage/codebase-fixes   # purge stale local
git checkout -b triage/codebase-fixes # recreate off main
docker-compose -f docker-compose.local.yml up -d
./scripts/ralph/ralph.sh --tool claude <N>
```

Ralph's iteration budget: 10–15 for a phase; small chunks work fine. Prompt at `scripts/ralph/RALPH_PROMPT.md`.

### Known gotchas (learned this session — several caused pain)

1. **`black` / `isort` are NOT installed** in the venv or Docker image. Match style by hand.
2. **Docker MUST be up locally** or Ralph's tests fall back to SQLite and hit the pre-existing `test_round_trip_dedup_goodreads_then_storygraph` "database table is locked" flake, and Ralph's polling shell hangs forever on the missing `OK`/`FAILED` summary line.
3. **Deploy race on multi-container migrations**: web and worker both run `manage.py migrate` from their entrypoints. Web usually wins; worker crashes with a duplicate-index error and stays dead because there's no `restart:` policy on it. Fix: on prod, `docker compose -f docker-compose.prod.yml up -d worker` — migrations are already applied so it boots cleanly. This bit us on the Phase 4+5 deploy. Worth a small follow-up PR: gate migrations to web only, OR add `restart: unless-stopped` on worker.
4. **Rate limiting (`django-ratelimit`) needs real client IP**: `key="ip"` reads `REMOTE_ADDR`, which behind Nginx is the proxy IP. Fixed in `core/ratelimit_utils.py` (`get_real_client_ip` prefers `X-Real-IP` — Nginx sets it to `$remote_addr`, unspoofable). Turnstile has the same requirement — both call sites now use the helper.
5. **Session keys are bearer credentials** — never log raw, always `hashlib.sha256(key.encode()).hexdigest()[:12]`. Established pattern in `core/tasks.py:claim_anonymous_dna_task`.
6. **HSTS preload is live** (`bibliotype/settings.py:213`) — 12-month browser commitment on `bibliotype.app`. Any HTTPS/cert misconfig breaks the domain until fixed.
7. **`_save_dna_to_profile` doesn't set the US-024 dispatch sentinel** before calling `.delay()` — a dashboard poll landing in the ms window before the rec task pickup can double-dispatch. Bounded to 2, idempotent. Documented in PR #108. Worth a follow-up story.
8. **US-017 enumeration reduced but not eliminated**. Signup redirects differ (fresh → `/` with `sessionid`; duplicate → `password_reset_done` with none). Fixing fully means an email-verification flow — deferred as user-facing feature work.
9. **`ENFORCE_TASK_OWNERSHIP` was removed in Phase 1 hardening.** Any external runbook / .env still setting it is inert but harmless.
10. **Prod migration gotcha:** `docker compose exec db psql -U $POSTGRES_USER ...` doesn't expand `$POSTGRES_USER` because the var lives inside the container, not the host shell. Use `docker compose exec db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "..."'`.

### Open follow-up work outside the PRD

**Live enrichment updates** — sibling worktree at `/Users/vanajmoorthy/Desktop/github/bibliotype-plan-live-enrichment` (branch `plan/live-enrichment-updates`). Contains ONE commit adding **`docs/live-enrichment-updates-plan.md`** (85 lines) — a fully-drafted 2-PR plan for making dashboard tiles update reactively while enrichment is in flight (instead of frozen until page reload). Currently only 3 stats swap live. Plan uses `Alpine.store('enrichment')` + chart `update()` calls (not partial re-render — the doc explains why). PR1 handles fields without CSV context (~150 lines), PR2 tackles reader-type recompute. **Not yet PR'd — worth reading before starting Phase 8 refactors that touch views.**

**Related earlier plan**: **`docs/plans/enrichment-ux-improvements.md`** — this is the predecessor; the "Completed" checkbox list is real. The "Outstanding" section is what `docs/live-enrichment-updates-plan.md` supersedes/expands.

**Earlier enrichment plan**: **`docs/plans/2026-04-06-feat-enrichment-ux-and-performance-plan.md`** — historical, shipped.

**Handoff for StoryGraph PR #98** (already merged): **`docs/handoffs/2026-04-30-storygraph-pr-handoff.md`** — good reference for how StoryGraph CSVs flow through the pipeline (normalized to Goodreads schema before analysis).

**Open PR #97** (`feat/settings-modal`) — settings modal for account management, opened 2026-03-31, mergeStateStatus CLEAN but sitting untouched. Not part of triage. If you touch views (Phase 8 splits), this PR will conflict.

**Old worktrees** — `.claude/worktrees/improve-genres-fic-nonfic-split/` (last touched 2026-03-31, ahead-of-main only via already-merged work). Ignore. Two dot-dir siblings (`agitated-colden`, `friendly-wing`) are old triage scratch dirs.

**Large branch backlog** (`git branch` output shows ~40 local branches from historical feature work). Assume dead unless proven otherwise; most correspond to already-merged PRs.

## Test invocation

```bash
docker-compose -f docker-compose.local.yml exec -T web poetry run python manage.py test -v 0
```

Current baseline on `triage/codebase-fixes`: 388+ tests (Ralph adds per-story tests). CI runs in Docker via `.github/workflows/django-tests.yml`.

## Deploy invocation

```bash
# on prod (only if migrations from a specific commit already applied on web,
# worker died on duplicate-index race):
docker compose -f docker-compose.prod.yml up -d worker
```

Standard: merge to main → GitHub Actions → Docker Hub → SSH deploy → `docker compose up -d --force-recreate` recreates both services on the new image.
