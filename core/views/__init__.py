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

MAX_UPLOAD_SIZE_BYTES = 10 * 1024 * 1024  # 10MB CSV upload limit

# Upload validation caps (US-018). 50k rows comfortably covers the largest
# Goodreads exports observed in the wild; 100 cols is well above any real
# export schema and rejects pathological inputs before pandas does deep work.
MAX_UPLOAD_ROWS = 50000
MAX_UPLOAD_COLUMNS = 100


def robots_txt_view(request):
    """Serve robots.txt file."""
    static_dirs = getattr(settings, "STATICFILES_DIRS", [])
    if static_dirs:
        robots_path = os.path.join(static_dirs[0], "robots.txt")
        try:
            with open(robots_path, "r") as f:
                content = f.read()
            # Replace sitemap URL with actual domain
            sitemap_url = f"{request.scheme}://{request.get_host()}/sitemap.xml"
            content = content.replace("https://bibliotype.com/sitemap.xml", sitemap_url)
            return HttpResponse(content, content_type="text/plain")
        except (FileNotFoundError, IndexError):
            pass
    # Fallback if file doesn't exist
    sitemap_url = f"{request.scheme}://{request.get_host()}/sitemap.xml"
    return HttpResponse(f"User-agent: *\nAllow: /\n\nSitemap: {sitemap_url}", content_type="text/plain")


def sitemap_xml_view(request):
    """Generate and serve sitemap.xml."""
    from django.utils import timezone

    from ..models import UserProfile

    base_url = f"{request.scheme}://{request.get_host()}"
    today = timezone.now().strftime("%Y-%m-%d")

    public_profiles = UserProfile.objects.filter(is_public=True, dna_data__isnull=False).select_related("user")[
        :1000
    ]  # Limit to 1000 most recent public profiles

    urls = [
        {
            "loc": f"{base_url}/",
            "lastmod": today,
            "changefreq": "daily",
            "priority": "1.0",
        },
        {
            "loc": f"{base_url}/login/",
            "lastmod": today,
            "changefreq": "monthly",
            "priority": "0.8",
        },
        {
            "loc": f"{base_url}/signup/",
            "lastmod": today,
            "changefreq": "monthly",
            "priority": "0.8",
        },
        {
            "loc": f"{base_url}/about/",
            "lastmod": today,
            "changefreq": "monthly",
            "priority": "0.6",
        },
        {
            "loc": f"{base_url}/privacy/",
            "lastmod": today,
            "changefreq": "monthly",
            "priority": "0.4",
        },
        {
            "loc": f"{base_url}/terms/",
            "lastmod": today,
            "changefreq": "monthly",
            "priority": "0.4",
        },
    ]

    # Add public profile URLs
    for profile in public_profiles:
        try:
            profile_url = reverse("core:public_profile", kwargs={"username": profile.user.username})
            lastmod = today
            if profile.recommendations_generated_at:
                lastmod = profile.recommendations_generated_at.strftime("%Y-%m-%d")
            urls.append(
                {
                    "loc": f"{base_url}{profile_url}",
                    "lastmod": lastmod,
                    "changefreq": "weekly",
                    "priority": "0.7",
                }
            )
        except Exception:
            logger.warning(f"Error generating sitemap entry for user {profile.user.username}", exc_info=True)
            continue

    sitemap_xml = '<?xml version="1.0" encoding="UTF-8"?>\n'
    sitemap_xml += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'

    for url_data in urls:
        sitemap_xml += "  <url>\n"
        sitemap_xml += f'    <loc>{url_data["loc"]}</loc>\n'
        sitemap_xml += f'    <lastmod>{url_data["lastmod"]}</lastmod>\n'
        sitemap_xml += f'    <changefreq>{url_data["changefreq"]}</changefreq>\n'
        sitemap_xml += f'    <priority>{url_data["priority"]}</priority>\n'
        sitemap_xml += "  </url>\n"

    sitemap_xml += "</urlset>"

    return HttpResponse(sitemap_xml, content_type="application/xml")


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


