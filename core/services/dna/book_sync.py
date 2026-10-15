"""Read-shelf sync: CSV rows to Author/Book/UserBook rows, inline enrichment, and DB backfill."""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import requests
from django.conf import settings
from django.db import DataError, IntegrityError, connections
from django.db.models import F
from django.utils import timezone

from ...dna_constants import ENRICHMENT_RETRY_AFTER, STORYGRAPH_TAG_TO_GENRE, enrichment_retry_window
from ...models import AnonymousUserSession, Author, Book, Genre, UserBook
from .enrichment_budget import _EnrichmentBudget
from .utils import _isbn_to_isbn13

logger = logging.getLogger(__name__)

# Process-wide cap on concurrent inline API enrichments (shared across uploads).
_INLINE_ENRICH_SEMAPHORE = threading.BoundedSemaphore(2)


def sync_read_books(read_df, csv_source, user, session_key, progress_cb, total_books, bulk_enrichment, dispatch_opts):
    """Sync every read-shelf row to Author/Book rows, enriching inline while the budget lasts.

    Returns (results, book_pks_by_idx, already_counted_book_ids, previous_anon_session,
    upload_user_id, upload_nonce); results holds one (book, genre_names, original_row) per row.
    """
    # Prefetch all ISBN-matched books in a single query to avoid N+1
    csv_isbns = read_df["ISBN13"].dropna().unique().tolist() if "ISBN13" in read_df.columns else []
    isbn_book_map = {}
    if csv_isbns:
        isbn_book_map = {b.isbn13: b for b in Book.objects.filter(isbn13__in=csv_isbns)}

    book_pks_by_idx = {}  # Track book PKs for inline enrichment
    # Upload nonce: enrichment tasks check this to exit early if a newer upload started
    upload_nonce = None
    upload_user_id = None
    if user:
        upload_user_id = user.id
        from ...cache_utils import safe_cache_get

        upload_nonce = safe_cache_get(f"upload_nonce_{user.id}")
    # Wall-clock budget for inline enrichment, shared across the 8 sync
    # workers via the process_book_row closure. Lazily started (the timer
    # only runs once a book actually needs inline enrichment) and
    # lock-guarded so the start is exactly-once under concurrency.
    enrichment_budget = _EnrichmentBudget()
    # Books this reader was already counted for by an earlier upload. A
    # re-upload must not add another "reader" to Book.global_read_count,
    # or niche-book stats drift for everyone.
    previous_anon_session = None
    if user:
        already_counted_book_ids = set(UserBook.objects.filter(user=user).values_list("book_id", flat=True))
    elif session_key:
        previous_anon_session = AnonymousUserSession.objects.filter(session_key=session_key).first()
        already_counted_book_ids = set(previous_anon_session.books_data) if previous_anon_session else set()
    else:
        already_counted_book_ids = set()
    # Two CSV rows can resolve to the same Book (duplicate shelf entries,
    # editions that normalise to one title); count it once per upload.
    counted_this_upload, counted_lock = set(), threading.Lock()
    with requests.Session() as session:
        session.headers.update({"User-Agent": settings.EXTERNAL_API_USER_AGENT})

        def _csv_text(value, max_length=255):
            # Blank cells arrive from pandas as NaN (a float), not "". Truncate
            # to the model's max_length so an overlong cell can't DataError.
            if value is None or (not isinstance(value, str) and pd.isna(value)):
                return ""
            return str(value).strip()[:max_length].strip()

        def process_book_row(original_row):
            # One malformed row must not fail the whole upload: log it and
            # skip it the same way blank rows are skipped. Only bad-data errors
            # are caught — infrastructure errors (DB down) still fail the task
            # rather than quietly saving a DNA with books missing.
            try:
                return _process_book_row(original_row)
            except (DataError, ValueError, TypeError, OverflowError) as e:
                # exc_info so a real code bug (not bad data) shows its traceback.
                logger.warning(f"Skipping CSV row {original_row.get('Title')!r} that failed to sync", exc_info=True)
                failed_rows.append(e)
                return None, [], original_row

        def _process_book_row(original_row):
            author_name_from_csv = _csv_text(original_row.get("Author"))
            title_from_csv = _csv_text(original_row.get("Title"))

            if not author_name_from_csv or not title_from_csv:
                return None, [], original_row

            normalized_author_name = Author._normalize(author_name_from_csv)
            author, created = Author.objects.get_or_create(
                normalized_name=normalized_author_name, defaults={"name": author_name_from_csv}
            )

            if created:
                from ...tasks import check_author_mainstream_status_task

                logger.info(f"New author found: '{author.name}'. Dispatching background check.")
                check_author_mainstream_status_task.apply_async(
                    args=(author.id,),
                    kwargs={"user_id": upload_user_id, "upload_nonce": upload_nonce},
                    **dispatch_opts,
                )

            normalized_book_title = Book._normalize_title(title_from_csv)

            publish_year_value = None
            raw_year = original_row.get("Original Publication Year")
            if pd.notna(raw_year):
                try:
                    publish_year_value = int(float(raw_year))
                except (ValueError, TypeError):
                    publish_year_value = None

            # Normalize to ISBN-13: Goodreads wraps in ="...", and exports may
            # contain ISBN-10s. _isbn_to_isbn13 handles both, plus the X check
            # digit. Idempotent for already-13-digit input from earlier passes.
            isbn13_value = _isbn_to_isbn13(original_row.get("ISBN13"))

            # Only include fields with actual values to avoid overwriting enriched data with None
            book_defaults = {}
            # Upper bound drops absurd page counts that would overflow the column.
            if pd.notna(p := original_row.get("Number of Pages")) and 1 <= p < 100_000:
                book_defaults["page_count"] = int(p)
            if pd.notna(r := original_row.get("Average Rating")) and float(r) > 0:
                book_defaults["average_rating"] = float(r)
            if publish_year_value:
                book_defaults["publish_year"] = publish_year_value
            if isbn13_value:
                book_defaults["isbn13"] = isbn13_value

            # ISBN-based deduplication: reuse existing book if ISBN matches
            existing_book_by_isbn = isbn_book_map.get(isbn13_value) if isbn13_value else None

            if existing_book_by_isbn:
                book = existing_book_by_isbn
                created = False
            else:
                try:
                    book, created = Book.objects.get_or_create(
                        normalized_title=normalized_book_title,
                        author=author,
                        defaults={"title": title_from_csv, **book_defaults},
                    )
                except IntegrityError:
                    # Duplicate ISBN13 — retry without it
                    book_defaults.pop("isbn13", None)
                    book, created = Book.objects.get_or_create(
                        normalized_title=normalized_book_title,
                        author=author,
                        defaults={"title": title_from_csv, **book_defaults},
                    )

            if not created:
                # The Book row is shared across readers, so one uploader's edition
                # must not overwrite another's or enrichment's values: only fill
                # NULL columns. average_rating is refreshed since enrichment never
                # writes it. Targeted UPDATE skips Book.save()'s _normalize_title.
                fields_to_fill = {
                    k: v
                    for k, v in book_defaults.items()
                    if getattr(book, k, None) is None or (k == "average_rating" and book.average_rating != v)
                }
                if fields_to_fill:
                    try:
                        Book.objects.filter(pk=book.pk).update(**fields_to_fill)
                        for k, v in fields_to_fill.items():
                            setattr(book, k, v)
                    except IntegrityError:
                        logger.warning(f"IntegrityError filling book {book.pk} (ISBN {isbn13_value}), skipping update")

            # Check if the book already has genres. `book.genres.exists()` runs
            # a single COUNT(*) on the through table and is not cached on the
            # instance — equivalent freshness to the previous reverse-side query
            # but one fewer JOIN and reuses the FK manager.
            if created:
                has_genres = False
            else:
                has_genres = book.genres.exists()

            # StoryGraph tags → canonical genres (applied before enrichment dispatch)
            if csv_source == "storygraph" and not has_genres:
                raw_tags_str = original_row.get("Tags")
                if raw_tags_str and pd.notna(raw_tags_str):
                    raw_tags = [t.strip().lower() for t in str(raw_tags_str).split(",") if t.strip()]
                    tag_genres = list({STORYGRAPH_TAG_TO_GENRE[t] for t in raw_tags if t in STORYGRAPH_TAG_TO_GENRE})
                    if tag_genres:
                        genre_objs = [Genre.objects.get_or_create(name=g)[0] for g in tag_genres]
                        book.genres.add(*genre_objs)
                        has_genres = True
                        logger.debug(f"Applied {len(tag_genres)} tag-derived genres for '{book.title}': {tag_genres}")

            # Inline-first enrichment: while the 90s budget lasts, enrich
            # synchronously with quick_mode timeouts so genres land in THIS
            # DNA calculation (no async race). On failure or once the budget
            # is exhausted, fall back to the async task exactly as before.
            # Skip only if the full pipeline ran *recently*. google_books_last_
            # checked is the last-attempt marker; a book attempted within the
            # retry window is trusted and skipped, but once the attempt is older
            # than enrichment_retry_window(book) it becomes eligible again so a
            # book that got zero genres can pick up data the APIs may have added
            # since (widening to 30 days once it has repeatedly come back empty).
            recently_attempted = (
                not created
                and book.google_books_last_checked is not None
                and timezone.now() - book.google_books_last_checked < enrichment_retry_window(book)
            )
            needs_page_data = not book.page_count or not book.publish_year
            if not recently_attempted and (created or not has_genres or needs_page_data):
                enriched_inline = False
                # Bulk runs never enrich inline — the whole point of the
                # bulk queue is that their API traffic obeys the Celery
                # rate limit, which in-process enrichment bypasses.
                if not bulk_enrichment and enrichment_budget.has_remaining():
                    try:
                        from ..book_enrichment_service import enrich_book_from_apis

                        logger.debug(
                            f"Inline enrichment for '{book.title}' (created={created}, "
                            f"has_genres={has_genres}, needs_page_data={needs_page_data})"
                        )
                        # Cap concurrent inline enrichments so 8 upload workers
                        # can't burst Open Library / Google Books past their limits.
                        # Re-check the budget after the wait so queued workers don't
                        # keep enriching inline once it has run out.
                        with _INLINE_ENRICH_SEMAPHORE:
                            if enrichment_budget.has_remaining():
                                enrich_book_from_apis(book, session, slow_down=False, quick_mode=True)
                                # Unstamped means the Google Books call failed (quota
                                # cooldown, 5xx, timeout), so hand it to the async task,
                                # which retries, instead of counting it as done.
                                enriched_inline = book.google_books_last_checked is not None
                    except Exception as e:
                        logger.warning(f"Inline enrichment failed for '{book.title}': {e}")
                if not enriched_inline:
                    # Bulk corpora overlap heavily on popular titles, and
                    # since bulk skips inline enrichment nothing stamps
                    # google_books_last_checked until the queued task runs
                    # — so every later CSV containing a still-queued book
                    # would re-dispatch it, and each duplicate burns a
                    # rate-limit slot as a no-op. A dispatch sentinel
                    # (TTL = ENRICHMENT_RETRY_AFTER, matching the guard the
                    # executed task applies) suppresses those duplicates.
                    # Interactive dispatches are left alone: their
                    # nonce/supersede semantics already handle re-uploads,
                    # and cross-user suppression could strand a book if the
                    # suppressing task gets superseded before running.
                    should_dispatch = True
                    if bulk_enrichment:
                        from ...cache_utils import safe_cache_add

                        should_dispatch = safe_cache_add(
                            f"enrich_dispatched_{book.pk}",
                            1,
                            timeout=int(ENRICHMENT_RETRY_AFTER.total_seconds()),
                        )
                    if should_dispatch:
                        from ...tasks import enrich_book_task

                        logger.debug(
                            f"Dispatching async enrichment for '{book.title}' (budget exhausted or inline failed)"
                        )
                        enrich_book_task.apply_async(
                            args=(book.pk,),
                            kwargs={"user_id": upload_user_id, "upload_nonce": upload_nonce},
                            **dispatch_opts,
                        )
                    else:
                        logger.debug(f"Bulk enrich dispatch for '{book.title}' suppressed — already queued")
            else:
                logger.debug(f"Book '{book.title}' recently enriched or complete. Skipping.")

            with counted_lock:
                count_now = book.pk not in already_counted_book_ids and book.pk not in counted_this_upload
                counted_this_upload.add(book.pk)
            if count_now:
                Book.objects.filter(pk=book.pk).update(global_read_count=F("global_read_count") + 1)

                # Pull only the post-increment value back into the Python object.
                # Niche-book stats (`book.global_read_count` reads downstream around
                # the most-niche-book and NICHE_THRESHOLD aggregations) depend on
                # this being current. `refresh_from_db(fields=[...])` avoids
                # reloading large columns like cover_url / JSON blobs that didn't
                # change.
                book.refresh_from_db(fields=["global_read_count"])

            genre_names = [g.name for g in book.genres.all()]
            return book, genre_names, original_row

        # 8 workers is safe under Postgres: row-level locking + a thread-local
        # connection per worker means concurrent get_or_create on Author/Book/Genre/
        # Publisher is resolved by the unique constraints rather than serialising at
        # the executor level. The legacy max_workers=1 was a vestige of SQLite's
        # database-wide write lock and is no longer needed in the prod stack.
        #
        # Shared inline-enrichment state across the workers:
        # - `session`: one requests.Session for all workers. Session.get is
        #   thread-safe for concurrent requests (urllib3's connection pool
        #   handles per-connection checkout); we only mutate headers once,
        #   before the executor starts.
        # - `enrichment_budget`: lazy start is lock-guarded (exactly-once);
        #   has_remaining() reads are race-tolerant by design — at the
        #   boundary a few in-flight workers may each admit one extra book.
        failed_rows = []  # list.append is thread-safe
        # Each worker opens its own DB connection. Django's connection wrapper
        # holds reference cycles, so a finished thread's connection lingers until
        # a full GC pass; close them explicitly once the pool has shut down.
        worker_connections = []  # list.append is thread-safe

        def _register_worker_connection():
            worker_connections.append(connections["default"])

        try:
            with ThreadPoolExecutor(max_workers=8, initializer=_register_worker_connection) as executor:
                results = []
                processed = 0
                row_dicts = read_df.to_dict("records")
                for row_idx, res in enumerate(executor.map(process_book_row, row_dicts)):
                    results.append(res)
                    # Track book PK by DataFrame index for inline enrichment
                    if res[0] is not None:
                        book_pks_by_idx[read_df.index[row_idx]] = res[0].pk
                    processed += 1
                    if progress_cb:
                        progress_cb(processed, total_books, "Syncing books")
        finally:
            for worker_connection in worker_connections:
                # The pool's threads are gone; allow this thread to close what they opened.
                worker_connection.inc_thread_sharing()
                try:
                    worker_connection.close()
                finally:
                    worker_connection.dec_thread_sharing()

        if failed_rows:
            logger.error(f"Skipped {len(failed_rows)} of {total_books} CSV rows that failed to sync")
            # Every row failing points at a bug, not bad data: fail the upload.
            if len(failed_rows) == total_books:
                raise failed_rows[0]
    return results, book_pks_by_idx, already_counted_book_ids, previous_anon_session, upload_user_id, upload_nonce


