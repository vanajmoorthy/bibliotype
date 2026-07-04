import json
import logging
import math
import os
from collections import Counter
from datetime import date
from io import StringIO

import pandas as pd
from celery.result import AsyncResult
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm, PasswordResetForm
from django.contrib.auth.models import User
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth.views import PasswordResetView
from django.core.exceptions import NON_FIELD_ERRORS
from django.db import transaction
from django.db.models import Count, Q
from django.forms.utils import ErrorDict, ErrorList
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse, reverse_lazy
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit
from django_ratelimit.exceptions import Ratelimited

from ..ratelimit_utils import client_ip_key, get_real_client_ip

from ..analytics.events import (
    track_anonymous_dna_claimed,
    track_anonymous_dna_displayed,
    track_dna_displayed,
    track_file_upload_started,
    track_profile_made_public,
    track_public_profile_viewed,
    track_recommendation_error,
    track_recommendations_generated,
    track_settings_updated,
    track_user_logged_in,
    track_user_signed_up,
)
from ..cache_utils import DNA_CACHE_TTL, safe_cache_add, safe_cache_delete, safe_cache_get, safe_cache_set
from ..dna_constants import CANONICAL_GENRE_MAP, FICTION_GENRES, GLOBAL_AVERAGES, NONFICTION_GENRES
from ..forms import CustomUserCreationForm, UpdateDisplayNameForm
from ..tasks import _save_dna_to_profile, claim_anonymous_dna_task, generate_reading_dna_task

logger = logging.getLogger(__name__)


def home_view(request):
    """Displays the main upload page."""
    return render(request, "core/home.html")


def about_view(request):
    """Displays the about page."""
    return render(request, "core/about.html")


def privacy_view(request):
    """Displays the privacy policy page."""
    return render(request, "core/privacy.html")


def terms_view(request):
    """Displays the terms of service page."""
    return render(request, "core/terms.html")