@require_POST
def upload_view(request):
    csv_file = request.FILES.get("csv_file")

    if not csv_file or not csv_file.name.endswith(".csv"):
        messages.error(request, "Please upload a valid .csv file.")
        return redirect("core:home")

    try:
        if csv_file.size > MAX_UPLOAD_SIZE_BYTES:
            messages.error(request, "File is too large. Please upload an export smaller than 10MB.")
            return redirect("core:home")

        # Track file upload started
        track_file_upload_started(request, csv_file.size)

        # utf-8-sig transparently strips BOM if present (some exports include it)
        csv_content = csv_file.read().decode("utf-8-sig")

        # US-018: pre-flight validation. Read only the first MAX_UPLOAD_ROWS rows
        # so a pathological CSV can't exhaust memory before we even start the
        # task. Then verify column count and schema look like Goodreads or
        # StoryGraph before passing csv_content downstream.
        try:
            df_head = pd.read_csv(StringIO(csv_content), nrows=MAX_UPLOAD_ROWS)
        except (pd.errors.ParserError, pd.errors.EmptyDataError, ValueError):
            messages.error(
                request,
                "CSV could not be parsed. Please upload a valid Goodreads or StoryGraph export.",
            )
            return redirect("core:home")

        if len(df_head.columns) > MAX_UPLOAD_COLUMNS:
            messages.error(
                request,
                f"CSV has too many columns (limit {MAX_UPLOAD_COLUMNS}). "
                "Please upload a Goodreads or StoryGraph export.",
            )
            return redirect("core:home")

        head_columns = set(df_head.columns)
        if not ({"Title", "Author"}.issubset(head_columns) or {"Title", "Authors"}.issubset(head_columns)):
            messages.error(
                request,
                "CSV does not look like a Goodreads or StoryGraph export. "
                "Expected columns: Title and Author/Authors.",
            )
            return redirect("core:home")

        # If the input exceeded the row cap, re-serialize the truncated head so
        # the downstream task doesn't reload the full file. For typical-sized
        # uploads (well under the cap) we pass csv_content through unchanged.
        if len(df_head) >= MAX_UPLOAD_ROWS:
            csv_content = df_head.to_csv(index=False)

        if request.user.is_authenticated:
            # Clear old session data so the view will use the updated profile data
            request.session.pop("dna_data", None)

            # If a previous upload is still running, revoke it before dispatching
            # the new one. Without this, both tasks contend on the same Book/Author
            # row locks in Postgres and the new task's progress bar stalls until
            # the old task releases its locks ("stuck at 50%").
            prior_task_id = request.user.userprofile.pending_dna_task_id
            if prior_task_id:
                try:
                    prior_result = AsyncResult(prior_task_id)
                    if not prior_result.ready():
                        prior_result.revoke(terminate=True, signal="SIGTERM")
                        logger.info(
                            f"Revoked pending DNA task {prior_task_id} for user {request.user.id} "
                            f"because a new upload superseded it"
                        )
                except Exception as e:
                    # Best-effort: a failed revoke shouldn't block the new upload.
                    # The nonce mechanism still protects enrichment tasks; the worst
                    # case is brief lock contention until the prior task finishes.
                    logger.warning(
                        f"Failed to revoke prior DNA task {prior_task_id} for user {request.user.id}: {e}"
                    )

            # Store upload nonce BEFORE dispatching the task so enrichment tasks
            # from previous uploads can detect they've been superseded. Using uuid
            # rather than task ID since the task hasn't been created yet.
            import uuid

            upload_nonce = str(uuid.uuid4())
            safe_cache_set(f"upload_nonce_{request.user.id}", upload_nonce, timeout=DNA_CACHE_TTL)

            result = generate_reading_dna_task.delay(csv_content, request.user.id)

            # Save the pending task ID to track regeneration progress.
            # Use .update() instead of fetching + saving the related object — the
            # eager task path runs the DNA save inline, and a cached stale
            # UserProfile instance here would clobber the DNA the task just wrote.
            from ..models import UserProfile

            UserProfile.objects.filter(user=request.user).update(pending_dna_task_id=result.id)

            messages.success(
                request,
                "Success! We're updating your Reading DNA. Your dashboard will update automatically when it's ready!",
            )

            processing_url = reverse("core:display_dna") + "?processing=true"

            return redirect(processing_url)
        else:
            # Ensure the session has a session_key so we can bind ownership of
            # the task to this caller. Without this, an attacker who guesses or
            # leaks the task_id could pull another visitor's DNA into their
            # own session.
            if request.session.session_key is None:
                request.session.save()
            session_key = request.session.session_key

            result = generate_reading_dna_task.delay(csv_content, None, session_key)
            task_id = result.id

            request.session["anonymous_task_id"] = task_id
            safe_cache_set(f"task_owner_{task_id}", session_key, DNA_CACHE_TTL)
            request.session.save()

            return redirect("core:task_status", task_id=task_id)

    except Exception as e:
        logger.error(f"Unexpected error in upload_view: {e}", exc_info=True)
        messages.error(request, "An unexpected error occurred. Please try again.")
        return redirect("core:home")


