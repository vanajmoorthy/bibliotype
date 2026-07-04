import logging
import time

import requests
from celery import shared_task
from django.contrib.auth.models import User
from django.db.models import Q
from django.utils import timezone

from ..management_command_registry import ALLOWED_COMMANDS
from ..models import Author, Book
from ..services.author_service import check_author_mainstream_status
from .dna import (  # noqa: F401 — re-exported for stable import paths
    _create_userbooks_from_anonymous_session,
    _save_dna_to_profile,
    claim_anonymous_dna_task,
    generate_reading_dna_task,
)

logger = logging.getLogger(__name__)

# Publisher mainstream re-check: batch size per weekly run and staleness window
PUBLISHER_CHECK_BATCH_LIMIT = 20
PUBLISHER_CHECK_AGE_THRESHOLD_DAYS = 90


@shared_task
def check_author_mainstream_status_task(author_id: int, user_id: int = None, upload_nonce: str = None):
    # If the upload that spawned this task has been superseded by a newer one,
    # exit immediately. Without this, hundreds of leftover author-check tasks
    # from a prior CSV upload drain the worker and starve the new DNA task
    # (the "stuck at 50%" symptom on re-upload).
    if user_id and upload_nonce:
        from ..cache_utils import safe_cache_get

        current_nonce = safe_cache_get(f"upload_nonce_{user_id}")
        if current_nonce and current_nonce != upload_nonce:
            logger.info(f"Author status task for author {author_id} skipped — superseded by newer upload")
            return

    try:
        author = Author.objects.get(pk=author_id)
        logger.info(f"Running mainstream status check for new author: {author.name}")

        with requests.Session() as session:
            headers = {"User-Agent": "BibliotypeApp/1.0 (contact@yourdomain.com)"}
            session.headers.update(headers)
            status_data = check_author_mainstream_status(author.name, session)

            if status_data["error"]:
                logger.warning(f"API Error for {author.name}: {status_data['error']}")
            else:
                if author.is_mainstream != status_data["is_mainstream"]:
                    author.is_mainstream = status_data["is_mainstream"]
                    logger.info(
                        f"Status updated to: {author.is_mainstream}. Reason: {status_data.get('reason', 'N/A')}"
                    )

                author.mainstream_last_checked = timezone.now()
                author.save()

    except Author.DoesNotExist:
        logger.warning(f"Author Status Task Error: Author with ID {author_id} not found")
    except Exception as e:
        logger.error(
            f"Critical error in check_author_mainstream_status_task for author_id {author_id}: {e}", exc_info=True
        )
        raise


@shared_task(bind=True, max_retries=3, rate_limit="30/m")
def enrich_book_task(self, book_id: int, user_id: int = None, upload_nonce: str = None):
    """Enrich a single book with data from Open Library and Google Books APIs.

    If upload_nonce is provided, checks that it still matches the current upload
    for this user. If a newer upload has started, this task exits early to avoid
    wasting API calls on stale enrichment work.
    """
    from ..cache_utils import safe_cache_get

    def _is_superseded():
        if not (user_id and upload_nonce):
            return False
        current_nonce = safe_cache_get(f"upload_nonce_{user_id}")
        return bool(current_nonce) and current_nonce != upload_nonce

    # Check if this enrichment task is still relevant (not superseded by a re-upload)
    if _is_superseded():
        logger.info(f"Enrich task for book {book_id} skipped at start — superseded by newer upload")
        return

    try:
        book = Book.objects.get(pk=book_id)
    except Book.DoesNotExist:
        logger.warning(f"Enrich task: Book with ID {book_id} not found")
        return

    # Re-check after the DB fetch. Tasks can sit in the queue for a while; the
    # nonce may have changed between dispatch and execution, and we don't want
    # to spend ~5s of API calls (plus the subsequent book.save) on stale work.
    if _is_superseded():
        logger.info(f"Enrich task for book {book_id} skipped before API call — superseded by newer upload")
        return

    logger.info(f"Enriching book '{book.title}' (id={book_id}) via background task")

    try:
        from ..services.book_enrichment_service import enrich_book_from_apis

        with requests.Session() as session:
            session.headers.update({"User-Agent": "BibliotypeApp/1.0"})
            enrich_book_from_apis(book, session, slow_down=True)

        logger.info(f"Successfully enriched '{book.title}'")
    except Exception as e:
        from celery.exceptions import MaxRetriesExceededError

        logger.error(f"Error enriching book '{book.title}' (id={book_id}): {e}", exc_info=True)
        try:
            raise self.retry(countdown=60 * (2**self.request.retries), exc=e)
        except MaxRetriesExceededError:
            # Mark book as attempted so polling completion detection can finish.
            # Without this, books that consistently fail enrichment leave the
            # dashboard banner stuck at the same percent forever.
            from django.utils import timezone

            Book.objects.filter(pk=book_id).update(google_books_last_checked=timezone.now())
            logger.warning(f"Max retries exhausted for book '{book.title}' (id={book_id}); marking as attempted")


