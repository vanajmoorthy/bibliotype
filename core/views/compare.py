"""Compare page: pairwise reading-taste similarity between public readers."""

import logging

from django.contrib.auth.models import User
from django.db.models.functions import Lower
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django_ratelimit.decorators import ratelimit
from django_ratelimit.exceptions import Ratelimited

from ..ratelimit_utils import client_ip_key
from ..services.user_similarity_service import compare_readers

logger = logging.getLogger(__name__)

# Phase 1 renders exactly one pair. The N-way phase reworks the whole render
# path (graph layout, callouts, template) — not just this number, so it is a
# single size rather than a min/max range that would suggest otherwise.
COMPARE_GROUP_SIZE = 2

# Display order + bar colors for the breakdown card. shared_correlation is
# only present when the pair co-rated enough books to score it (see
# calculate_user_similarity_from_context), hence the membership guard in
# _breakdown_rows. These classes are composed here where the Tailwind scanner
# can't see them — compare.html carries a scanner comment listing them all.
COMPONENT_ROWS = [
    ("shared_correlation", "rating taste on shared books", "bg-brand-green"),
    ("genre_similarity", "genres", "bg-brand-purple"),
    ("author_similarity", "authors", "bg-brand-cyan"),
    ("jaccard", "library overlap", "bg-brand-orange"),
    ("top_overlap", "top 5 books", "bg-brand-pink"),
    ("rating_pattern", "rating style", "bg-badge-4"),
    ("era_similarity", "reading eras", "bg-brand-yellow"),
]


def _not_found(request):
    # Same generic 404 as public_profile_view so nonexistent, private,
    # DNA-less, and empty-library readers are indistinguishable from a
    # probe's point of view.
    return render(request, "core/404.html", {"profile_page": True}, status=404)


def _breakdown_rows(pair):
    return [
        {"label": label, "pct": pair["components_pct"][key], "color": color}
        for key, label, color in COMPONENT_ROWS
        if key in pair["components_pct"]
    ]


# No method= restriction: HEAD goes through the same view code and must not
# bypass the throttle.
@ratelimit(key=client_ip_key, rate="30/m", block=True)
def _compare_view_throttled(request, usernames):
    # Usernames are stored lowercased by the forms (clean_username), but
    # admin/createsuperuser bypasses that — hence the Lower() lookup below.
    parts = [part.strip().lower() for part in usernames.split(",")]
    unique = sorted(set(part for part in parts if part))

    if len(unique) != COMPARE_GROUP_SIZE:
        return _not_found(request)

    canonical = ",".join(unique)
    if usernames != canonical:
        return redirect(reverse("core:compare", kwargs={"usernames": canonical}), permanent=True)

    # Eligibility is filtered in SQL so ineligible users are simply absent
    # (one indistinguishable 404 for every reason) and no dna_data JSON is
    # transferred just to check truthiness.
    users = list(
        User.objects.annotate(username_lower=Lower("username"))
        .filter(
            username_lower__in=unique,
            userprofile__is_public=True,
            userprofile__dna_data__isnull=False,
        )
        .only("id", "username")
    )
    if len(users) != len(unique):
        return _not_found(request)

    try:
        comparison = compare_readers(users)
    except ValueError:
        # Empty library — not comparable, and not distinguishable from unknown.
        return _not_found(request)

    pair = comparison["pairs"][0]
    username_by_id = {u.id: u.username for u in users}

    context = {
        "pair": pair,
        "breakdown_rows": _breakdown_rows(pair),
        "usernames": [username_by_id[user_id] for user_id in pair["user_ids"]],
    }
    return render(request, "core/compare.html", context)


def compare_view(request, usernames):
    try:
        return _compare_view_throttled(request, usernames)
    except Ratelimited:
        return HttpResponse("Too many requests. Please try again in a minute.", status=429)
