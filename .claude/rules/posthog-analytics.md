---
paths:
  - "core/analytics/**"
  - "core/views.py"
  - "core/tasks.py"
  - "core/cache_utils.py"
---

# PostHog Analytics

## Adding a New Event

1. Add a `track_*` function in `core/analytics/events.py`:
```python
def track_my_event(user_id, custom_prop):
    capture_event(
        distinct_id=str(user_id) if user_id else "anonymous",
        event_name="my_event",
        properties={"user_id": user_id, "custom_prop": custom_prop},
    )
```

2. Export in `core/analytics/__init__.py`
3. Call from your view/task: `from core.analytics import track_my_event`

## Conventions

- **Event names:** snake_case (`file_upload_started`, `dna_generation_completed`)
- **Distinct IDs:** `str(user.id)` for authenticated, `session.session_key` for anonymous, `"system"` for infrastructure events
- **All events** automatically include `environment` ("production"/"development") and `server_hostname`
- **Error messages:** Always truncated to 500 chars, sensitive patterns (api_key, password, secret, token) regex-stripped

## Client Initialization

- Lazy init on first `capture_event()` call — not at startup
- API key from `POSTHOG_API_KEY` env var
- EU instance: `https://eu.i.posthog.com`
- Missing API key silently disables all tracking (logs warning once)

## Frontend (`base.html`)

```javascript
posthog.init(API_KEY, {
    api_host: 'https://eu.i.posthog.com',
    defaults: '2025-05-24',
    person_profiles: 'identified_only',   // No anon person profiles
    persistence: 'localStorage',          // No cookies (localStorage only)
    capture_exceptions: true,             // Error tracking: JS exception autocapture
    session_recording: { maskAllInputs: true },
});
// Authenticated users: posthog.identify('{{ user.id }}')
```

Do NOT re-add `cookieless_mode` — it requires a server-side "cookieless server hash mode" project toggle (events are silently dropped without it), hard-disables session replay and surveys, and conflicts with `identify()`.

Session replay and JS exception autocapture also need their project-level toggles enabled in PostHog settings (Session replay → "Record user sessions"; Error tracking → "Exception autocapture").

Context processor `posthog_settings` provides `POSTHOG_API_KEY` to templates.

## Active Middleware

- `PostHogExceptionMiddleware` — **ACTIVE** (in settings.py middleware stack). Catches unhandled exceptions, sanitizes error info, sends a `$exception` event via `posthog.capture_exception()` → PostHog Error Tracking product. **Production only.**
- `PostHogPageviewMiddleware` — **DEFINED BUT NOT ACTIVE** (not in middleware stack)
- Celery: `task_failure` signal handler in `bibliotype/celery.py` sends unhandled task exceptions to Error Tracking (distinct_id = "system", production only)

## Event Registry

**DNA lifecycle:**
- `file_upload_started` — CSV uploaded (views.py)
- `dna_generation_started` / `completed` / `failed` — Task lifecycle (tasks.py); `completed` carries `reader_type`
- `anonymous_dna_generated` — Anonymous success (tasks.py), carries `reader_type`
- `dna_displayed` / `anonymous_dna_displayed` — Dashboard viewed (views.py), carry `reader_type`

**Authentication:**
- `user_signed_up` — With `signup_source`: "with_task_claim", "with_session_dna", "before_dna"
- `user_logged_in` — With `had_dna_in_session` flag
- `anonymous_dna_claimed` — Anonymous DNA transferred to account

**Profile & settings:**
- `profile_made_public` — Privacy toggle
- `public_profile_viewed` — distinct_id is always the VIEWER (user id, session key, or "anonymous" — never the owner). Properties: `profile_username`, `profile_user_id`, `viewer_type` ("owner"/"authenticated"/"anonymous"), `profile_reader_type`, `profile_books_count`
- `settings_updated` — With `setting_type`: "display_name" or "recommendation_visibility"
- `recommendations_generated` — Fires when recs are actually generated (Celery task for auth users with `source="task"` + `reader_type`, `similar_users_count`, `max_similarity_pct`, `uniqueness_label`; inline for anonymous with `source="anonymous_dashboard_view"`)
- `recommendations_displayed` — Fires per dashboard render with stored recs (count + `reader_type`)

**LLM vibe (production only, never captures vibe text or book data):**
- `vibe_requested` — Per DNA calculation, with `cache_hit` (DB hash match) — headline LLM cost metric
- `vibe_generation_completed` — `provider`, `model`, `latency_ms`, `prompt_chars`, `response_chars` (distinct_id = "system")
- `vibe_generation_failed` — `error_type` (JSONDecodeError/UnexpectedFormat/exception class), truncated `error_message`, `latency_ms` (distinct_id = "system")

**Infrastructure (distinct_id = "system"):**
- `external_api_call` — Open Library/Google Books calls with status (success/error/not_found)
- `redis_cache_error` — Cache failures with operation/key/error (production only)

## Error Tracking in Cache (`cache_utils.py`)

`track_redis_cache_error(operation, key, error_type, error_message)`:
- Sanitizes long keys: `key[:50] + "..." + key[-50:]` if >100 chars
- Truncates error messages to 500 chars
- **Production only** — skips in development

## Graceful Failure

All tracking operations are wrapped in try/except. If PostHog is down or misconfigured, the app continues normally — events are simply lost.