@shared_task
def research_publisher_mainstream_task():
    """Periodic task to check unchecked publishers for mainstream status via AI research."""
    from ..models import Publisher, Author
    from ..services.publisher_service import research_publisher_identity

    cutoff = timezone.now() - timezone.timedelta(days=PUBLISHER_CHECK_AGE_THRESHOLD_DAYS)
    publishers_to_check = list(
        Publisher.objects.filter(
            Q(mainstream_last_checked__isnull=True) | Q(mainstream_last_checked__lt=cutoff),
            parent__isnull=True,
        )[:PUBLISHER_CHECK_BATCH_LIMIT]
    )

    if not publishers_to_check:
        logger.info("Publisher mainstream check: no publishers need checking.")
        return 0

    logger.info(f"Publisher mainstream check: processing {len(publishers_to_check)} publishers")
    updated_count = 0

    with requests.Session() as session:
        session.headers.update({"User-Agent": "BibliotypeApp/1.0"})

        for publisher in publishers_to_check:
            try:
                findings = research_publisher_identity(publisher.name, session)

                if findings["error"]:
                    logger.warning(f"Publisher research error for '{publisher.name}': {findings['error']}")
                    publisher.mainstream_last_checked = timezone.now()
                else:
                    is_mainstream_result = findings.get("is_mainstream")
                    publisher.is_mainstream = is_mainstream_result if isinstance(is_mainstream_result, bool) else False
                    publisher.mainstream_last_checked = timezone.now()

                    if parent_name := findings.get("parent_company_name"):
                        parent_obj, _ = Publisher.objects.get_or_create(
                            normalized_name=Author._normalize(parent_name),
                            defaults={"name": parent_name, "is_mainstream": True},
                        )
                        publisher.parent = parent_obj

                    updated_count += 1

                publisher.save()
                time.sleep(2)
            except Exception as e:
                logger.error(f"Error researching publisher '{publisher.name}': {e}", exc_info=True)
                continue

    logger.info(f"Publisher mainstream check complete: updated {updated_count} publishers")
    return updated_count


@shared_task
def run_management_command_task(command_name: str, args: list = None, kwargs: dict = None):
    """Run a Django management command and store the output in cache."""
    if command_name not in ALLOWED_COMMANDS:
        raise ValueError(f"command not allowed: {command_name}")

    import io
    from django.core.management import call_command
    from ..cache_utils import safe_cache_set

    args = args or []
    kwargs = kwargs or {}

    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()

    try:
        call_command(command_name, *args, stdout=stdout_buffer, stderr=stderr_buffer, **kwargs)
        stdout_output = stdout_buffer.getvalue()
        stderr_output = stderr_buffer.getvalue()

        if stdout_output:
            logger.info(f"[{command_name}] stdout:\n{stdout_output}")
        if stderr_output:
            logger.warning(f"[{command_name}] stderr:\n{stderr_output}")

        result = {
            "status": "success",
            "stdout": stdout_output,
            "stderr": stderr_output,
        }
    except Exception as e:
        stdout_output = stdout_buffer.getvalue()
        stderr_output = stderr_buffer.getvalue()

        if stdout_output:
            logger.info(f"[{command_name}] stdout:\n{stdout_output}")

        logger.error(f"Management command '{command_name}' failed: {e}", exc_info=True)
        result = {
            "status": "error",
            "stdout": stdout_output,
            "stderr": stderr_output,
            "error": str(e),
        }

    return result


@shared_task
def anonymize_expired_sessions_task():
    """Periodic task to convert expired anonymous sessions to anonymized profiles"""
    from ..services.anonymization_service import batch_anonymize_expired_sessions

    count = batch_anonymize_expired_sessions()
    logger.info(f"Anonymized {count} expired sessions")
    return count


