import json
import logging


import posthog

logger = logging.getLogger(__name__)
from django.core.cache import cache
from celery.result import AsyncResult
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import (
    AuthenticationForm,
)
from django.contrib.auth.models import User
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.conf import settings
import os

from .forms import CustomUserCreationForm, UpdateDisplayNameForm
from .tasks import _save_dna_to_profile, claim_anonymous_dna_task, generate_reading_dna_task
from .analytics.events import (
    track_file_upload_started,
    track_dna_displayed,
    track_anonymous_dna_displayed,
    track_recommendations_generated,
    track_user_signed_up,
    track_anonymous_dna_claimed,
    track_user_logged_in,
    track_profile_made_public,
    track_public_profile_viewed,
    track_settings_updated,
    track_recommendation_error,
)


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
    from django.urls import reverse

    base_url = f"{request.scheme}://{request.get_host()}"

    # Get public profiles (limit to recent/public ones for performance)
    from django.contrib.auth.models import User
    from .models import UserProfile

    public_profiles = UserProfile.objects.filter(is_public=True, dna_data__isnull=False).select_related("user")[
        :1000
    ]  # Limit to 1000 most recent public profiles

    urls = [
        {
            "loc": f"{base_url}/",
            "changefreq": "daily",
            "priority": "1.0",
        },
        {
            "loc": f"{base_url}/login/",
            "changefreq": "monthly",
            "priority": "0.8",
        },
        {
            "loc": f"{base_url}/signup/",
            "changefreq": "monthly",
            "priority": "0.8",
        },
    ]

    # Add public profile URLs
    for profile in public_profiles:
        try:
            profile_url = reverse("core:public_profile", kwargs={"username": profile.user.username})
            urls.append(
                {
                    "loc": f"{base_url}{profile_url}",
                    "changefreq": "weekly",
                    "priority": "0.7",
                }
            )
        except Exception:
            continue

    sitemap_xml = '<?xml version="1.0" encoding="UTF-8"?>\n'
    sitemap_xml += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'

    for url_data in urls:
        sitemap_xml += "  <url>\n"
        sitemap_xml += f'    <loc>{url_data["loc"]}</loc>\n'
        sitemap_xml += f'    <changefreq>{url_data["changefreq"]}</changefreq>\n'
        sitemap_xml += f'    <priority>{url_data["priority"]}</priority>\n'
        sitemap_xml += "  </url>\n"

    sitemap_xml += "</urlset>"

    return HttpResponse(sitemap_xml, content_type="application/xml")


def home_view(request):
    """Displays the main upload page."""
    return render(request, "core/home.html")


