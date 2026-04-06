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
    person_profiles: 'identified_only',  // No anon person profiles
    cookieless_mode: 'always',           // No cookies needed
});
// Authenticated users: posthog.identify('{{ user.id }}')
```

Context processor `posthog_settings` provides `POSTHOG_API_KEY` to templates.

## Active Middleware

- `PostHogExceptionMiddleware` — **ACTIVE** (in settings.py middleware stack). Catches unhandled exceptions, sanitizes error info, tracks as `"exception"` event. **Production only.**
- `PostHogPageviewMiddleware` — **DEFINED BUT NOT ACTIVE** (not in middleware stack)

## Event Registry

**DNA lifecycle:**
- `file_upload_started` — CSV uploaded (views.py)
- `dna_generation_started` / `completed` / `failed` — Task lifecycle (tasks.py)
- `anonymous_dna_generated` — Anonymous success (tasks.py)
- `dna_displayed` / `anonymous_dna_displayed` — Dashboard viewed (views.py)

**Authentication:**
- `user_signed_up` — With `signup_source`: "with_task_claim", "with_session_dna", "before_dna"
- `user_logged_in` — With `had_dna_in_session` flag
- `anonymous_dna_claimed` — Anonymous DNA transferred to account

**Profile & settings:**
- `profile_made_public` — Privacy toggle
- `public_profile_viewed` — With viewer/owner context
- `settings_updated` — With `setting_type`: "display_name" or "recommendation_visibility"
- `recommendations_generated` — With count

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
