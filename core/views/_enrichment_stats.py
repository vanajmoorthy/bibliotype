"""Enrichment-derived dashboard stats and progress, recomputed from the DB while enrichment runs."""

import math
from collections import Counter

from django.db import transaction
from django.db.models import Count, Q

from ..cache_utils import safe_cache_get, safe_cache_set
from ..services.dna.utils import _build_cover_url, _cover_initial
from ..services.genre_classification import canonicalize_genre_names, count_fiction_nonfiction, resolve_genre_labels

ENRICHMENT_STATS_CACHE_TTL = 2  # seconds — short, since dashboard polls every 5s


def _compute_enrichment_stats(
    user, csv_context=None, shelf_signals=None, fresh=False, current_reader_type=None, current_explanation=None
):
    """Compute enrichment-derived stats from DB in a single QuerySet pass.

    Cached briefly so a 5s polling cadence (plus a possible page load in the
    same window) doesn't re-hit Postgres on every tick for users with
    thousands of books.

    When csv_context is provided (newer users who have reader_type_csv_context
    persisted in dna_data), reader-type fields are included in the returned
    stats dict and stored in the same cache entry.  The cache key encodes
    whether csv_context is present so a None call never returns a stale entry
    that happened to include reader-type fields (or vice-versa).

    shelf_signals — the sparse {book_id: [shelf_fiction, shelf_nonfiction,
    shelf_genres]} map persisted at generation — lets the fiction/nonfiction
    split match generation quality instead of degrading to API-genre-only.

    fresh=True skips the cache read (finalize path, so a ≤2s-stale entry can't
    be locked in permanently).  current_reader_type / current_explanation keep
    the reader-type blurb stable across polls when the winning type is unchanged.
    """
    from ..models import Book
    from ..services.dna.reader_type import recompute_reader_type_from_db

    cache_key = f"enrichment_stats_{user.id}_{'rt' if csv_context is not None else 'nort'}"
    if not fresh:
        cached = safe_cache_get(cache_key)
        if cached is not None:
            return cached

    books = list(
        Book.objects.filter(readers__user=user).select_related("author", "publisher").prefetch_related("genres")
    )
    if not books:
        return None

    total = len(books)
    page_counts = [b.page_count for b in books if b.page_count]

    # Context-dependent fiction/nonfiction classification (shared helpers).
    # Books with no classifiable genres land in defaulted_count, never fiction.
    # When generation persisted per-book shelf signals, align them with `books`
    # and feed them in so the split matches generation quality (not API-only).
    # Genre labels are axis-resolved per book with the SAME signals, mirroring
    # generation in core/services/dna/__init__.py — otherwise top_genres labels
    # flip between the stored DNA and live-enrichment polls.
    resolved = []
    genre_sets = []
    shelf_signal_list = []
    mainstream_count = 0
    for book in books:
        book_genres = [g.name for g in book.genres.all()]
        genre_sets.append(canonicalize_genre_names(book_genres))
        sig = shelf_signals.get(str(book.id)) if shelf_signals else None
        signal = (bool(sig[0]), bool(sig[1]), frozenset(sig[2])) if sig else (False, False, frozenset())
        shelf_signal_list.append(signal)
        resolved.extend(
            resolve_genre_labels(
                book_genres, shelf_fiction=signal[0], shelf_nonfiction=signal[1], shelf_genres=signal[2]
            )
        )
        if book.author.is_mainstream or (book.publisher and book.publisher.is_mainstream):
            mainstream_count += 1

    fiction_count, nonfiction_count, defaulted_count = count_fiction_nonfiction(genre_sets, shelf_signal_list)

    # Book extremes (longest/shortest read). Mirror the generation-time logic in
    # core/services/dna/stats.py (compute_book_extremes) so live-poll values match the final render.
    # Needs >= 2 books with a page count and a genuine spread, else stays None.
    longest_book = None
    shortest_book = None
    page_difference = None
    books_with_pages = [b for b in books if b.page_count]
    if len(books_with_pages) >= 2:
        books_with_pages.sort(key=lambda b: (-b.page_count, b.normalized_title))
        longest = books_with_pages[0]
        shortest = books_with_pages[-1]
        if longest.page_count != shortest.page_count:
            longest_book = {
                "title": longest.title,
                "author": longest.author.name,
                "page_count": longest.page_count,
                "cover_url": longest.cover_url or _build_cover_url(longest.isbn13),
                "initial": _cover_initial(longest.title),
            }
            shortest_book = {
                "title": shortest.title,
                "author": shortest.author.name,
                "page_count": shortest.page_count,
                "cover_url": shortest.cover_url or _build_cover_url(shortest.isbn13),
                "initial": _cover_initial(shortest.title),
            }
            page_difference = longest.page_count - shortest.page_count

    stats = {
        "total_pages_read": sum(page_counts) if page_counts else None,
        "avg_book_length": round(sum(page_counts) / len(page_counts)) if page_counts else None,
        "top_genres": Counter(resolved).most_common(10),
        "unique_genres_count": len(set(resolved)),
        "fiction_nonfiction_split": (
            {
                "fiction_count": fiction_count,
                "nonfiction_count": nonfiction_count,
                "defaulted_count": defaulted_count,
            }
            if (fiction_count + nonfiction_count) > 0
            else None
        ),
        "mainstream_score_percent": round((mainstream_count / total) * 100),
        "longest_book": longest_book,
        "shortest_book": shortest_book,
        "page_difference": page_difference,
    }

    # Reader-type recompute: fold into the same DB pass when csv_context is available.
    # Pass the currently-stored type/explanation so an unchanged winning type keeps
    # its blurb instead of re-rolling random.choice on every poll.
    if csv_context is not None:
        reader_type_result = recompute_reader_type_from_db(
            user,
            csv_context,
            books=books,
            current_reader_type=current_reader_type,
            current_explanation=current_explanation,
            shelf_signal_list=shelf_signal_list,
        )
        if reader_type_result:
            stats.update(reader_type_result)

    safe_cache_set(cache_key, stats, timeout=ENRICHMENT_STATS_CACHE_TTL)
    return stats