def display_dna_view(request):
    is_processing = request.GET.get("processing") == "true"

    dna_data = request.session.get("dna_data")
    user_profile = None
    recommendations = []
    rec_error = None

    def get_match_badge_class(confidence_pct):
        if confidence_pct >= 95:
            return "bg-match-100"
        if confidence_pct >= 85:
            return "bg-match-90"
        if confidence_pct >= 75:
            return "bg-match-80"
        if confidence_pct >= 60:
            return "bg-match-70"
        return "bg-match-low"

    # --- NEW: Maps quality label to a tuple of (border_class, text_class) ---
    quality_badge_class_map = {
        "Extremely Similar - Literary Twin": ("border-quality-twin", "text-quality-twin", "LITERARY TWIN"),
        "Very Similar - Kindred Reader": ("border-quality-kindred", "text-quality-kindred", "KINDRED READER"),
        "Moderately Similar - Shared Tastes": ("border-quality-tastes", "text-quality-tastes", "SHARED TASTES"),
        "Somewhat Similar - Some Overlap": ("border-quality-overlap", "text-quality-overlap", "SOME OVERLAP"),
    }

    badge_color_map = {
        "Literary twin": "bg-badge-5",
        "Kindred reader": "bg-badge-4",
        "Some shared tastes": "bg-badge-3",
        "Some overlap": "bg-badge-2",
        # We use a neutral background for weaker matches for better visual distinction
        "Different preferences": "bg-gray-200",
        "Opposite tastes": "bg-gray-200",
    }

    if request.user.is_authenticated:
        user_profile = request.user.userprofile
        user_profile.refresh_from_db()

        # Show processing screen for authenticated users when flagged, regardless of existing data
        if is_processing:
            return render(request, "core/dna_display.html", {"is_processing": True})

        if dna_data is None and user_profile.dna_data:
            dna_data = user_profile.dna_data

        # Get recommendations for registered user
        if user_profile.dna_data:
            try:
                from .services.recommendation_service import get_recommendations_for_user

                recommendations = get_recommendations_for_user(request.user, limit=6)

                for rec in recommendations:
                    # 1. Add a ready-to-use percentage for the match score
                    rec["confidence_pct"] = int(rec.get("confidence", 0) * 100)

                    # 2. Find the best "similar_user" to link to
                    rec["primary_source_user"] = None
                    best_similarity = 0

                    for source in rec.get("sources", []):
                        # We only want to link to actual, registered users
                        if source.get("type") == "similar_user":
                            # Find the user with the highest similarity score for this specific book
                            if source.get("similarity_score", 0) > best_similarity:
                                best_similarity = source.get("similarity_score", 0)
                                rec["primary_source_user"] = source

                    if rec["primary_source_user"]:
                        match_quality = rec["primary_source_user"].get("match_quality", "")
                        # Assign a badge class based on the map, with a default
                        rec["primary_source_user"]["badge_class"] = badge_color_map.get(
                            match_quality, "bg-brand-purple"
                        )

                logger.info(f"Generated {len(recommendations)} recommendations for user {request.user.id}")
                # Track recommendations generated
                track_recommendations_generated(
                    user_id=request.user.id,
                    recommendation_count=len(recommendations),
                    is_authenticated=True,
                )
            except Exception as e:
                logger.error(f"Error generating recommendations for user {request.user.id}: {e}", exc_info=True)
                rec_error = "Unable to load recommendations at this time."
    else:
        # Anonymous user recommendations
        if dna_data and request.session.session_key:
            try:
                from .services.recommendation_service import get_recommendations_for_anonymous
                from .models import AnonymousUserSession, Author
                from django.utils import timezone
                from datetime import timedelta
                from collections import Counter

                # Check if AnonymousUserSession exists, if not try to recreate it
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
                        book_ratings = request.session.get("book_ratings", {})  # Get ratings if stored

                        # Create a minimal AnonymousUserSession from dna_data
                        anon_session = AnonymousUserSession.objects.create(
                            session_key=request.session.session_key,
                            dna_data=dna_data,
                            books_data=books_data,
                            top_books_data=top_books_data,
                            genre_distribution=genre_dist,
                            author_distribution=author_dist,
                            book_ratings=book_ratings,  # Store ratings if available
                            expires_at=timezone.now() + timedelta(days=7),
                        )

                        # Now try to get recommendations
                        recommendations = get_recommendations_for_anonymous(request.session.session_key, limit=6)
                        logger.info(
                            f"Recreated AnonymousUserSession and generated {len(recommendations)} recommendations"
                        )
                    except Exception as recreate_error:
                        logger.error(f"Error recreating anonymous session: {recreate_error}", exc_info=True)
                        recommendations = []
                        rec_error = (
                            "Unable to load recommendations. Session may have expired. Please upload your file again."
                        )

                # Process recommendations if we got any
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
                rec_error = "Unable to load recommendations at this time."

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

    context = {
        "dna": dna_data,
        "user_profile": user_profile,
        "is_processing": False,
        "recommendations": recommendations,
        "rec_error": rec_error,
        "title": title,
    }

    return render(request, "core/dna_display.html", context)


