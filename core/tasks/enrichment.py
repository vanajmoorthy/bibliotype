"""Enrichment tasks: book API enrichment and author/publisher mainstream research."""

import logging
import time

import requests
from celery import shared_task
from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from ..models import Author, Book
from ..services.author_service import check_author_mainstream_status

logger = logging.getLogger(__name__)

# Publisher mainstream re-check: batch size per weekly run and staleness window
PUBLISHER_CHECK_BATCH_LIMIT = 20
PUBLISHER_CHECK_AGE_THRESHOLD_DAYS = 90

# Unmapped subjects sent to the LLM per weekly run, most common first
GENRE_SUGGESTION_BATCH_LIMIT = 50


# name pinned: wire name must survive the package split (queued msgs + beat)
# ignore_result: nothing reads the return value, and bulk runs would otherwise
# write tens of thousands of 24h-TTL result keys into the shared Redis.
# rate_limit: the task makes up to 2 unthrottled HTTP calls (Open Library
# author search + Wikipedia pageviews); a bulk seed dispatches one per new
# author, so without a cap those requests stack on top of enrich_book_task's
# 60/m to the same Open Library host.
@shared_task(name="core.tasks.check_author_mainstream_status_task", rate_limit="30/m", ignore_result=True)
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
            headers = {"User-Agent": settings.EXTERNAL_API_USER_AGENT}
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


class GoogleBooksUnavailable(Exception):
    """Enrichment ran but the Google Books exchange didn't complete (book left unstamped)."""


# name pinned: wire name must survive the package split (queued msgs + beat).
# It must ALSO stay the single name for both interactive and bulk dispatch:
# rate_limit is per task NAME per worker, so a separately named bulk task would
# silently double our Open Library/Google Books throughput.
# ignore_result: progress is computed from google_books_last_checked counts,
# not task results; bulk runs would otherwise flood Redis with result keys.
@shared_task(bind=True, max_retries=3, rate_limit="60/m", name="core.tasks.enrich_book_task", ignore_result=True)
def enrich_book_task(self, book_id: int, user_id: int = None, upload_nonce: str = None, force: bool = False):
    """Enrich a single book with data from Open Library and Google Books APIs.

    If upload_nonce is provided, checks that it still matches the current upload
    for this user. If a newer upload has started, this task exits early to avoid
    wasting API calls on stale enrichment work.

    Respects the enrichment_retry_window: a book attempted within the
    window is skipped so the async path agrees with the inline path in
    dna_analyser. force=True bypasses the window (operator re-runs).

    Queue routing: interactive uploads dispatch to ENRICHMENT_QUEUE; bulk runs
    (seed_from_exports, enrich_books) dispatch to ENRICHMENT_BULK_QUEUE.
    self.retry() re-publishes with the original delivery info, so retries stay
    on whichever queue the task arrived on — bulk retries never jump the line.
    """
    from ..cache_utils import safe_cache_get
    from ..dna_constants import enrichment_retry_window

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

    # Honour the retry window unless forced: a book attempted within its
    # enrichment_retry_window is skipped (it already carries a timestamp, so
    # completion detection still counts it as attempted).
    if not force and book.google_books_last_checked is not None:
        if timezone.now() - book.google_books_last_checked < enrichment_retry_window(book):
            logger.info(f"Enrich task for book {book_id} skipped — attempted within retry window")
            return

    logger.info(f"Enriching book '{book.title}' (id={book_id}) via background task")

    try:
        from ..services.book_enrichment_service import enrich_book_from_apis

        with requests.Session() as session:
            session.headers.update({"User-Agent": settings.EXTERNAL_API_USER_AGENT})
            enrich_book_from_apis(book, session, slow_down=True)

        # enrich_book_from_apis returns normally but leaves the book unstamped
        # when the Google Books call failed (quota cooldown, 5xx, timeout).
        # Treat that as a failure so it's retried; otherwise nothing ever
        # retries it and the dashboard banner polls forever below 100%.
        if book.google_books_last_checked is None:
            raise GoogleBooksUnavailable(f"Google Books fetch did not complete for '{book.title}'")

        logger.info(f"Successfully enriched '{book.title}'")
    except Exception as e:
        from ..services.book_enrichment_service import GOOGLE_BOOKS_COOLDOWN_KEY, GOOGLE_BOOKS_COOLDOWN_SECONDS

        if isinstance(e, GoogleBooksUnavailable):
            logger.warning(f"{e} (id={book_id})")
        else:
            logger.error(f"Error enriching book '{book.title}' (id={book_id}): {e}", exc_info=True)

        # Check exhaustion ourselves: self.retry(exc=e) re-raises `e` (not
        # MaxRetriesExceededError) once retries run out, so catching that never fired.
        if self.request.retries >= self.max_retries:
            # Mark book as attempted so polling completion detection can finish.
            # Without this, books that consistently fail enrichment leave the
            # dashboard banner stuck at the same percent forever.
            Book.objects.filter(pk=book_id).update(google_books_last_checked=timezone.now())
            logger.warning(f"Max retries exhausted for book '{book.title}' (id={book_id}); marking as attempted")
            return

        # During a quota cooldown every attempt short-circuits, so wait it out
        # instead of burning retries on the exponential backoff. Capped below
        # the Redis broker's 1h visibility_timeout: a held ETA task older than
        # that gets redelivered and runs twice.
        if safe_cache_get(GOOGLE_BOOKS_COOLDOWN_KEY):
            countdown = min(GOOGLE_BOOKS_COOLDOWN_SECONDS, 3000)
        else:
            countdown = 60 * (2**self.request.retries)
        raise self.retry(countdown=countdown, exc=e)


