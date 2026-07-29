---
title: Bibliotype improvements queue — handoff (remaining PRs 12–18)
date: 2026-07-22
branch_base: main
orchestration_plan: docs/plans/2026-07-04-triage-completion-and-improvements-plan.md
---

# Handoff: finish the improvements queue

## Where we are

The **entire triage-remediation PRD is done and deployed** (69 stories, PRs #106–#118, incl. the `core/views/`, `core/services/dna/`, `core/tasks/` package splits). On top of that, these improvement PRs have **merged**: button press-in animation (#110) + hover half-press (#119), auth password toggles + forgot-link spacing (#112), deploy migration-race / US-024 sentinel / session-key log hygiene (#111), flaky rate-limit fix (#116), **genre accuracy overhaul parts 1 & 2** (#120, #121), and `docs/GENRES.md` (#122). `main` is green.

## What's left (in order)

Full detail per item is in `docs/plans/2026-07-04-triage-completion-and-improvements-plan.md` (waves 4–5) and the two feature plan docs noted below.

1. **PR 12 — live enrichment updates, part 1** (branch `feat/live-enrichment-updates` exists but is CLEAN — a prior agent was killed by a session limit before committing anything; start fresh). Spec: `docs/plans/live-enrichment-updates-plan.md`, **PR1 section only**. Make dashboard tiles/charts (top genres list+donut, fiction/nonfiction numbers+pie, book extremes) update live during enrichment instead of freezing until the completion reload. Rebase notes: `enrichment_status_view` is in `core/views/upload.py:214`; `_compute_enrichment_stats` is in `core/views/_helpers.py:20`; the fiction/nonfiction split dict now has a third key `defaulted_count` (via `count_fiction_nonfiction` in `core/services/genre_classification.py`) — include it in the payload and keep it out of the pie; there's a live `genre_coverage_pct` + "Based on X of Y books" subtitle on `fiction_nonfiction_card.html` to coordinate with. Approach: `Alpine.store('enrichment')` + a window event; charts call `chart.update()` (no teardown). Also check the "book covers only appear after refresh on DNA generation" todo while here. Keep the 5s→12s polling cadence and completion reload.
2. **PR 13 — reader-type overhaul. GATED: needs Vanaj's approval of the design doc first.** The design doc is written: `docs/plans/2026-07-04-feat-reader-type-overhaul-plan.md`. Do NOT implement until Vanaj signs off (he explicitly parked this for review). It normalizes per-type scoring to 0–100, kills the Rapacious short-circuit, adds ~6 new types, gives each type a brand-palette colour + pixel-checkerboard banner, and adds a distribution validation harness. Has 5 open questions for Vanaj in its final section.
3. **PR 14 — live enrichment part 2**: live reader-type recompute during enrichment (`docs/plans/live-enrichment-updates-plan.md` PR2 section). Sequence AFTER PR 13 so it recomputes the new scoring, not the old.
4. **PR 15 — settings modal**: close the stale scaffold PR #97 (comment pointing here), rebuild fresh into `core/views/profile.py` (or a new `settings.py`). Use #97's body as the reference spec. Neobrutalist modal with toggle switches: privacy, recommendations opt-out (endpoint exists, UI missing), display name, email update, change password, delete account (confirmation-gated). Flip `UserProfile.is_public` default `False`→`True` (+ migration). Full `test_settings.py`.
5. **PR 16 — comparative analytics methodology page**: new page describing how each comparison stat is derived + assumptions; move the 3 hard-coded `[source]` links in `comparative_analytics_card.html` into structured metadata alongside `GLOBAL_AVERAGES` in `dna_constants.py`; card links to the page. Verify the external URLs still resolve.
6. **PR 17 — banner lifecycle + skeleton cohesion**: `x-show`→`x-if` for the enrichment banner (`reader_type_card.html`) and sub-banners (`comparative_analytics_card.html`) so completed banners unmount cleanly; unify the three skeleton templates' copy/design ("Still figuring out…" / "Still discovering…" / "Still fetching…" → one voice).
7. **PR 18 — final browser E2E sweep**: a chrome-devtools MCP subagent manually exercises the whole app (home, CSV upload → DNA → dashboard tiles/charts, login/signup toggles, button hover/press, settings modal, public profile, enrichment banners). Fix + re-verify anything broken. (Vanaj explicitly asked for this manual verification pass at the end.)

## Working conventions (established this run — follow them)

- **One PR at a time, land before starting the next.** Per PR: implement → full Docker test suite green → push → open PR → wait for CI (`gh pr checks <n>`) → **merge with a merge commit (never squash** — the split PRs rely on per-commit revertibility; keep merge-commit as the default for all).
- Tests: `docker-compose -f docker-compose.local.yml exec -T web poetry run python manage.py test -v 0` (Docker must be up; SQLite fallback flakes). Current baseline: **462 tests**.
- Delegating an implementation to a subagent works well BUT: the Docker stack mounts the single working tree, so only ONE agent can own it at a time — keep the pipeline serial. Give subagents the rebase deltas explicitly (plans predate the package splits; line numbers are stale — re-anchor on symbols). Subagents have been killed by session limits mid-run twice; on resume, first `git status`/`git log origin/main..HEAD` to see what (if anything) they persisted before continuing.
- `black`/`isort` are NOT installed — hand-format to 120 cols, f-strings only.
- Plans live in `docs/plans/` (gitignored — local only, don't force-add); handoffs in `docs/handoffs/` (committed). See CLAUDE.md "Plans & Handoffs".
- Button interaction rule (memory + `.claude/rules/ui-and-styling.md`): buttons only ever move DOWN — hover = half-press (translate half the shadow offset, shadow shrinks in step), click = full press. Never lift on z-axis.
- Task tracking via the Task tools; tasks #12–#18 map to PRs 12–18.

## Pending USER (Vanaj) actions — flag these, don't do them yourself

1. **Approve the reader-type design doc** (`docs/plans/2026-07-04-feat-reader-type-overhaul-plan.md`) — gates PR 13. It has 5 open questions for him.
2. **Post-deploy for the genre PRs (already merged/deployed):** run on the VPS in tmux — `docker compose -f docker-compose.prod.yml exec web poetry run python manage.py enrich_books --process-all` — to re-fetch + merge Google Books genres into existing books (`--process-all` resets `google_books_last_checked`). Until then, existing users' genres/splits only refresh on re-upload.
