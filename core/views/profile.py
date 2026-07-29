"""Profile settings views: privacy, display name, email, password, account deletion, and recommendation-visibility."""

import json
import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.http import JsonResponse
from django.shortcuts import redirect
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit
from django_ratelimit.exceptions import Ratelimited

from ..analytics.events import track_account_deleted, track_profile_made_public, track_settings_updated
from ..cache_utils import safe_cache_delete
from ..forms import ChangePasswordForm, UpdateDisplayNameForm, UpdateEmailForm

logger = logging.getLogger(__name__)


def _wants_json(request):
    """Return True when the caller expects a JSON response (AJAX fetch)."""
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def _ratelimited_response(request):
    """Uniform 429 for the throttled settings endpoints (JSON for AJAX, redirect otherwise)."""
    if _wants_json(request):
        return JsonResponse({"status": "error", "message": "Too many attempts, try again later."}, status=429)
    messages.error(request, "Too many attempts, try again later.")
    return redirect("core:display_dna")


@login_required
@require_POST
def update_privacy_view(request):
    is_public = request.POST.get("is_public") == "true"
    profile = request.user.userprofile
    was_public = profile.is_public
    profile.is_public = is_public
    profile.save()

    # Going private removes the user from the recommendation candidate pool, so drop
    # the recs caches that could still surface them (mirrors update_recommendation_visibility).
    if was_public and not is_public:
        safe_cache_delete(f"user_recommendations_{request.user.id}")
        safe_cache_delete(f"similar_users_{request.user.id}")
        safe_cache_delete("public_users_for_recs_sample")
        logger.info(
            "profile set private; cleared recs caches",
            extra={"user_id": request.user.id},
        )

    if is_public:
        track_profile_made_public(request.user.id)

        public_url = request.build_absolute_uri(
            reverse("core:public_profile", kwargs={"username": request.user.username})
        )
        if _wants_json(request):
            return JsonResponse({"status": "success", "is_public": True, "public_url": public_url})

        message_text = render_to_string(
            "core/partials/messages_with_link.html",
            {"public_url": public_url, "username": request.user.username},
        )
        messages.success(request, message_text)
    else:
        if _wants_json(request):
            return JsonResponse({"status": "success", "is_public": False})
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

    safe_cache_delete(f"user_recommendations_{request.user.id}")
    safe_cache_delete(f"similar_users_{request.user.id}")

    if was_visible and not is_visible:
        safe_cache_delete("public_users_for_recs_sample")
        logger.info(
            "user opted out of recs; cleared candidate sample cache",
            extra={"user_id": request.user.id},
        )

    track_settings_updated(request.user.id, setting_type="recommendation_visibility")

    if _wants_json(request):
        msg = (
            "You are now visible as a recommendation source to similar readers!"
            if is_visible
            else "You've opted out of being shown as a recommendation source."
        )
        return JsonResponse({"status": "success", "visible_in_recommendations": is_visible, "message": msg})

    if is_visible:
        messages.success(request, "You are now visible as a recommendation source to similar readers!")
    else:
        messages.success(request, "You've opted out of being shown as a recommendation source.")

    return redirect("core:display_dna")


def _notify_old_email_of_change(request, old_email, new_email):
    """Best-effort notify the previous address that the account email changed."""
    if not old_email or old_email.strip().lower() == new_email.strip().lower():
        return
    try:
        reset_url = request.build_absolute_uri(reverse("password_reset"))
        send_mail(
            subject="Your Bibliotype email was changed",
            message=(
                f"Your Bibliotype account email was changed to {new_email}.\n\n"
                f"If this wasn't you, reset your password right away at {reset_url}"
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[old_email],
            fail_silently=False,
        )
    except Exception as exc:
        logger.warning(f"Could not send email-change notification to old address: {exc}")


@ratelimit(key="user", rate="5/m", method="POST", block=True)
def _update_email_view_throttled(request):
    """Change the authenticated user's email address (requires current password)."""
    user = request.user
    old_email = user.email
    form = UpdateEmailForm(request.POST, user=user)
    if form.is_valid():
        new_email = form.cleaned_data["email"]
        user.email = new_email
        user.save(update_fields=["email"])
        track_settings_updated(user.id, setting_type="email")
        _notify_old_email_of_change(request, old_email, new_email)

        if _wants_json(request):
            return JsonResponse({"status": "success", "message": "Email updated successfully."})

        messages.success(request, "Your email has been updated.")
        return redirect("core:display_dna")

    if _wants_json(request):
        errors = [msg for field_errors in form.errors.values() for msg in field_errors]
        return JsonResponse({"status": "error", "message": errors[0] if errors else "Invalid email."}, status=400)

    for error in form.errors.values():
        messages.error(request, error)
    return redirect("core:display_dna")


@login_required
@require_POST
def update_email_view(request):
    try:
        return _update_email_view_throttled(request)
    except Ratelimited:
        return _ratelimited_response(request)


@ratelimit(key="user", rate="5/m", method="POST", block=True)
def _change_password_view_throttled(request):
    """Change the authenticated user's password while keeping the session alive."""
    form = ChangePasswordForm(request.POST, user=request.user)
    if form.is_valid():
        user = request.user
        user.set_password(form.cleaned_data["new_password1"])
        user.save()
        update_session_auth_hash(request, user)
        track_settings_updated(user.id, setting_type="password")

        if _wants_json(request):
            return JsonResponse({"status": "success", "message": "Password changed successfully."})

        messages.success(request, "Your password has been changed.")
        return redirect("core:display_dna")

    if _wants_json(request):
        errors = [msg for field_errors in form.errors.values() for msg in field_errors]
        return JsonResponse({"status": "error", "message": errors[0] if errors else "Invalid input."}, status=400)

    for error in form.errors.values():
        messages.error(request, error)
    return redirect("core:display_dna")


@login_required
@require_POST
def change_password_view(request):
    try:
        return _change_password_view_throttled(request)
    except Ratelimited:
        return _ratelimited_response(request)


@ratelimit(key="user", rate="5/m", method="POST", block=True)
def _delete_account_view_throttled(request):
    """Permanently delete the authenticated user's account after double verification."""
    confirmation = request.POST.get("confirmation", "")
    password = request.POST.get("password", "")
    user = request.user

    if confirmation != "DELETE":
        if _wants_json(request):
            return JsonResponse({"status": "error", "message": "Please type DELETE to confirm."}, status=400)
        messages.error(request, "Please type DELETE to confirm.")
        return redirect("core:display_dna")

    if not user.check_password(password):
        if _wants_json(request):
            return JsonResponse({"status": "error", "message": "Incorrect password."}, status=400)
        messages.error(request, "Incorrect password.")
        return redirect("core:display_dna")

    # Capture UID before deletion so the analytics event can still fire
    uid = user.id
    track_account_deleted(uid)
    # Drop the recs candidate sample so the deleted user can't linger in it.
    safe_cache_delete("public_users_for_recs_sample")
    logout(request)
    User.objects.filter(pk=uid).delete()

    if _wants_json(request):
        return JsonResponse({"status": "success", "redirect": reverse("core:home")})

    messages.success(request, "Your account has been permanently deleted.")
    return redirect("core:home")


@login_required
@require_POST
def delete_account_view(request):
    try:
        return _delete_account_view_throttled(request)
    except Ratelimited:
        return _ratelimited_response(request)
