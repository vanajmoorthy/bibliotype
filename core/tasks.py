import logging
import os
import time

import google.generativeai as genai
import requests
from celery import shared_task
from celery.result import AsyncResult
from django.contrib.auth.models import User
from django.db.models import Q
from django.utils import timezone
from dotenv import load_dotenv

from .models import Author, Book
from .services.author_service import check_author_mainstream_status
from .services.dna_analyser import _save_dna_to_profile, calculate_full_dna
from .analytics.events import (
    track_dna_generation_started,
    track_dna_generation_completed,
    track_anonymous_dna_generated,
    track_dna_generation_failed,
    track_anonymous_dna_claimed,
)

load_dotenv()

logger = logging.getLogger(__name__)

api_key = os.getenv("GEMINI_API_KEY")

if api_key:
    genai.configure(api_key=api_key)
else:
    logger.warning("GEMINI_API_KEY environment variable not found. Vibe generation will be disabled.")


@shared_task
def check_author_mainstream_status_task(author_id: int):
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
        logger.error(f"Author Status Task Error: Author with ID {author_id} not found")
    except Exception as e:
        logger.error(
            f"Critical error in check_author_mainstream_status_task for author_id {author_id}: {e}", exc_info=True
        )
        raise


@shared_task(bind=True, max_retries=3, rate_limit="30/m")
def enrich_book_task(self, book_id: int):
    """Enrich a single book with data from Open Library and Google Books APIs."""
    try:
        book = Book.objects.get(pk=book_id)
    except Book.DoesNotExist:
        logger.error(f"Enrich task: Book with ID {book_id} not found")
        return

    logger.info(f"Enriching book '{book.title}' (id={book_id}) via background task")

    try:
        from .book_enrichment_service import enrich_book_from_apis

        with requests.Session() as session:
            session.headers.update({"User-Agent": "BibliotypeApp/1.0"})
            enrich_book_from_apis(book, session, slow_down=True)

        logger.info(f"Successfully enriched '{book.title}'")
    except Exception as e:
        logger.error(f"Error enriching book '{book.title}' (id={book_id}): {e}", exc_info=True)
        raise self.retry(countdown=60 * (2**self.request.retries), exc=e)


@shared_task(bind=True, max_retries=5)
def claim_anonymous_dna_task(self, user_id: int, task_id: str):
    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        logger.error(f"User with id {user_id} not found. Cannot claim task")
        return

    from .cache_utils import safe_cache_get

    cached_dna = safe_cache_get(f"dna_result_{task_id}")
    cached_session_key = safe_cache_get(f"session_key_{task_id}")

    if cached_dna:
        logger.info(f"Found cached DNA for task {task_id}. Saving to user {user_id}")
        _save_dna_to_profile(user.userprofile, cached_dna)

        # Try to create UserBooks from AnonymousUserSession if it exists
        if cached_session_key:
            _create_userbooks_from_anonymous_session(user, cached_session_key)

        user.userprofile.pending_dna_task_id = None
        user.userprofile.save()
        logger.info(f"Successfully claimed and saved DNA for user {user_id} from task {task_id}")

        # Track anonymous DNA claimed
        track_anonymous_dna_claimed(
            user_id=user_id,
            task_id=task_id,
            session_key=cached_session_key,
        )
        return

    result = AsyncResult(task_id)

    if result.ready():
        if result.successful():
            dna_data = result.get()
            _save_dna_to_profile(user.userprofile, dna_data)

            # Try to create UserBooks from AnonymousUserSession if session_key was cached
            if cached_session_key:
                _create_userbooks_from_anonymous_session(user, cached_session_key)

            user.userprofile.pending_dna_task_id = None
            user.userprofile.save()
            logger.info(f"Successfully claimed and saved DNA for user {user_id} from task {task_id}")

            # Track anonymous DNA claimed
            track_anonymous_dna_claimed(
                user_id=user_id,
                task_id=task_id,
                session_key=cached_session_key,
            )
        else:
            logger.error(f"Task {task_id} failed. Cannot claim DNA for user {user_id}")
            user.userprofile.pending_dna_task_id = None
            user.userprofile.save()
    else:
        logger.info(f"Task {task_id} not ready yet. Retrying claim for user {user_id} in 10s")
        raise self.retry(countdown=10, exc=Exception(f"Task {task_id} not ready"))