def _recalculate_enrichment_stats(user, dna_data, fresh=False):
    """Apply enrichment-derived stats to dna_data in place.

    Called on each page load and poll while enrichment is pending, so the
    dashboard reflects the latest enriched data without requiring a re-upload.
    fresh=True forwards to _compute_enrichment_stats to bypass the 2s cache
    (used on the finalize path).
    """
    csv_context = dna_data.get("reader_type_csv_context")
    stats = _compute_enrichment_stats(
        user,
        csv_context=csv_context,
        shelf_signals=dna_data.get("shelf_signals"),
        fresh=fresh,
        current_reader_type=dna_data.get("reader_type"),
        current_explanation=dna_data.get("reader_type_explanation"),
    )
    if not stats:
        return
    user_stats = dna_data.setdefault("user_stats", {})
    if stats["total_pages_read"] is not None:
        user_stats["total_pages_read"] = stats["total_pages_read"]
        user_stats["avg_book_length"] = stats["avg_book_length"]
    dna_data["top_genres"] = stats["top_genres"]
    dna_data["unique_genres_count"] = stats["unique_genres_count"]
    dna_data["fiction_nonfiction_split"] = stats["fiction_nonfiction_split"]
    dna_data["mainstream_score_percent"] = stats["mainstream_score_percent"]
    # Only overwrite extremes once we have a genuine pair — never regress a
    # populated Book Extremes card back to an empty/skeleton state mid-enrichment.
    if stats["longest_book"] is not None:
        dna_data["longest_book"] = stats["longest_book"]
        dna_data["shortest_book"] = stats["shortest_book"]
        dna_data["page_difference"] = stats["page_difference"]
    # Reader type: update whenever the recompute ran (csv_context present and DB has books).
    if "reader_type" in stats:
        dna_data["reader_type"] = stats["reader_type"]
        dna_data["reader_type_explanation"] = stats["reader_type_explanation"]
        dna_data["top_reader_types"] = stats["top_reader_types"]
        dna_data["reader_type_scores"] = stats["reader_type_scores"]
        dna_data["reader_type_scores_version"] = stats["reader_type_scores_version"]


