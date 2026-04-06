---
paths:
  - "core/views.py"
  - "core/tasks.py"
---

# User Flows

## Three Parallel Paths

### 1. Authenticated Upload
```
upload_view: POST CSV → validate (10MB, .csv) → read UTF-8-SIG
→ generate_reading_dna_task.delay(csv_content, user.id)
→ save pending_dna_task_id on UserProfile
→ clear session["dna_data"]
→ redirect to /dashboard/?processing=true
```

### 2. Anonymous Upload
```
upload_view: POST CSV → same validation
→ generate_reading_dna_task.delay(csv_content, None, session.session_key)
→ save task_id in session["anonymous_task_id"]
→ redirect to /task/<task_id>/
```

### 3. Login/Signup with Session DNA
- **Signup with task_id:** `?task_id=X` param → `claim_anonymous_dna_task.delay(user.id, task_id)` → redirect to `/dashboard/?processing=true`
- **Signup with session DNA:** Pop `session["dna_data"]` → `_save_dna_to_profile()` → redirect to `/dashboard/`
- **Login with session DNA:** If `dna_data` in session AND user has no existing DNA → save and redirect

## Status Polling (Two Different Endpoints)

**Authenticated:** `check_dna_status_view`
- Checks `profile.pending_dna_task_id` → `AsyncResult` state
- Returns `{"status": "PENDING"|"SUCCESS"|"FAILURE", "progress": {...}}`

**Anonymous:** `get_task_result_view`
- Checks Redis cache `dna_result_{task_id}` first (fast path)
- Falls back to `AsyncResult` if cache miss
- On SUCCESS: stores DNA in session, loads book data from `AnonymousUserSession`
- Returns `{"status": "SUCCESS", "redirect_url": "/dashboard/"}`

## Dashboard (`display_dna_view`)

**Authenticated:**
- Loads from `session["dna_data"]` first, then `profile.dna_data`
- Enriches with fresh community averages and percentiles (cached 10min)
- Loads recommendations from `profile.recommendations_data`
- If recommendations missing: triggers `generate_recommendations_task.delay()` and shows pending state
- Checks enrichment progress (genre/page_count completion on UserBooks)

**Anonymous:**
- Loads from `session["dna_data"]`
- Generates recommendations on-the-fly via `get_recommendations_for_anonymous(session_key)`
- If `AnonymousUserSession` expired: recreates from session data with 7-day expiry

## Session Data Keys

| Key | Type | Purpose |
|-----|------|---------|
| `dna_data` | dict | Complete DNA analysis result |
| `anonymous_task_id` | str | Task ID for progress polling |
| `book_ids` | list | Book IDs from AnonymousUserSession.books_data |
| `top_book_ids` | list | Top 5 book IDs |
| `book_ratings` | dict | {book_id: rating} for correlation |

## CAPTCHA (Cloudflare Turnstile)

- Checked on signup and password reset
- `verify_turnstile_token(token, remote_ip)` in `core/turnstile.py`
- **Dev mode:** Returns `True` if no `TURNSTILE_SECRET_KEY` set
- Frontend: `<div class="cf-turnstile" data-sitekey="...">`

## Public Profiles (`public_profile_view`)

- Checks `profile.is_public` (or viewer is owner)
- Enriches DNA with fresh percentiles/community data
- Reconstructs recommendation book dicts for template
- Tracks view via `track_public_profile_viewed()` with viewer info
- Returns 404 template (not Http404) for missing users

## Gotchas

- **Authentication is email-based** (case-insensitive `email__iexact` lookup), not username-based
- Anonymous DNA cache expires after 1 hour — claiming after that falls back to Celery result backend
- `session["dna_data"]` is only saved to profile on login if user has NO existing DNA
- `?processing=true` is just a UI hint — the view doesn't validate it
- CAPTCHA is production-only (bypassed when `TURNSTILE_SECRET_KEY` is unset)
- `claim_anonymous_dna_task` failure doesn't surface errors to the user
