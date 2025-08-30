import json

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib.auth.models import User
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from .analytics import generate_reading_dna
from .forms import CustomUserCreationForm, UpdateDisplayNameForm
from .models import UserProfile


def _save_dna_to_profile(profile, dna_data):
    """
    A reusable helper to correctly save all parts of the DNA dictionary
    to the user's profile, populating both the main JSON blob and the
    optimized, separate fields.
    """
    profile.dna_data = dna_data

    profile.reader_type = dna_data.get("reader_type")
    profile.total_books_read = dna_data.get("user_stats", {}).get("total_books_read")
    profile.reading_vibe = dna_data.get("reading_vibe")
    profile.vibe_data_hash = dna_data.get("vibe_data_hash")

    profile.save()
    print(f"   [DB] Saved DNA data and promoted fields for user: {profile.user.username}")


def home_view(request):
    """Displays the main upload page."""
    return render(request, "core/home.html")


def display_dna_view(request):
    """
    Displays the user's DNA. This is the main dashboard view.
    It has robust logic to handle both session-based (anonymous) and
    database-backed (authenticated) DNA data.
    """
    dna_data = None
    user_profile = None

    if "dna_data" in request.session:
        dna_data = request.session.get("dna_data")
    elif request.user.is_authenticated:
        user_profile = request.user.userprofile

        if user_profile.dna_data:
            dna_data = user_profile.dna_data

    if not dna_data:
        messages.info(request, "First, upload your library file to generate your Bibliotype!")
        return redirect("core:home")

    # Add the update form to the context for the dashboard
    update_form = UpdateDisplayNameForm(instance=request.user) if request.user.is_authenticated else None

    context = {"dna": dna_data, "user_profile": user_profile, "update_form": update_form}
    return render(request, "core/dna_display.html", context)


@require_POST
def upload_view(request):
    csv_file = request.FILES.get("csv_file")

    if not csv_file or not csv_file.name.endswith(".csv"):
        messages.error(request, "Please upload a valid .csv file.")
        return redirect("core:home")

    try:
        csv_content = csv_file.read().decode("utf-8")
        dna_data = generate_reading_dna(csv_content, request.user)

        # Always save the full, fresh data to the session for immediate display.
        request.session["dna_data"] = dna_data

        if request.user.is_authenticated:
            try:
                _save_dna_to_profile(request.user.userprofile, dna_data)

                messages.success(request, "Your Reading DNA has been updated and saved to your profile!")
            except UserProfile.DoesNotExist:
                messages.error(request, "Could not find a user profile to save DNA to.")

        return redirect("core:display_dna")

    except ValueError as e:
        messages.error(request, f"Analysis Error: {e}")
        return redirect("core:home")
    except Exception as e:
        print(f"UNEXPECTED ERROR in upload_view: {e}")
        messages.error(request, "An unexpected error occurred during analysis. Please try again.")
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
    # MODIFIED: Add logic to allow login with email address
    if request.method == "POST":
        form = AuthenticationForm(data=request.POST)

        # --- NEW: EMAIL LOGIN LOGIC ---
        email = request.POST.get("username")  # The form field is named 'username'
        password = request.POST.get("password")

        if email and password:
            # Try to find a user with this email
            try:
                user_obj = User.objects.get(email=email)
                # Authenticate using the found user's actual username
                user = authenticate(request, username=user_obj.username, password=password)
                if user is not None:
                    login(request, user)
                    # ... (rest of the logic is the same)
                    if "dna_data" in request.session:
                        # ... (save if empty logic)
                        messages.success(request, "Logged in and your latest Bibliotype has been saved!")
                        return redirect("core:display_dna")
                    return redirect("core:home")
            except User.DoesNotExist:
                # If no user with that email, fall through to the default form validation
                pass

        # If the email login fails, the standard form validation will show the error
        if form.is_valid():
            # This block is for users who log in with their actual username
            user = form.get_user()
            login(request, user)
            return redirect("core:home")
    else:
        form = AuthenticationForm()

    # Change the label for the username field to "Email"
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
    """Toggle the public status of a user's profile."""
    is_public = request.POST.get("is_public") == "true"
    profile = request.user.userprofile
    profile.is_public = is_public
    profile.save()
    messages.success(request, f"Your profile is now {'public' if is_public else 'private'}.")
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
        from django.http import Http44

        raise Http404("User does not exist.")