# name pinned: wire name must survive the package split (queued msgs + beat)
@shared_task(name="core.tasks.research_publisher_mainstream_task")
def research_publisher_mainstream_task():
    """Periodic task to check unchecked publishers for mainstream status via AI research."""
    from ..models import Author, Publisher
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
        session.headers.update({"User-Agent": settings.EXTERNAL_API_USER_AGENT})

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


# name pinned: wire name must survive the package split (queued msgs + beat)
@shared_task(name="core.tasks.suggest_genre_aliases_task")
def suggest_genre_aliases_task():
    """Weekly: ask the LLM which canonical genre, if any, each common unmapped subject means.

    Suggestions are stored for review in analyze_genres and never applied: one bad
    alias would mislabel every book that carries the subject.
    """
    from ..dna_constants import GENRE_ALIASES
    from ..models import UnmappedSubject
    from ..services import _llm

    pending = list(
        UnmappedSubject.objects.filter(suggested_at__isnull=True).order_by("-seen_count")[:GENRE_SUGGESTION_BATCH_LIMIT]
    )
    if not pending:
        return 0

    genres = sorted(GENRE_ALIASES)
    subject_lines = "\n".join(f'- "{s.name}" (e.g. on "{s.example_title}")' for s in pending)
    prompt = f"""You map library subject headings for books onto a fixed list of genres.

Genres: {", ".join(genres)}

For each subject below, answer with exactly one genre from the list if the subject clearly means that genre, otherwise "ignore". Character names, places, people, awards, bestseller lists, audiences, formats and vague topics are "ignore".

Subjects:
{subject_lines}

Respond with only a JSON object mapping each subject string, exactly as written, to its answer."""

    answers = _llm.generate_json(prompt, temperature=0, max_tokens=2048)
    if not isinstance(answers, dict):
        logger.warning("Genre alias suggestions: LLM returned no usable response")
        return 0

    valid = set(genres) | {"ignore"}
    now = timezone.now()
    suggested = 0
    for subject in pending:
        answer = str(answers.get(subject.name, "")).strip().lower()
        if answer in valid:  # anything off-list is dropped and asked again next week
            subject.suggested_genre = answer
            subject.suggested_at = now
            subject.save(update_fields=["suggested_genre", "suggested_at"])
            suggested += 1

    logger.info(f"Genre alias suggestions: stored {suggested} of {len(pending)}")
    return suggested