def signup_view(request):
    task_id = request.GET.get("task_id")

    if request.method == "POST":
        form = CustomUserCreationForm(request.POST)
        task_id_to_claim = request.POST.get("task_id_to_claim")

        # Verify Turnstile CAPTCHA
        from ..turnstile import verify_turnstile_token

        turnstile_token = request.POST.get("cf-turnstile-response", "")
        if not verify_turnstile_token(turnstile_token, remote_ip=get_real_client_ip(request)):
            messages.error(request, "CAPTCHA verification failed. Please try again.")
            return render(request, "core/signup.html", {"form": form, "task_id_to_claim": task_id})

        if form.is_valid():
            # US-017: if an account already exists for this email, do NOT reveal
            # that fact to the requester. Trigger a password-reset email to the
            # legitimate owner (so a user who forgot they had an account gets a
            # path back in), short-circuit to the "check your inbox" page, and
            # skip user creation. `clean_email` deliberately does not error on
            # duplicates so we have to gate it here at the view layer.
            submitted_email = form.cleaned_data.get("email")
            if submitted_email and User.objects.filter(email__iexact=submitted_email).exists():
                reset_form = PasswordResetForm({"email": submitted_email})
                if reset_form.is_valid():
                    reset_form.save(
                        request=request,
                        use_https=request.is_secure(),
                        token_generator=default_token_generator,
                        from_email=None,
                        email_template_name="core/password_reset_email.html",
                        subject_template_name="core/password_reset_subject.txt",
                    )
                logger.info(
                    "signup short-circuited: duplicate email; password-reset email dispatched",
                    extra={"email_hash": hash(submitted_email.lower())},
                )
                return redirect("password_reset_done")

            # US-003 (defense in depth): validate the task_id being claimed
            # actually belongs to this session BEFORE creating the account or
            # calling login(). Two checks layered:
            #   1) session["anonymous_task_id"] == POSTed task_id — internal consistency.
            #   2) cache.get("task_owner_<task_id>") == session.session_key — proves
            #      the task was originally uploaded from this session, not just
            #      that the session knows the task_id (which could be POST-tampered).
            # Django rotates session_key on login, so we capture the pre-login key
            # now and pass it to the claim task for the final task-level check.
            if task_id_to_claim:
                expected_task_id = request.session.get("anonymous_task_id")
                cached_owner = safe_cache_get(f"task_owner_{task_id_to_claim}")
                session_owns_task = expected_task_id == task_id_to_claim
                cache_owns_task = cached_owner is not None and cached_owner == request.session.session_key
                if not (session_owns_task and cache_owns_task):
                    logger.warning(
                        "signup claim rejected: task_id ownership not verified",
                        extra={
                            "task_id_to_claim": task_id_to_claim,
                            "session_consistent": session_owns_task,
                            "cache_consistent": cache_owns_task,
                        },
                    )
                    messages.error(
                        request,
                        "We couldn't verify that this Bibliotype belongs to your current session. "
                        "Please upload your library again from this browser.",
                    )
                    return render(request, "core/signup.html", {"form": form, "task_id_to_claim": task_id})

            pre_login_session_key = request.session.session_key

            user = form.save()
            login(request, user)

            had_dna_in_session = "dna_data" in request.session

            if task_id_to_claim:
                user.userprofile.pending_dna_task_id = task_id_to_claim
                user.userprofile.save()
                claim_anonymous_dna_task.delay(user.id, task_id_to_claim, pre_login_session_key)

                # Track signup and DNA claim
                track_user_signed_up(
                    user_id=user.id,
                    signup_source="with_task_claim",
                    task_id_to_claim=task_id_to_claim,
                    had_dna_in_session=had_dna_in_session,
                )
                track_anonymous_dna_claimed(
                    user_id=user.id,
                    task_id=task_id_to_claim,
                    session_key=None,  # Session key not needed, task_id is sufficient identifier
                )

                messages.success(request, "Account created! We'll save your Bibliotype as soon as it's ready.")
                processing_url = reverse("core:display_dna") + "?processing=true"
                return redirect(processing_url)

            if "dna_data" in request.session:
                dna_to_save = request.session.pop("dna_data")
                _save_dna_to_profile(user.userprofile, dna_to_save)

                # Track signup with session DNA (could be after anonymous DNA)
                track_user_signed_up(
                    user_id=user.id,
                    signup_source="with_session_dna",
                    had_dna_in_session=True,
                )

                messages.success(request, "Account created and your Bibliotype has been saved!")
                return redirect("core:display_dna")

            # Track signup before DNA generation
            track_user_signed_up(
                user_id=user.id,
                signup_source="before_dna",
                had_dna_in_session=False,
            )

            messages.success(request, "Account created! Now, let's generate your Bibliotype.")
            return redirect("core:home")

    else:
        form = CustomUserCreationForm()

    return render(request, "core/signup.html", {"form": form, "task_id_to_claim": task_id})