def _create_userbooks_from_anonymous_session(user, session_key):
    """Create UserBook records from AnonymousUserSession when claiming anonymous DNA"""
    from .models import AnonymousUserSession, UserBook, Book
    from .services.top_books_service import calculate_and_store_top_books

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


@shared_task(bind=True)
def generate_reading_dna_task(self, csv_file_content: str, user_id: int | None, session_key: str = None):
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
        logger.error(f"Could not run task. User with id {user_id} not found")
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
                from .cache_utils import safe_cache_set

                safe_cache_set(f"dna_result_{self.request.id}", result_data, timeout=3600)
                # Also cache the session_key so we can find AnonymousUserSession when claiming
                if session_key:
                    safe_cache_set(f"session_key_{self.request.id}", session_key, timeout=3600)
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


@shared_task
def research_publisher_mainstream_task():
    """Periodic task to check unchecked publishers for mainstream status via AI research."""
    from .models import Publisher, Author
    from .services.publisher_service import research_publisher_identity

    BATCH_LIMIT = 20
    AGE_THRESHOLD_DAYS = 90

    cutoff = timezone.now() - timezone.timedelta(days=AGE_THRESHOLD_DAYS)
    publishers_to_check = list(
        Publisher.objects.filter(
            Q(mainstream_last_checked__isnull=True) | Q(mainstream_last_checked__lt=cutoff),
            parent__isnull=True,
        )[:BATCH_LIMIT]
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
    import io
    from django.core.management import call_command
    from .cache_utils import safe_cache_set

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
    from .services.anonymization_service import batch_anonymize_expired_sessions

    count = batch_anonymize_expired_sessions()
    logger.info(f"Anonymized {count} expired sessions")
    return count


@shared_task(bind=True, max_retries=3)
def generate_recommendations_task(self, user_id: int):
    """
    Generate and store recommendations for a user after their DNA is created/updated.
    This runs asynchronously so it doesn't slow down DNA generation.
    """
    from .services.recommendation_service import get_recommendations_for_user

    try:
        user = User.objects.get(pk=user_id)
        profile = user.userprofile

        # Only generate if user has DNA data
        if not profile.dna_data:
            logger.warning(f"Cannot generate recommendations for user {user_id}: no DNA data")
            return None

        logger.info(f"Generating recommendations for user {user_id}")

        recommendations = get_recommendations_for_user(user, limit=6)

        processed_recs = []
        for rec in recommendations:
            processed_rec = {
                "book_id": rec["book"].id,
                "book_title": rec["book"].title,
                "book_author": rec["book"].author.name,
                "book_average_rating": rec["book"].average_rating,
                "confidence": rec.get("confidence", 0),
                "confidence_pct": int(rec.get("confidence", 0) * 100),
                "score": rec.get("score", 0),
                "recommender_count": rec.get("recommender_count", 0),
                "genre_alignment": rec.get("genre_alignment", 0),
                "sources": rec.get("sources", []),
                "explanation_components": rec.get("explanation_components", {}),
            }

            primary_source_user = None
            best_similarity = 0
            for source in rec.get("sources", []):
                if source.get("type") == "similar_user":
                    if source.get("similarity_score", 0) > best_similarity:
                        best_similarity = source.get("similarity_score", 0)
                        primary_source_user = source

            if primary_source_user:
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
        from .cache_utils import safe_cache_delete

        safe_cache_delete(f"user_recommendations_{user_id}")

        logger.info(f"Successfully generated and stored {len(processed_recs)} recommendations for user {user_id}")
        return len(processed_recs)

    except User.DoesNotExist:
        logger.error(f"User with id {user_id} not found for recommendations generation")
        return None
    except Exception as e:
        logger.error(f"Error generating recommendations for user {user_id}: {e}", exc_info=True)
        # Retry with exponential backoff
        raise self.retry(countdown=60 * (2**self.request.retries), exc=e)