@shared_task(bind=True, max_retries=3)
def generate_recommendations_task(self, user_id: int):
    """
    Generate and store recommendations for a user after their DNA is created/updated.
    This runs asynchronously so it doesn't slow down DNA generation.
    """
    from ..cache_utils import safe_cache_delete
    from ..services.recommendation_service import get_recommendations_for_user

    # Track whether the task is terminally complete. Cleared on retry so
    # the sentinel set in display_dna_view stays in place across the retry
    # countdown and continues to block duplicate dispatches.
    clear_sentinel_on_exit = True

    try:
        user = User.objects.get(pk=user_id)
        profile = user.userprofile

        # Only generate if user has DNA data
        if not profile.dna_data:
            logger.warning(f"Cannot generate recommendations for user {user_id}: no DNA data")
            return None

        logger.info(f"Generating recommendations for user {user_id}")

        recommendations = get_recommendations_for_user(user, limit=6)

        # Imported here to avoid a circular import with core.views.
        from ..views import BADGE_COLOR_MAP

        processed_recs = []
        for rec in recommendations:
            book = rec["book"]
            processed_rec = {
                "book_id": book.id,
                "book_title": book.title,
                "book_author": book.author.name,
                "book_average_rating": book.average_rating,
                "confidence": rec.get("confidence", 0),
                "confidence_pct": int(rec.get("confidence", 0) * 100),
                "score": rec.get("score", 0),
                "recommender_count": rec.get("recommender_count", 0),
                "genre_alignment": rec.get("genre_alignment", 0),
                "sources": rec.get("sources", []),
                "explanation_components": rec.get("explanation_components", {}),
                # US-032: bake the nested book dict templates expect, so
                # views no longer need to reconstruct it on every render.
                "book": {
                    "id": book.id,
                    "title": book.title,
                    "author": {"name": book.author.name},
                    "average_rating": book.average_rating,
                },
            }

            primary_source_user = None
            best_similarity = 0
            for source in rec.get("sources", []):
                if source.get("type") == "similar_user":
                    if source.get("similarity_score", 0) > best_similarity:
                        best_similarity = source.get("similarity_score", 0)
                        primary_source_user = source

            if primary_source_user:
                # US-032: bake badge_class alongside the source so the view
                # can skip the legacy expansion when "book" is already set.
                match_quality = primary_source_user.get("match_quality", "")
                primary_source_user["badge_class"] = BADGE_COLOR_MAP.get(match_quality, "bg-brand-purple")
                processed_rec["primary_source_user"] = primary_source_user

            processed_recs.append(processed_rec)

        similar_user_set = set()
        min_overlap_pct = None
        for rec in processed_recs:
            for source in rec.get("sources", []):
                if source.get("type") == "similar_user" and source.get("user_id"):
                    similar_user_set.add(source["user_id"])
                    similarity = source.get("similarity_score", 0)
                    overlap = int(round(similarity * 100))
                    if min_overlap_pct is None or overlap < min_overlap_pct:
                        min_overlap_pct = overlap

        recommendations_meta = {
            "similar_users_count": len(similar_user_set),
            "min_overlap_pct": min_overlap_pct or 0,
        }

        profile.recommendations_data = processed_recs
        profile.recommendations_meta = recommendations_meta
        profile.recommendations_generated_at = timezone.now()
        profile.save(update_fields=["recommendations_data", "recommendations_meta", "recommendations_generated_at"])

        # Also clear the cache so fresh data is used
        safe_cache_delete(f"user_recommendations_{user_id}")

        logger.info(f"Successfully generated and stored {len(processed_recs)} recommendations for user {user_id}")
        return len(processed_recs)

    except User.DoesNotExist:
        logger.warning(f"User with id {user_id} not found for recommendations generation")
        return None
    except Exception as e:
        logger.error(f"Error generating recommendations for user {user_id}: {e}", exc_info=True)
        # Task is being re-queued; the sentinel must outlive this attempt so
        # dashboard polls don't dispatch a duplicate while the retry is pending.
        clear_sentinel_on_exit = False
        raise self.retry(countdown=60 * (2**self.request.retries), exc=e)
    finally:
        # Clear the dispatch sentinel set in display_dna_view so the next
        # dashboard poll can spawn a fresh task if needed.
        if clear_sentinel_on_exit:
            safe_cache_delete(f"recs_dispatching_{user_id}")
