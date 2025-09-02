import json

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
from .tasks import _save_dna_to_profile, generate_reading_dna_task


def home_view(request):
    """Displays the main upload page."""
    return render(request, "core/home.html")


def display_dna_view(request):
    # Check for the signal in the URL
    is_processing = request.GET.get("processing") == "true"

    dna_data = request.session.get("dna_data")
    user_profile = None

    if request.user.is_authenticated:
        user_profile = request.user.userprofile
        user_profile.refresh_from_db()

        # If we don't have DNA data in the profile yet BUT we are in the processing state,
        # we will force the template to show the spinner.
        if is_processing and not user_profile.dna_data:
            return render(request, "core/dna_display.html", {"is_processing": True})

        # If not processing, try to load existing DNA data
        if dna_data is None and user_profile.dna_data:
            dna_data = user_profile.dna_data

    # This handles anonymous users
    if not request.user.is_authenticated and dna_data is None:
        messages.info(request, "First, upload your library file to generate your Bibliotype!")
        return redirect("core:home")

    context = {
        "dna": dna_data,
        "user_profile": user_profile,
        "is_processing": False,  # Explicitly set to false unless handled above
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
            # Authenticated users still use the original background flow
            generate_reading_dna_task.delay(csv_content, request.user.id)

            messages.success(
                request,
                "Success! We're updating your Reading DNA. Your dashboard will update automatically when it's ready!",
            )

            processing_url = reverse("core:display_dna") + "?processing=true"

            return redirect(processing_url)
        else:
            result = generate_reading_dna_task.delay(csv_content, None)
            task_id = result.id
            return redirect("core:task_status", task_id=task_id)

    except Exception as e:
        print(f"UNEXPECTED ERROR in upload_view: {e}")
        messages.error(request, "An unexpected error occurred. Please try again.")
        return redirect("core:home")


def signup_view(request):
    if request.method == "POST":
        form = CustomUserCreationForm(request.POST)

        if form.is_valid():
            user = form.save()
            login(request, user)

            if "dna_data" in request.session:
                dna_to_save = request.session.pop("dna_data")
                _save_dna_to_profile(user.userprofile, dna_to_save)
                messages.success(request, "Account created and your Bibliotype has been saved!")

                return redirect("core:display_dna")
            messages.success(request, "Account created! Now, let's generate your Bibliotype.")

            return redirect("core:home")
    else:
        form = CustomUserCreationForm()

    return render(request, "core/signup.html", {"form": form})


def login_view(request):
    if request.method == "POST":
        email = request.POST.get("username")
        password = request.POST.get("password")

        user = None
        if email and password:
            try:
                # Find the user by their email, case-insensitive
                user_obj = User.objects.get(email__iexact=email)

                # Use Django's backend to authenticate with the found user's
                # actual username and the provided password.
                user = authenticate(request, username=user_obj.username, password=password)
            except User.DoesNotExist:
                # If the email doesn't exist, user remains None
                pass

        if user is not None:
            # If authentication was successful, log them in
            login(request, user)

            # Check for and save any anonymous DNA data
            if "dna_data" in request.session:
                dna_to_save = request.session.pop("dna_data")
                # Only save if they don't already have DNA to prevent overwriting
                if not user.userprofile.dna_data:
                    _save_dna_to_profile(user.userprofile, dna_to_save)
                messages.success(request, "Logged in successfully!")
                return redirect("core:display_dna")

            # If no DNA in session, redirect to home
            return redirect("core:home")

        # If authentication fails for any reason, show a clear error message.
        messages.error(request, "Invalid email or password. Please try again.")

    # For a GET request, create a blank form
    form = AuthenticationForm()
    # Ensure the label on the page says "Email"
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
    """
    Toggle the public status of a user's profile and provide a shareable
    link in the success message if made public.
    """
    is_public = request.POST.get("is_public") == "true"
    profile = request.user.userprofile
    profile.is_public = is_public
    profile.save()

    if is_public:
        # If the profile is now public, generate the full URL for their profile.
        # This assumes your public profile URL is named 'core:public_profile'
        # and takes a 'username' keyword argument.
        public_url = request.build_absolute_uri(
            reverse("core:public_profile", kwargs={"username": request.user.username})
        )
        # Create a message that includes the clickable link.
        # Adding a few utility classes to make the link match the site's style.
        message_text = f'Your profile is now public! Share it here: <a href="{public_url}" class="hover:bg-brand-yellow font-bold underline" target="_blank">{public_url}</a>'
        messages.success(request, message_text)
    else:
        # If the profile is now private, show a simpler message.
        messages.success(request, "Your profile is now private.")

    # After updating, we must clear any stale session data before redirecting.
    if "dna_data" in request.session:
        request.session.pop("dna_data", None)

    return redirect("core:display_dna")


@login_required
@require_POST
def update_display_name_view(request):
    """Handles the form submission for updating a user's display name."""
    form = UpdateDisplayNameForm(request.POST, user=request.user, instance=request.user)
    if form.is_valid():
        form.save()
        messages.success(request, "Your display name has been updated!")
    else:
        # If there are errors (like the name is taken), display them.
        for error in form.errors.values():
            messages.error(request, error)

    return redirect("core:display_dna")


@login_required
@require_POST
def update_username_api(request):
    """
    An API-style view to handle inline username updates.
    Expects a JSON request body with a 'username' key.
    Now uses the Django messages framework for feedback.
    """
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
            # We still return the error message in the JSON for immediate feedback.
            return JsonResponse({"status": "error", "message": error_message}, status=400)

    except Exception as e:
        print(f"Error in update_username_api: {e}")

        return JsonResponse({"status": "error", "message": "An unexpected server error occurred."}, status=500)


@login_required
def check_dna_status_view(request):
    """
    An API endpoint for the frontend to poll to see if DNA processing is complete.
    """
    profile = request.user.userprofile
    # The presence of dna_data is our signal that processing is done.
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
        # --------------------------------

        context = {
            "dna": profile.dna_data,
            "profile_user": profile_user,
            "user_profile": profile,
            "title": title,  # <-- Pass the pre-formatted title to the template
        }
        return render(request, "core/public_profile.html", context)
    except User.DoesNotExist:
        from django.http import Http404

        raise Http404("User does not exist.")


def task_status_view(request, task_id):
    """
    Renders the "waiting" page. The JavaScript on this page will
    do the actual work of polling for the result.
    """
    # We pass the task_id to the template
    return render(request, "core/task_status.html", {"task_id": task_id})


def get_task_result_view(request, task_id):
    """
    An API endpoint that the frontend can call to check a task's status.
    """

    # Get the task result from Celery
    result = AsyncResult(task_id)

    if result.ready():
        # The task is complete.
        if result.successful():
            # The task completed without errors. Get the result.
            dna_data = result.get()

            # Store the final DNA data in the session for the anonymous user
            request.session["dna_data"] = dna_data
            request.session.save()

            return JsonResponse(
                {
                    "status": "SUCCESS",
                    # Tell the frontend where to redirect the user
                    "redirect_url": reverse("core:display_dna"),
                }
            )
        else:
            # The task failed.
            return JsonResponse({"status": "FAILURE", "error": "An error occurred during processing."})
    else:
        # The task is still running.
        return JsonResponse({"status": "PENDING"})
