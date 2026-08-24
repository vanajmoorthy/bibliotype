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

| Task | Bind | Max Retries | Rate Limit | Countdown | ignore_result |
|------|------|-------------|------------|-----------|---------------|
| `generate_reading_dna_task` | Yes | None | None | — | No |
| `claim_anonymous_dna_task` | Yes | 5 | None | Fixed 10s | No |
| `generate_recommendations_task` | Yes | 3 | None | `60 * 2^retries` | No |
| `enrich_book_task` | Yes | 3 | 60/min | `60 * 2^retries` | Yes |
| `check_author_mainstream_status_task` | No | None | None | — | Yes |
| `research_publisher_mainstream_task` | No | None | None | 2s sleep between | No |
| `anonymize_expired_sessions_task` | No | None | None | — | No |
| `run_management_command_task` | No | None | None | — | No |

## Queues (bulk vs interactive)

Two queues, one worker, deterministic priority:

- **`celery`** (default) — interactive work: uploads, DNA generation, recommendations, inline-fallback enrichment.
- **`enrichment_bulk`** (`ENRICHMENT_BULK_QUEUE` in `core/dna_constants.py`) — bulk enrichment: `seed_from_exports` (via `calculate_full_dna(..., bulk_enrichment=True)`, which also skips inline enrichment) and `enrich_books` backfills.

Rules that keep this working:

- Priority comes from `CELERY_BROKER_TRANSPORT_OPTIONS = {"queue_order_strategy": "sorted"}` — alphabetical by queue NAME, so `celery` drains before `enrichment_bulk`. **Never switch to `"priority"`** (celery/celery#8673: hash-randomized order per worker start). Renaming the bulk queue must keep it sorting after `"celery"` (test-enforced in `test_enrichment_queue_routing.py`).
- `CELERY_WORKER_PREFETCH_MULTIPLIER = 1` — at most ~1 reserved bulk task runs ahead of a new interactive task. SLA: interactive enrichment starts within ~1–2 bulk task durations, not instantly.
- Worker commands need `-Q celery,enrichment_bulk` (selection only — order in `-Q` is irrelevant). A `celeryd_after_setup` guard in `bibliotype/celery.py` hard-exits any worker not consuming the bulk queue, so a bad `-Q` shows up as a crash loop instead of silently stranded messages.
- `enrich_book_task` keeps ONE name for both queues — `rate_limit` is per task name, so a second name would double external API throughput.
- `self.retry()` re-publishes with the original delivery info: bulk retries stay on the bulk queue.
- Do NOT use ETA-spread dispatch for bulk fan-outs: ETA tasks bypass prefetch and sit in worker RAM; ETAs past the 1h visibility timeout get redelivered as duplicates.
- Reverting the split strands anything still in `enrichment_bulk` — drain first, or `redis-cli -n 0 DEL enrichment_bulk` and re-run `enrich_books`.

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