def _compute_enrichment_progress(user, profile, dna_data):
    """Compute enrichment progress + apply DB-derived stats to dna_data.

    Single chokepoint shared by display_dna_view and the enrichment status poll.
    Returns:
        - None if user has no books
        - {"pending": False, "total": N} when enrichment has finished
          (also persists the finalized dna_data to the profile on first
          completion).
        - {"pending": True, "percent": ..., ...} otherwise (mutates dna_data
          with fresh stats).
    """
    from ..models import Book

    # Scope progress to the current upload cohort when it was persisted at
    # generation. On a re-upload the user's previously-enriched books already
    # have google_books_last_checked set, so counting all of their books would
    # make attempted == total almost immediately and flip the dashboard to
    # "complete" before the newly-added books finish enriching. Legacy profiles
    # generated before enrichment_cohort_ids existed fall back to all-books.
    cohort = dna_data.get("enrichment_cohort_ids")
    qs = Book.objects.filter(readers__user=user)
    if cohort:
        qs = qs.filter(id__in=cohort)

    counts = qs.aggregate(
        total=Count("id", distinct=True),
        genres_done=Count("id", filter=Q(genres__isnull=False), distinct=True),
        pages_done=Count("id", filter=Q(page_count__isnull=False), distinct=True),
        year_done=Count("id", filter=Q(publish_year__isnull=False), distinct=True),
        # A book counts as attempted when it was stamped (google_books_last_checked)
        # OR already has complete data (genres + pages + year) — the dispatch gate
        # in calculate_full_dna never enriches complete books, so an unstamped
        # complete book would otherwise leave `attempted < total` FOREVER and the
        # dashboard banner would poll eternally. Bit us on 2026-09-05: a bulk
        # re-enrichment run reset stamps and hit the Google Books daily quota,
        # leaving ~5k complete-but-unstamped books; every upload overlapping them
        # stuck at a frozen percent.
        attempted=Count(
            "id",
            filter=Q(google_books_last_checked__isnull=False)
            | (Q(genres__isnull=False) & Q(page_count__isnull=False) & Q(publish_year__isnull=False)),
            distinct=True,
        ),
    )
    total = counts["total"]
    if total == 0:
        return None

    attempted = counts["attempted"]
    pending = attempted < total

    if not pending:
        # Two concurrent requests (page render + AJAX poll, or two open tabs)
        # could both race past the unfinalized check and both write. Lock the
        # profile row, re-check inside the transaction, then save once.
        if not dna_data.get("enrichment_finalized"):
            from ..models import UserProfile

            with transaction.atomic():
                locked = UserProfile.objects.select_for_update().get(pk=profile.pk)
                locked_dna = locked.dna_data or {}
                if locked_dna.get("enrichment_finalized"):
                    # Another request already finalized — pick up its data so
                    # the in-memory dna_data the caller holds is consistent.
                    dna_data.clear()
                    dna_data.update(locked_dna)
                elif locked.pending_dna_task_id or (locked_dna.get("vibe_data_hash") != dna_data.get("vibe_data_hash")):
                    # A newer generation superseded this request between the
                    # unfinalized read and acquiring the lock (re-upload in
                    # flight, or the locked row is a different DNA). Don't clobber
                    # it with our pre-lock snapshot — surface the locked data.
                    dna_data.clear()
                    dna_data.update(locked_dna)
                else:
                    # Legacy profiles have no persisted shelf_signals; a shelf-less
                    # recompute would degrade the fiction/nonfiction split, so keep
                    # the generation-time value rather than permanently downgrading.
                    has_shelf_signals = bool(dna_data.get("shelf_signals"))
                    original_split = dna_data.get("fiction_nonfiction_split")
                    # fresh=True busts the 2s stats cache so finalize can't lock
                    # in stale numbers (e.g. a book added within the last 2s).
                    _recalculate_enrichment_stats(user, dna_data, fresh=True)
                    if not has_shelf_signals and original_split is not None:
                        dna_data["fiction_nonfiction_split"] = original_split
                    dna_data["enrichment_finalized"] = True
                    locked.dna_data = dna_data
                    finalize_update_fields = ["dna_data"]
                    if dna_data.get("reader_type"):
                        locked.reader_type = dna_data["reader_type"]
                        finalize_update_fields.append("reader_type")
                    locked.save(update_fields=finalize_update_fields)
                profile.dna_data = dna_data
        return {"pending": False, "total": total}

    _recalculate_enrichment_stats(user, dna_data)
    genres_done = counts["genres_done"]
    pages_done = counts["pages_done"]
    return {
        "pending": True,
        "total": total,
        "percent": round(attempted / total * 100),
        "genres_done": genres_done,
        # Genre-coverage gate — applies to ALL CSV sources (Goodreads included,
        # not just StoryGraph): while fewer than 50% of the user's books have
        # genres, genre-derived stats are flagged pending. genre_coverage_pct
        # feeds the fiction/nonfiction card's coverage subtitle.
        "genres_pending": (genres_done / total) < 0.5,
        "genre_coverage_pct": round(genres_done / total * 100),
        "pages_done": pages_done,
        "pages_pending": (pages_done / total) < 0.5,
        # Per-stat banner gates: True iff any book is still missing this field.
        # Distinct from pages_pending above (a 50%-done sparseness threshold
        # for skeletons). Goodreads CSVs supply both fields per row, so these
        # stay False throughout enrichment for Goodreads uploads.
        "pages_any_missing": pages_done < total,
        "year_any_missing": counts["year_done"] < total,
        "remaining_minutes": max(1, math.ceil((total - attempted) / 20)),
        "csv_source": dna_data.get("csv_source", "goodreads"),
    }
