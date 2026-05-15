# Bibliotype — Phase 1 Manual Regression Test (Chrome MCP)

You are an autonomous QA agent. Your job: run a manual smoke test of all essential Bibliotype features in a real browser, confirm Phase 1's security fixes don't regress anything visible to users, and write a pass/fail report.

You have the **chrome-devtools** MCP tools available (`new_page`, `navigate_page`, `take_screenshot`, `take_snapshot`, `click`, `fill`, `fill_form`, `wait_for`, `list_console_messages`, `evaluate_script`, etc.). Use them.

---

## Context (what just shipped)

- The branch under test is `triage/codebase-fixes` on the local Bibliotype repo at `/Users/vanajmoorthy/Desktop/github/bibliotype`.
- This is **Phase 1** of an 8-phase cleanup. The full PRD is at `tasks/prd-codebase-triage-fixes.md`; the relevant 5 stories are US-001..US-005 (see `progress.txt`).
- **What changed under the hood:**
  - Anonymous CSV uploads now bind a Redis cache entry `task_owner_<task_id>` to the visitor's session_key (US-001).
  - `GET /api/task-result/<task_id>/` checks that binding; if `ENFORCE_TASK_OWNERSHIP=True` it returns 403 on mismatch, otherwise it warn-and-allows (US-002).
  - Signup with `task_id_to_claim` validates the task belongs to the visitor's session BEFORE creating the user (US-003).
  - `DEBUG` env var now defaults to `False`. Local dev MUST set `DEBUG=True` explicitly (US-004). `docker-compose.local.yml` already does this.
  - The Celery `run_management_command_task` enforces its own whitelist (US-005). User-visible: unchanged. Server-internal: stronger.

You will not see the new env vars or cache keys in the UI. Your job is to confirm **all user-visible flows still work** and **the security paths behave correctly**.

---

## Environment

- App URL: **`http://localhost:8000`**
- Confirm the Docker stack is running first (`docker-compose -f docker-compose.local.yml ps`). If not, ask the user to run `docker-compose -f docker-compose.local.yml up -d` and pause.
- Test CSV fixtures live at `core/tests/fixtures/csv/` (or `csv/` if the move hasn't happened — try both). Pick a synthetic Goodreads export, e.g. `goodreads_library_export synthetic_lit_fiction1.csv`.
- For email-flow tests (signup, password reset), Django writes emails to console by default in dev. To inspect them, tail the web container: `docker-compose -f docker-compose.local.yml logs --tail=200 web | grep -A 20 "Subject:"`.
- For Redis cache inspection: `docker-compose -f docker-compose.local.yml exec redis redis-cli KEYS 'task_owner_*'`.

---

## Setup

1. **Open a browser page** at `http://localhost:8000`. Take a screenshot. Confirm the home page loads and shows the upload UI.
2. **Capture console messages** with `list_console_messages` and note any errors (an empty list is good; warnings are tolerable).
3. **Use evaluate_script to confirm DEBUG is off in browser-visible code paths** — there shouldn't be any Django debug pages. Try navigating to a deliberately broken URL like `http://localhost:8000/this-does-not-exist/` and verify you get the custom 404 page (the project's neobrutalist 404), NOT Django's yellow debug 500.

If the home page doesn't load, log it as a P0 and stop.

---

## Test suite

For each test below: run the actions, take a screenshot, capture console messages, note pass/fail, and continue to the next test even if one fails. Compile everything into a final report.

### T1 — Anonymous CSV upload happy path

