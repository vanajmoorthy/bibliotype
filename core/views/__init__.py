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
from .pages import about_view, home_view, privacy_view, terms_view  # noqa: F401 — re-exported for stable import paths
from .profile import (  # noqa: F401 — re-exported for stable import paths
    _update_username_api_throttled,
    update_display_name_view,
    update_privacy_view,
    update_recommendation_visibility,
    update_username_api,
)
