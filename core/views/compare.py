"""Compare page: pairwise reading-taste similarity between public readers."""

import logging
import math

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

MIN_COMPARE_USERS = 2
MAX_COMPARE_USERS = 6

# Display order + bar colors for the breakdown card. shared_correlation is
# only present when the pair co-rated enough books to score it (see
# calculate_user_similarity_from_context), hence the membership guard in
# _breakdown_rows. These classes are composed here where the Tailwind scanner
# can't see them — compare.html carries a scanner comment listing them all.
COMPONENT_ROWS = [
    ("shared_correlation", "Shared-book ratings", "bg-brand-green"),
    ("genre_similarity", "Genres", "bg-brand-purple"),
    ("author_similarity", "Authors", "bg-brand-cyan"),
    ("jaccard", "Library overlap", "bg-brand-orange"),
    ("top_overlap", "Top 5 books", "bg-brand-pink"),
    ("rating_pattern", "Rating style", "bg-badge-4"),
    ("era_similarity", "Reading eras", "bg-brand-yellow"),
]

# SVG attribute values (not Tailwind classes): node fills cycle the brand
# palette; edge-label chips tier with match strength like the badge scale.
GRAPH_NODE_FILLS = ["#f9a8d4", "#67e8f9", "#fde047", "#c4b5fd", "#86efac", "#ffa75e"]
GRAPH_VIEWBOX = (640, 560)
GRAPH_CENTER = (320, 272)
GRAPH_RADIUS = 196


def _chip_fill(pct):
    if pct >= 65:
        return "#86efac"
    if pct >= 50:
        return "#a7f3d0"
    return "#ffffff"


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


def _overlap_sentence(names, shared, rated):
    """One plain sentence for what a pair concretely shares."""
    first, second = names
    if shared == 0:
        return f"{first} and {second} have no books in common — this score comes from genres, authors and eras."
    books = "book" if shared == 1 else "books"
    if rated == 0:
        return f"{first} and {second} have {shared} {books} in common."
    if rated == shared:
        both = "it was" if shared == 1 else f"all {shared}"
        return f"{first} and {second} have {shared} {books} in common — {both} rated by both."
    return f"{first} and {second} have {shared} {books} in common — {rated} rated by both."


def _names_sentence(names):
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return ", ".join(names[:-1]) + f" & {names[-1]}"


def _build_graph(usernames, pairs_view):
    """Fixed polygon layout — node i on a circle, no physics needed."""
    n = len(usernames)
    cx, cy = GRAPH_CENTER
    index_of = {name: i for i, name in enumerate(usernames)}

    nodes = []
    for i, name in enumerate(usernames):
        angle = -math.pi / 2 + 2 * math.pi * i / n
        x = round(cx + GRAPH_RADIUS * math.cos(angle), 1)
        y = round(cy + GRAPH_RADIUS * math.sin(angle), 1)
        w = max(78, 30 + 13 * len(name))
        # Django templates can't do arithmetic on floats, so every SVG
        # coordinate (box, offset shadow, label baseline) is precomputed here.
        nodes.append(
            {
                "name": name,
                "x": x,
                "y": y,
                "w": w,
                "fill": GRAPH_NODE_FILLS[i % len(GRAPH_NODE_FILLS)],
                "rect_x": round(x - w / 2, 1),
                "rect_y": round(y - 23, 1),
                "shadow_x": round(x - w / 2 + 4, 1),
                "shadow_y": round(y - 19, 1),
                "text_y": round(y + 9, 1),
            }
        )

    edges = []
    for pair in pairs_view:
        a = index_of[pair["names"][0]]
        b = index_of[pair["names"][1]]
        x1, y1 = nodes[a]["x"], nodes[a]["y"]
        x2, y2 = nodes[b]["x"], nodes[b]["y"]
        # Chords get their label pulled toward the lower-indexed node so the
        # labels of center-crossing diagonals don't pile up in the middle.
        span = abs(a - b)
        t = 0.5 if span == 1 or span == n - 1 else 0.36
        lx = round(x1 + (x2 - x1) * t, 1)
        ly = round(y1 + (y2 - y1) * t, 1)
        edges.append(
            {
                "index": pair["index"],
                "x1": x1,
                "y1": y1,
                "x2": x2,
                "y2": y2,
                "width": round(2 + pair["pct"] * 0.12, 1),
                "pct": pair["pct"],
                "chip_fill": _chip_fill(pair["pct"]),
                "chip_x": round(lx - 26, 1),
                "chip_y": round(ly - 15, 1),
                "shadow_x": round(lx - 23, 1),
                "shadow_y": round(ly - 12, 1),
                "text_x": lx,
                "text_y": round(ly + 8, 1),
            }
        )

    # Thickest edges drawn last sit visually on top where lines cross.
    edges.sort(key=lambda e: e["width"])
    return {"nodes": nodes, "edges": edges, "viewbox": f"0 0 {GRAPH_VIEWBOX[0]} {GRAPH_VIEWBOX[1]}"}