1. Start on `/` in a fresh page (clear cookies first, or use `new_page` for an incognito-style window).
2. Take a screenshot of the upload form.
3. Use `fill` / `upload_file` to attach `goodreads_library_export synthetic_lit_fiction1.csv` to the file input. If a Turnstile CAPTCHA appears, it's stubbed in dev; just submit.
4. Click the submit button.
5. You'll be redirected to `/task/<task_id>/`. Take a screenshot of the polling/loading page.
6. **Wait** up to 3 minutes for DNA generation. Use `wait_for` polling. The page should auto-redirect to `/dashboard/` when DNA is ready.
7. On `/dashboard/`: take a screenshot. Verify these sections render:
   - "Reading DNA" heading with reader type
   - Top genres chart (Chart.js — wait for it to render)
   - Top books with cover art
   - Page-count / publish-year banners (may show a "still loading" state for enrichment)
   - Recommendations section (may still be loading — that's OK; just confirm it shows a skeleton/spinner)
8. **PASS** if the dashboard renders and the reader-type heading is visible.
9. **Capture the task_id from the URL** during step 5 and save it for T7.

### T2 — Authenticated signup (no DNA claim)

1. New page / fresh session. Navigate to `/signup/`.
2. Fill the form with a fresh email (e.g., `regression-T2-$(date +%s)@example.test`) and a strong password.
3. Submit.
4. Expected: redirected to `/dashboard/` (or `/upload/` if no DNA yet). Take a screenshot.
5. The user is now logged in. The header should show their email/username.
6. **PASS** if signup completed and the user is authenticated.

### T3 — Authenticated CSV upload

1. Continue from T2's logged-in session.
2. Navigate to `/upload/` if not already.
3. Upload `goodreads_library_export test_reader1.csv`.
4. Wait for DNA generation (same polling as T1).
5. Verify dashboard renders the user's DNA.
6. **PASS** if the dashboard shows fresh DNA for the just-uploaded library.

### T4 — Privacy toggle

1. Continue logged in. Navigate to `/dashboard/`.
2. Locate the privacy toggle (look for "Make profile public" / "Make profile private" button or switch).
3. Click it. A success message should appear (and may include a link to the public profile).
4. Take a screenshot of the success state.
5. Click to make public if not already, then navigate to `/u/<username>/` (the public profile URL — should appear in the success message or be derivable from the user's display name).
6. **PASS** if the public profile renders OR a "private" message appears when toggled off.

### T5 — Display name change

1. Logged in still. Look for a "Change display name" / settings UI (could be in a dropdown or on the dashboard).
2. Submit a new display name (e.g., `RegressionUser-T5`).
3. Confirm the new name appears on the dashboard / header.
4. **PASS** if the change persists across a page refresh.

### T6 — Logout + login

1. Logout (find the logout button/link).
2. Verify you're back on the home page or `/login/`. Take a screenshot.
3. Navigate to `/login/`. Submit the credentials from T2.
4. Verify you're back on the dashboard.
5. **NEGATIVE CASE**: logout again, navigate to `/login/`, submit a WRONG password. Expect a generic error message (not a stack trace). Take a screenshot.
6. **PASS** if both happy + error paths behave correctly.

### T7 — Cross-session task_id rejection (Phase 1 specific)

This is the regression test for the US-002 / US-003 security fix. It depends on whether `ENFORCE_TASK_OWNERSHIP=True` is set in the env (check the container: `docker-compose -f docker-compose.local.yml exec web env | grep ENFORCE_TASK_OWNERSHIP`).

1. **Open a fresh incognito-style page** (no cookies from prior tests).
2. Using the task_id you saved from T1, attempt to GET `/api/task-result/<task_id>/` directly (or attempt to view `/task/<task_id>/` then trigger a poll).
3. **If `ENFORCE_TASK_OWNERSHIP=True`:** expect a 403 response (JSON `{"status": "FORBIDDEN"}` or an error page). Take a screenshot. **PASS**.
4. **If `ENFORCE_TASK_OWNERSHIP=False` (the PR's default):** the response should still succeed (legacy warn-and-allow), but check the web container logs (`docker-compose -f docker-compose.local.yml logs --tail=100 web | grep "task_owner check skipped"`) and confirm a warning fired. **PASS** if either the 403 OR the warning is observed.
5. If neither happens, **FAIL** — the security path isn't wired.

### T8 — Signup-time DNA claim (positive + negative)

This is the regression for US-003.

**Positive case:**
1. Fresh page. Navigate to `/`.
2. Upload `goodreads_library_export synthetic_eclectic1.csv`.
3. While the task is running, navigate to `/signup/` from the SAME session (don't clear cookies).
4. The signup form should include the task_id as a hidden field, OR be reachable via a "save my DNA" link on the task status page.
5. Sign up with a fresh email. After signup, the new user's dashboard should show the DNA from the upload.
6. **PASS** if the claimed DNA appears on the new account's dashboard.

**Negative case (cross-session theft attempt):**
1. From T8's positive run, save the task_id.
2. Open a DIFFERENT incognito page (no cookies). Navigate to `/signup/?task_id=<the_task_id>` (the URL the signup form uses to pre-fill).
3. Try to submit signup with that task_id.
4. **Expected**: signup either rejects (form error: "couldn't claim" or similar) OR completes WITHOUT importing the foreign DNA. Take a screenshot.
5. **FAIL** if the attacker's new account inherits the victim's library.

### T9 — Password reset flow

1. Logged out. Navigate to `/login/`, click "Forgot password?".
2. Submit the email from T2.
3. Take a screenshot of the "check your inbox" confirmation page.
4. Tail the web container logs: `docker-compose -f docker-compose.local.yml logs --tail=200 web | grep -A 30 "Password reset"` — capture the reset link URL.
5. Open the reset link in the browser. Submit a new password.
6. Login with the new password. Verify dashboard loads.
7. **PASS** if all steps complete.

### T10 — Custom 404

1. Navigate to `http://localhost:8000/this-page-does-not-exist/`.
2. Verify the custom neobrutalist 404 page renders (NOT Django's debug page).
3. Take a screenshot.
4. **PASS** if it's the custom 404.

### T11 — Static asset smoke

1. On any rendered page, verify `static/dist/output.css` returns 200 and `static/src/...` returns 200.
2. Open dev tools → Network tab. Reload `/dashboard/`. Confirm no 404s on static assets.
3. Confirm Chart.js renders (the dashboard's chart should be visible — `evaluate_script` to read `document.querySelectorAll('canvas').length` and verify it's > 0).
4. **PASS** if no broken assets.

### T12 — Console-error sweep

After all the above, on the dashboard:
1. `list_console_messages` — count errors (severity `error`).
2. Compare to the Phase 1 baseline: errors expected = 0. Warnings tolerable (note them).
3. **PASS** if there are zero unexpected console errors.

---

## Reporting

Write the final report to `scripts/regression/reports/phase1-<timestamp>.md` with this structure:

```markdown
# Phase 1 Regression Report — <timestamp>

## Environment
- Branch: triage/codebase-fixes
- Commit: <git rev-parse HEAD>
- ENFORCE_TASK_OWNERSHIP: <value from container env>
- DEBUG: <value from container env>

## Summary
- Total tests: 12
- Passed: X
- Failed: Y
- Skipped: Z

## Test results

### T1 — Anonymous CSV upload happy path
- Status: PASS / FAIL
- Notes: ...
- Screenshot: <path>

(repeat for T2..T12)

## Console messages of note
- ...

## Server log messages of note
- (from `docker-compose -f docker-compose.local.yml logs --tail=500 web`)

## Recommendations
- Fail follow-ups (if any)
- Phase 2 readiness
```

Save all screenshots under `scripts/regression/reports/screenshots/<timestamp>/`.

---

## Hard rules

- **Do not modify any code.** This is a regression test, not a fix-it pass. If something is broken, log it; don't patch it.
- **Do not commit anything.** Your output is the report file and screenshots.
- **If Docker is down**, stop and ask the user to start it before continuing. Don't try to start it yourself.
- **If a test depends on a previous test's state** (T7 needs T1's task_id, T8b needs T8a's task_id), make sure to save state cleanly between steps.
- **If you can't perform a test** (e.g., CAPTCHA can't be bypassed in dev), mark it SKIP with the reason; don't fail it.
- **Take screenshots aggressively.** A picture is worth a thousand log lines.

Total time budget: ~45–60 min.

When done, output the absolute path of the report file and a one-line verdict: `READY / READY_WITH_NOTES / NOT_READY`.