@require_POST
def upload_view(request):
    csv_file = request.FILES.get("csv_file")

    if not csv_file or not csv_file.name.endswith(".csv"):
        messages.error(request, "Please upload a valid .csv file.")
        return redirect("core:home")

    try:
        if csv_file.size > 10 * 1024 * 1024:  # 10MB limit
            messages.error(request, "File is too large. Please upload an export smaller than 10MB.")
            return redirect("core:home")

        # Track file upload started
        track_file_upload_started(request, csv_file.size)

        csv_content = csv_file.read().decode("utf-8")

        if request.user.is_authenticated:
            # Clear old session data so the view will use the updated profile data
            request.session.pop("dna_data", None)
            result = generate_reading_dna_task.delay(csv_content, request.user.id)

            # Save the pending task ID to track regeneration progress
            request.user.userprofile.pending_dna_task_id = result.id
            request.user.userprofile.save()

            messages.success(
                request,
                "Success! We're updating your Reading DNA. Your dashboard will update automatically when it's ready!",
            )

            processing_url = reverse("core:display_dna") + "?processing=true"

            return redirect(processing_url)
        else:
            result = generate_reading_dna_task.delay(csv_content, None, request.session.session_key)
            task_id = result.id

            request.session["anonymous_task_id"] = task_id
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

        if form.is_valid():
            user = form.save()
            login(request, user)

            had_dna_in_session = "dna_data" in request.session

            if task_id_to_claim:
                user.userprofile.pending_dna_task_id = task_id_to_claim
                user.userprofile.save()
                claim_anonymous_dna_task.delay(user.id, task_id_to_claim)

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


def login_view(request):
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
        message_text = f'Your profile is now public! Share it here: <a href="{public_url}" class="hover:bg-brand-yellow font-bold underline" target="_blank">{public_url}</a>'
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


@login_required
@require_POST
def update_username_api(request):
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
def update_recommendation_visibility(request):
    """Toggle visibility in recommendations"""
    is_visible = request.POST.get("visible_in_recommendations") == "true"
    profile = request.user.userprofile
    profile.visible_in_recommendations = is_visible
    profile.save()

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

        # Get recommendations for the profile owner
        recommendations = []
        if profile.dna_data:
            from .services.recommendation_service import get_recommendations_for_user
            import logging

            logger = logging.getLogger(__name__)
            try:
                recommendations = get_recommendations_for_user(profile_user, limit=6)
                
                # Process recommendations to add confidence_pct (same as display_dna_view)
                for rec in recommendations:
                    rec["confidence_pct"] = int(rec.get("confidence", 0) * 100)
            except Exception as e:
                # Log the error but don't break the page if recommendations fail
                logger.error(f"Failed to get recommendations for user {profile_user.username}: {e}", exc_info=True)
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

        context = {
            "dna": profile.dna_data,
            "profile_user": profile_user,
            "user_profile": profile,
            "title": title,
            "heading_name": heading_name,
            "recommendations": recommendations,
        }
        return render(request, "core/public_profile.html", context)
    except User.DoesNotExist:
        # Show a nicer 404 page instead of the default Django 404
        return render(request, "core/404.html", {"username": username}, status=404)


def task_status_view(request, task_id):
    return render(request, "core/task_status.html", {"task_id": task_id})


def get_task_result_view(request, task_id):
    from .models import AnonymousUserSession
    from .services.recommendation_service import safe_cache_get

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
    # Check if this is a user profile path to show user-not-found message
    path = request.path.strip("/")
    username = None
    if path.startswith("u/") and len(path.split("/")) >= 2:
        # Extract username from path like "u/username" or "u/username/"
        parts = path.split("/")
        if parts[0] == "u" and len(parts) > 1:
            username = parts[1]

    return render(request, "core/404.html", {"username": username}, status=404)


def catch_all_404_view(request, unused_path):
    """Catch-all view for unmatched URLs that shows our custom 404 page."""
    # Check if this is a user profile path to show user-not-found message
    path = request.path.strip("/")
    username = None
    if path.startswith("u/") and len(path.split("/")) >= 2:
        # Extract username from path like "u/username" or "u/username/"
        parts = path.split("/")
        if parts[0] == "u" and len(parts) > 1:
            username = parts[1]

    return render(request, "core/404.html", {"username": username}, status=404)
