"""Shared enrichment/display helpers used by multiple view modules."""

import logging
import math
from datetime import date

from ..cache_utils import safe_cache_get, safe_cache_set
from ..dna_constants import GLOBAL_AVERAGES

logger = logging.getLogger(__name__)


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
        # Each average is stored as 0 when the library has no data for it: skip its sentence then.
        # Book length
        len_pct = percentiles.get("avg_book_length", 50)
        user_len = user_stats.get("avg_book_length") or 0
        comm_len = community.get("avg_book_length")
        if user_len <= 0:
            pass
        elif comm_len and user_len >= comm_len:
            comparative_text["length_direction"] = "longer"
            comparative_text["length_pct"] = round(len_pct)
        else:
            comparative_text["length_direction"] = "shorter"
            comparative_text["length_pct"] = round(100 - len_pct)

        # Book age
        year_pct = percentiles.get("avg_publish_year", 50)
        user_year = user_stats.get("avg_publish_year") or 0
        comm_year = community.get("avg_publish_year")
        if user_year <= 0:
            pass
        elif comm_year and user_year <= comm_year:
            comparative_text["age_direction"] = "older"
            comparative_text["age_pct"] = round(year_pct)
        else:
            comparative_text["age_direction"] = "newer"
            comparative_text["age_pct"] = round(100 - year_pct)

        # Books per year
        bpy_pct = percentiles.get("avg_books_per_year", 50)
        user_bpy = user_stats.get("avg_books_per_year") or 0
        comm_bpy = community.get("avg_books_per_year")
        if user_bpy <= 0:
            pass
        elif comm_bpy and user_bpy >= comm_bpy:
            comparative_text["bpy_direction"] = "more"
            comparative_text["bpy_pct"] = round(bpy_pct)
        else:
            comparative_text["bpy_direction"] = "fewer"
            comparative_text["bpy_pct"] = round(100 - bpy_pct)

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
        if v is not None and v > 0
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
        if v is not None and v > 0
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


def _resolve_rec_source_names(recommendations, recommendations_meta=None, owner_name=None):
    """Name other readers on rec cards only if their profile is public *now*, by current username.

    Stored recs/meta snapshot usernames at generation time, so they go stale when a reader renames,
    deletes their account or goes private. Resolve by user id at render time; anything not public
    renders unnamed. Returns a copy of recommendations_meta; recommendations are updated in place.
    """
    from django.contrib.auth.models import User

    meta = dict(recommendations_meta or {})
    similar_sources = [
        s
        for rec in recommendations
        for s in rec.get("sources", [])
        if s.get("type") == "similar_user" and s.get("user_id")
    ]
    closest = max(similar_sources, key=lambda s: s.get("similarity_score", 0), default=None)
    primaries = [rec["primary_source_user"] for rec in recommendations if rec.get("primary_source_user")]

    ids = {s.get("user_id") for s in primaries if s.get("user_id")}
    if closest:
        ids.add(closest["user_id"])
    public_names = (
        dict(
            User.objects.filter(
                id__in=ids, userprofile__is_public=True, userprofile__visible_in_recommendations=True
            ).values_list("id", "username")
        )
        if ids
        else {}
    )

    for source in primaries:
        source["username"] = public_names.get(source.get("user_id"))
    if "closest_match_username" in meta:
        meta["closest_match_username"] = public_names.get(closest["user_id"]) if closest else None

    # The public-page "X and Y share N books" line must never name the other reader.
    for rec in recommendations:
        components = rec.get("explanation_components") or {}
        if "shared_public" in components:
            shared = (rec.get("primary_source_user") or {}).get("shared_books")
            if owner_name and shared:
                components["shared_public"] = f"{owner_name} and this reader share {shared} books in common"
            else:
                components.pop("shared_public")

    return meta
