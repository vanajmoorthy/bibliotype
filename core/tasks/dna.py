"""DNA tasks: CSV → Reading-DNA generation and anonymous-DNA claiming."""

import logging
import time

from celery import shared_task
from celery.result import AsyncResult
from django.contrib.auth.models import User

from ..analytics.events import (
    track_dna_generation_started,
    track_dna_generation_completed,
    track_anonymous_dna_generated,
    track_dna_generation_failed,
    track_anonymous_dna_claimed,
)
from ..services.dna import _save_dna_to_profile, calculate_full_dna

logger = logging.getLogger(__name__)


# name pinned: wire name must survive the package split (queued msgs + beat)
@shared_task(bind=True, max_retries=5, name="core.tasks.claim_anonymous_dna_task")
def claim_anonymous_dna_task(self, user_id: int, task_id: str, session_key: str):
    """
    Claim cached anonymous DNA for a freshly-signed-up user.

    `session_key` is REQUIRED — callers (production code: `signup_view`) must
    pass the visitor's pre-login session_key so the task can verify it matches
    the `task_owner_<task_id>` value stored at upload time (US-001 + US-003).

    Fail-closed policy (post-review hardening):
      - missing/empty session_key      → reject, clear pending_dna_task_id
      - cache miss on task_owner_<id>  → reject (TTL expired or never bound)
      - session_key mismatch           → reject + warn (real attack signal)
    Any rejection logs hashed session keys (never raw — they're bearer creds).

    DEPLOY ORDERING: this task's positional signature is a security boundary,
    not a convenience. Workers MUST be restarted in the same deploy as web —
    Celery has no autoreload, and a stale worker running the pre-US-003
    2-arg signature crashes with `TypeError` on every claim from new web,
    silently losing the user's just-uploaded DNA. If you ever need to change
    this signature again, coordinate the worker restart explicitly or fan
    out via a versioned task name (e.g. `claim_anonymous_dna_task_v2`) so
    old and new workers can co-exist during the rolling deploy. Do NOT
    soften by adding `**kwargs` to "accept" old calls — that re-opens the
    hijack window the explicit signature was created to close.
    """
    import hashlib

    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        logger.warning(f"User with id {user_id} not found. Cannot claim task")
        return

    def _hash_key(k):
        return hashlib.sha256(k.encode()).hexdigest()[:12] if k else "none"

    def _clear_pending_and_return():
        """Clear the user's pending_dna_task_id so the dashboard doesn't poll forever."""
        try:
            user.userprofile.pending_dna_task_id = None
            user.userprofile.save()
        except Exception:
            logger.exception("Failed to clear pending_dna_task_id on claim rejection")

    if not session_key:
        logger.warning(
            "claim rejected: missing session_key (direct-broker invocation or bug)",
            extra={"user_id": user_id, "task_id": task_id},
        )
        _clear_pending_and_return()
        return

    from ..cache_utils import safe_cache_get

    owner_session_key = safe_cache_get(f"task_owner_{task_id}")
    if owner_session_key is None:
        # TTL expired (1hr) or task_id never bound. Fail closed — the cached
        # DNA at `dna_result_<task_id>` shares the same TTL so it'd be moot.
        logger.warning(
            "claim rejected: task_owner cache miss",
            extra={"user_id": user_id, "task_id": task_id, "caller_hash": _hash_key(session_key)},
        )
        _clear_pending_and_return()
        return

    if owner_session_key != session_key:
        logger.warning(
            "claim rejected: session_key mismatch",
            extra={
                "user_id": user_id,
                "task_id": task_id,
                "owner_hash": _hash_key(owner_session_key),
                "caller_hash": _hash_key(session_key),
            },
        )
        _clear_pending_and_return()
        return

    cached_session_key = safe_cache_get(f"session_key_{task_id}")
    dna_data = safe_cache_get(f"dna_result_{task_id}")

    if dna_data:
        logger.info(f"Found cached DNA for task {task_id}. Saving to user {user_id}")
    else:
        result = AsyncResult(task_id)
        if not result.ready():
            logger.info(f"Task {task_id} not ready yet. Retrying claim for user {user_id} in 10s")
            raise self.retry(countdown=10, exc=Exception(f"Task {task_id} not ready"))
        if not result.successful():
            logger.error(f"Task {task_id} failed. Cannot claim DNA for user {user_id}")
            user.userprofile.pending_dna_task_id = None
            user.userprofile.save()
            return
        dna_data = result.get()

    _save_dna_to_profile(user.userprofile, dna_data)

    if cached_session_key:
        _create_userbooks_from_anonymous_session(user, cached_session_key)

    user.userprofile.pending_dna_task_id = None
    user.userprofile.save()
    logger.info(f"Successfully claimed and saved DNA for user {user_id} from task {task_id}")

    track_anonymous_dna_claimed(
        user_id=user_id,
        task_id=task_id,
        session_key=cached_session_key,
    )


