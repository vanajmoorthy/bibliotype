"""Shared enrichment/display helpers used by multiple view modules."""

import logging
import math
from collections import Counter
from datetime import date

from django.db import transaction
from django.db.models import Count, Q

from ..cache_utils import safe_cache_get, safe_cache_set
from ..dna_constants import CANONICAL_GENRE_MAP, FICTION_GENRES, GLOBAL_AVERAGES, NONFICTION_GENRES

logger = logging.getLogger(__name__)

ENRICHMENT_STATS_CACHE_TTL = 2  # seconds — short, since dashboard polls every 5s


def _compute_enrichment_stats(user):
    """Compute enrichment-derived stats from DB in a single QuerySet pass.

    Cached briefly so a 5s polling cadence (plus a possible page load in the
    same window) doesn't re-hit Postgres on every tick for users with
    thousands of books.
    """
    from ..models import Book

    cache_key = f"enrichment_stats_{user.id}"
    cached = safe_cache_get(cache_key)
    if cached is not None:
        return cached

    books = list(
        Book.objects.filter(readers__user=user)
        .select_related("author", "publisher")
        .prefetch_related("genres")
    )
    if not books:
        return None

    total = len(books)
    page_counts = [b.page_count for b in books if b.page_count]

    all_genres = []
    fiction_count = 0
    nonfiction_count = 0
    mainstream_count = 0
    for book in books:
        book_genres = [g.name for g in book.genres.all()]
        all_genres.extend(book_genres)
        canonical = {CANONICAL_GENRE_MAP.get(g, g) for g in book_genres}
        if canonical & FICTION_GENRES:
            fiction_count += 1
        elif canonical & NONFICTION_GENRES:
            nonfiction_count += 1
        if book.author.is_mainstream or (book.publisher and book.publisher.is_mainstream):
            mainstream_count += 1

    mapped = [CANONICAL_GENRE_MAP.get(g, g) for g in all_genres]

    stats = {
        "total_pages_read": sum(page_counts) if page_counts else None,
        "avg_book_length": round(sum(page_counts) / len(page_counts)) if page_counts else None,
        "top_genres": Counter(mapped).most_common(10),
        "unique_genres_count": len(set(mapped)),
        "fiction_nonfiction_split": (
            {"fiction_count": fiction_count, "nonfiction_count": nonfiction_count}
            if (fiction_count + nonfiction_count) > 0
            else None
        ),
        "mainstream_score_percent": round((mainstream_count / total) * 100),
    }

    safe_cache_set(cache_key, stats, timeout=ENRICHMENT_STATS_CACHE_TTL)
    return stats


def _recalculate_enrichment_stats(user, dna_data):
    """Apply enrichment-derived stats to dna_data in place.

    Called on each page load and poll while enrichment is pending, so the
    dashboard reflects the latest enriched data without requiring a re-upload.
    """
    stats = _compute_enrichment_stats(user)
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

    counts = Book.objects.filter(readers__user=user).aggregate(
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
                if not (locked.dna_data or {}).get("enrichment_finalized"):
                    _recalculate_enrichment_stats(user, dna_data)
                    dna_data["enrichment_finalized"] = True
                    locked.dna_data = dna_data
                    locked.save(update_fields=["dna_data"])
                else:
                    # Another request already finalized — pick up its data so
                    # the in-memory dna_data the caller holds is consistent.
                    dna_data.clear()
                    dna_data.update(locked.dna_data or {})
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
        "genres_pending": (genres_done / total) < 0.5,
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
