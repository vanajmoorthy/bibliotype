"""Enrichment tasks: book API enrichment and author/publisher mainstream research."""

import logging
import time

import requests
from celery import shared_task
from django.db.models import Q
from django.utils import timezone

from ..models import Author, Book
from ..services.author_service import check_author_mainstream_status

logger = logging.getLogger(__name__)

# Publisher mainstream re-check: batch size per weekly run and staleness window
PUBLISHER_CHECK_BATCH_LIMIT = 20
PUBLISHER_CHECK_AGE_THRESHOLD_DAYS = 90


# name pinned: wire name must survive the package split (queued msgs + beat)
@shared_task(name="core.tasks.check_author_mainstream_status_task")
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


# name pinned: wire name must survive the package split (queued msgs + beat)
@shared_task(bind=True, max_retries=3, rate_limit="30/m", name="core.tasks.enrich_book_task")
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


# name pinned: wire name must survive the package split (queued msgs + beat)
@shared_task(name="core.tasks.research_publisher_mainstream_task")
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