# No method= restriction: HEAD goes through the same view code and must not
# bypass the throttle.
@ratelimit(key=client_ip_key, rate="30/m", block=True)
def _compare_view_throttled(request, usernames):
    # Usernames are stored lowercased by the forms (clean_username), but
    # admin/createsuperuser bypasses that — hence the Lower() lookup below.
    parts = [part.strip().lower() for part in usernames.split(",")]
    unique = sorted(set(part for part in parts if part))

    if not (MIN_COMPARE_USERS <= len(unique) <= MAX_COMPARE_USERS):
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

    username_by_id = {u.id: u.username for u in users}
    display_names = sorted(username_by_id.values(), key=str.lower)

    pairs_view = []
    for index, pair in enumerate(comparison["pairs"]):
        names = sorted((username_by_id[uid] for uid in pair["user_ids"]), key=str.lower)
        pairs_view.append(
            {
                "index": index,
                "names": names,
                "title": f"{names[0]} + {names[1]}",
                "pct": pair["pct"],
                "label": pair["label"],
                "rows": _breakdown_rows(pair),
                "overlap_sentence": _overlap_sentence(names, pair["shared_books_count"], pair["shared_rated_count"]),
            }
        )

    n = len(display_names)
    best_index = max(range(len(pairs_view)), key=lambda i: pairs_view[i]["pct"])

    # Separators pre-built so the template can glue them to the anchor tags —
    # prettier rewraps template whitespace and would float a bare comma.
    name_items = [
        {"name": name, "sep": ("" if i == n - 1 else " vs" if i == n - 2 else ",")}
        for i, name in enumerate(display_names)
    ]

    context = {
        "n": n,
        "names": display_names,
        "name_items": name_items,
        "names_sentence": _names_sentence(display_names),
        "pairs": pairs_view,
        "best_index": best_index,
        "can_add_more": n < MAX_COMPARE_USERS,
        "canonical_usernames": canonical,
    }

    if n == 2:
        context["group_pct"] = pairs_view[0]["pct"]
        context["group_label"] = pairs_view[0]["label"]
    else:
        context["group_pct"] = comparison["mean_pct"]
        context["group_label"] = comparison["mean_label"]
        context["closest"] = pairs_view[best_index]
        # Odd one out: the reader with the lowest mean similarity to the rest.
        averages = {
            name: round(sum(p["pct"] for p in pairs_view if name in p["names"]) / (n - 1)) for name in display_names
        }
        odd_name = min(averages, key=averages.get)
        context["odd"] = {"name": odd_name, "avg_pct": averages[odd_name]}
        context["graph"] = _build_graph(display_names, pairs_view)

    return render(request, "core/compare.html", context)


def compare_view(request, usernames):
    try:
        return _compare_view_throttled(request, usernames)
    except Ratelimited:
        return HttpResponse("Too many requests. Please try again in a minute.", status=429)
