# Handoff — Bibliotype triage cleanup, Phase 1 in review

**Current date:** 2026-05-15 (or whenever you're reading this).
**Project:** Bibliotype at `/Users/vanajmoorthy/Desktop/github/bibliotype`.

---

## Where we are right now

- **PR #106** is open: https://github.com/vanajmoorthy/bibliotype/pull/106
  - Title: "Phase 1: critical security — bind anon DNA tasks, fix DEBUG default, lock task whitelist"
  - Branch: `triage/codebase-fixes` (currently checked out in main working tree).
  - Latest commit: `e80c734 security: fix Phase 1 review blockers + harden ownership checks` (this commit answers the post-Phase-1 security review's 4 blockers + several mediums).
  - Test status: 359 / 359 green (run inside Docker).
- **Two things in flight while context was cleared:**
  1. **User is reviewing the PR themselves** (manual code review of `e80c734` mainly).
  2. **Another LLM session is running a Chrome MCP regression test** using the prompt at `scripts/regression/phase1-chrome-mcp-prompt.md`. Report will land at `scripts/regression/reports/phase1-<timestamp>.md` with a final verdict line `READY / READY_WITH_NOTES / NOT_READY`.

When the user comes back, they will either say:

- **"merged"** → proceed to Phase 2 launch sequence below.
- **"regression report says X"** → triage what the report flags.
- **"review found X"** → fix the findings on the same branch + push.
- **"hit a blocker"** → diagnose and fix.

---

## What Phase 1 actually shipped

The triage report flagged 3 Critical security findings; Phase 1 closes all three:

1. **Anonymous DNA hijack via guessable task_id** — was: any caller knowing a `task_id` could pull another user's DNA into their session and even sign up with it to inherit the library. Fixed by binding `task_owner_<task_id>` in Redis to the visitor's `session_key` at upload time (US-001), and checking that binding on both the result endpoint (US-002) and signup-claim flow (US-003). The post-review commit `e80c734` hardened this so it now fails closed on cache miss AND on mismatch, regardless of any flag.

2. **`DEBUG` defaulting to `True`** — fixed by flipping the env-var default to `False` and adding an `ImproperlyConfigured` startup assert when `DEBUG=True` in a non-dev environment (US-004 + `e80c734`'s whitelist refinement of `development`/`test`/`ci`).

3. **`run_management_command_task` had no in-task whitelist** — fixed by carving `ALLOWED_COMMANDS` into a new `core/management_command_registry.py` module that both `admin.py` and `tasks.py` import (US-005).

**Important detail about `ENFORCE_TASK_OWNERSHIP`:** the env var still exists in `settings.py` but is now functionally a no-op. The post-review commit made fail-closed semantics unconditional, so the flag no longer affects the response. It's retained for deploy-doc compat. Do NOT tell the user they need to wait an hour and flip it — that's stale advice from the original PR description.

---

## Files you'll want to read first

In this order:

1. **`tasks/prd-codebase-triage-fixes.md`** — the full 69-story PRD for the 8-phase remediation. Story IDs `US-001..US-005` are Phase 1 (done). Phase 2 is `US-006..US-014` (dead code + repo hygiene). Section 13 has a one-line summary table for all 69 stories.

2. **`progress.txt`** — append-only chronological log. Top of file has `## Codebase Patterns` (durable findings to honor in future work). Then per-story sections with what was implemented and learnings. Latest entries are US-001..005.

3. **`AGENTS.md`** — durable project conventions (function-based views only, `safe_cache_*` wrappers, f-strings only, email auth, Redis key registry, settings invariants).

4. **`CLAUDE.md`** — project baseline (Docker dev stack, testing patterns, code formatting).

5. **`scripts/ralph/prd.json`** — Ralph's machine-readable work list. `passes: true` already set for US-000, US-001..005, and US-034 (the latter pre-passed because the third-pass PRD review removed it as test-breaking).

6. **`scripts/ralph/RALPH_PROMPT.md`** — the prompt Ralph feeds to each fresh Claude instance per iteration. Includes block-conditions (US-033 blocked until operator confirms `regenerate_dna` ran).

---

## Post-merge action sequence (when user says "merged")

### 1. Confirm prod deploy env

The user must set in production:
```
DEBUG=False           (or unset — default is now False)
DJANGO_ENV=production
```

No need to set `ENFORCE_TASK_OWNERSHIP` (no-op).

### 2. Rebase the working branch and continue

```bash
cd /Users/vanajmoorthy/Desktop/github/bibliotype
git checkout main
git pull
git checkout triage/codebase-fixes
git merge main
docker-compose -f docker-compose.local.yml up -d
docker-compose -f docker-compose.local.yml exec -T web poetry run python manage.py migrate
docker-compose -f docker-compose.local.yml exec -T web poetry run python manage.py test core.tests.test_views_e2e -v 0
```

Last line is the sanity test.

### 3. Launch Phase 2 Ralph

```bash
./scripts/ralph/ralph.sh --tool claude 20
```

20 iterations covers all of Phase 2 (US-006..US-014, ~9 stories — most are small deletions) plus the start of Phase 3 (US-015..US-020 high-severity security: HSTS, rate-limit login, generic signup error, CSV schema cap, replace messages|safe with template, rate-limit username API).

**You (Claude in the new session) CANNOT launch ralph.sh yourself** — the sandbox blocks recursive `claude --dangerously-skip-permissions` invocation as "Create Unsafe Agents". The user must run that line. Tell them exactly:
> Open a new terminal at `/Users/vanajmoorthy/Desktop/github/bibliotype` and run: `./scripts/ralph/ralph.sh --tool claude 20`

### 4. Monitor

Use `ScheduleWakeup` (the dynamic-pacing wakeup tool) to poll every ~25 minutes. Each check should run:
- `git log --oneline triage/codebase-fixes ^main`
- `jq '[.userStories[] | select(.passes==true)] | length' scripts/ralph/prd.json` (Phase 1 baseline = 7, Phase 2 done = ~17, full Phase 2+3 = ~24)
- `pgrep -fl ralph.sh || echo "not running"`
- `tail -80 progress.txt`
- `git status --short` to catch mid-iteration commits

Iterations average 10–30 min. If one runs >60 min idle (no commit, no file mtime updates, claude PID showing 0% CPU `S+`), the agent is stuck — kill ralph.sh, inspect, decide.

### 5. PR cut at Phase 2 boundary

When US-014 lands (pass count ≈ 16), kill the loop and open a PR:

```bash
pkill -f ralph.sh   # only if loop is still running
git push
gh pr create --base main \
  --title "chore: remove dead code, scripts, profiles, personal CSVs, test fixtures" \
  --body "Phase 2 of triage cleanup..."
```

(Subagent-review-then-fix-then-push cycle as Phase 1.)

Then keep going with the remaining iterations for Phase 3.

---

## Phase 3 cut after US-020 (high-severity security)

Same pattern: kill loop, push, open PR titled `security: add HSTS, rate-limit login + username API, generic signup error, CSV schema cap`.

Then Phase 4 (US-021..US-027b, perf), Phase 5 (indexes), Phase 6 (simplifications), Phase 7 (pattern norms), Phase 8 (architecture splits — 18 stories, will need a separate Ralph batch).

PR cut points (from PRD §12):
- US-014 — dead code
- US-020 — high security
- US-027b — perf
- US-029 — db indexes
- US-035b — simplifications
- US-042 — patterns
- US-043+ — per-story for architecture splits

---

## Known gotchas to honor

1. **`black` and `isort` are NOT installed** in this project's Poetry env or Docker image. Don't try to run them. Match existing style by hand. (Captured in `progress.txt`'s Codebase Patterns section.)

2. **The Bibliotype docker stack must be up** for tests to work. `docker-compose -f docker-compose.local.yml exec web poetry run python manage.py test ...` — if Docker isn't up, ask user to start it; don't try to without explicit ask.

3. **Don't touch other worktrees.** Sibling dirs under `.claude/worktrees/` belong to other agent sessions.

4. **The `.claude/worktrees/` and `scripts/ralph/.last-branch`** paths are now gitignored (commit `e80c734`). Don't accidentally commit them.

5. **`scripts/ralph/`, `scripts/regression/`, `tasks/`, `progress.txt`, `AGENTS.md`, `.claude/`** are now in `.dockerignore` (commit `e80c734`). Internal triage scaffolding doesn't ship to production. If you add new triage artifacts, follow the same pattern.

6. **Cache key registry** (from `AGENTS.md`):
   - `task_owner_<task_id>` — TTL 3600s, written by upload_view, read by get_task_result_view + claim_anonymous_dna_task. Convention: `<purpose>_<scope_id>`.
   - `user_recommendations_<user_id>` (NOT `recs_<user_id>` — common mistake)
   - `similar_users_<user_id>`
   - `public_users_for_recs_sample`
   - `dna_result_<task_id>`, `session_key_<task_id>`

7. **Session keys are bearer credentials.** Never log them raw. Always hash with `hashlib.sha256(key.encode()).hexdigest()[:12]`. The Phase 1 review caught a real leak; the pattern is now established.

8. **US-033 is blocked** until `progress.txt` contains a line matching `^\d{4}-\d{2}-\d{2}: regenerate_dna run in production` (someone must run the management command in prod first). Ralph will skip it automatically. When user is ready to unblock, they append the line to `progress.txt` and commit.

9. **Phase 8 (US-043..US-060) won't start until all Phase 0–7 stories are `passes:true`.** Ralph's prompt enforces this. Don't bypass.

10. **Ralph's prompt is at `scripts/ralph/RALPH_PROMPT.md`.** Each iteration is one fresh Claude instance reading that prompt. The instructions tell Ralph to commit one story per iteration, update prd.json, append to progress.txt.

---

## What to do if the Chrome MCP regression test report comes back NOT_READY

1. Read the report at `scripts/regression/reports/phase1-<timestamp>.md` — it'll list which T1..T12 tests failed.
2. Reproduce the failure manually or with `docker-compose ... exec web poetry run python manage.py test <relevant_module>`.
3. If it's a real Phase 1 regression, fix on `triage/codebase-fixes` (don't open a new branch — keep the PR alive).
4. If it's a pre-existing bug not in Phase 1 scope, log to `progress.txt` for triage and move on.

---

## Quick state-check commands

```bash
# Where am I?
git branch --show-current   # should be triage/codebase-fixes
git log --oneline ^main     # ~8 commits ahead of main

# Phase 1 work
git diff main..HEAD -- core/views.py core/tasks.py bibliotype/settings.py | head -50

# Test sanity (if Docker is up)
docker-compose -f docker-compose.local.yml ps
docker-compose -f docker-compose.local.yml exec -T web poetry run python manage.py test core.tests.test_views_e2e -v 0

# Story progress
jq '[.userStories[] | select(.passes==true)] | length' scripts/ralph/prd.json
jq '.userStories | map(select(.passes==false)) | map(.id) | .[0:10]' scripts/ralph/prd.json
```

---

## TL;DR for the first 2 sentences of the new session

> Bibliotype Phase 1 PR (#106) is open and being reviewed by the user + a Chrome MCP regression test in another session. The next move depends on which result comes back first: "merged" → run the Phase 2 Ralph launch sequence in section "Post-merge action sequence", "regression NOT_READY" → fix on the same branch, "review found X" → fix on the same branch.

End of handoff.