def display_dna_view(request):
    is_processing = request.GET.get("processing") == "true"

    dna_data = request.session.get("dna_data")
    user_profile = None
    recommendations = []

    if request.user.is_authenticated:
        user_profile = request.user.userprofile
        user_profile.refresh_from_db()

        # Show processing screen for authenticated users when flagged, regardless of existing data
        if is_processing:
            return render(request, "core/dashboard.html", {"is_processing": True})

        if dna_data is None and user_profile.dna_data:
            dna_data = user_profile.dna_data

        if user_profile.dna_data:
            try:
                if user_profile.recommendations_data:
                    stored_recs = user_profile.recommendations_data

                    for rec in stored_recs:
                        if "book" not in rec:
                            rec["book"] = _expand_book_dict(rec, BADGE_COLOR_MAP)

                    recommendations = stored_recs
                    logger.info(f"Loaded {len(recommendations)} stored recommendations for user {request.user.id}")
                else:
                    # No stored recommendations yet - they're being generated asynchronously.
                    # Sentinel-guard via cache.add so concurrent dashboard polls don't
                    # spawn duplicate tasks. The sentinel is deleted when the task
                    # finishes (in generate_recommendations_task's finally block).
                    if safe_cache_add(f"recs_dispatching_{request.user.id}", 1, timeout=300):
                        from ..tasks import generate_recommendations_task

                        generate_recommendations_task.delay(request.user.id)
                        logger.info(f"No stored recommendations for user {request.user.id}, triggered generation")
                    else:
                        logger.info(
                            f"Skipped duplicate recommendations dispatch for user {request.user.id} "
                            f"(sentinel held)"
                        )
                    recommendations = []

                # Track recommendations displayed (only if we have some)
                if recommendations:
                    track_recommendations_generated(
                        user_id=request.user.id,
                        recommendation_count=len(recommendations),
                        is_authenticated=True,
                    )
            except Exception as e:
                logger.error(f"Error loading recommendations for user {request.user.id}: {e}", exc_info=True)
    else:
        # Anonymous user recommendations
        if dna_data and request.session.session_key:
            try:
                from datetime import timedelta

                from django.utils import timezone

                from ..models import AnonymousUserSession, Author
                from ..services.recommendation_service import get_recommendations_for_anonymous

                try:
                    anon_session = AnonymousUserSession.objects.get(session_key=request.session.session_key)
                    # Session exists, get recommendations normally
                    recommendations = get_recommendations_for_anonymous(request.session.session_key, limit=6)
                except AnonymousUserSession.DoesNotExist:
                    # Session record doesn't exist - try to recreate from session dna_data
                    # This can happen if the session expired but session data still exists
                    logger.warning(
                        f"AnonymousUserSession not found for session {request.session.session_key}, attempting to recreate from dna_data..."
                    )
                    try:

                        # Extract distributions from dna_data
                        genre_dist = {}
                        for genre, count in dna_data.get("top_genres", []):
                            genre_dist[genre] = count

                        author_dist = {}
                        for author, count in dna_data.get("top_authors", [])[:20]:
                            normalized = Author._normalize(author)
                            author_dist[normalized] = count

                        # Try to get book IDs from session if stored, otherwise use empty list
                        books_data = request.session.get("book_ids", [])
                        top_books_data = request.session.get("top_book_ids", [])
                        book_ratings = request.session.get("book_ratings", {})

                        # Recreate (or refresh) the AnonymousUserSession from dna_data.
                        # Use update_or_create so a concurrent dashboard request that
                        # already created the row doesn't blow up the second caller
                        # with a unique-constraint IntegrityError.
                        anon_session, _ = AnonymousUserSession.objects.update_or_create(
                            session_key=request.session.session_key,
                            defaults={
                                "dna_data": dna_data,
                                "books_data": books_data,
                                "top_books_data": top_books_data,
                                "genre_distribution": genre_dist,
                                "author_distribution": author_dist,
                                "book_ratings": book_ratings,
                                "expires_at": timezone.now() + timedelta(days=7),
                            },
                        )

                        # Now try to get recommendations
                        recommendations = get_recommendations_for_anonymous(request.session.session_key, limit=6)
                        logger.info(
                            f"Recreated AnonymousUserSession and generated {len(recommendations)} recommendations"
                        )
                    except Exception as recreate_error:
                        logger.error(f"Error recreating anonymous session: {recreate_error}", exc_info=True)
                        recommendations = []

                if recommendations:
                    for rec in recommendations:
                        rec["confidence_pct"] = int(rec.get("confidence", 0) * 100)
                        rec["primary_source_user"] = None
                        best_similarity = 0

                        for source in rec.get("sources", []):
                            if source.get("type") == "similar_user":
                                if source.get("similarity_score", 0) > best_similarity:
                                    best_similarity = source.get("similarity_score", 0)
                                    rec["primary_source_user"] = source

                    logger.info(f"Generated {len(recommendations)} recommendations for anonymous session")
                    # Track recommendations generated for anonymous user
                    track_recommendations_generated(
                        session_key=request.session.session_key,
                        recommendation_count=len(recommendations),
                        is_authenticated=False,
                    )
            except Exception as e:
                logger.error(f"Error generating recommendations for anonymous user: {e}", exc_info=True)

    if not request.user.is_authenticated and dna_data is None:
        messages.info(request, "First, upload your library file to generate your Bibliotype!")
        return redirect("core:home")

    # Track DNA displayed
    has_recommendations = len(recommendations) > 0
    if request.user.is_authenticated:
        track_dna_displayed(request, is_authenticated=True, has_recommendations=has_recommendations)
    else:
        # Track anonymous DNA displayed
        track_anonymous_dna_displayed(
            session_key=request.session.session_key,
            has_recommendations=has_recommendations,
        )
        track_dna_displayed(request, is_authenticated=False, has_recommendations=has_recommendations)

    # Calculate title with proper possessive form
    title = None
    if request.user.is_authenticated:
        display_name = request.user.first_name if request.user.first_name else request.user.username
        display_name_lower = display_name.lower()
        if display_name_lower.endswith("s"):
            title = f"{display_name_lower}' bibliotype"
        else:
            title = f"{display_name_lower}'s bibliotype"

    # Nudge users with old DNA data to re-upload for currently-reading features
    if request.user.is_authenticated and user_profile and user_profile.dna_data:
        raw_dna = user_profile.dna_data
        if "currently_reading_count" not in raw_dna:
            messages.info(
                request,
                'Re-upload your library to see your currently-reading books and get better recommendations! '
                '<a href="/" class="hover:bg-brand-yellow font-bold underline">Upload now</a>',
            )

    dna_data = _enrich_dna_for_display(dna_data)

    # Compute per-tile enrichment progress (applies to all CSV sources —
    # genres are always async-enriched via Open Library / Google Books).
    # Anonymous users have no async enrichment, so we skip this entirely for them.
    enrichment = None
    if dna_data and request.user.is_authenticated:
        progress = _compute_enrichment_progress(request.user, user_profile, dna_data)
        if progress and progress["pending"]:
            enrichment = progress

    recommendations_meta = {}
    if request.user.is_authenticated and user_profile:
        recommendations_meta = user_profile.recommendations_meta or {}

    # Flag to tell the template to poll for recommendations
    recommendations_pending = (
        request.user.is_authenticated
        and user_profile
        and user_profile.dna_data
        and not recommendations
    )

    context = {
        "dna": dna_data,
        "user_profile": user_profile,
        "is_processing": False,
        "recommendations": recommendations,
        "recommendations_meta": recommendations_meta,
        "recommendations_pending": recommendations_pending,
        "title": title,
        "enrichment": enrichment,
    }

    return render(request, "core/dashboard.html", context)


