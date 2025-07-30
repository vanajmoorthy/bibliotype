from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib.auth.models import User
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from .analytics import generate_reading_dna
from .models import UserProfile


def home_view(request):
    """Displays the main upload page."""
    return render(request, "core/home.html")


@require_POST
def upload_view(request):
    """Handles the CSV upload and redirects to the DNA page."""
    csv_file = request.FILES.get("csv_file")
    if not csv_file or not csv_file.name.endswith(".csv"):
        messages.error(request, "Please upload a valid .csv file.")
        return redirect("core:home")

    try:
        csv_content = csv_file.read().decode("utf-8")
        dna_data = generate_reading_dna(csv_content)

        # Store DNA in the session for anonymous users
        request.session["dna_data"] = dna_data

        # If user is logged in, save it directly to their profile
        if request.user.is_authenticated:
            profile = request.user.userprofile
            profile.dna_data = dna_data
            profile.save()
            return redirect("core:dashboard")

        return redirect("core:dna")

    except ValueError as e:
        messages.error(request, f"Analysis Error: {e}")
        return redirect("core:home")
    except Exception as e:
        messages.error(request, f"An unexpected error occurred: {e}")
        return redirect("core:home")


def dna_view(request):
    """Displays the temporary DNA results from the session."""
    dna_data = request.session.get("dna_data")
    if not dna_data:
        messages.info(
            request, "No Reading DNA found. Please upload your Goodreads export first."
        )
        return redirect("core:home")

    context = {"dna": dna_data}
    return render(request, "core/dna_results.html", context)


# --- Authenticated Flow ---


def signup_view(request):
    if request.method == "POST":
        form = UserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            # Log the user in
            login(request, user)
            # Check if there is temporary DNA in the session and assign it
            if "dna_data" in request.session:
                user.userprofile.dna_data = request.session.pop("dna_data")
                user.userprofile.save()
                messages.success(
                    request, "Account created and your Reading DNA has been saved!"
                )
            return redirect("core:dashboard")
    else:
        form = UserCreationForm()
    return render(request, "core/signup.html", {"form": form})


def login_view(request):
    if request.method == "POST":
        form = AuthenticationForm(data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            # If there's DNA in the session, ask the user if they want to overwrite
            if "dna_data" in request.session:
                # For simplicity, we'll just overwrite it. A better UX could ask.
                user.userprofile.dna_data = request.session.pop("dna_data")
                user.userprofile.save()
            return redirect("core:dashboard")
    else:
        form = AuthenticationForm()
    return render(request, "core/login.html", {"form": form})


def logout_view(request):
    logout(request)
    messages.info(request, "You have been logged out.")
    return redirect("core:home")


@login_required
def dashboard_view(request):
    """Displays the logged-in user's saved dashboard."""
    profile = request.user.userprofile
    context = {"dna": profile.dna_data, "user_profile": profile}
    return render(request, "core/dashboard.html", context)


# --- Stretch Goals ---


@login_required
@require_POST
def update_privacy_view(request):
    """Toggle the public status of a user's profile."""
    is_public = request.POST.get("is_public") == "true"
    profile = request.user.userprofile
    profile.is_public = is_public
    profile.save()
    messages.success(
        request, f"Your profile is now {'public' if is_public else 'private'}."
    )
    return redirect("core:dashboard")


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
        # Handle user not found (404)
        from django.http import Http404

        raise Http404("User does not exist.")
