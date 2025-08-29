from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib.auth.models import User
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from .analytics import generate_reading_dna
from .forms import CustomUserCreationForm
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
    dna_data = None
    user_profile = None

    if "dna_data" in request.session:
        print("   [View] Found fresh DNA in session.")
        dna_data = request.session.get("dna_data")

    # If no session data, check if the user is logged in and has a saved profile.
    elif request.user.is_authenticated:
        print(f"   [View] No session DNA. Checking profile for user {request.user.username}.")
        user_profile = request.user.userprofile
        if user_profile.dna_data:
            dna_data = user_profile.dna_data

    if not dna_data:
        messages.info(request, "First, upload your library file to generate your Readprint!")
        return redirect("core:home")

    context = {"dna": dna_data, "user_profile": user_profile}
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

            # Check if there's a pending DNA in the session from before signup.
            if "dna_data" in request.session:
                dna_to_save = request.session.pop("dna_data")

                _save_dna_to_profile(user.userprofile, dna_to_save)

                messages.success(request, "Account created and your Bibliotype has been saved!")
                # Redirect to the DNA page to show them their saved results.
                return redirect("core:display_dna")

            # If no DNA in session, just redirect to the home page.
            messages.success(request, "Account created! Now, let's generate your Bibliotype.")
            return redirect("core:home")
    else:
        form = CustomUserCreationForm()
    return render(request, "core/signup.html", {"form": form})


def login_view(request):
    if request.method == "POST":
        form = AuthenticationForm(data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)

            if "dna_data" in request.session:
                profile = user.userprofile

                # Only save the session DNA if the user's profile is currently empty.
                if not profile.dna_data:
                    dna_to_save = request.session.pop("dna_data")
                    _save_dna_to_profile(profile, dna_to_save)
                    messages.success(request, "Logged in and your first Bibliotype has been saved!")
                    return redirect("core:display_dna")
                else:
                    # If the profile already has data, discard the temporary session data.
                    request.session.pop("dna_data", None)  # Safely pop it
                    messages.success(request, f"Welcome back, {user.username}!")

            return redirect("core:home")
    else:
        form = AuthenticationForm()
    return render(request, "core/login.html", {"form": form})


def logout_view(request):
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
    messages.success(request, f"Your profile is now {"public" if is_public else "private"}.")
    return redirect("core:display_dna")


def public_profile_view(request, username):
    """Displays a user's public DNA."""
    try:
        user = User.objects.get(username=username)
        profile = user.userprofile
        if not profile.is_public:
            return render(request, "core/profile_private.html")

        context = {"dna": profile.dna_data, "profile_user": user}
        return render(request, "core/public_profile.html", context)
    except User.DoesNotExist:
        from django.http import Http404

        raise Http404("User does not exist.")
