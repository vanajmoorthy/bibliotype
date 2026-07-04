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
from .dashboard import display_dna_view, public_profile_view  # noqa: F401 — re-exported for stable import paths