@ratelimit(key=client_ip_key, rate="5/m", method="POST", block=True)
def _login_view_throttled(request):
    if request.method == "POST":
        email = request.POST.get("username")
        password = request.POST.get("password")

        user = None
        if email and password:
            try:
                user_obj = User.objects.get(email__iexact=email)
                user = authenticate(request, username=user_obj.username, password=password)
            except User.DoesNotExist:
                pass

        if user is not None:
            login(request, user)

            had_dna_in_session = "dna_data" in request.session

            if "dna_data" in request.session:
                dna_to_save = request.session.pop("dna_data")
                if not user.userprofile.dna_data:
                    _save_dna_to_profile(user.userprofile, dna_to_save)
                messages.success(request, "Logged in successfully!")

                # Track login
                track_user_logged_in(user.id, had_dna_in_session=True)
                return redirect("core:display_dna")

            # Track login
            track_user_logged_in(user.id, had_dna_in_session=False)
            return redirect("core:home")

        messages.error(request, "Invalid email or password. Please try again.")

    form = AuthenticationForm()
    form.fields["username"].label = "Email"

    return render(request, "core/login.html", {"form": form})


def login_view(request):
    try:
        return _login_view_throttled(request)
    except Ratelimited:
        form = AuthenticationForm()
        form.fields["username"].label = "Email"
        form._errors = ErrorDict()
        form._errors[NON_FIELD_ERRORS] = ErrorList(["Too many attempts. Please try again in a minute."])
        return render(request, "core/login.html", {"form": form}, status=429)


