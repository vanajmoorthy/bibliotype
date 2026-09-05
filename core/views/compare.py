"""Compare page: pairwise reading-taste similarity between public readers."""

import logging

from django.contrib.auth.models import User
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django_ratelimit.decorators import ratelimit
from django_ratelimit.exceptions import Ratelimited

from ..ratelimit_utils import client_ip_key
from ..services.user_similarity_service import compare_readers

logger = logging.getLogger(__name__)

# Phase 1 ships the 2-reader page; the URL scheme, service, and canonicalization
# already accommodate larger groups for the N-way graph phase (cap will be 6).
MIN_COMPARE_USERS = 2
MAX_COMPARE_USERS = 2

# Display order + colors for the score components on the breakdown card.
# shared_correlation is appended only when the pair had enough co-rated books
# for it to carry weight (see calculate_user_similarity_from_context).
COMPONENT_ROWS = [
    ("genre_similarity", "genres", "bg-brand-purple"),
    ("author_similarity", "authors", "bg-brand-cyan"),
    ("jaccard", "library overlap", "bg-brand-orange"),
    ("top_overlap", "top 5 books", "bg-brand-pink"),
    ("rating_pattern", "rating style", "bg-brand-green"),
    ("era_similarity", "reading eras", "bg-brand-yellow"),
]


def _not_found(request):
    # Same generic 404 as public_profile_view so nonexistent, private, and
    # DNA-less readers are indistinguishable from a probe's point of view.
    return render(request, "core/404.html", {"profile_page": True}, status=404)


def _breakdown_rows(pair):
    rows = []
    if pair["components"].get("shared_correlation") is not None:
        rows.append(
            {
                "label": "rating taste on shared books",
                "pct": pair["components_pct"]["shared_correlation"],
                "color": "bg-brand-green",
            }
        )
    for key, label, color in COMPONENT_ROWS:
        if key in pair["components_pct"]:
            rows.append({"label": label, "pct": pair["components_pct"][key], "color": color})
    return rows


@ratelimit(key=client_ip_key, rate="30/m", method="GET", block=True)
def _compare_view_throttled(request, usernames):
    # Usernames are stored lowercased (see clean_username in core/forms.py),
    # so lowercase + dedup + sort gives the canonical group URL.
    parts = [part.strip().lower() for part in usernames.split(",")]
    unique = sorted(set(part for part in parts if part))

    if not (MIN_COMPARE_USERS <= len(unique) <= MAX_COMPARE_USERS):
        return _not_found(request)

    canonical = ",".join(unique)
    if usernames != canonical:
        return redirect(reverse("core:compare", kwargs={"usernames": canonical}), permanent=True)

    users = list(User.objects.filter(username__in=unique).select_related("userprofile"))
    if len(users) != len(unique):
        return _not_found(request)
    if any(not u.userprofile.is_public or not u.userprofile.dna_data for u in users):
        return _not_found(request)

    comparison = compare_readers(users)
    pair = comparison["pairs"][0]

    context = {
        "comparison": comparison,
        "pair": pair,
        "breakdown_rows": _breakdown_rows(pair),
        "usernames": pair["usernames"],
    }
    return render(request, "core/compare.html", context)


def compare_view(request, usernames):
    try:
        return _compare_view_throttled(request, usernames)
    except Ratelimited:
        return HttpResponse("Too many requests. Please try again in a minute.", status=429)
