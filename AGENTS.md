# Bibliotype — Agents Guide

Durable, project-wide invariants. This file is mutated in place; old entries
are corrected, not appended. For chronological learnings, see `progress.txt`.

## Project conventions

(Mirrors `CLAUDE.md` baselines — repeated here so agents reading only this
file still pick them up.)

- **Function-based views only.** No class-based views, with the single
  documented exception of `CustomPasswordResetView` (subclasses Django's
  `PasswordResetView`).
- **f-strings only.** No `.format()` or `%` formatting in production code.
- **All Redis goes through `safe_cache_get / safe_cache_set / safe_cache_delete`**
  in `core/cache_utils.py`. No bare `cache.get/set/delete` calls.
- **Email-based auth (case-insensitive).** Not username-based.
- **Services query the ORM directly.** No dependency injection. No service
  base class.
- **`poetry add ...`** for dependencies. Never `pip install`.

## Settings invariants

(Populated by Phase 1, 3, 4, 7 stories. Update this section when those land.)

- `DEBUG`: defaults to `False` (US-004). Production raises
  `ImproperlyConfigured` if `DEBUG=True` is set with `DJANGO_ENV=production`.
- `ENFORCE_TASK_OWNERSHIP`: kill-switch on the Phase-1 hijack fix (US-002).
  Default `False`. Production must flip to `True` no later than 1 hour after
  US-001's deploy.
- `ENABLE_PARALLEL_ENRICHMENT`: feature flag for US-027. Default `False`.
  Operator flips to `True` only after confirming Open Library + Google Books
  rate-limit headroom.
- `GEMINI_MODEL`: env-var-overridable model id (US-037). Default `gemini-2.5-flash`.

## Redis key registry

(Populated by US-001 and US-024. Convention: `<purpose>_<scope_id>` in
snake_case.)

- `task_owner_<task_id>` — owner session_key for an anon DNA task; written at
  upload (US-001), read by `get_task_result_view` (US-002) and
  `claim_anonymous_dna_task` (US-003); TTL 3600s.
- `dna_result_<task_id>` — pre-existing; cached DNA payload. TTL 3600s.
- `session_key_<task_id>` — pre-existing.
- `recs_<user_id>` — pre-existing; cached recommendations payload.
- `recs_dispatching_<user_id>` — sentinel preventing duplicate recommendation
  task dispatches from polling dashboard renders (US-024). TTL 300s.
- `public_users_for_recs_sample` — pre-existing; 30-min cache of candidate
  pool for anonymous recommendations.

## Service-layer rules

(Populated by Phase 8 splits.)

- All services live under `core/services/` (after US-043).
- DNA logic lives under `core/services/dna/` (after US-050–US-055).
- Tasks live under `core/tasks/` (after US-056–US-059).

## How to update this file vs `progress.txt`

`AGENTS.md` is for **durable, project-wide invariants** — settings keys,
Redis key formats, code conventions, layering rules. It is mutated in place:
old entries are corrected, not appended. New stories that establish a new
invariant MUST update the relevant section here.

`progress.txt` is an **append-only chronological learning log**. Each line:
`YYYY-MM-DD: <one-sentence learning>`. Use it for: surprising findings during
a story, prerequisites that must be satisfied for a future story (e.g.
`regenerate_dna run on $DATE — US-033 unblocked`), follow-up items, and
things that are time-bounded (e.g. `30-day shim, remove after $DATE`). Never
edit existing progress.txt lines — only append.