@login_required
def logout_view(request):
    if "dna_data" in request.session:
        request.session.pop("dna_data", None)
    logout(request)
    messages.info(request, "You have been logged out.")
    return redirect("core:home")


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


def check_dna_status_view(request):
    # For anonymous users, they don't have pending task tracking
    if not request.user.is_authenticated:
        return JsonResponse({"status": "PENDING"})

    profile = request.user.userprofile
    profile.refresh_from_db()  # Ensure we get the latest data from the database

    # If there's a pending task ID, DNA is still being generated
    if profile.pending_dna_task_id:
        try:
            result = AsyncResult(profile.pending_dna_task_id)

            # Check for failure first
            if result.state == "FAILURE":
                profile.pending_dna_task_id = None
                profile.save(update_fields=["pending_dna_task_id"])
                return JsonResponse({"status": "FAILURE", "error": "An error occurred while processing your file."})

            info = result.info or {}
            current = info.get("current")
            total = info.get("total")
            stage = info.get("stage", "")
            progress = None
            if current is not None or total is not None or stage:
                percent = None
                if isinstance(current, int) and isinstance(total, int) and total > 0:
                    percent = round((current * 100) / total)
                progress = {"current": current, "total": total, "percent": percent, "stage": stage}
            return JsonResponse({"status": "PENDING", "progress": progress})
        except Exception:
            return JsonResponse({"status": "PENDING"})

    # Otherwise, check if DNA data exists
    if profile.dna_data:
        return JsonResponse({"status": "SUCCESS"})
    else:
        return JsonResponse({"status": "PENDING"})


@login_required
def check_recommendations_status_view(request):
    """AJAX endpoint to check if recommendations have been generated."""
    profile = request.user.userprofile
    profile.refresh_from_db()

    if profile.recommendations_data:
        return JsonResponse({"status": "ready"})
    return JsonResponse({"status": "pending"})


@login_required
def enrichment_status_view(request):
    """AJAX endpoint for polling enrichment progress. Returns updated stats for live dashboard updates."""
    profile = request.user.userprofile
    dna_data = profile.dna_data
    if not dna_data:
        return JsonResponse({"pending": False})

    progress = _compute_enrichment_progress(request.user, profile, dna_data)
    if progress is None or not progress["pending"]:
        return JsonResponse({"pending": False})

    user_stats = dna_data.get("user_stats", {})
    return JsonResponse({
        **progress,
        "updated_stats": {
            "total_pages_read": user_stats.get("total_pages_read", 0),
            "avg_book_length": user_stats.get("avg_book_length", 0),
            "top_genres": dna_data.get("top_genres", []),
            "mainstream_score_percent": dna_data.get("mainstream_score_percent", 0),
            "fiction_nonfiction_split": dna_data.get("fiction_nonfiction_split"),
        },
    })


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


def task_status_view(request, task_id):
    return render(request, "core/task_status.html", {"task_id": task_id})


