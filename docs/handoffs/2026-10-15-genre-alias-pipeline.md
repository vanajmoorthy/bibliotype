# Handoff: enrichment backoff and genre alias pipeline (2026-10-15)

## Where things stand

Three stacked PRs, all open, none merged:

| PR | Branch | Base | What |
|----|--------|------|------|
| #230 | `fix/enrichment-backoff-incomplete-books` | `main` | After 3 complete lookups that still leave genres, pages or year missing, retry every 30 days instead of 24h. Migration 0032. CI green. |
| #235 | `feat/record-unmapped-genre-subjects` | #230 | `UnmappedSubject` table records subjects that match no alias. `analyze_genres` rewritten to list them (`--limit`, `--prune`). Migration 0033. |
| #236 | `feat/llm-genre-alias-suggestions` | #235 | Weekly beat task (Sun 04:00) asks the LLM chain for a genre or "ignore" per top-50 unmapped subject. Stored for review, never applied. Migration 0034. |

**Merge in order: #230, #235, #236.** After each squash-merge, retarget the next PR to `main` and rebase it (`git rebase --onto origin/main <old-base-branch>`). CI only runs on PRs into `main`, so #235 and #236 have no CI run yet. They passed 145 targeted tests locally on top of the full stack, and `makemigrations --check` was clean.

## How it got here

- Prod enrichment sat at 96% during a Google Books 503 outage on 2026-10-11. Not a bug: each failing book retries 3 times (60s, 120s, 240s), then gets stamped as attempted so the banner finishes. Those books end up with Open Library data only.
- Google Books is called on every enrichment, not as a fallback. Open Library runs first, Google Books second, and their genres are merged. The free quota is the scaling ceiling, which is why #230 exists.
- Before #235, unmatched subjects were dropped, so the old `analyze_genres` (which audited `Genre` rows) always said "0 unmapped".

## After deploy

1. Run `analyze_genres` from the admin command runner after a few days of uploads to see what's being recorded.
2. From the first Sunday after #236 deploys, suggestions show next to each subject, plus a block grouped by genre.
3. Review them by hand. Add real aliases to `GENRE_ALIASES` and junk to `EXCLUDED_GENRES` in `core/dna_constants.py`, ship that, then run `analyze_genres --prune`.
4. Books already enriched keep their old genres until they're next re-enriched (`enrich_books --process-all` re-checks everything, but watch the Google Books quota).

## Open items

- No way to run the suggestion task on demand. Wrap it in a management command if waiting for Sunday gets annoying.
- `UnmappedSubject` grows by tens of rows per new book (Open Library sends up to ~100 subjects). Fine for now. If it gets large, prune rows with `seen_count=1` older than some age.
- Option not taken: skipping Google Books when Open Library already returns genres, pages and year. It's the biggest quota saving, but genres get noisier. Revisit if the quota starts binding.
- 5xx responses don't trigger the shared Google Books cooldown (only 429 and quota 403s do). During an outage every book burns 4 calls. Worth a shared short pause if outages turn out to be frequent.
