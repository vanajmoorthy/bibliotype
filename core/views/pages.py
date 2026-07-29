"""Static page views: home, about, privacy, terms, and methodology."""

from django.shortcuts import render

from core.dna_constants import GLOBAL_AVERAGES, GLOBAL_AVERAGES_SOURCES


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


def methodology_view(request):
    """Displays the Comparative Analytics methodology page."""
    return render(
        request,
        "core/methodology.html",
        {"global_averages": GLOBAL_AVERAGES, "global_averages_sources": GLOBAL_AVERAGES_SOURCES},
    )