def get_task_result_view(request, task_id):
    from ..models import AnonymousUserSession

    # US-002 (hardened post-review): refuse task_ids that aren't bound to the
    # caller's session. Both cache-miss AND mismatch fail closed — the previous
    # "warn-and-allow" legacy path still wrote DNA into the requester's session,
    # which combined with the signup view's `if "dna_data" in request.session`
    # branch to re-open the original hijack.
    owner = safe_cache_get(f"task_owner_{task_id}")
    caller_key = request.session.session_key
    is_owner = owner is not None and caller_key is not None and owner == caller_key
    if not is_owner:
        import hashlib

        caller_hash = hashlib.sha256(caller_key.encode()).hexdigest()[:12] if caller_key else "none"
        if owner is None:
            # TTL expired or task_id never bound (pre-US-001 in-flight, or invalid).
            logger.info(
                "task_owner cache miss",
                extra={"task_id": task_id, "caller_hash": caller_hash},
            )
        else:
            owner_hash = hashlib.sha256(owner.encode()).hexdigest()[:12]
            logger.warning(
                "task_owner mismatch — cross-session access attempted",
                extra={"task_id": task_id, "owner_hash": owner_hash, "caller_hash": caller_hash},
            )
        return JsonResponse({"status": "FORBIDDEN"}, status=403)

    cached_result = safe_cache_get(f"dna_result_{task_id}")
    if cached_result is not None:
        request.session["dna_data"] = cached_result
        # Also store book IDs and ratings from AnonymousUserSession if it exists
        if request.session.session_key:
            try:
                anon_session = AnonymousUserSession.objects.get(session_key=request.session.session_key)
                request.session["book_ids"] = anon_session.books_data or []
                request.session["top_book_ids"] = anon_session.top_books_data or []
                # Use getattr for backwards compatibility if migration hasn't run yet
                request.session["book_ratings"] = getattr(anon_session, "book_ratings", None) or {}
            except AnonymousUserSession.DoesNotExist:
                pass
        request.session.save()

        return JsonResponse(
            {
                "status": "SUCCESS",
                "redirect_url": reverse("core:display_dna"),
            }
        )

    result = AsyncResult(task_id)

    if result.state == "SUCCESS":
        dna_data = result.get()

        request.session["dna_data"] = dna_data
        # Also store book IDs and ratings from AnonymousUserSession if it exists
        if request.session.session_key:
            try:
                anon_session = AnonymousUserSession.objects.get(session_key=request.session.session_key)
                request.session["book_ids"] = anon_session.books_data or []
                request.session["top_book_ids"] = anon_session.top_books_data or []
                # Use getattr for backwards compatibility if migration hasn't run yet
                request.session["book_ratings"] = getattr(anon_session, "book_ratings", None) or {}
            except AnonymousUserSession.DoesNotExist:
                pass
        request.session.save()

        return JsonResponse(
            {
                "status": "SUCCESS",
                "redirect_url": reverse("core:display_dna"),
            }
        )
    elif result.state == "PROGRESS":
        info = result.info or {}
        current = info.get("current")
        total = info.get("total")
        stage = info.get("stage", "")
        percent = None
        if isinstance(current, int) and isinstance(total, int) and total > 0:
            percent = round((current * 100) / total)
        progress = {"current": current, "total": total, "percent": percent, "stage": stage}
        return JsonResponse({"status": "PENDING", "progress": progress})
    elif result.state == "FAILURE":
        return JsonResponse({"status": "FAILURE", "error": "An error occurred during processing."})
    else:
        return JsonResponse({"status": "PENDING"})


def handler404(request, exception=None):
    """Custom 404 handler that renders our fun 404 page."""
    path = request.path.strip("/")
    username = None
    if path.startswith("u/") and len(path.split("/")) >= 2:
        # Extract username from path like "u/username" or "u/username/"
        parts = path.split("/")
        if parts[0] == "u" and len(parts) > 1:
            username = parts[1]

    return render(request, "core/404.html", {"username": username}, status=404)


class CustomPasswordResetView(PasswordResetView):
    template_name = "core/password_reset_form.html"
    email_template_name = "core/password_reset_email.html"
    subject_template_name = "core/password_reset_subject.txt"
    success_url = reverse_lazy("password_reset_done")

    def form_valid(self, form):
        from ..turnstile import verify_turnstile_token

        turnstile_token = self.request.POST.get("cf-turnstile-response", "")
        if not verify_turnstile_token(turnstile_token, remote_ip=get_real_client_ip(self.request)):
            form.add_error(None, "CAPTCHA verification failed. Please try again.")
            return self.form_invalid(form)

        return super().form_valid(form)


from ._helpers import (  # noqa: F401 — re-exported for stable import paths
    BADGE_COLOR_MAP,
    ENRICHMENT_STATS_CACHE_TTL,
    _compute_enrichment_progress,
    _compute_enrichment_stats,
    _enrich_dna_for_display,
    _expand_book_dict,
    _recalculate_enrichment_stats,
)
