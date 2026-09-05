"""Shared enrichment/display helpers used by multiple view modules."""

import logging
import math
from collections import Counter
from datetime import date

from django.db import transaction
from django.db.models import Count, Q

from ..cache_utils import safe_cache_get, safe_cache_set
from ..dna_constants import GLOBAL_AVERAGES
from ..services.dna.utils import _build_cover_url, _cover_initial
from ..services.genre_classification import canonicalize_genre_names, count_fiction_nonfiction, resolve_genre_labels

logger = logging.getLogger(__name__)

ENRICHMENT_STATS_CACHE_TTL = 2  # seconds — short, since dashboard polls every 5s

# Don't advertise the pool size until it's large enough to be flattering
RECS_POOL_MIN_DISPLAY = 100


def _friendly_floor(n):
    """Round down to a display-friendly magnitude, so copy reads "over 230" not "over 237"."""
    if n < 500:
        return (n // 10) * 10
    if n < 2000:
        return (n // 50) * 50
    return (n // 100) * 100


def _get_recommendation_pool_display():
    """Rounded-down eligible-reader count for "out of over X" copy, or None while the pool is small."""
    from ..services.user_similarity_service import get_recommendation_pool_size

    count = get_recommendation_pool_size()
    if count < RECS_POOL_MIN_DISPLAY:
        return None
    return _friendly_floor(count)


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
    # core/services/dna/__init__.py so live-poll values match the final render.
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

    Single chokepoint shared by display_dna_view and enrichment_status_view.
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
        attempted=Count("id", filter=Q(google_books_last_checked__isnull=False), distinct=True),
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


def _enrich_dna_for_display(dna_data):
    """Patch dna_data with fresh community averages, global averages, and comparative text.

    Community averages and percentiles are always up-to-date rather than stale
    from generation time.
    """
    if not dna_data:
        return dna_data

    from ..percentile_engine import calculate_community_means, calculate_percentiles_from_aggregates

    # Always use current global averages constant
    dna_data["global_averages"] = GLOBAL_AVERAGES

    # Fallback values when community has no data yet
    COMMUNITY_FALLBACKS = {
        "avg_book_length": GLOBAL_AVERAGES["avg_book_length_pages"],
        "avg_publish_year": GLOBAL_AVERAGES["avg_publish_year"],
        "total_books_read": 50,
        "avg_books_per_year": GLOBAL_AVERAGES["avg_books_per_year"],
    }

    # Always compute fresh community averages from current histogram data (cached 10 min)
    community_cache_key = "community_means"
    raw_community = safe_cache_get(community_cache_key)
    if raw_community is None:
        raw_community = calculate_community_means()
        safe_cache_set(community_cache_key, raw_community, 600)
    dna_data["community_averages"] = {
        k: v if v is not None else COMMUNITY_FALLBACKS.get(k, 0) for k, v in raw_community.items()
    }

    user_stats = dna_data.get("user_stats", {})

    # Recalculate percentiles from current aggregate data so they're never stale.
    # Cached for 10 minutes to avoid a DB hit on every page load while still staying fresh.
    bl = user_stats.get("avg_book_length", 0)
    br = user_stats.get("total_books_read", 0)
    bpy = user_stats.get("avg_books_per_year", 0)
    py = user_stats.get("avg_publish_year", 0)
    cache_key = f"fresh_pct_{bl}_{br}_{bpy}_{py}"
    fresh_percentiles = safe_cache_get(cache_key)
    if fresh_percentiles is None:
        fresh_percentiles = calculate_percentiles_from_aggregates(user_stats)
        safe_cache_set(cache_key, fresh_percentiles or {}, 600)
    if fresh_percentiles:
        dna_data["bibliotype_percentiles"] = fresh_percentiles

    # Recompute comparative_text from current percentiles + community averages
    percentiles = dna_data.get("bibliotype_percentiles", {})
    community = dna_data.get("community_averages", {})
    comparative_text = {}

    if percentiles:
        # Book length
        len_pct = percentiles.get("avg_book_length", 50)
        user_len = user_stats.get("avg_book_length", 0)
        comm_len = community.get("avg_book_length")
        if comm_len and user_len >= comm_len:
            comparative_text["length_direction"] = "longer"
            comparative_text["length_pct"] = round(len_pct, 1)
        else:
            comparative_text["length_direction"] = "shorter"
            comparative_text["length_pct"] = round(100 - len_pct, 1)

        # Book age
        year_pct = percentiles.get("avg_publish_year", 50)
        user_year = user_stats.get("avg_publish_year", 2025)
        comm_year = community.get("avg_publish_year")
        if comm_year and user_year <= comm_year:
            comparative_text["age_direction"] = "older"
            comparative_text["age_pct"] = round(year_pct, 1)
        else:
            comparative_text["age_direction"] = "newer"
            comparative_text["age_pct"] = round(100 - year_pct, 1)

        # Books per year
        bpy_pct = percentiles.get("avg_books_per_year", 50)
        user_bpy = user_stats.get("avg_books_per_year", 0)
        comm_bpy = community.get("avg_books_per_year")
        if comm_bpy and user_bpy >= comm_bpy:
            comparative_text["bpy_direction"] = "more"
            comparative_text["bpy_pct"] = round(bpy_pct, 1)
        else:
            comparative_text["bpy_direction"] = "fewer"
            comparative_text["bpy_pct"] = round(100 - bpy_pct, 1)

    dna_data["comparative_text"] = comparative_text

    # Compute dynamic number line ranges so markers are well-spread
    current_year = date.today().year

    page_vals = [
        v
        for v in [
            user_stats.get("avg_book_length"),
            community.get("avg_book_length"),
            GLOBAL_AVERAGES["avg_book_length_pages"],
        ]
        if v is not None
    ]
    if page_vals:
        lo, hi = min(page_vals), max(page_vals)
        pages_min = 300 if lo >= 300 else max(0, math.floor(lo / 50) * 50 - 50)
        pages_max = 400 if hi <= 400 else math.ceil(hi / 50) * 50 + 50
    else:
        pages_min, pages_max = 300, 400

    year_vals = [
        v
        for v in [
            user_stats.get("avg_publish_year"),
            community.get("avg_publish_year"),
            GLOBAL_AVERAGES["avg_publish_year"],
        ]
        if v is not None
    ]
    years_min = min(1980, math.floor(min(year_vals) / 5) * 5) if year_vals else 1980
    years_max = current_year

    bpy_vals = [
        v
        for v in [
            user_stats.get("avg_books_per_year"),
            community.get("avg_books_per_year"),
            GLOBAL_AVERAGES["avg_books_per_year"],
        ]
        if v is not None
    ]
    bpy_max = 10 if (not bpy_vals or max(bpy_vals) <= 10) else math.ceil(max(bpy_vals) / 5) * 5 + 5

    dna_data["number_line_ranges"] = {
        "pages": {
            "min": pages_min,
            "max": pages_max,
            "min_label": f"{pages_min} pages",
            "max_label": f"{pages_max} pages",
        },
        "year": {"min": years_min, "max": years_max, "min_label": f"{years_min} CE", "max_label": f"{years_max} CE"},
        "bpy": {"min": 0, "max": bpy_max, "min_label": "0 per year", "max_label": f"{bpy_max} per year"},
    }

    return dna_data


BADGE_COLOR_MAP = {
    "Literary twin": "bg-badge-5",
    "Kindred reader": "bg-badge-4",
    "Some shared tastes": "bg-badge-3",
    "Some overlap": "bg-badge-2",
    "Different preferences": "bg-gray-200",
    "Opposite tastes": "bg-gray-200",
}


def _expand_book_dict(rec, badge_color_map):
    # Legacy fallback: stored recs predating US-032 only have flat
    # `book_*` keys. Reconstruct the nested template shape and bake
    # the `primary_source_user.badge_class` here so both views can
    # collapse the old 18-line for-loop to a single guard.
    if rec.get("primary_source_user"):
        match_quality = rec["primary_source_user"].get("match_quality", "")
        rec["primary_source_user"]["badge_class"] = badge_color_map.get(match_quality, "bg-brand-purple")
    return {
        "id": rec.get("book_id"),
        "title": rec.get("book_title", "Unknown Title"),
        "author": {"name": rec.get("book_author", "Unknown Author")},
        "average_rating": rec.get("book_average_rating"),
    }
