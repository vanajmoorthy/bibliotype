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


def display_dna_view(request):
    dna_data = None
    user_profile = None

    # --- THIS IS THE NEW, CORRECTED LOGIC ---
    # 1. ALWAYS prioritize fresh data from the session first.
    if "dna_data" in request.session:
        print("   [View] Found fresh DNA in session.")
        # .pop() gets the data and removes it so the page is correct on reload.
        dna_data = request.session.pop("dna_data")

    # 2. If no session data, check if the user is logged in and has a saved profile.
    elif request.user.is_authenticated:
        print(f"   [View] No session DNA. Checking profile for user {request.user.username}.")
        user_profile = request.user.userprofile
        if user_profile.dna_data:
            dna_data = user_profile.dna_data

    # If we still have no data, the user needs to upload.
    if not dna_data:
        messages.info(request, "No Reading DNA found. Please upload your Goodreads export to begin.")

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
        # --- The function no longer needs the user object ---
        dna_data = generate_reading_dna(csv_content, request.user)

        # 1. Always save the full, fresh data to the session for immediate display.
        request.session["dna_data"] = dna_data

        # 2. If the user is logged in, ALSO save the full data to their profile for future visits.
        if request.user.is_authenticated:
            try:
                profile = request.user.userprofile
                profile.dna_data = dna_data  # Save the FULL dictionary
                profile.save()
                messages.success(request, "Your Reading DNA has been updated and saved to your profile!")
                print(f"   [DB] Saved FULL DNA data for user: {request.user.username}")
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
        form = UserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            if "dna_data" in request.session:
                user.userprofile.dna_data = request.session.pop("dna_data")
                user.userprofile.save()
                messages.success(request, "Account created and your Reading DNA has been saved!")
            return redirect("core:display_dna")  # Redirect to the consolidated view
    else:
        form = UserCreationForm()
    return render(request, "core/signup.html", {"form": form})


def login_view(request):
    if request.method == "POST":
        form = AuthenticationForm(data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            if "dna_data" in request.session:
                user.userprofile.dna_data = request.session.pop("dna_data")
                user.userprofile.save()
            return redirect("core:display_dna")  # Redirect to the consolidated view
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
    return redirect("core:display_dna")  # Redirect to the consolidated view


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
