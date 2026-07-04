"""Static page views: home, about, privacy, and terms."""

from django.shortcuts import render


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