@login_required
@require_POST
def update_privacy_view(request):
    is_public = request.POST.get("is_public") == "true"
    profile = request.user.userprofile
    profile.is_public = is_public
    profile.save()

    if is_public:
        # Track profile made public
        track_profile_made_public(request.user.id)

        public_url = request.build_absolute_uri(
            reverse("core:public_profile", kwargs={"username": request.user.username})
        )
        message_text = render_to_string(
            "core/partials/messages_with_link.html",
            {"public_url": public_url, "username": request.user.username},
        )
        messages.success(request, message_text)
    else:
        messages.success(request, "Your profile is now private.")

    if "dna_data" in request.session:
        request.session.pop("dna_data", None)

    return redirect("core:display_dna")


@login_required
@require_POST
def update_display_name_view(request):
    form = UpdateDisplayNameForm(request.POST, user=request.user, instance=request.user)
    if form.is_valid():
        form.save()
        # Track settings update
        track_settings_updated(request.user.id, setting_type="display_name")
        messages.success(request, "Your display name has been updated!")
    else:
        for error in form.errors.values():
            messages.error(request, error)

    return redirect("core:display_dna")


@ratelimit(key="user", rate="10/m", method="POST", block=True)
def _update_username_api_throttled(request):
    try:
        data = json.loads(request.body)
        new_username = data.get("username")

        if not new_username:
            return JsonResponse({"status": "error", "message": "Display name cannot be empty."}, status=400)

        form = UpdateDisplayNameForm({"username": new_username}, user=request.user, instance=request.user)

        if form.is_valid():
            form.save()
            messages.success(request, "Display name updated successfully!")

            return JsonResponse({"status": "success", "new_username": new_username})
        else:
            error_message = form.errors.get("username")[0]
            return JsonResponse({"status": "error", "message": error_message}, status=400)

    except Exception as e:
        logger.error(f"Error in update_username_api: {e}", exc_info=True)

        return JsonResponse({"status": "error", "message": "An unexpected server error occurred."}, status=500)


@login_required
@require_POST
def update_username_api(request):
    try:
        return _update_username_api_throttled(request)
    except Ratelimited:
        return JsonResponse({"error": "Too many attempts, try again later."}, status=429)


@login_required
@require_POST
def update_recommendation_visibility(request):
    """Toggle visibility in recommendations"""
    is_visible = request.POST.get("visible_in_recommendations") == "true"
    profile = request.user.userprofile
    was_visible = profile.visible_in_recommendations
    profile.visible_in_recommendations = is_visible
    profile.save()

    # Invalidate caches keyed on this user's recommendation/similarity output so
    # the toggle takes effect immediately (US-024c).
    safe_cache_delete(f"user_recommendations_{request.user.id}")
    safe_cache_delete(f"similar_users_{request.user.id}")

    # When opting OUT, also flush the candidate-pool cache so the user is
    # dropped from other readers' similarity searches on the next refresh.
    if was_visible and not is_visible:
        safe_cache_delete("public_users_for_recs_sample")
        logger.info(
            "user opted out of recs; cleared candidate sample cache",
            extra={"user_id": request.user.id},
        )

    # Track settings update
    track_settings_updated(request.user.id, setting_type="recommendation_visibility")

    if is_visible:
        messages.success(request, "You are now visible as a recommendation source to similar readers!")
    else:
        messages.success(request, "You've opted out of being shown as a recommendation source.")

    return redirect("core:display_dna")