def sync_read_counts_and_user_books(user, results, already_counted_book_ids):
    """Decrement read counts for books dropped since the last upload and store UserBook rows."""
    # Books in the previous upload but not this one lose this reader. The
    # floor guards against counts that were never incremented (seed data).
    current_book_ids = {book.id for book, genres, original_row in results if book}
    dropped_book_ids = already_counted_book_ids - current_book_ids
    if results and dropped_book_ids:
        Book.objects.filter(pk__in=dropped_book_ids, global_read_count__gt=0).update(
            global_read_count=F("global_read_count") - 1
        )

    # Store UserBook entries for registered users
    if user and results:
        # Delete stale UserBook records from previous uploads that are no longer in the CSV
        stale_count, _ = UserBook.objects.filter(user=user).exclude(book_id__in=current_book_ids).delete()
        if stale_count:
            logger.info(f"Removed {stale_count} stale UserBook records for user {user.id}")

        # Store book data with ratings and reviews - now we have the original row
        for book, genres, original_row in results:
            if book:
                rating_value = None
                review_value = ""

                if pd.notna(original_row.get("My Rating")) and original_row["My Rating"] > 0:
                    try:
                        rating_value = int(original_row["My Rating"])
                    except (ValueError, TypeError):
                        rating_value = None

                if pd.notna(original_row.get("My Review")):
                    review_value = str(original_row["My Review"]).strip()

                date_read_value = None
                if pd.notna(original_row.get("Date Read")):
                    date_read_value = pd.to_datetime(original_row["Date Read"], errors="coerce")
                    if pd.isna(date_read_value):
                        date_read_value = None

                # Use update_or_create to handle duplicates better
                UserBook.objects.update_or_create(
                    user=user,
                    book=book,
                    defaults={
                        "user_rating": rating_value,
                        "user_review": review_value,
                        "date_read": date_read_value,
                    },
                )


