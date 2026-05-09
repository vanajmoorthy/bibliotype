# PRD: Codebase Triage Fixes

**Branch name:** `triage/codebase-fixes`
**Strategy:** Single feature branch, multiple PRs cut at phase boundaries (option 3B).
**Source:** Multi-agent triage report dated 2026-05-05 covering security, performance, dead code, simplification, architecture, and pattern review of the Bibliotype Django app.

---

## 1. Introduction / Overview

The Bibliotype codebase has accumulated debt across six dimensions surfaced by a parallel multi-agent review:

- **3 Critical security issues** including unauthenticated cross-account DNA hijack via guessable `task_id`.
- **High-impact performance footguns** in the upload/enrichment pipeline (4–6k redundant queries per upload, single-threaded book sync, 1000-fold serialized API calls).
- **~200+ LOC of dead code** including stray scripts, unused views/templates, and 7 dead management commands.
- **~150 LOC of pure deletion wins** (duplicated comparative-text computation, an unnecessary class wrapper, defensive checks that can't trigger).
- **Architectural drift** — `views.py` (1296 LOC) and `dna_analyser.py` (1129 LOC) each doing the work of 4–6 modules.
- **Pattern drift** — deprecated `datetime.utcnow()`, mixed Gemini model versions, duplicated constants, magic numbers.

This PRD captures the full remediation as a sequence of small, atomic stories suitable for Ralph-loop execution.

## 2. Goals

- Eliminate the 3 critical and ~6 high-severity security findings.
- Cut typical CSV-upload latency by reducing per-row queries and parallelizing book sync.
- Delete all confirmed dead code without breaking existing flows.
- Apply pure-deletion simplifications validated by the simplicity reviewer.
- Reorganize `core/views.py`, `core/services/dna_analyser.py`, and `core/tasks.py` into packages **after** security and performance fixes have shipped.
- Normalize convention drift (deprecated APIs, mixed Gemini models, magic numbers).
- Add the indexes that hot-path queries need; defer migration squash.
- Maintain green CI on every commit.

## 3. PR / Phase Strategy

Stories are grouped into 8 phases. PR cuts happen at each phase boundary so changes ship incrementally without one giant blast-radius PR.

| Phase | Theme | PR cut |
|---|---|---|
| 1 | Critical security fixes | PR after Phase 1 |
| 2 | Repo hygiene + dead code | PR after Phase 2 |
| 3 | High-severity security (headers, rate limit, enumeration) | PR after Phase 3 |
| 4 | High-impact performance fixes | PR after Phase 4 |
| 5 | Database indexes (defer migration squash) | PR after Phase 5 |
| 6 | Simplification deletions | PR after Phase 6 |
| 7 | Pattern normalization | PR after Phase 7 |
| 8 | Architecture refactors (splits) | PRs cut per split, after Phase 7 lands |

**Between-phase rebase:** when a phase PR lands in `main`, the working branch must be reconciled before starting the next phase. Procedure: `git fetch origin && git merge origin/main` (or rebase if the working branch has no other commits since the PR). Resolve any conflicts in a single commit before Ralph picks up the next story. This keeps phase boundaries clean and avoids drift.

**Per-PR rollback rule (Phase 8 specifically):** every Phase 8 file-move story MUST be revertible with a single `git revert`. To enforce this, file moves and content edits must NOT be combined in the same commit. If a story's acceptance criteria require both, split it.

## 4. Conventions for Stories

Every story:
- Lists explicit files to touch (so Ralph's fresh-context iteration can scope itself).
- Includes verifiable acceptance criteria.
- Includes the exact feedback-loop commands.
- Adds notes to `AGENTS.md` if a non-obvious pattern was discovered.
- Updates `progress.txt` with one-line learnings.

**Standard feedback-loop commands** (Bibliotype Django stack — no static type checker):
```bash
# Format
black --line-length 120 <changed_files>
isort --profile black --line-length 120 <changed_files>
# (Templates only) npx prettier --write "<changed_template>"

# Test the touched modules
docker-compose -f docker-compose.local.yml exec web poetry run python manage.py test core.tests.<scoped_test_module>
# Or, if the change is small and isolated, run the full suite:
docker-compose -f docker-compose.local.yml exec web poetry run python manage.py test

# Migrations sanity (when a model changes)
docker-compose -f docker-compose.local.yml exec web poetry run python manage.py makemigrations --check --dry-run

# Production-settings check (for Phase 1 + 3 settings stories)
docker-compose -f docker-compose.local.yml exec web poetry run python manage.py check --deploy
```

A story is "done" when:
1. Acceptance criteria are met.
2. `manage.py test` passes (scoped or full).
3. New behavior changes have new tests.
4. `black` + `isort` produce no diff.
5. `progress.txt` has a learning line for any non-obvious discovery.
6. `AGENTS.md` updated if a project-wide convention was established.

---

## 5. User Stories

> **Note:** Each story is sized to fit a single context window. Story IDs are stable; phases group them. PR cuts happen at the end of each phase.

---

### Phase 0 — Ralph scaffolding (PR cut: none — lands with Phase 1)

#### US-000: Create `AGENTS.md` and `progress.txt` scaffolding
**Description:** As Ralph, I want the inter-iteration memory files in place so each fresh context can pick up project conventions and prior learnings.

**Files:** `AGENTS.md` (new, repo root), `progress.txt` (new, repo root).

**Acceptance Criteria:**
- [ ] `AGENTS.md` created with the following sections (each a short paragraph or bullet list):
  - "Project conventions" — mirrors CLAUDE.md baselines: f-strings only, function-based views, `safe_cache_*` wrappers, email-based auth.
  - "Settings invariants" — placeholder; populated by US-004 (`DEBUG=False` default), US-027 (`ENABLE_PARALLEL_ENRICHMENT`), US-002 (`ENFORCE_TASK_OWNERSHIP`), US-037 (`GEMINI_MODEL`).
  - "Redis key registry" — placeholder; populated by US-001 (`task_owner_<id>`).
  - "Service-layer rules" — placeholder; populated when boundaries are established.
  - "How to update this file vs progress.txt" — see below.
- [ ] **AGENTS.md vs progress.txt convention** (write this section into AGENTS.md verbatim):
  > `AGENTS.md` is for durable, project-wide invariants — settings keys, Redis key formats, code conventions, layering rules. It is mutated in place; old entries are corrected, not appended. New stories that establish a new invariant MUST update the relevant section here.
  >
  > `progress.txt` is an append-only chronological learning log. Each line: `YYYY-MM-DD: <one-sentence learning>`. Use it for: surprising findings during a story, prerequisites that must be satisfied for a future story (e.g. "regenerate_dna run on $DATE — US-033 unblocked"), follow-up items, and things that are time-bounded (e.g. "30-day shim, remove after $DATE"). Never edit existing progress.txt lines — only append.
- [ ] `progress.txt` created with header `# Bibliotype triage progress log` and one initial line: `2026-05-05: PRD generated from multi-agent triage report`.
- [ ] No code change. Just files in place.
- [ ] `manage.py test` still passes (sanity).

---

### Phase 1 — Critical Security (PR cut after US-005)

#### US-001: Bind anonymous task_id to originating session at upload time
**Description:** As a Bibliotype operator, I want anonymous DNA tasks bound to the visitor's session so that a leaked or guessed task_id cannot be used to view someone else's reading data.

**Files:**
- `core/views.py` (`upload_view` ~L733; relevant cache writes around L749–810)
- `AGENTS.md` (Redis key registry)

**Acceptance Criteria:**
- [ ] In `upload_view`, after dispatching the DNA Celery task, ensure `request.session.session_key` exists (call `request.session.save()` if `session_key is None`). Store `request.session["anonymous_task_id"] = task_id` and `safe_cache_set(f"task_owner_{task_id}", request.session.session_key, 3600)`.
- [ ] **Redis key registry:** add a "Redis key registry" section to `AGENTS.md` documenting:
  - `task_owner_<task_id>` — owner session_key for an anon DNA task; written at upload, read by `get_task_result_view` and `claim_anonymous_dna_task`; TTL 3600s.
  - `dna_result_<task_id>` — pre-existing; cached DNA payload.
  - `session_key_<task_id>` — pre-existing.
  - **Convention going forward:** all keys use the format `<purpose>_<scope_id>` with snake_case purpose. Cross-reference this section from US-024 (which adds `recs_dispatching_<user_id>`).
- [ ] **Positive test:** after a successful upload via the Django test client, both `request.session["anonymous_task_id"]` and `cache.get(f"task_owner_{task_id}")` are populated and equal to the expected values.
- [ ] **No regression test:** an existing upload-flow integration test still passes unchanged (no behavior change for happy-path).
- [ ] `manage.py test core.tests.test_views_e2e` passes.

---

#### US-002: Reject `task_id` in `get_task_result_view` if not bound to caller's session
**Description:** As a Bibliotype user, I want the task-result endpoint to refuse task_ids I don't own so that no one can pull my DNA into their session.

**Files:**
- `core/views.py` (`get_task_result_view` L1192–1253)

**Acceptance Criteria:**
- [ ] At the top of `get_task_result_view`, look up `owner = cache.get(f"task_owner_{task_id}")`.
- [ ] **Strict ownership check (NO fallback):** if `owner is None OR owner != request.session.session_key`, return `JsonResponse({"status": "FORBIDDEN"}, status=403)`.
- [ ] **Legacy compat (constrained):** the strict check is gated by a new flag `ENFORCE_TASK_OWNERSHIP = os.environ.get("ENFORCE_TASK_OWNERSHIP", "False") == "True"` — default OFF in this story, flipped ON in a follow-up after verifying production task IDs predating US-001 have drained (≥1 hour after US-001 deploy). Document in `progress.txt`.
- [ ] When `ENFORCE_TASK_OWNERSHIP=False`, log a `logger.warning("task_owner check skipped, key=%s", task_id)` per request so operators can quantify how many in-flight legacy task lookups occur. Once that count hits 0 in metrics for ≥1 hour, the operator flips the flag.
- [ ] **Positive test:** session A uploads, then fetches own task → 200.
- [ ] **Negative test:** session A uploads, session B fetches A's task → with `ENFORCE_TASK_OWNERSHIP=True` (use `@override_settings` in the test), receives 403. With the default off, receives 200 + warning log captured by `assertLogs`.
- [ ] **AGENTS.md:** add to "Settings invariants": "`ENFORCE_TASK_OWNERSHIP` is the kill-switch on the Phase-1 hijack fix. Production must set it to `True` no later than 1 hour after US-001's deploy."
- [ ] `manage.py test core.tests.test_views_e2e` passes.

---

#### US-003: Bind `task_id_to_claim` on signup to caller's pre-login session_key
**Description:** As a Bibliotype user, I want signup-time DNA claims bound to my session so that nobody can sign up with a stolen task_id and inherit a stranger's library.

**Files:**
- `core/views.py` (`signup_view` ~L818–890)
- `core/tasks.py` (`claim_anonymous_dna_task` ~L119–178)

**Acceptance Criteria:**
- [ ] **Session-rotation handling (critical):** Django rotates `request.session.session_key` on `login()`. This story must capture the session_key BEFORE `login()` is called and use that captured value for verification, not the post-login key.
- [ ] In `signup_view`, before any `login(...)` call: validate `task_id_to_claim` equals `request.session.get("anonymous_task_id")`. Reject mismatches with a form validation error rendered on the signup page.
- [ ] Capture `pre_login_session_key = request.session.session_key`. Pass it explicitly into `claim_anonymous_dna_task.delay(user.id, task_id_to_claim, pre_login_session_key)`.
- [ ] `claim_anonymous_dna_task` gains a `session_key` argument; the task verifies `cache.get(f"task_owner_{task_id}") == session_key` before claiming. On mismatch, log `logger.warning("claim rejected: session_key mismatch", extra={...})` and return early (no exception, no UserBook creation, no DNA write).
- [ ] **Regression test 1 (negative):** session A uploads, session B signs up with A's task_id, signup form rejects with the specific validation error message.
- [ ] **Regression test 2 (positive):** session A uploads, then signs up — DNA claim succeeds and books are created. Test must use `Client()` with the same session across the upload + signup requests, and explicitly verify `pre_login_session_key` ≠ `request.session.session_key` after login (i.e., simulates the rotation that happens in production).
- [ ] **Regression test 3 (security):** task fed a mismatched session_key via direct invocation logs the warning and creates no UserBooks. Use `assertLogs`.
- [ ] `manage.py test core.tests.test_views_e2e core.tests.test_tasks_integration` passes.

---

#### US-004: `DEBUG` defaults to `False` and add startup assert in production
**Description:** As an operator, I want a misconfigured `DEBUG` env var to never silently leave production in debug mode.

**Files:**
- `bibliotype/settings.py` (L11 `DEBUG`; new assert section near top)

**Acceptance Criteria:**
- [ ] Change `DEBUG = os.environ.get("DEBUG", "True") == "True"` to default `"False"`.
- [ ] Add: `if os.environ.get("DJANGO_ENV") == "production" and DEBUG: raise ImproperlyConfigured("DEBUG must be False in production")`.
- [ ] Document this in `AGENTS.md` under a new "Settings invariants" section.
- [ ] Run `python manage.py check --deploy` and confirm no new warnings introduced (existing warnings are fine).
- [ ] Update any test that relied on `DEBUG=True` defaulting (likely none — tests use `@override_settings`).
- [ ] `manage.py test` passes.

**Test command:** `python manage.py test`

---

#### US-005: Move management-command whitelist into `run_management_command_task`
**Description:** As an operator, I want the management-command Celery task to refuse non-whitelisted commands even if a malicious caller publishes a message directly to the broker.

**Files:**
- `core/tasks.py` (`run_management_command_task` L391–433)
- `core/admin.py` (the existing whitelist near L304)

**Acceptance Criteria:**
- [ ] Define `ALLOWED_MANAGEMENT_COMMANDS = frozenset({...})` in `core/tasks.py`, populated from the existing admin whitelist.
- [ ] First line of `run_management_command_task`: `if command_name not in ALLOWED_MANAGEMENT_COMMANDS: raise ValueError(f"command not allowed: {command_name}")`.
- [ ] `core/admin.py` imports and uses the same constant.
- [ ] **Negative test:** unit test verifies the task raises `ValueError` on a non-whitelisted name; verify `call_command` was not invoked using `mock.patch`.
- [ ] **Positive test:** unit test verifies a whitelisted command (use a no-op like `check`) executes successfully.
- [ ] `manage.py test core.tests.test_tasks_unit core.tests.test_tasks_integration` passes.

---

### Phase 2 — Repo hygiene & dead code (PR cut after US-014)

#### US-006: Delete `check_models.py`
**Description:** As a maintainer, I want stray throwaway scripts removed from the repo root.

**Files:** `check_models.py`

**Acceptance Criteria:**
- [ ] `git rm check_models.py`.
- [ ] Verify nothing imports it (`grep -rn check_models . --exclude-dir=.git`).
- [ ] `manage.py test` passes.

---

#### US-007: Remove tracked `.prof` files from repo
**Description:** As a maintainer, I want the 110 silk profile dumps removed from version control.

**Files:** Repo root `*.prof`, `.gitignore` (verify pattern).

**Acceptance Criteria:**
- [ ] Confirm `.gitignore` already covers `*.prof`. Add it if missing.
- [ ] Run `git ls-files '*.prof' | xargs git rm` (explicit list of tracked files — avoids glob-expansion surprises).
- [ ] `git status` shows working tree clean of `.prof` entries.
- [ ] `manage.py test` passes.

---

#### US-008a-i: Introduce `TEST_FIXTURES_DIR` constant and move synthetic CSV files
**Description:** As a maintainer, I want a stable fixtures path constant in place before any test imports change.

**Files:** `core/tests/__init__.py` (or `core/tests/conftest.py`-equivalent), `csv/` source files, new `core/tests/fixtures/csv/` destination.

**Acceptance Criteria:**
- [ ] Create `core/tests/__init__.py` (if not present; preserve any existing content) that defines `from pathlib import Path; TEST_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "csv"`.
- [ ] `mkdir -p core/tests/fixtures/csv/` and move the 16 synthetic + 5 test_reader CSV files there.
- [ ] Move the three subdirectories (`test/`, `test_storygraph/`, `test-csvs/`) into `core/tests/fixtures/csv/` preserving structure.
- [ ] **Do NOT update test imports yet** — this is US-008a-ii. The current tests will fail; that's expected and proves the move worked.
- [ ] **Verification:** `manage.py test` should now FAIL because tests still reference the old `csv/...` paths. The failure is the signal; do not "fix" it in this story.
- [ ] **Commit guard:** commit message must include `WIP: tests broken until US-008a-ii (fixture-path imports)`.

---

#### US-008a-ii: Update test and management-command imports to use `TEST_FIXTURES_DIR` / new paths
**Description:** As a maintainer, I want the broken-by-US-008a-i imports updated to point at the new fixtures location.

**Files:** All `core/tests/*.py` that reference `csv/...` (find with `grep -rn "csv/" core/tests/`), all `core/management/commands/*.py` that hardcode `csv/...` (find with `grep -rn "csv/" core/management/`).

**Acceptance Criteria:**
- [ ] In each test file: replace `csv/...` literal paths with `TEST_FIXTURES_DIR / "..."`.
- [ ] In each management command: replace hardcoded `csv/...` paths with paths to `core/tests/fixtures/csv/...` (these commands are mostly dead — verify they aren't covered by US-012/US-013 first; if they are, no change needed).
- [ ] `manage.py test` passes — this story's success condition is the test suite going green again.
- [ ] `progress.txt`: note where test fixtures now live and the constant name.

---

#### US-008b: Delete personal Goodreads exports and gitignore stray paths
**Description:** As a maintainer / privacy steward, I want personal data out of the repo and the empty `csv/` root tracked-but-ignored so locally generated CSVs can't accidentally be committed. Also gitignore `.claude/worktrees/`.

**Files:** `csv/` (root files), `.gitignore`.

**Acceptance Criteria:**
- [ ] `git rm` the personal exports (`csv/goodreads_library_export anya.csv`, `csv/goodreads_library_export niamh.csv`, `csv/goodreads_library_export(1).csv`, `csv/.DS_Store`).
- [ ] Add `csv/`, `*.DS_Store`, and `.claude/worktrees/` to `.gitignore` (verify each isn't already present before adding).
- [ ] If the directory ends up empty after fixture moves and personal-export deletes, `rmdir csv/`.
- [ ] `manage.py test` passes.
- [ ] **Note in `progress.txt`:** "Personal Goodreads CSVs removed from repo on YYYY-MM-DD; check git history if regression analysis ever needs them."

---

#### US-009: Move `csv/generate_test_data.py` and `generate_synthetic_test_data.py` into management commands
**Description:** As a maintainer, I want CSV-generator scripts to be discoverable via `manage.py`.

**Files:** `csv/generate_test_data.py`, `csv/generate_synthetic_test_data.py`, `core/management/commands/`.

**Acceptance Criteria:**
- [ ] `generate_test_data.py` already inherits from `BaseCommand`; move to `core/management/commands/generate_csvs.py` (rename to avoid collision with existing `generate_test_data` command).
- [ ] `generate_synthetic_test_data.py` becomes `core/management/commands/generate_synthetic_csvs.py` wrapped in a `BaseCommand` if it isn't already.
- [ ] Verify with `python manage.py help` they appear.
- [ ] Delete the old root-level files.
- [ ] `manage.py test` passes.

---

#### US-010: Delete `catch_all_404_view` and unwire it
**Description:** As a maintainer, I want unused view code removed.

**Files:** `core/views.py` L1276–1279, plus any urls.py reference (verified zero).

**Acceptance Criteria:**
- [ ] `git grep catch_all_404` returns zero results outside of removal commit.
- [ ] `manage.py test` passes (the e2e 404 test should still hit `handler404`).

---

#### US-011: Delete unused share-card template `card_full.html`
**Description:** As a maintainer, I want unused templates removed.

**Files:** `core/templates/core/partials/share/card_full.html`

**Acceptance Criteria:**
- [ ] Confirm zero references via `git grep "card_full"` (the three themed variants `card_full_miami|neon_picnic|sherbet` remain).
- [ ] Delete file.
- [ ] `manage.py test` passes.
- [ ] Visit any DNA dashboard locally and confirm the share modal still renders the three themed variants.

---

#### US-012: Delete confirmed-dead management commands (batch 1)
**Description:** As a maintainer, I want one-shot scripts that are clearly done removed.

**Files:**
- `core/management/commands/create_users_from_csvs.py`
- `core/management/commands/populate_test_dna.py`
- `core/management/commands/re_enrich_all_books.py`

**Acceptance Criteria:**
- [ ] For each file, confirm `git grep <command_name>` shows no references outside the file itself.
- [ ] `git rm` the files.
- [ ] `python manage.py help` shows they are gone; remaining commands still listed.
- [ ] `manage.py test` passes.
- [ ] `progress.txt`: list of removed commands.

---

#### US-013: Delete confirmed-dead management commands (batch 2)
**Description:** As a maintainer, finish removing the dead command files.

**Files:**
- `core/management/commands/reset_genres.py`
- `core/management/commands/seed_test_books.py`
- `core/management/commands/seed_mainstream_publishers.py`

**Acceptance Criteria:**
- [ ] Same verification as US-012.
- [ ] `manage.py test` passes.

---

#### US-014: Remove unused imports flagged by AST scan
**Description:** As a maintainer, I want the dead-import lines removed.

**Files:**
- `core/services/recommendation_service.py` L8 (`safe_cache_delete`)
- `core/services/user_similarity_service.py` L3 (`Avg`)
- `core/services/anonymization_service.py` L3 (`Author`)

**Acceptance Criteria:**
- [ ] Imports removed; `isort` produces no diff.
- [ ] `manage.py test` passes.

---

### Phase 3 — High-severity security (PR cut after US-020)

#### US-015: Add production security headers
**Description:** As an operator, I want HSTS, Referrer-Policy, and content-type-nosniff enforced in production so that S1's task_id leak via Referer is blocked and SSL stripping is mitigated.

**Files:** `bibliotype/settings.py` (production block ~L163–171)

**Acceptance Criteria:**
- [ ] Inside `if not DEBUG:` block add: `SECURE_HSTS_SECONDS = 31536000`, `SECURE_HSTS_INCLUDE_SUBDOMAINS = True`, `SECURE_HSTS_PRELOAD = True`, `SECURE_CONTENT_TYPE_NOSNIFF = True`, `SECURE_REFERRER_POLICY = "same-origin"`.
- [ ] `python manage.py check --deploy` reports zero warnings for the items above (other warnings are OK to log but not regress).
- [ ] `manage.py test` passes.

---

#### US-016: Add `django-ratelimit` and apply to login view
**Description:** As an operator, I want failed-login rate limiting so credential stuffing is impractical.

**Files:** `pyproject.toml`, `bibliotype/settings.py`, `core/views.py` (`login_view` L891–928).

**Acceptance Criteria:**
- [ ] Add `django-ratelimit` to dependencies via `poetry add django-ratelimit` (commit `pyproject.toml` AND `poetry.lock`).
- [ ] Apply `@ratelimit(key="ip", rate="5/m", method="POST", block=True)` to `login_view`.
- [ ] On 429, render the login template with a "Too many attempts" form-level error.
- [ ] **Negative test:** 6th login POST from same IP within a minute receives 429 / template error message.
- [ ] **Positive test:** 5th login POST in same window with VALID credentials still authenticates successfully.
- [ ] `manage.py test` passes.

---

#### US-017: Generic error message on signup duplicate-email
**Description:** As a user, I don't want signup forms revealing which emails are registered. **Decision:** ALSO send a password-reset email to legitimate users who forgot they have an account (better UX than only showing a vague page).

**Files:** `core/forms.py` `clean_email` L28–31, `core/views.py` `signup_view`.

**Acceptance Criteria:**
- [ ] Remove the duplicate-email error from `clean_email`. Allow the form to validate.
- [ ] In `signup_view`, after form validation but before `User.objects.create_user`, check if a user with `email__iexact` already exists. If yes:
  - Trigger Django's password-reset email flow for that user (use `PasswordResetForm` programmatically or call the same code path used by `CustomPasswordResetView`).
  - Render a "Check your inbox" page with the same message regardless of whether the email exists.
  - Do NOT create a new user.
- [ ] **Negative test (new email):** signup completes normally, user is created.
- [ ] **Negative test (duplicate email):** signup short-circuits to inbox page; `User.objects.count()` did NOT increment; `mail.outbox` contains a reset email.
- [ ] **Positive test (legitimate flow unchanged):** same as the first negative test — confirms a fresh signup still works.
- [ ] `manage.py test` passes.

---

#### US-018: CSV upload row + column + schema validation
**Description:** As an operator, I want upload validation that prevents pathological CSVs from exhausting memory AND ensures the file actually looks like a Goodreads/StoryGraph export before pandas does deep work on it.

**Files:** `core/views.py` `upload_view` L729–810.

**Acceptance Criteria:**
- [ ] Read CSV head with `pandas.read_csv(StringIO(csv_content), nrows=50000)`. Reject if `len(df.columns) > 100` with a user error.
- [ ] **Schema validation:** verify the CSV contains at least one of these signature column sets before passing to `_detect_and_normalize_csv`:
  - Goodreads: `{"Title", "Author"}` (case-sensitive).
  - StoryGraph: `{"Title", "Authors"}` (note plural).
  - If neither set is fully present, reject with user error: "CSV does not look like a Goodreads or StoryGraph export. Expected columns: Title and Author/Authors."
- [ ] Add `MAX_UPLOAD_ROWS = 50000` and `MAX_UPLOAD_COLUMNS = 100` module constants in `core/views.py`.
- [ ] **Negative tests:** (a) 50_001-row CSV is truncated, (b) 101-column CSV is rejected, (c) CSV missing required columns is rejected with the schema error.
- [ ] **Positive test:** an existing valid Goodreads or StoryGraph fixture still uploads.
- [ ] `manage.py test` passes.

---

#### US-019: Replace `messages|safe` HTML construction with rendered template fragment
**Description:** As an operator, I want operator-built HTML out of message strings so that `request.get_host()` can never poison the displayed copy.

**Files:** `core/views.py` `update_privacy_view` L955; `core/templates/core/partials/messages_with_link.html` (new).

**Acceptance Criteria:**
- [ ] Create `core/templates/core/partials/messages_with_link.html` that renders the privacy-toggle success message with `{% url 'core:public_profile' username=username %}` and `{{ public_url }}` content (auto-escaped).
- [ ] `update_privacy_view` calls `render_to_string("core/partials/messages_with_link.html", {"public_url": public_url, "username": username})` and passes the result to `messages.success(...)`. Do NOT use the alternative "store metadata on the message" approach — pick this one.
- [ ] No raw f-string HTML remains in `update_privacy_view`.
- [ ] **Manual browser check:** `dev-browser` skill — toggle privacy on, confirm message renders identically (link present, message visible, link target equal to public profile URL).
- [ ] `manage.py test` passes.

---

#### US-020: Add rate limit to `update_username_api`
**Description:** As an operator, I want display-name lookup unable to enumerate the userbase.

**Files:** `core/views.py` `update_username_api` L984–1006.

**Acceptance Criteria:**
- [ ] Apply `@ratelimit(key="user", rate="10/m", method="POST", block=True)`.
- [ ] On 429, return `JsonResponse({"error": "Too many attempts, try again later."}, status=429)`.
- [ ] **Negative test:** 11th POST in a minute returns 429.
- [ ] **Positive test:** 10th POST returns the normal response (form error or success).
- [ ] `manage.py test` passes.

---

### Phase 4 — High-impact performance (PR cut after US-027)

#### US-021: Lift `max_workers` for book sync to 8 in `process_book_row`
**Description:** As an operator, I want the upload pipeline to use the worker pool actually appropriate for Postgres.

**Files:** `core/services/dna_analyser.py` (`ThreadPoolExecutor(max_workers=1)` ~L609).

**Acceptance Criteria:**
- [ ] Change `max_workers=1` to `max_workers=8`.
- [ ] Update the comment explaining "this is safe under Postgres".
- [ ] Run the existing storygraph + integration test suite (which exercises a multi-row upload) and confirm no regression.
- [ ] **Concurrency-safety check:** run `manage.py test core.tests.test_storygraph_integration core.tests.test_tasks_integration` 5 times consecutively (loop). Tests must pass deterministically every run. If any run fails order-dependently, the worker count is exposing a real bug — investigate before completing the story. Capture run output in `progress.txt`.
- [ ] `manage.py test core.tests.test_storygraph_integration core.tests.test_tasks_integration` passes.

---

#### US-022: Remove redundant per-row Book/Genre queries in `process_book_row`
**Description:** As an operator, I want the 3 redundant queries inside the per-book loop eliminated.

**Files:** `core/services/dna_analyser.py` (`process_book_row` around L574, L604, L606).

**Acceptance Criteria:**
- [ ] Use the `created` flag from `update_or_create` instead of re-`exists()`-checking genres.
- [ ] Drop the `Book.objects.get(pk=book.pk)` re-fetch — operate on the `book` object already in scope.
- [ ] Reuse `book.genres.all()` directly without a re-query.
- [ ] Existing integration tests pass (`test_tasks_integration`, `test_storygraph_integration`, `test_subtitle_data`, `test_views_e2e` upload-flow tests).
- [ ] **Query-count regression test:** add or augment a test in `core/tests/test_tasks_integration.py` that wraps the upload flow in `from django.test.utils import CaptureQueriesContext; with CaptureQueriesContext(connection) as ctx: ...` and asserts `len(ctx) < BASELINE_QUERIES`. Establish `BASELINE_QUERIES` by capturing the count before this story's edit and storing the pre-change number in the test file's docstring; the post-change assertion is `<= BASELINE - 3 * NUM_BOOKS_IN_FIXTURE`.
- [ ] **Manual canary:** upload one of the synthetic CSVs in `core/tests/fixtures/csv/` end-to-end via the running dev server (`/upload/` page). Confirm DNA generates correctly and books appear in the dashboard. Capture in `progress.txt`.
- [ ] **Risk note:** this is the upload pipeline's hot path. Genre membership and `update_or_create` semantics differ subtly when a Book row already exists with different `defaults={...}`. If tests fail, do NOT loosen them — investigate the semantic change.
- [ ] `manage.py test` passes.

---

#### US-023: `_save_dna_to_profile` dispatches recommendations via `.delay()`
**Description:** As an operator, I don't want DNA-task latency to include the synchronous recommendations build.

**Files:** `core/services/dna_analyser.py` L305–312.

**Acceptance Criteria:**
- [ ] Replace the inline `generate_recommendations_task(profile.user.id)` with `.delay(profile.user.id)`.
- [ ] Drop the surrounding try/except now that retries can fire normally.
- [ ] Tests still pass with `CELERY_TASK_ALWAYS_EAGER=True` (which makes `.delay()` synchronous in tests anyway).
- [ ] `manage.py test core.tests.test_recommendations core.tests.test_tasks_integration` passes.

---

#### US-024: Sentinel-guard recommendations dispatch in `display_dna_view`
**Description:** As an operator, I don't want every dashboard poll to spawn a duplicate recommendations task.

**Files:** `core/views.py` `display_dna_view` ~L558.

**Acceptance Criteria:**
- [ ] Wrap the `generate_recommendations_task.delay(...)` call in: `if cache.add(f"recs_dispatching_{user.id}", 1, timeout=300): generate_recommendations_task.delay(...)`.
- [ ] On task completion (`generate_recommendations_task` itself or `_save_dna_to_profile`), `cache.delete(f"recs_dispatching_{user.id}")`.
- [ ] New regression test: 5 simultaneous dashboard renders produce only 1 task dispatch (use `mock.patch` on `.delay`).
- [ ] `manage.py test` passes.

---

#### US-025: Replace `order_by("?")` with random-id sampling on `AnonymizedReadingProfile`
**Description:** As an operator, I don't want a `RANDOM()` table scan inside hot recommendation paths.

**Files:** `core/services/recommendation_service.py` L486 and L542.

**Acceptance Criteria:**
- [ ] Replace `AnonymizedReadingProfile.objects.order_by("?")[:100]` with: `ids = list(AnonymizedReadingProfile.objects.values_list('id', flat=True)); sampled = random.sample(ids, min(len(ids), 100)); profiles = AnonymizedReadingProfile.objects.filter(id__in=sampled)`.
- [ ] Both call sites updated.
- [ ] Existing recommendation tests pass.
- [ ] `manage.py test core.tests.test_recommendations` passes.

---

#### US-026: Annotate `book_count` on `GenreAdmin` and `PublisherAdmin`
**Description:** As an operator, I want admin changelist pages free of N+1 count queries.

**Files:** `core/admin.py` L51 (`GenreAdmin.book_count`), L75 (`PublisherAdmin.book_count`).

**Acceptance Criteria:**
- [ ] Override `get_queryset` on each admin class to add `.annotate(book_count_annot=Count("books"))`.
- [ ] Replace `obj.books.count()` with `return obj.book_count_annot`.
- [ ] Add `book_count_annot.admin_order_field = "book_count_annot"` on the method.
- [ ] Manual check (dev-browser): admin changelist for genres and publishers loads with same numbers but fewer queries (verify via Silk in DEBUG).
- [ ] `manage.py test` passes.

---

#### US-027: Replace per-task `time.sleep(1.2)` in book enrichment with Celery `rate_limit` (feature-flagged)
**Description:** As an operator, I don't want enrichment workers held hostage by `time.sleep` — but I also don't want to surge external API rate limits the moment the change ships.

**Files:** `bibliotype/settings.py` (new flag), `core/book_enrichment_service.py` (5 sleep calls L171, L209, L234, L261, L299), `core/tasks.py` `enrich_book_task` L69.

**Acceptance Criteria:**
- [ ] Add `ENABLE_PARALLEL_ENRICHMENT = os.environ.get("ENABLE_PARALLEL_ENRICHMENT", "False") == "True"` to `bibliotype/settings.py`. Document in `AGENTS.md` under "Settings invariants": "default `False`; flip to `True` only after confirming Open Library + Google Books rate-limit headroom."
- [ ] Wrap each `time.sleep(...)` in `if not settings.ENABLE_PARALLEL_ENRICHMENT: time.sleep(1.2)`. (Cleaner: extract a `_throttle()` helper that does the conditional check, call it at the 5 sites.)
- [ ] Confirm `enrich_book_task` declares `rate_limit="30/m"` (add if missing) — this is the safety net regardless of the flag.
- [ ] `progress.txt`: "Parallel enrichment lands disabled. Operator must set `ENABLE_PARALLEL_ENRICHMENT=True` and validate API headroom before enabling."
- [ ] Existing enrichment tests pass with `CELERY_TASK_ALWAYS_EAGER=True`.
- [ ] `manage.py test core.tests.test_integration core.tests.test_enrichment_stats` passes.

---

#### US-027b: Precompile genre canonicalization regex patterns at module load
**Description:** As an operator, I want the genre canonicalization regex patterns compiled once at import, not once per book per alias.

**Files:** `core/book_enrichment_service.py` `_clean_and_canonicalize_genres` ~L25–68 and `_canonicalize_google_books_categories` ~L71–113.

**Acceptance Criteria:**
- [ ] At module load, build `_COMPILED_ALIAS_PATTERNS = [(re.compile(r"\b" + re.escape(a) + r"\b"), CANONICAL_GENRE_MAP[a]) for a in sorted(CANONICAL_GENRE_MAP.keys(), key=len, reverse=True)]`.
- [ ] Both helpers iterate `_COMPILED_ALIAS_PATTERNS` instead of recomputing.
- [ ] **Module-load test:** add a tiny test asserting `from core.book_enrichment_service import _COMPILED_ALIAS_PATTERNS; assert len(_COMPILED_ALIAS_PATTERNS) > 0`. This catches accidental empty-build regressions.
- [ ] Genre-canonicalization tests still pass.
- [ ] `manage.py test core.tests.test_integration core.tests.test_enrichment_stats` passes.
- [ ] `progress.txt`: note that genre-pattern compilation now happens at import time.

---

#### US-024b: Switch `AnonymousUserSession` recreation in `display_dna_view` to `update_or_create`
**Description:** As a maintainer, I want session recreation idempotent so a unique-constraint race can't 500 a user.

**Files:** `core/views.py` `display_dna_view` ~L593–620 (currently calls `AnonymousUserSession.objects.create(session_key=request.session.session_key, ...)`).

**Acceptance Criteria:**
- [ ] Replace `AnonymousUserSession.objects.create(...)` with `AnonymousUserSession.objects.update_or_create(session_key=..., defaults={...})`.
- [ ] **Negative test:** call `display_dna_view` twice in succession with the same session — second call does not raise `IntegrityError`; only one `AnonymousUserSession` row exists.
- [ ] **Positive test:** existing dashboard tests still pass.
- [ ] `manage.py test core.tests.test_views_e2e` passes.

---

#### US-024c: Invalidate recommendations cache on `update_recommendation_visibility`
**Description:** As a privacy-conscious user, I want my cached recommendations and similarity-candidate entries cleared when I toggle visibility off.

**Files:** `core/views.py` `update_recommendation_visibility` L1011–1027.

**Acceptance Criteria:**
- [ ] After saving the new visibility flag, call `safe_cache_delete(f"recs_{user.id}")` AND any similar-users cache key (`safe_cache_delete(f"similar_users_{user.id}")` if it exists; if the key naming differs, document the actual key in `AGENTS.md` Redis registry).
- [ ] If the user toggled FROM visible TO invisible, also clear the candidate-set cache (`safe_cache_delete("public_users_for_recs_sample")` — this is the 30-min cached candidate sample referenced in `recommendation_service.py`). When toggling visible→invisible, log `logger.info("user opted out of recs; cleared candidate sample cache")`.
- [ ] **Test:** toggle visibility off, then assert the relevant cache keys are absent.
- [ ] `manage.py test core.tests.test_profile_and_recommendations` passes.

---

### Phase 5 — Database indexes (PR cut after US-029)

#### US-028: Add `db_index=True` on `Book.google_books_last_checked`
**Description:** As an operator, I want the polling query indexed.

**Files:** `core/models.py` (Book model field), new migration.

**Acceptance Criteria:**
- [ ] Add `db_index=True` to the `google_books_last_checked` DateTimeField.
- [ ] `python manage.py makemigrations` produces a single index-add migration.
- [ ] `python manage.py migrate` applies cleanly.
- [ ] `manage.py test` passes (migrations replay).

---

#### US-029: Composite partial index on `UserProfile (visible_in_recommendations, is_public) WHERE dna_data IS NOT NULL`
**Description:** As an operator, I want the `find_similar_users` candidate query supported by an index.

**Files:** `core/models.py`, new migration.

**Acceptance Criteria:**
- [ ] Add a `Meta.indexes = [models.Index(fields=["visible_in_recommendations", "is_public"], condition=Q(dna_data__isnull=False), name="userprofile_recs_partial_idx")]` to `UserProfile`.
- [ ] Migration applies cleanly.
- [ ] `manage.py test` passes.

---

### Phase 6 — Simplification deletions (PR cut after US-035)

#### US-030: Delete duplicated `comparative_text` block in `dna_analyser.py`
**Description:** As a maintainer, I want one source of truth for comparative-text computation. `_enrich_dna_for_display` recomputes and overwrites the values on every render.

**Files:** `core/services/dna_analyser.py` L984–1018, `core/views.py` `_enrich_dna_for_display` L415–449 (read for verification).

**Acceptance Criteria:**
- [ ] **Pre-flight verification:** open `core/views.py` `_enrich_dna_for_display` and confirm it unconditionally writes `comparative_text` keys (`length_direction`, `age_direction`, `bpy_direction` and their `_pct` companions) on every call. List the keys in the commit message.
- [ ] Delete the entire 35-line block in `dna_analyser.py` L984–1018.
- [ ] **Coverage test:** add a test asserting that `_enrich_dna_for_display(dna_data)` populates each of the listed `comparative_text` keys when called on a fresh `dna_data` dict that lacks them.
- [ ] `manage.py test core.tests.test_views_e2e core.tests.test_math_accuracy` passes.

---

#### US-031a: Convert `RecommendationEngine` methods to module-level functions
**Description:** As a maintainer, I want recommendation logic as module functions so the class wrapper can be retired. Keep parameters intact for now — behavior must be byte-identical.

**Files:** `core/services/recommendation_service.py`, plus tests that instantiate the class (`core/tests/test_recommendations.py`, `core/tests/test_cache_refactor.py`, `core/tests/test_profile_and_recommendations.py`, `core/tests/test_currently_reading.py` — verify with `git grep RecommendationEngine`).

**Acceptance Criteria:**
- [ ] **Pre-flight:** run `git grep RecommendationEngine` and capture every file/line. There are 30+ matches including `test_currently_reading.py` (10 occurrences). Every one must be updated.
- [ ] Remove `class RecommendationEngine:` line; dedent body. Each `def method(self, ...)` becomes `def method(...)`.
- [ ] Replace `self.method(...)` internal calls with bare `method(...)` calls. Replace `self.min_similarity` and `self.quality_threshold` with module-level constants `MIN_SIMILARITY = 0.15`, `QUALITY_THRESHOLD = 3.5`. Drop `self.diversity_factor` entirely (it is set but never read — US-031b deletes the parameter from the signature too).
- [ ] Update `get_recommendations_for_user` / `get_recommendations_for_anonymous` to call functions directly (no `RecommendationEngine()` instantiation).
- [ ] **Test command:** `manage.py test` (full suite, not scoped) — class deletion ripples wider than any single test module.
- [ ] **Final verification:** `git grep RecommendationEngine` returns ZERO matches.
- [ ] **Risk note:** highest-risk story in the PRD. If full test suite goes red, do NOT loosen tests — investigate semantic divergence in the converted functions.

---

#### US-031b: Remove unused recommendation parameters
**Description:** As a maintainer, drop the parameters that no caller overrides.

**Files:** `core/services/recommendation_service.py`.

**Acceptance Criteria:**
- [ ] Drop the `diversity_factor` parameter from any function signature that still has it (US-031a left the constant out but the parameter may remain).
- [ ] Drop the `is_queryset` parameter from `_extract_series_info` (the only caller passes `False`).
- [ ] Drop the `include_explanations` parameter (always `True` at every call site); always run `_add_explanations`.
- [ ] **Test command:** `manage.py test` (full suite).

---

#### US-032: Bake `rec["book"]` dict into `processed_recs` at storage time (dual-shape compat)
**Description:** As a maintainer, I want recommendations stored in the shape templates need. Existing in-DB `recommendations_data` will not have the baked dict, so views must accept BOTH shapes during the rollout.

**Files:** `core/tasks.py` L469–481 (storage), `core/views.py` `display_dna_view` L535–552, `public_profile_view` L1126–1144.

**Acceptance Criteria:**
- [ ] Inside `generate_recommendations_task`, build the full `rec["book"] = {...}` dict before saving to `UserProfile.recommendations_data`.
- [ ] In both views, replace the 18-line for-loop with: `if "book" not in rec: rec["book"] = _expand_book_dict(rec, badge_color_map)` — fallback expansion for legacy stored shapes. Extract `_expand_book_dict` as a small helper in the view module (or `core/views.py` if Phase 8 hasn't split yet).
- [ ] **Test 1 (new shape):** mock a recommendation that already has `rec["book"]`; view renders without re-expansion.
- [ ] **Test 2 (legacy shape):** mock a recommendation without `rec["book"]`; fallback runs and renders identically.
- [ ] **`dev-browser` check:** dashboard and `/u/<username>` render identical recommendations against test data containing both shapes.
- [ ] **Follow-up note in `progress.txt`:** "US-032 ships dual-shape compat. After ≥30 days OR after a forced `regenerate_recommendations` run, the legacy fallback (`if 'book' not in rec`) can be removed in a follow-up story."
- [ ] `manage.py test` passes.

---

#### US-033: Pick one — delete legacy backfills OR re-upload nudge
**Description:** As a maintainer, I want one source of truth for handling old DNA-data shapes.

**Files:** `core/views.py` `_enrich_dna_for_display` L316–389 (backfills) and L681–686 (`messages.info` re-upload nudge).

**Acceptance Criteria:**
- [ ] **BLOCKING PRE-FLIGHT:** before changing any code, `progress.txt` must already contain a line matching the regex `^\d{4}-\d{2}-\d{2}: regenerate_dna run in production` followed by an operator name. If absent, exit the story without modifying code and emit a comment in the commit message: `BLOCKED: regenerate_dna prerequisite (Q4) unresolved.` Do NOT mark this story complete until the prerequisite line exists.
- [ ] Delete the L316–389 backfill block.
- [ ] Keep the re-upload nudge (acts as fallback for any user who never triggers a regeneration).
- [ ] `manage.py test core.tests.test_views_e2e` passes.

---

#### US-034: Inline trivial defensive checks in `_build_cover_url` and `_sanitize_review_text`
**Description:** As a maintainer, I want unreachable defensive branches removed.

**Files:** `core/services/dna_analyser.py` L42–48 (`_sanitize_review_text`), L96–99 (`_build_cover_url`).

**Acceptance Criteria:**
- [ ] In `_sanitize_review_text`, drop `or not isinstance(text, str)` from the early-return guard.
- [ ] In `_build_cover_url`, simplify to `if not isbn13: return None`.
- [ ] Sanitize-review-text test still passes.
- [ ] `manage.py test core.tests.test_sanitize_review` passes.

---

#### US-035: Collapse duplicated cache-hit / result-hit branches in `claim_anonymous_dna_task`
**Description:** As a maintainer, I want one save-block for the two code paths that produce `dna_data`.

**Files:** `core/tasks.py` L131–178.

**Acceptance Criteria:**
- [ ] Resolve `dna_data` from cache or AsyncResult into a single variable.
- [ ] Run save-DNA / create-userbooks / clear-pending-task / save-profile / track-event once.
- [ ] Existing claim integration test still passes.
- [ ] `manage.py test core.tests.test_tasks_integration` passes.

---

#### US-035b: Dedupe `_canonicalize_google_books_categories` with `_clean_and_canonicalize_genres`
**Description:** As a maintainer, I want a single canonicalization implementation. The two functions differ only in that Google Books inputs need a `/`-split first.

**Files:** `core/book_enrichment_service.py` L25–68 (`_clean_and_canonicalize_genres`) and L71–113 (`_canonicalize_google_books_categories`).

**Acceptance Criteria:**
- [ ] Rewrite `_canonicalize_google_books_categories(categories)` as: `flat = [part for cat in categories for part in cat.split("/")]; return _clean_and_canonicalize_genres(flat)`.
- [ ] The cap difference (5 vs up to 6) becomes a `[:N]` slice at the call site (do NOT add a parameter to `_clean_and_canonicalize_genres` — keep its signature stable).
- [ ] **Snapshot test:** before the refactor, run the existing function against three fixture inputs (`["Fiction/Romance", "Self-Help"]`, `["Juvenile Fiction"]`, `["Computers/Programming Languages"]`) and capture the outputs. After refactor, assert outputs match exactly. Add the snapshot test to `core/tests/test_integration.py`.
- [ ] `manage.py test core.tests.test_integration core.tests.test_enrichment_stats` passes.
- [ ] **Risk note:** order of priority application may differ subtly because Google Books had its own ordering. The snapshot test catches drift.

---

### Phase 7 — Pattern normalization (PR cut after US-042)

#### US-036: Replace `datetime.utcnow()` with `timezone.now()` in `services/author_service.py`
**Description:** As a maintainer, I want deprecated naive-datetime usage gone.

**Files:** `core/services/author_service.py` L38.

**Acceptance Criteria:**
- [ ] Use `django.utils.timezone.now()`. Adjust any `.strftime` calls (still works on aware datetimes).
- [ ] Author-service test still passes.
- [ ] `manage.py test` passes.

---

#### US-037: Reconcile Gemini model versions across services
**Description:** As a maintainer, I want a single source of truth for the Gemini model id.

**Files:** `bibliotype/settings.py` (new constant), `core/services/llm_service.py` L107, `core/services/publisher_service.py` L84, `core/tasks.py` L29–34.

**Acceptance Criteria:**
- [ ] Add `GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")` to settings.
- [ ] All three call sites import from settings.
- [ ] Centralize `genai.configure(api_key=settings.GEMINI_API_KEY)` into one helper module (e.g., `core/services/_gemini.py`); each consumer calls `_gemini.client()`.
- [ ] `manage.py test` passes.
- [ ] `progress.txt`: note that LLM model is now configurable via env var.

---

#### US-038: Centralize Open Library cover URL helper
**Description:** As a maintainer, I want one cover-URL builder.

**Files:** `core/services/dna_analyser.py` (existing `OPEN_LIBRARY_COVER_URL`), `core/book_enrichment_service.py` L526, L528, `core/management/commands/backfill_covers.py` L66, L149.

**Acceptance Criteria:**
- [ ] Extract `cover_url_from_isbn(isbn)` and `cover_url_from_olid(olid)` helpers in `core/services/_book_urls.py` (or move into `dna_analyser.py` if simpler).
- [ ] Replace all four hardcoded URLs.
- [ ] `manage.py test core.tests.test_backfill_covers` passes.

---

#### US-039: Hoist `genre_priority` list into `dna_constants.py`
**Description:** As a maintainer, I want one canonical priority list.

**Files:** `core/book_enrichment_service.py` L387–415, L480–504; `core/dna_constants.py`.

**Acceptance Criteria:**
- [ ] Move list to `dna_constants.GENRE_PRIORITY`.
- [ ] Both call sites import and use it; cap difference handled with a `[:N]` slice at call site.
- [ ] `manage.py test` passes.

---

#### US-040: Reconcile top-book scoring formulas (auth vs anonymous)
**Description:** As a maintainer / user, I want top-books ranked the same way regardless of auth state. **Canonical formula:** `top_books_service.py:14-32` (rating-weight 100/80/×15 + sentiment×30) — chosen because it has direct test coverage and is the more recently maintained path. `dna_analyser.save_anonymous_session_data` is the divergent code.

**Files:** `core/services/top_books_service.py` (read for reference), `core/services/dna_analyser.py` `save_anonymous_session_data` L338–352.

**Acceptance Criteria:**
- [ ] Replace the anonymous scoring block in `save_anonymous_session_data` with the canonical formula. Either extract a shared helper (`compute_book_score(rating, sentiment) -> int`) in `top_books_service.py` and call it from both places, or copy the formula inline with a `# Canonical: see top_books_service.compute_book_score` comment.
- [ ] If extracted: the helper is module-level, takes scalar inputs, has its own unit test in `test_fiction_book_extremes.py`.
- [ ] **Behavior change documented:** anonymous users may see different top books after this lands. Add `progress.txt` line: "US-040: anonymous top-book ranking now matches authenticated formula. Pre-change anonymous DNA snapshots may show different `top_books` after re-analysis."
- [ ] **Test:** add a unit test that calls the helper (or the inlined formula) with the same fixture inputs and asserts identical output across auth and anon paths.
- [ ] `manage.py test core.tests.test_fiction_book_extremes` passes.

---

#### US-041: Promote magic numbers to named constants
**Description:** As a maintainer, I want recurring magic numbers labelled.

**Files:** `core/views.py`, `core/tasks.py`, `core/services/recommendation_service.py`.

**Acceptance Criteria:**
- [ ] Add module-level constants:
  - `core/views.py`: `MAX_UPLOAD_SIZE_BYTES = 10 * 1024 * 1024`, `DNA_CACHE_TTL = 3600`.
  - `core/tasks.py`: `BATCH_LIMIT = 20`, `AGE_THRESHOLD_DAYS = 90`.
  - `core/services/recommendation_service.py`: `RECOMMENDATION_WEIGHTS = {...}` capturing 0.1, 0.15, 0.3, 0.5, 0.8 with descriptive keys.
- [ ] All inline literals replaced.
- [ ] `manage.py test` passes.

---

#### US-042: Logger severity drift + missing `exc_info=True`
**Description:** As a maintainer, I want consistent log severity and tracebacks for caught exceptions.

**Files:**
- `core/tasks.py` L61, L89, L123, L247 — drop `error` to `warning` for `DoesNotExist` outcomes.
- `core/services/dna_analyser.py` L315 — add `exc_info=True`.
- `core/services/anonymization_service.py` L54 — add `exc_info=True`.
- `core/services/llm_service.py` L117 — log with `exc_info=True` instead of returning the error string silently.

**Acceptance Criteria:**
- [ ] Each line updated.
- [ ] `manage.py test` passes.

---

### Phase 8 — Architecture splits (PRs cut per split, after Phase 7 lands)

> **Important:** Each of these stories is itself broken into substeps so a single Ralph context window can complete it. The bigger splits get a story per source file moved.

#### US-043: Move `core/book_enrichment_service.py` into `core/services/`
**Description:** As a maintainer, I want every service under `core/services/`.

**Files:** `core/book_enrichment_service.py` (move), all call sites.

**Acceptance Criteria:**
- [ ] `git mv core/book_enrichment_service.py core/services/book_enrichment_service.py`.
- [ ] Update imports across `core/tasks.py`, tests, and any other callers.
- [ ] `manage.py test` passes.
- [ ] `progress.txt`: now all services live under `core/services/`.

---

#### US-044: Create `core/views/` package skeleton with `__init__.py` re-exports
**Description:** As a maintainer, I want the views package introduced before splitting content into it.

**Files:** `core/views.py` → `core/views/__init__.py` (single-file package), `core/urls.py` (no change required if re-exports include all symbols).

**Acceptance Criteria:**
- [ ] `git mv core/views.py core/views/__init__.py`.
- [ ] Verify `core/urls.py` imports still resolve (`from . import views`).
- [ ] `python manage.py check` passes (no import errors).
- [ ] `python -c 'from bibliotype.urls import urlpatterns; print(len(urlpatterns))'` prints the expected URL count.
- [ ] `manage.py test` passes.

---

#### US-044b: Extract `_compute_enrichment_*` helpers and `_enrich_dna_for_display` into `core/views/_helpers.py`
**Description:** As a maintainer, I want the four private helpers — used by multiple views that will land in different modules — extracted into a shared helper module BEFORE moving any view symbols.

**Files:** `core/views/__init__.py` (still contains everything post-US-044), `core/views/_helpers.py` (new).

**Acceptance Criteria:**
- [ ] Create `core/views/_helpers.py`. Move `_compute_enrichment_stats`, `_recalculate_enrichment_stats`, `_compute_enrichment_progress`, `_enrich_dna_for_display` into it (keep the leading underscore — internal API).
- [ ] In `core/views/__init__.py`, replace the helper bodies with `from ._helpers import _compute_enrichment_stats, _recalculate_enrichment_stats, _compute_enrichment_progress, _enrich_dna_for_display`.
- [ ] `python manage.py check` passes.
- [ ] `manage.py test` passes.
- [ ] **Risk note:** these helpers DB-write inside a GET handler. Don't change any of that behavior in this story — only the file location.

---

#### US-045: Move SEO views (`robots_txt_view`, `sitemap_xml_view`) into `core/views/seo.py`
**Description:** As a maintainer, isolate the SEO endpoints.

**Files:** `core/views/seo.py` (new), `core/views/__init__.py` (re-export).

**Acceptance Criteria:**
- [ ] `seo.py` contains both views.
- [ ] **Delete the moved view function bodies from `core/views/__init__.py`** and replace with `from .seo import robots_txt_view, sitemap_xml_view  # re-export`.
- [ ] `python manage.py check` passes; `python -c 'from bibliotype.urls import urlpatterns; print(len(urlpatterns))'` matches the pre-change count.
- [ ] `manage.py test` passes.

---

#### US-046: Move auth views (`signup_view`, `login_view`, `logout_view`, `CustomPasswordResetView`, `handler404`) into `core/views/auth.py`
**Description:** As a maintainer, isolate auth-related views.

**Files:** `core/views/auth.py` (new).

**Acceptance Criteria:**
- [ ] All five symbols moved into `auth.py`.
- [ ] **Delete the moved bodies from `core/views/__init__.py`** and replace with `from .auth import signup_view, login_view, logout_view, CustomPasswordResetView, handler404  # re-export`.
- [ ] `bibliotype/urls.py` import (`from core.views import CustomPasswordResetView, handler404`) still resolves.
- [ ] `python manage.py check` passes; `python -c 'from bibliotype.urls import urlpatterns; print(len(urlpatterns))'` matches pre-change count.
- [ ] `manage.py test core.tests.test_views_e2e core.tests.test_password_reset` passes.

---

#### US-047: Move upload + task-status views into `core/views/upload.py`
**Description:** As a maintainer, isolate upload-pipeline views.

**Files:** `core/views/upload.py` (new): `upload_view`, `task_status_view`, `get_task_result_view`, `check_dna_status_view`, `enrichment_status_view`, `check_recommendations_status_view`.

**Acceptance Criteria:**
- [ ] All six views moved.
- [ ] **Delete the moved bodies from `core/views/__init__.py`** and add re-export imports from `.upload`.
- [ ] `python manage.py check` passes; URL count check matches.
- [ ] `manage.py test core.tests.test_views_e2e core.tests.test_tasks_integration` passes.

---

#### US-048a: Move dashboard / public-profile views into `core/views/dashboard.py`
**Description:** As a maintainer, isolate display views.

**Files:** `core/views/dashboard.py` (new): `display_dna_view`, `public_profile_view`.

**Acceptance Criteria:**
- [ ] Both views moved. Helpers stay in `core/views/_helpers.py` (extracted in US-044b); `dashboard.py` imports them with `from ._helpers import _compute_enrichment_stats, _compute_enrichment_progress, _enrich_dna_for_display`.
- [ ] **Delete the moved bodies from `core/views/__init__.py`** and add re-export from `.dashboard`.
- [ ] `python manage.py check` passes; URL count check matches.
- [ ] `manage.py test` passes.

---

#### US-048b: Move static-page views into `core/views/pages.py`
**Description:** As a maintainer, isolate the four static-page views.

**Files:** `core/views/pages.py` (new): `home_view`, `about_view`, `privacy_view`, `terms_view`.

**Acceptance Criteria:**
- [ ] All four views moved.
- [ ] **Delete moved bodies from `core/views/__init__.py`** and add re-export from `.pages`.
- [ ] `python manage.py check` passes; URL count check matches.
- [ ] `manage.py test` passes.

---

#### US-049: Move profile-update views into `core/views/profile.py`
**Description:** As a maintainer, isolate profile-mutation views. By the time this story runs, `update_recommendation_visibility` already includes the cache-invalidation logic added in US-024c (Phase 4) — moving the function brings that logic with it, no extra work needed.

**Files:** `core/views/profile.py` (new): `update_privacy_view`, `update_display_name_view`, `update_username_api`, `update_recommendation_visibility`.

**Acceptance Criteria:**
- [ ] All four views moved.
- [ ] **Delete moved bodies from `core/views/__init__.py`** and add re-export from `.profile`.
- [ ] `python manage.py check` passes; URL count check matches.
- [ ] `manage.py test core.tests.test_profile_and_recommendations` passes.

---

#### US-050: Convert `core/services/dna_analyser.py` to `core/services/dna/` package — step 1 (skeleton + utility helpers)
**Description:** As a maintainer, start the dna analyser split with a backwards-compat package skeleton.

**Files:** `core/services/dna_analyser.py` → `core/services/dna/__init__.py` (re-exports `calculate_full_dna` and any existing public symbols), new `core/services/dna/utils.py` containing `_isbn_to_isbn13`, `_build_cover_url`, `_cover_initial`, `_sanitize_review_text`.

**Acceptance Criteria:**
- [ ] `__init__.py` re-exports preserve every public symbol that callers reference.
- [ ] All existing imports (`from core.services.dna_analyser import calculate_full_dna`) still work via the package's `__init__.py`. Add a deprecation comment with a 30-day shim plan.
- [ ] `manage.py test` passes.

---

#### US-051: Move `_detect_and_normalize_csv` and CSV parsing into `core/services/dna/csv_parser.py`
**Description:** As a maintainer, isolate CSV detection and normalization.

**Files:** `core/services/dna/csv_parser.py` (new), import updates in `core/services/dna/__init__.py`, `core/dna_constants.py` (receives `STORYGRAPH_TAG_TO_GENRE`).

**Acceptance Criteria:**
- [ ] Move `_detect_and_normalize_csv` and CSV-only helpers into `csv_parser.py`.
- [ ] Move `STORYGRAPH_TAG_TO_GENRE` into `core/dna_constants.py` (per architecture review).
- [ ] **Delete moved code from `core/services/dna/__init__.py`** (or its temporary monolithic source); add `from .csv_parser import _detect_and_normalize_csv` re-export.
- [ ] `manage.py test core.tests.test_storygraph_integration` passes.

---

#### US-052: Move `process_book_row` + book-sync into `core/services/dna/book_sync.py`
**Description:** As a maintainer, isolate the book-sync hot path.

**Files:** `core/services/dna/book_sync.py` (new).

**Acceptance Criteria:**
- [ ] Move `process_book_row` and the `ThreadPoolExecutor` orchestration block.
- [ ] **Delete moved code from `core/services/dna/__init__.py`**; add re-export.
- [ ] `manage.py test core.tests.test_tasks_integration` passes.

---

#### US-053: Move `assign_reader_type` into `core/services/dna/reader_type.py`
**Description:** As a maintainer, isolate reader-type classification.

**Files:** `core/services/dna/reader_type.py` (new); promote `DIVERSITY_THRESHOLD`, `DIVERSITY_BONUS` to module-level constants there.

**Acceptance Criteria:**
- [ ] Move function and constants.
- [ ] **Delete moved code from `core/services/dna/__init__.py`**; add re-export.
- [ ] `manage.py test core.tests.test_math_accuracy` passes.

---

#### US-054: Move DNA persistence (`_save_dna_to_profile`, `save_anonymous_session_data`) into `core/services/dna/persistence.py`
**Description:** As a maintainer, isolate DNA-persistence orchestration.

**Files:** `core/services/dna/persistence.py` (new).

**Acceptance Criteria:**
- [ ] Both functions moved; lazy import of `..tasks.generate_recommendations_task` retained but consolidated into one place.
- [ ] **Delete moved code from `core/services/dna/__init__.py`**; add re-export.
- [ ] `manage.py test core.tests.test_views_e2e core.tests.test_tasks_integration` passes.

---

#### US-055: Slim `core/services/dna/__init__.py` and remove the old single-file `dna_analyser.py` shim
**Description:** As a maintainer, finalize the dna split.

**Files:** `core/services/dna/__init__.py`; delete any leftover backwards-compat shim file.

**Acceptance Criteria:**
- [ ] `__init__.py` exports `calculate_full_dna` (and any public helper) from the new submodules.
- [ ] No file `core/services/dna_analyser.py` remains. (Or, if the shim is needed for one more deploy, document in `progress.txt` with a removal date.)
- [ ] `manage.py test` passes.

---

#### US-056: Convert `core/tasks.py` into `core/tasks/` package — step 1 (skeleton)
**Description:** As a maintainer, introduce the tasks package without breaking Celery autodiscovery.

**Files:** `core/tasks.py` → `core/tasks/__init__.py` (re-exports every existing task).

**Acceptance Criteria:**
- [ ] Celery autodiscovery still finds tasks (run `celery -A bibliotype inspect registered` or rely on the existing test that imports tasks).
- [ ] `manage.py test` passes.

---

#### US-057: Move DNA tasks into `core/tasks/dna.py`
**Description:** As a maintainer, isolate DNA tasks.

**Files:** `core/tasks/dna.py` (new): `generate_dna_task`, `claim_anonymous_dna_task`, helper `_create_userbooks_from_anonymous_session`.

**Acceptance Criteria:**
- [ ] Tasks importable both directly (`from core.tasks.dna import ...`) and via `core.tasks.__init__`.
- [ ] **Delete moved code from `core/tasks/__init__.py`**; add re-export from `.dna`.
- [ ] **Celery autodiscovery check:** in a dev container, run `celery -A bibliotype inspect registered` (or, if a worker isn't running, `python -c "from bibliotype.celery import app; print([t for t in app.tasks if t.startswith('core.')])"`) and confirm both `core.tasks.generate_dna_task` and `core.tasks.claim_anonymous_dna_task` appear. Capture the output line in `progress.txt`.
- [ ] `manage.py test core.tests.test_tasks_integration` passes.

---

#### US-058: Move enrichment tasks into `core/tasks/enrichment.py`
**Description:** As a maintainer, isolate enrichment tasks.

**Files:** `core/tasks/enrichment.py` (new): `enrich_book_task`, `check_author_mainstream_status_task`, `research_publisher_mainstream_task`.

**Acceptance Criteria:**
- [ ] All three tasks moved.
- [ ] **Delete moved code from `core/tasks/__init__.py`**; add re-export from `.enrichment`.
- [ ] **Celery autodiscovery check** (same command as US-057); confirm all three appear in `app.tasks`.
- [ ] `manage.py test core.tests.test_integration core.tests.test_enrichment_stats` passes.

---

#### US-059: Move recommendations + maintenance tasks into `core/tasks/recommendations.py` and `core/tasks/maintenance.py`
**Description:** As a maintainer, finish the tasks split.

**Files:**
- `core/tasks/recommendations.py` (new): `generate_recommendations_task`.
- `core/tasks/maintenance.py` (new): `anonymize_user_task`, `run_management_command_task`.

**Acceptance Criteria:**
- [ ] Tasks moved.
- [ ] **`core/tasks/__init__.py` is now a thin import-aggregator only** — its sole content is `from .dna import *; from .enrichment import *; from .recommendations import *; from .maintenance import *` (with explicit `__all__` lists in each submodule).
- [ ] **Celery autodiscovery check** (same command as US-057); confirm all 6+ tasks appear.
- [ ] `manage.py test` passes.

---

#### US-060: Drop the `core/services/recommendation_service.py` `safe_cache_*` re-export
**Description:** As a maintainer, I want tests patching `core.cache_utils` directly.

**Files:** `core/services/recommendation_service.py` L8 (the `# noqa: F401` re-export), `core/tests/test_cache_refactor.py` (and any other test that imports `safe_cache_*` from the service).

**Acceptance Criteria:**
- [ ] Re-export removed.
- [ ] Tests updated to patch `core.cache_utils.safe_cache_get` etc.
- [ ] `manage.py test core.tests.test_cache_refactor` passes.

---

## 6. Functional Requirements (cross-cutting)

- **FR-1:** No story may be marked `passes: true` without the listed test command exiting 0.
- **FR-2:** Every story commit must include the touched files plus a one-line `progress.txt` learning when the discovery is non-obvious.
- **FR-3:** Stories that establish a new project-wide invariant (e.g., `DEBUG=False` default, services live under `core/services/`) must add a line to `AGENTS.md`.
- **FR-4:** No story may introduce a new dependency without updating `pyproject.toml` and `poetry.lock` in the same commit.
- **FR-5:** No `print()` statements, no `.format()`, no `%` formatting in production code (CLAUDE.md baseline) — Ralph must verify.
- **FR-6:** Phase 1 (security critical) stories may not be skipped or deferred.
- **FR-7:** Phase 8 (architecture splits) may not begin until all of Phase 1–7 has passed.
- **FR-8:** Migration-touching stories (US-028, US-029) include `--dry-run` check and a `migrate` round-trip in the acceptance criteria.

## 7. Non-Goals (Out of Scope)

- **No migration squash.** Defer per scope choice 5B.
- **No new application features.** This PRD strictly cleans up existing behavior.
- **No refactor of `core/admin.py`** beyond the `book_count` annotate (US-026) and the whitelist-constant import (US-005).
- **No dependency upgrades** (Django, Celery, etc.) unless required by a story.
- **No Server-Sent Events** replacement for the polling endpoints (mentioned in the perf review's architecture recommendations) — defer to a future PRD.
- **No introduction of class-based views** — keep FBV per CLAUDE.md.
- **No simplification of fundamentally working code** that wasn't flagged by the triage.

## 8. Design Considerations

- **Backwards compatibility:** Phase 1 security fixes leave a 30-day legacy compat path for in-flight task_ids predating the new code (US-002). After 30 days, that path should be removed in a follow-up.
- **Test-data exposure:** US-008 removes personally-identifiable Goodreads CSVs from the repo. This is a privacy fix in addition to a hygiene one.
- **Dev-browser verification:** Stories US-019, US-026, US-032 each ship a UI-visible change and require manual `dev-browser` skill verification.

## 9. Technical Considerations

- **Feedback loops:** the project is Python with no `mypy`. Ralph's "typecheck" step is `python manage.py check`; the actual safety net is the test suite plus migrations sanity (`makemigrations --check --dry-run`).
- **Celery in tests:** every task-touching story relies on `@override_settings(CELERY_TASK_ALWAYS_EAGER=True, CELERY_TASK_EAGER_PROPAGATES=True)`. Don't introduce real broker calls.
- **Redis in tests:** use `LocMemCache` per CLAUDE.md.
- **Migration replays:** `manage.py test` runs migrations from scratch, so a broken migration breaks the whole test suite.
- **Docker is the canonical dev env.** All test commands assume `docker-compose -f docker-compose.local.yml exec web poetry run ...`.
- **The `claude/worktrees/` directory** exists from prior sessions and should be ignored by every story (do not edit files inside `.claude/worktrees/`).

## 10. Success Metrics

- **Security:** all 3 Critical and ~6 High security findings closed; `python manage.py check --deploy` reports zero warnings for the items in US-015.
- **Performance:** typical 1000-book CSV upload completes with <500 SQL queries (down from estimated 4–6k); admin Genre/Publisher changelist completes in 1 query + N rows (down from N+1).
- **Dead code:** `git ls-files | xargs wc -l` drops by >300 LOC across deleted scripts, view, template, mgmt commands.
- **Architecture:** no source file in `core/` exceeds 800 LOC after Phase 8.
- **CI:** every commit on the branch is green.
- **Convention drift:** zero `datetime.utcnow()` calls; one Gemini model identifier; one `OPEN_LIBRARY_COVER_URL` builder.

## 11. Open Questions (resolved during PRD review)

- **Q1.** ✅ Resolved in US-017: legitimate users with existing accounts receive a password-reset email; the generic "check your inbox" page is shown either way.
- **Q2.** ✅ Resolved in US-027: ships behind `ENABLE_PARALLEL_ENRICHMENT` flag (default `False`). Operator flips when API headroom confirmed.
- **Q3.** ✅ Resolved in US-040: canonical formula is `top_books_service.py` (rating-weight 100/80/×15 + sentiment×30).
- **Q4.** ✅ Resolved in US-033: blocked-prereq AC requires a `progress.txt` line confirming `regenerate_dna` ran in production. Story exits without code changes if absent.
- **Q5.** ✅ Resolved in US-000: `AGENTS.md` at repo root.

---

## 12. Appendix: PR cut points

| After story | Open PR title | Approx LOC delta |
|---|---|---|
| US-005 | `security: bind anon DNA tasks to session, fix DEBUG default, lock task whitelist` | +250 / −20 |
| US-014 | `chore: remove dead code, scripts, profiles, personal CSVs, test fixtures` | +60 / −650 |
| US-020 | `security: add HSTS, rate-limit login + username API, generic signup, CSV schema cap` | +160 / −30 |
| US-027b | `perf: bulk-prefetch upload, cache stampede locks, admin annotate, regex precompile` | +120 / −90 |
| US-029 | `db: add UserProfile composite index + Book.google_books_last_checked index` | +30 |
| US-035b | `refactor: drop RecommendationEngine, kill duplicated comparative_text, dedupe canonicalization` | +60 / −350 |
| US-042 | `chore: pattern normalization (utcnow, gemini model, magic numbers, log levels)` | +120 / −80 |
| US-043 | `refactor: move book_enrichment_service into core/services/` | +20 / −20 |
| US-049 | `refactor: split core/views.py into views/ package` | +220 / −110 |
| US-055 | `refactor: split dna_analyser into core/services/dna/ package` | +200 / −100 |
| US-059 | `refactor: split tasks.py into core/tasks/ package` | +200 / −100 |
| US-060 | `chore: drop cache-helper re-export from recommendation_service` | +5 / −5 |

---

## 13. Story summary table

| ID | Phase | One-line |
|---|---|---|
| US-000 | 0 | Create `AGENTS.md` and `progress.txt` scaffolding |
| US-001 | 1 | Bind anon task_id to session + Redis key registry |
| US-002 | 1 | Reject unowned task_id in result endpoint (flag-gated) |
| US-003 | 1 | Bind task_id_to_claim to pre-login session_key |
| US-004 | 1 | DEBUG defaults False + production assert |
| US-005 | 1 | Move mgmt-command whitelist into task |
| US-006 | 2 | Delete `check_models.py` |
| US-007 | 2 | Untrack 110 `.prof` files |
| US-008a-i | 2 | Move synthetic CSV files + introduce `TEST_FIXTURES_DIR` |
| US-008a-ii | 2 | Update test imports to new fixtures path |
| US-008b | 2 | Delete personal CSVs + gitignore stray paths |
| US-009 | 2 | Move CSV generators into mgmt commands |
| US-010 | 2 | Delete `catch_all_404_view` |
| US-011 | 2 | Delete unused `card_full.html` template |
| US-012 | 2 | Delete dead mgmt commands batch 1 |
| US-013 | 2 | Delete dead mgmt commands batch 2 |
| US-014 | 2 | Remove unused imports |
| US-015 | 3 | Add HSTS / Referrer-Policy / nosniff |
| US-016 | 3 | Rate-limit login |
| US-017 | 3 | Generic signup duplicate-email + reset email |
| US-018 | 3 | CSV row + column + schema validation |
| US-019 | 3 | Replace `messages\|safe` with template fragment |
| US-020 | 3 | Rate-limit `update_username_api` |
| US-021 | 4 | `max_workers=8` for book sync (5x repeat test) |
| US-022 | 4 | Drop redundant per-row queries (with assertNumQueries) |
| US-023 | 4 | `.delay()` recommendations from `_save_dna_to_profile` |
| US-024 | 4 | Sentinel-guard recs dispatch |
| US-024b | 4 | `update_or_create` for AnonymousUserSession recreation |
| US-024c | 4 | Invalidate recs cache on visibility-change |
| US-025 | 4 | Replace `order_by("?")` with random sample |
| US-026 | 4 | Annotate `book_count` in admin |
| US-027 | 4 | Drop `time.sleep` (flag-gated) |
| US-027b | 4 | Precompile genre canonicalization regex |
| US-028 | 5 | Index `Book.google_books_last_checked` |
| US-029 | 5 | UserProfile composite partial index |
| US-030 | 6 | Delete duplicated `comparative_text` block |
| US-031a | 6 | Convert `RecommendationEngine` to module functions |
| US-031b | 6 | Drop unused recommendation parameters |
| US-032 | 6 | Bake `rec["book"]` into stored recs (dual-shape) |
| US-033 | 6 | Remove legacy backfills (Q4-blocked) |
| US-034 | 6 | Inline trivial defensive checks |
| US-035 | 6 | Collapse claim_anonymous_dna_task branches |
| US-035b | 6 | Dedupe Google Books canonicalization (with snapshot) |
| US-036 | 7 | `datetime.utcnow` → `timezone.now` |
| US-037 | 7 | One Gemini model id + central client |
| US-038 | 7 | Centralize Open Library cover URL |
| US-039 | 7 | Hoist `genre_priority` constant |
| US-040 | 7 | Reconcile top-book scoring (canonical: top_books_service) |
| US-041 | 7 | Magic numbers → named constants |
| US-042 | 7 | Logger severity drift + `exc_info=True` |
| US-043 | 8 | Move `book_enrichment_service` into services |
| US-044 | 8 | Create `core/views/` package skeleton |
| US-044b | 8 | Extract `_compute_enrichment_*` helpers to `_helpers.py` |
| US-045 | 8 | Move SEO views (delete from `__init__`) |
| US-046 | 8 | Move auth views (delete from `__init__`) |
| US-047 | 8 | Move upload + task-status views |
| US-048a | 8 | Move dashboard + public-profile views |
| US-048b | 8 | Move static-page views |
| US-049 | 8 | Move profile views |
| US-050 | 8 | Skeleton `core/services/dna/` package |
| US-051 | 8 | Move CSV parser into dna package |
| US-052 | 8 | Move book_sync into dna package |
| US-053 | 8 | Move reader_type into dna package |
| US-054 | 8 | Move persistence into dna package |
| US-055 | 8 | Drop dna_analyser.py shim |
| US-056 | 8 | Skeleton `core/tasks/` package |
| US-057 | 8 | Move DNA tasks (autodiscovery check) |
| US-058 | 8 | Move enrichment tasks (autodiscovery check) |
| US-059 | 8 | Move recs + maintenance tasks |
| US-060 | 8 | Drop cache-helper re-export |

**Total stories:** 70 across 8 phases (Phase 0 scaffolding + 5 critical security + 12 dead-code/hygiene + 6 high-severity security + 11 perf + 2 indexes + 8 simplification + 7 patterns + 18 architecture splits).