def public_profile_view(request, username):
    """Displays a user's public DNA."""
    try:
        profile_user = User.objects.get(username=username)
        profile = profile_user.userprofile
        # Refresh from database to ensure we have the latest is_public value
        profile.refresh_from_db()

        # Check privacy: only show if public OR if user is viewing their own profile
        if not profile.is_public and (not request.user.is_authenticated or request.user != profile_user):
            return render(request, "core/profile_private.html")

        display_name = profile_user.first_name if profile_user.first_name else profile_user.username
        display_name_lower = display_name.lower()

        if display_name_lower.endswith("s"):
            title = f"{display_name_lower}' bibliotype"
            heading_name = f"{display_name}'"
        else:
            title = f"{display_name_lower}'s bibliotype"
            heading_name = f"{display_name}'s"

        recommendations = []
        if profile.dna_data:
            try:
                if profile.recommendations_data:
                    stored_recs = profile.recommendations_data

                    for rec in stored_recs:
                        if "book" not in rec:
                            rec["book"] = _expand_book_dict(rec, BADGE_COLOR_MAP)

                    recommendations = stored_recs
                    logger.info(
                        f"Loaded {len(recommendations)} stored recommendations for public profile {profile_user.username}"
                    )
                else:
                    # No stored recommendations - they may be generating
                    logger.info(f"No stored recommendations for public profile {profile_user.username}")
                    recommendations = []
            except Exception as e:
                # Log the error but don't break the page if recommendations fail
                logger.error(f"Failed to load recommendations for user {profile_user.username}: {e}", exc_info=True)
                # Track recommendation error in PostHog
                track_recommendation_error(
                    profile_user_id=profile_user.id,
                    error_type=type(e).__name__,
                    error_message=str(e),
                    context="public_profile_view",
                )
                recommendations = []  # Continue with empty recommendations

        # Track public profile viewed
        track_public_profile_viewed(
            profile_username=profile_user.username,
            profile_user_id=profile_user.id,
            viewer_is_authenticated=request.user.is_authenticated,
            viewer_is_owner=request.user.is_authenticated and request.user == profile_user,
            viewer_user_id=request.user.id if request.user.is_authenticated else None,
            viewer_session_id=request.session.session_key if not request.user.is_authenticated else None,
        )

        enriched_dna = _enrich_dna_for_display(profile.dna_data)

        context = {
            "dna": enriched_dna,
            "profile_user": profile_user,
            "title": title,
            "heading_name": heading_name,
            "recommendations": recommendations,
            "recommendations_meta": profile.recommendations_meta or {},
        }
        return render(request, "core/public_profile.html", context)
    except User.DoesNotExist:
        # Show a nicer 404 page instead of the default Django 404
        return render(request, "core/404.html", {"username": username}, status=404)


from ._helpers import (  # noqa: F401 — re-exported for stable import paths
    BADGE_COLOR_MAP,
    ENRICHMENT_STATS_CACHE_TTL,
    _compute_enrichment_progress,
    _compute_enrichment_stats,
    _enrich_dna_for_display,
    _expand_book_dict,
    _recalculate_enrichment_stats,
)

from .seo import robots_txt_view, sitemap_xml_view  # noqa: F401 — re-exported for stable import paths
from .auth import (  # noqa: F401 — re-exported for stable import paths
    CustomPasswordResetView,
    _login_view_throttled,
    handler404,
    login_view,
    logout_view,
    signup_view,
)
from .upload import (  # noqa: F401 — re-exported for stable import paths
    MAX_UPLOAD_COLUMNS,
    MAX_UPLOAD_ROWS,
    MAX_UPLOAD_SIZE_BYTES,
    check_dna_status_view,
    check_recommendations_status_view,
    enrichment_status_view,
    get_task_result_view,
    task_status_view,
    upload_view,
)
