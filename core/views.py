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
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from .forms import CustomUserCreationForm, UpdateDisplayNameForm
from .tasks import _save_dna_to_profile, claim_anonymous_dna_task, generate_reading_dna_task


def home_view(request):
    """Displays the main upload page."""
    return render(request, "core/home.html")


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
            return render(request, "core/dna_display.html", {"is_processing": True})

        if dna_data is None and user_profile.dna_data:
            dna_data = user_profile.dna_data
        
        # Get recommendations for registered user
        if user_profile.dna_data:
            from .services.recommendation_service import get_recommendations_for_user
            recommendations = get_recommendations_for_user(request.user, limit=6)
    else:
        # Anonymous user recommendations
        if dna_data and request.session.session_key:
            from .services.recommendation_service import get_recommendations_for_anonymous
            recommendations = get_recommendations_for_anonymous(request.session.session_key, limit=6)

    if not request.user.is_authenticated and dna_data is None:
        messages.info(request, "First, upload your library file to generate your Bibliotype!")
        return redirect("core:home")

    context = {
        "dna": dna_data,
        "user_profile": user_profile,
        "is_processing": False,
        "recommendations": recommendations,
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

            if task_id_to_claim:
                user.userprofile.pending_dna_task_id = task_id_to_claim
                user.userprofile.save()
                claim_anonymous_dna_task.delay(user.id, task_id_to_claim)
                messages.success(request, "Account created! We'll save your Bibliotype as soon as it's ready.")
                processing_url = reverse("core:display_dna") + "?processing=true"
                return redirect(processing_url)

            if "dna_data" in request.session:
                dna_to_save = request.session.pop("dna_data")
                _save_dna_to_profile(user.userprofile, dna_to_save)
                messages.success(request, "Account created and your Bibliotype has been saved!")
                return redirect("core:display_dna")

            messages.success(request, "Account created! Now, let's generate your Bibliotype.")

            posthog.capture("user_signed_up_not_after_generating", properties={"example_property": "with_some_value"})
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

            if "dna_data" in request.session:
                dna_to_save = request.session.pop("dna_data")
                if not user.userprofile.dna_data:
                    _save_dna_to_profile(user.userprofile, dna_to_save)
                messages.success(request, "Logged in successfully!")
                return redirect("core:display_dna")

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

        if not profile.is_public and request.user != profile_user:
            return render(request, "core/profile_private.html")

        display_name = profile_user.first_name if profile_user.first_name else profile_user.username

        if display_name.lower().endswith("s"):
            title = f"{display_name}' Reading DNA"
        else:
            title = f"{display_name}'s Reading DNA"

        # Get recommendations for the profile owner
        recommendations = []
        if profile.dna_data:
            from .services.recommendation_service import get_recommendations_for_user
            recommendations = get_recommendations_for_user(profile_user, limit=6)

        context = {
            "dna": profile.dna_data,
            "profile_user": profile_user,
            "user_profile": profile,
            "title": title,
            "recommendations": recommendations,
        }
        return render(request, "core/public_profile.html", context)
    except User.DoesNotExist:
        from django.http import Http404

        raise Http404("User does not exist.")


def task_status_view(request, task_id):
    return render(request, "core/task_status.html", {"task_id": task_id})


def get_task_result_view(request, task_id):
    cached_result = cache.get(f"dna_result_{task_id}")
    if cached_result is not None:
        request.session["dna_data"] = cached_result
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
        request.session.save()

        return JsonResponse(
            {
                "status": "SUCCESS",
                "redirect_url": reverse("core:display_dna"),
            }
        )
    elif result.state == "FAILURE":
        return JsonResponse({"status": "FAILURE", "error": "An error occurred during processing."})
    else:
        return JsonResponse({"status": "PENDING"})
