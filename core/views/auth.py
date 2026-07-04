"""Authentication views: signup, login, logout, password reset, and the 404 handler."""

import logging

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm, PasswordResetForm
from django.contrib.auth.models import User
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth.views import PasswordResetView
from django.core.exceptions import NON_FIELD_ERRORS
from django.forms.utils import ErrorDict, ErrorList
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy
from django_ratelimit.decorators import ratelimit
from django_ratelimit.exceptions import Ratelimited

from ..analytics.events import track_anonymous_dna_claimed, track_user_logged_in, track_user_signed_up
from ..cache_utils import safe_cache_get
from ..forms import CustomUserCreationForm
from ..ratelimit_utils import client_ip_key, get_real_client_ip
from ..tasks import _save_dna_to_profile, claim_anonymous_dna_task

logger = logging.getLogger(__name__)


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
