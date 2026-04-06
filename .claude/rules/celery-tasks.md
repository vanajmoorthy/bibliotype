---
paths:
  - "core/tasks.py"
  - "bibliotype/celery.py"
---

# Celery Tasks

## Configuration

- **Broker:** Redis DB 0 (`CELERY_BROKER_URL`, default `redis://localhost:6379/0`)
- **Result backend:** Redis DB 0 (`CELERY_RESULT_BACKEND`)
- **Serializer:** JSON only
- **Timezone:** UTC
- **Broker retry on startup:** Disabled (prevents hanging)
- **Connection timeout:** 5 seconds

## Task Registry

| Task | Bind | Max Retries | Rate Limit | Countdown |
|------|------|-------------|------------|-----------|
| `generate_reading_dna_task` | Yes | None | None | — |
| `claim_anonymous_dna_task` | Yes | 5 | None | Fixed 10s |
| `generate_recommendations_task` | Yes | 3 | None | `60 * 2^retries` |
| `enrich_book_task` | Yes | 3 | 30/min | `60 * 2^retries` |
| `check_author_mainstream_status_task` | No | None | None | — |
| `research_publisher_mainstream_task` | No | None | None | 2s sleep between |
| `anonymize_expired_sessions_task` | No | None | None | — |
| `run_management_command_task` | No | None | None | — |

## Celery Beat Schedule

- `anonymize_expired_sessions_task`: Daily at 2:00 AM UTC
- `research_publisher_mainstream_task`: Weekly on Sundays at 3:00 AM UTC

## Task Chain: Upload → DNA → Recommendations

```
upload_view
├─ Auth user: generate_reading_dna_task.delay(csv_content, user.id)
└─ Anon user: generate_reading_dna_task.delay(csv_content, None, session_key)
    ↓
generate_reading_dna_task
├─ Parses CSV (Goodreads/StoryGraph auto-detected)
├─ For each book:
│   ├─ Create/fetch Author → if new: check_author_mainstream_status_task.delay()
│   └─ Create/fetch Book → if new/no genres: enrich_book_task.delay() [rate-limited]
├─ Create UserBook records (auth only)
├─ Calculate stats, reader type, vibe (LLM)
├─ Auth: _save_dna_to_profile() → generate_recommendations_task(user.id) [inline]
└─ Anon: cache dna_result_{task_id} + session_key_{task_id} (1hr TTL)
```

## Progress Tracking

`generate_reading_dna_task` passes a `progress_cb` to `calculate_full_dna()`:
```python
self.update_state(state="PROGRESS", meta={"current": N, "total": M, "stage": "description"})
```

**Stages:** "Parsing your library" → "Syncing books" → "Crunching stats" → "Finishing up"

Progress updates are wrapped in try/except — they fail silently if the result backend is unavailable.

**Frontend polling:** `get_task_result_view` returns progress JSON every 3 seconds. Percentage calculated client-side with stage-based caps (syncing: 70%, crunching: 90%, finishing: 98%).

## Anonymous → Claim Flow

```
claim_anonymous_dna_task(user_id, task_id)
1. Check cache: safe_cache_get("dna_result_{task_id}")
2. If cached: save to profile, create UserBooks from AnonymousUserSession
3. If not cached: check AsyncResult(task_id)
   - Ready + successful: save to profile
   - Ready + failed: clear pending_dna_task_id
   - Not ready: retry with 10s countdown (max 5 retries)
```

## Error Handling

- **All tasks:** Log errors via `logger.error(exc_info=True)`
- **DNA tasks:** Track failures in PostHog (`track_dna_generation_failed`)
- **Model.DoesNotExist:** Log and return early (don't retry)
- **Generic Exception:** Log and re-raise (triggers retry if configured)
- **Progress callback failures:** Silently caught (never blocks DNA generation)

## Testing

```python
@override_settings(
    CELERY_TASK_ALWAYS_EAGER=True,          # Tasks run synchronously
    CELERY_TASK_EAGER_PROPAGATES=True,      # Exceptions propagate
    CELERY_RESULT_BACKEND="django-db",
)
class MyTaskTest(TransactionTestCase):      # Must use TransactionTestCase
```

Always mock external service calls (`generate_vibe_with_llm`, `enrich_book_task.delay`, API calls).
