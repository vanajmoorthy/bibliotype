# In core/services/author_service.py

import requests
from datetime import datetime, timedelta
from urllib.parse import quote

# --- Heuristic Thresholds (Now with a new "icon" level) ---
WORK_COUNT_THRESHOLD = 10
# Standard threshold for a popular, working author.
MONTHLY_PAGEVIEW_THRESHOLD = 2000
# Exceptionally high threshold for authors who are cultural mainstays, regardless of work count.
# This is the "Harper Lee" exception.
CULTURAL_ICON_PAGEVIEW_THRESHOLD = 50000


def check_author_mainstream_status(author_name: str, session: requests.Session) -> dict:
    """
    Checks an author's mainstream status using a two-path logic:
    1. Prolific authors with consistent interest.
    2. Cultural icons with massive interest (e.g., Harper Lee).
    """
    findings = {
        "work_count": 0,
        "average_monthly_views": 0,
        "is_mainstream": False,
        "reason": None,  # We will now include a reason for the decision
        "error": None,
    }

    try:
        # --- Step 1: Query Open Library for work count ---
        ol_search_url = "https://openlibrary.org/search/authors.json"
        res_ol = session.get(ol_search_url, params={"q": author_name}, timeout=10)
        res_ol.raise_for_status()
        ol_data = res_ol.json()

        if ol_data.get("numFound", 0) > 0:
            author_info = ol_data["docs"][0]
            findings["work_count"] = author_info.get("work_count", 0)

        # --- Step 2: Query Wikipedia for average page views ---
        today = datetime.utcnow()
        ninety_days_ago = today - timedelta(days=90)
        start_date = ninety_days_ago.strftime("%Y%m%d")
        end_date = today.strftime("%Y%m%d")
        encoded_author_name = quote(author_name.replace(" ", "_"), safe="")

        pageviews_url = (
            f"https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
            f"en.wikipedia/all-access/user/{encoded_author_name}/daily/{start_date}/{end_date}"
        )

        res_wiki = session.get(pageviews_url, timeout=10)

        if res_wiki.status_code == 200:
            wiki_data = res_wiki.json()
            items = wiki_data.get("items", [])
            if items:
                total_views = sum(item["views"] for item in items)
                findings["average_monthly_views"] = round(total_views / 3)

        # --- NEW: Print the page views for visibility ---
        print(f"    -> Work Count: {findings['work_count']}, Avg Monthly Views: {findings['average_monthly_views']}")

        # --- NEW: Final Decision Logic with two paths ---

        # Path 1: A prolific career author with sustained public interest.
        is_prolific_and_popular = (
            findings["work_count"] >= WORK_COUNT_THRESHOLD
            and findings["average_monthly_views"] >= MONTHLY_PAGEVIEW_THRESHOLD
        )

        # Path 2: A cultural icon with massive public interest, regardless of work count.
        is_cultural_icon = findings["average_monthly_views"] >= CULTURAL_ICON_PAGEVIEW_THRESHOLD

        if is_cultural_icon:
            findings["is_mainstream"] = True
            findings["reason"] = (
                f"Met cultural icon threshold with {findings['average_monthly_views']} avg monthly views."
            )
        elif is_prolific_and_popular:
            findings["is_mainstream"] = True
            findings["reason"] = (
                f"Met prolific author threshold (Works: {findings['work_count']}, Views: {findings['average_monthly_views']})."
            )
        else:
            findings["is_mainstream"] = False
            findings["reason"] = "Did not meet prolific or cultural icon thresholds."

    except requests.RequestException as e:
        findings["error"] = str(e)

    return findings