def _create_userbooks_from_anonymous_session(user, session_key):
    """Create UserBook records from AnonymousUserSession when claiming anonymous DNA"""
    from ..models import AnonymousUserSession, UserBook, Book
    from ..services.top_books_service import calculate_and_store_top_books

    try:
        anon_session = AnonymousUserSession.objects.get(session_key=session_key)
        book_ids = anon_session.books_data or []
        top_book_ids = anon_session.top_books_data or []

        if not book_ids:
            logger.warning(f"No book IDs found in AnonymousUserSession {session_key}")
            return

        books_created = 0
        for book_id in book_ids:
            try:
                book = Book.objects.get(pk=book_id)
                UserBook.objects.get_or_create(user=user, book=book, defaults={})
                books_created += 1
            except Book.DoesNotExist:
                logger.warning(f"Book with id {book_id} not found when creating UserBooks")
                continue

        logger.info(f"Created {books_created} UserBook records for user {user.username} from anonymous session")

        if books_created > 0:
            calculate_and_store_top_books(user, limit=5)
            # Also mark the top books from the anonymous session if they exist
            for position, book_id in enumerate(top_book_ids[:5], 1):
                try:
                    book = Book.objects.get(pk=book_id)
                    user_book = UserBook.objects.filter(user=user, book=book).first()
                    if user_book:
                        user_book.is_top_book = True
                        user_book.top_book_position = position
                        user_book.save()
                except Book.DoesNotExist:
                    continue

    except AnonymousUserSession.DoesNotExist:
        logger.warning(f"AnonymousUserSession {session_key} not found when claiming DNA for user {user.username}")
    except Exception as e:
        logger.error(f"Error creating UserBooks from anonymous session: {e}", exc_info=True)


# name pinned: wire name must survive the package split (queued msgs + beat)
@shared_task(bind=True, name="core.tasks.generate_reading_dna_task")
def generate_reading_dna_task(self, csv_file_content: str, user_id: int | None, session_key: str = None):
    # US-018 defense-in-depth: the upload view caps payload size before dispatch,
    # but the task must also bound its own input — any future caller (mgmt commands,
    # internal scripts, direct broker publishes) bypasses the HTTP layer. Cap on
    # newline count is cheap and runs before pandas does any deep parsing work.
    # Constant mirrors `core.views.MAX_UPLOAD_ROWS`; kept local to avoid an
    # import cycle between views ↔ tasks.
    MAX_TASK_ROWS = 50000
    if csv_file_content.count("\n") > MAX_TASK_ROWS + 1:  # +1 for header
        logger.error(
            f"generate_reading_dna_task rejected oversize CSV: "
            f"{csv_file_content.count(chr(10))} newlines > {MAX_TASK_ROWS}"
        )
        raise ValueError(f"CSV exceeds {MAX_TASK_ROWS}-row cap")

    logger.info("Running the latest (refactored) version of the Celery task")
    start_time = time.time()
    task_id = self.request.id
    user = None
    is_anonymous = user_id is None

    # Track DNA generation started
    track_dna_generation_started(
        task_id=task_id,
        user_id=user_id,
        session_key=session_key,
        is_anonymous=is_anonymous,
    )

    try:
        if user_id is not None:
            user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        logger.warning(f"Could not run task. User with id {user_id} not found")
        # Track failure
        track_dna_generation_failed(
            task_id=task_id,
            user_id=user_id,
            session_key=session_key,
            is_anonymous=is_anonymous,
            error_type="UserDoesNotExist",
            error_message=f"User with id {user_id} not found",
        )
        raise

    try:
        def progress_cb(current: int, total: int, stage: str):
            try:
                self.update_state(state="PROGRESS", meta={"current": current, "total": total, "stage": stage})
            except Exception:
                # If result backend is unavailable, ignore progress updates gracefully
                pass

        result_data = calculate_full_dna(csv_file_content, user, session_key, progress_cb=progress_cb)

        # Calculate processing time
        processing_time = time.time() - start_time

        # Extract books count from result_data (always a dict)
        user_stats = result_data.get("user_stats", {})
        books_count = user_stats.get("total_books_read")

        if not user:
            # Track anonymous DNA generated
            track_anonymous_dna_generated(
                task_id=task_id,
                session_key=session_key,
                books_count=books_count,
                processing_time=processing_time,
            )

            if self.request.id:
                from ..cache_utils import DNA_CACHE_TTL, safe_cache_set

                safe_cache_set(f"dna_result_{self.request.id}", result_data, timeout=DNA_CACHE_TTL)
                # Also cache the session_key so we can find AnonymousUserSession when claiming
                if session_key:
                    safe_cache_set(f"session_key_{self.request.id}", session_key, timeout=DNA_CACHE_TTL)
                logger.info(f"DNA result for task {self.request.id} saved to cache")

            # Track completion
            track_dna_generation_completed(
                task_id=task_id,
                user_id=None,
                session_key=session_key,
                is_anonymous=True,
                books_count=books_count,
                processing_time=processing_time,
            )

            return result_data
        else:
            # Track completion for authenticated user
            track_dna_generation_completed(
                task_id=task_id,
                user_id=user_id,
                session_key=None,
                is_anonymous=False,
                books_count=books_count,
                processing_time=processing_time,
            )

            return result_data

    except Exception as e:
        # Track failure
        track_dna_generation_failed(
            task_id=task_id,
            user_id=user_id,
            session_key=session_key,
            is_anonymous=is_anonymous,
            error_type=type(e).__name__,
            error_message=str(e),
        )
        logger.error(f"Task failed due to an error in the analysis engine: {e}", exc_info=True)
        raise