def backfill_from_db(read_df, book_pks_by_idx, user_book_objects):
    """Fill blank pages/year/rating in read_df from the Book table; return refreshed book objects."""
    # Phase A: Backfill from DB -- single batch query
    all_book_pks = list(book_pks_by_idx.values())
    enriched_books = {
        b["pk"]: b
        for b in Book.objects.filter(pk__in=all_book_pks).values("pk", "page_count", "publish_year", "average_rating")
    }
    backfilled = 0
    for idx, book_pk in book_pks_by_idx.items():
        book_data = enriched_books.get(book_pk)
        if book_data:
            if pd.isna(read_df.at[idx, "Number of Pages"]) and book_data["page_count"]:
                read_df.at[idx, "Number of Pages"] = book_data["page_count"]
                backfilled += 1
            if pd.isna(read_df.at[idx, "Original Publication Year"]) and book_data["publish_year"]:
                read_df.at[idx, "Original Publication Year"] = book_data["publish_year"]
            if pd.isna(read_df.at[idx, "Average Rating"]) and book_data["average_rating"]:
                read_df.at[idx, "Average Rating"] = book_data["average_rating"]
    logger.info(f"DB backfill: enriched {backfilled}/{len(all_book_pks)} books from existing data")

    # Re-coerce numeric columns after backfill
    read_df["Number of Pages"] = pd.to_numeric(read_df["Number of Pages"], errors="coerce")
    read_df["Average Rating"] = pd.to_numeric(read_df["Average Rating"], errors="coerce")
    if "Original Publication Year" in read_df.columns:
        read_df["Original Publication Year"] = pd.to_numeric(read_df["Original Publication Year"], errors="coerce")

    # Refresh book objects so enriched data is available for scoring
    refreshed_pks = [b.pk for b in user_book_objects]
    refreshed_map = {b.pk: b for b in Book.objects.filter(pk__in=refreshed_pks)}
    user_book_objects = [refreshed_map.get(b.pk, b) for b in user_book_objects]
    return user_book_objects
