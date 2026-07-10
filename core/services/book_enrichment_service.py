import logging
import os
import re
import time

import requests
from django.conf import settings
from django.db import IntegrityError
from django.utils import timezone

from ..analytics.events import track_external_api_call
from ..dna_constants import CANONICAL_GENRE_MAP, EXCLUDED_GENRES, GENRE_PRIORITY
from ..models import Author, Book, Genre, Publisher
from ._book_urls import cover_url_from_isbn, cover_url_from_olid

logger = logging.getLogger(__name__)

GOOGLE_BOOKS_API_KEY = os.getenv("GOOGLE_BOOKS_API_KEY")

# US-027b: Precompile alias regexes at import time so we don't re-run
# `re.compile(r"\b" + re.escape(alias) + r"\b")` once per book per alias.
# Longest aliases first so e.g. "science fiction" beats "science".
_COMPILED_ALIAS_PATTERNS = [
    (re.compile(r"\b" + re.escape(alias) + r"\b"), CANONICAL_GENRE_MAP[alias])
    for alias in sorted(CANONICAL_GENRE_MAP.keys(), key=len, reverse=True)
]


def _throttle():
    """US-027: per-call sleep between external API hits.

    Skipped when `settings.ENABLE_PARALLEL_ENRICHMENT` is True; the Celery
    `rate_limit="30/m"` on `enrich_book_task` is the unconditional safety net.
    """
    if not settings.ENABLE_PARALLEL_ENRICHMENT:
        time.sleep(1.2)


def _clean_title_for_api(title):
    clean_title = re.sub(r"[\(\[].*?[\)\]]", "", title)
    clean_title = clean_title.split(":")[0]
    return clean_title.strip()


def _clean_and_canonicalize_genres(subjects):
    """
    Canonicalize Open Library subjects into our genre taxonomy.

    This function is more strict about matching to prevent false positives where
    books get random genres that don't match their actual content.
    """
    if not subjects:
        return set()

    canonical_genres = set()

    for subject in subjects:
        s_lower = subject.lower().strip()

        # Skip if this entire subject is in our exclusion list
        if s_lower in EXCLUDED_GENRES:
            continue

        matched = False
        for pattern, canonical_name in _COMPILED_ALIAS_PATTERNS:
            if pattern.search(s_lower):
                # Double-check canonical name isn't excluded
                if canonical_name not in EXCLUDED_GENRES:
                    canonical_genres.add(canonical_name)
                    matched = True
                    logger.debug(f"Matched '{subject}' to genre '{canonical_name}'")
                    break

        # Debug logging for unmatched subjects (helps identify missing aliases or needed exclusions)
        if not matched and s_lower:
            logger.debug(f"Could not match subject: '{subject}'")

    return canonical_genres


def _canonicalize_google_books_categories(categories):
    """
    Canonicalize Google Books categories into our genre taxonomy.

    Google Books categories are often more accurate than Open Library subjects.
    They come formatted like: "Fiction / Literary" or "History / Ancient / Rome",
    so we split on `/` first and then delegate to `_clean_and_canonicalize_genres`.
    """
    if not categories:
        return set()

    flat = [part for cat in categories for part in cat.split("/")]
    return _clean_and_canonicalize_genres(flat)


def get_book_details_for_seeder(title: str, author: str, session: requests.Session) -> dict:
    details = {}
    try:
        search_url = "https://openlibrary.org/search.json"
        search_params = {"title": _clean_title_for_api(title), "author": author}
        res_search = session.get(search_url, params=search_params, timeout=10)
        res_search.raise_for_status()
        search_data = res_search.json()

        if not search_data.get("docs"):
            return {}  # Book not found

        search_result = search_data["docs"][0]
        edition_key = search_result.get("cover_edition_key")

        # Populate details we can get from the search
        if "first_publish_year" in search_result:
            details["publish_year"] = search_result["first_publish_year"]
        if "number_of_pages_median" in search_result:
            details["page_count"] = int(search_result["number_of_pages_median"])

        if edition_key:
            edition_url = f"https://openlibrary.org/books/{edition_key}.json"
            res_edition = session.get(edition_url, timeout=5)

            if res_edition.status_code == 200:
                edition_data = res_edition.json()
                if pubs := edition_data.get("publishers"):
                    details["publisher_name"] = pubs[0]  # Return the first publisher

    except requests.RequestException as e:
        logger.warning(f"API Error for '{title}': {e}")
        return {}

    return details


def _extract_edition_data(edition_data, book_details):
    """Extract page count, publisher, publish year, and ISBN from an OL edition response."""
    if pub_date := edition_data.get("publish_date"):
        if match := re.search(r"\d{4}", str(pub_date)):
            book_details["publish_year"] = int(match.group())
    if pages := edition_data.get("number_of_pages"):
        book_details["page_count"] = int(pages)
    if pubs := edition_data.get("publishers"):
        book_details["publisher"] = pubs[0]
    if isbns_13 := edition_data.get("isbn_13"):
        book_details["isbn_13"] = isbns_13[0]
    elif isbns_10 := edition_data.get("isbn_10"):
        book_details["isbn_13"] = isbns_10[0]


def _fetch_work_genres(work_key, book_title, session, book_details, slow_down=False):
    """Fetch genres from an OL work endpoint. Returns number of API calls made."""
    work_url = f"https://openlibrary.org{work_key}.json"
    work_response = session.get(work_url, timeout=5)
    if slow_down:
        _throttle()
    if work_response.status_code == 200:
        work_data = work_response.json()
        raw_subjects = work_data.get("subjects", [])
        logger.debug(f"Raw Subjects from API for '{book_title}': {raw_subjects}")
        final_genres = list(_clean_and_canonicalize_genres(raw_subjects))
        logger.debug(f"Canonicalized Genres for '{book_title}': {final_genres}")
        book_details["genres"] = final_genres
    return 1


def _fetch_from_open_library(book, session, slow_down=False):
    """
    Fetches metadata from Open Library. Uses direct ISBN endpoint when available
    (skips search), then work endpoint for genres, and edition endpoint only if
    the book is missing page count/publisher/year data.
    """
    logger.debug(f"Querying Open Library for '{book.title}'")

    book_details = {
        "genres": [],
        "publish_year": None,
        "publisher": None,
        "page_count": None,
        "isbn_13": None,
        "cover_id": None,
    }
    api_calls = 0

    try:
        # Fast path: direct ISBN lookup (skips search entirely)
        if book.isbn13:
            isbn_url = f"https://openlibrary.org/isbn/{book.isbn13}.json"
            res = session.get(isbn_url, timeout=5)
            api_calls += 1
            if slow_down:
                _throttle()

            if res.status_code == 200:
                edition_data = res.json()
                _extract_edition_data(edition_data, book_details)

                # Get cover ID from the edition
                if covers := edition_data.get("covers"):
                    book_details["cover_id"] = covers[0]

                # Follow work key for genres
                if works := edition_data.get("works"):
                    work_key = works[0].get("key")
                    if work_key:
                        api_calls += _fetch_work_genres(work_key, book.title, session, book_details, slow_down)

                track_external_api_call("open_library", book.pk, book.title, "success")
                return book_details, api_calls
            # ISBN lookup failed (404 etc) — fall through to search

        # Fallback: search by title+author
        search_url = "https://openlibrary.org/search.json"
        search_params = {"title": _clean_title_for_api(book.title), "author": book.author.name}
        res = session.get(search_url, params=search_params, timeout=10)
        api_calls += 1
        if slow_down:
            _throttle()

        res.raise_for_status()
        search_data = res.json()

        if not search_data.get("docs"):
            logger.debug(f"Open Library: Not found in search for '{book.title}'")
            track_external_api_call("open_library", book.pk, book.title, "not_found")
            return {}, api_calls

        search_result = search_data["docs"][0]
        work_key = search_result.get("key")
        edition_key = search_result.get("cover_edition_key")
        book_details["cover_id"] = search_result.get("cover_i")

        # Fetch genres from work endpoint
        if work_key:
            api_calls += _fetch_work_genres(work_key, book.title, session, book_details, slow_down)

        # Skip edition endpoint if book already has all edition data
        if book.page_count and book.publisher and book.publish_year and book.isbn13:
            logger.debug(f"Skipping OL edition for '{book.title}' — already has page/publisher/year/isbn data")
        elif edition_key:
            edition_url = f"https://openlibrary.org/books/{edition_key}.json"
            edition_response = session.get(edition_url, timeout=5)
            api_calls += 1
            if slow_down:
                _throttle()
            if edition_response.status_code == 200:
                _extract_edition_data(edition_response.json(), book_details)

        track_external_api_call("open_library", book.pk, book.title, "success")
        return book_details, api_calls

    except requests.RequestException as e:
        logger.error(f"Open Library API Error for '{book.title}': {e}")
        status_code = getattr(e.response, "status_code", None) if hasattr(e, "response") else None
        track_external_api_call("open_library", book.pk, book.title, "error", status_code=status_code, error_message=str(e))
        return {}, api_calls or 1


def _fetch_ratings_and_categories_from_google_books(book, session, slow_down=False):
    """
    Fetches ratings AND categories (genres) from Google Books.
    Google Books categories are often more accurate than Open Library subjects.
    Returns a dictionary of data and the number of API calls made (always 1 or 0).
    """
    if not GOOGLE_BOOKS_API_KEY:
        return {}, 0  # No API key, so no call is made

    logger.debug(f"Querying Google Books for ratings and categories for '{book.title}'")
    if book.isbn13:
        query = f"isbn:{book.isbn13}"
    else:
        title_q = requests.utils.quote(book.title)
        author_q = requests.utils.quote(book.author.name)
        query = f"intitle:{title_q}+inauthor:{author_q}"

    url = f"https://www.googleapis.com/books/v1/volumes?q={query}&key={GOOGLE_BOOKS_API_KEY}"

    try:
        res = session.get(url, timeout=10)

        if slow_down:
            _throttle()

        res.raise_for_status()
        data = res.json()

        if data.get("totalItems", 0) == 0:
            logger.debug(f"Google Books: Not found for '{book.title}'")
            track_external_api_call("google_books", book.pk, book.title, "not_found")
            return {}, 1

        volume_info = data["items"][0].get("volumeInfo", {})

        result = {
            "ratings_count": volume_info.get("ratingsCount"),
            "average_rating": volume_info.get("averageRating"),
        }

        # Capture thumbnail URL for cover
        image_links = volume_info.get("imageLinks", {})
        if thumbnail := image_links.get("thumbnail"):
            result["thumbnail_url"] = thumbnail.replace("http://", "https://")

        # Also fetch categories (Google Books' genre equivalent)
        if categories := volume_info.get("categories"):
            # Google Books categories are often prefixed with something like "Fiction / Literary"
            # We want to canonicalize these
            logger.debug(f"Google Books categories for '{book.title}': {categories}")

            # Filter out generic "Fiction" category - not useful for genre classification
            filtered_categories = [cat for cat in categories if cat.lower() not in ["fiction", "general"]]

            if filtered_categories:
                result["categories"] = filtered_categories
                logger.debug(f"Filtered Google Books categories: {filtered_categories}")
            else:
                logger.debug(f"All Google Books categories filtered out (too generic)")

        track_external_api_call("google_books", book.pk, book.title, "success")
        return result, 1

    except requests.RequestException as e:
        logger.error(f"Google Books API Error for '{book.title}': {e}")
        status_code = getattr(e.response, "status_code", None) if hasattr(e, "response") else None
        track_external_api_call("google_books", book.pk, book.title, "error", status_code=status_code, error_message=str(e))
        return {}, 1


def enrich_book_from_apis(book, session, slow_down=False):
    """
    The main public function to enrich a single Book object.
    It orchestrates the calls to the different APIs, but only if data is missing.
    Returns the updated book object and the total number of API calls made.
    """
    ol_api_calls = 0
    gb_api_calls = 0
    gb_data = {}  # May not be populated if GB enrichment already ran
    is_updated = False

    # Always refresh genres from Open Library even if other data exists
    ol_data, calls_made = _fetch_from_open_library(book, session, slow_down)
    ol_api_calls += calls_made

    if ol_data:
        if not book.isbn13 and ol_data.get("isbn_13"):
            book.isbn13 = ol_data["isbn_13"]
            is_updated = True
        if not book.page_count and ol_data.get("page_count"):
            book.page_count = ol_data["page_count"]
            is_updated = True
        if not book.publish_year and ol_data.get("publish_year"):
            book.publish_year = ol_data["publish_year"]
            is_updated = True

        if publisher_name := ol_data.get("publisher"):
            if not book.publisher:
                publisher_obj, _ = Publisher.objects.get_or_create(
                    normalized_name=Author._normalize(publisher_name), defaults={"name": publisher_name}
                )
                book.publisher = publisher_obj
                is_updated = True

    # Open Library genres are held back and merged with Google Books below —
    # neither source replaces the other any more.
    ol_genres = set(ol_data.get("genres") or []) if ol_data else set()

    # Google Books: fetched once per book (guarded by google_books_last_checked)
    # for ratings + categories. Its categories carry higher confidence than OL
    # subjects, so they lead the merge below.
    gb_genres = set()
    if book.google_books_last_checked is None:
        gb_data, calls_made = _fetch_ratings_and_categories_from_google_books(book, session, slow_down)
        gb_api_calls += calls_made

        if gb_data:
            if gb_data.get("ratings_count") is not None:
                book.google_books_ratings_count = gb_data["ratings_count"]
                is_updated = True
            if gb_data.get("average_rating") is not None:
                book.google_books_average_rating = gb_data["average_rating"]
                is_updated = True

            if google_genres := gb_data.get("categories"):
                gb_genres = set(_canonicalize_google_books_categories(google_genres))

        # Mark as checked *after* the API call is attempted.
        book.google_books_last_checked = timezone.now()
        is_updated = True  # Always true if we ran the check

    # Merge both sources: Google Books canonical genres first (higher
    # confidence), Open Library supplements. The combined set is priority-sorted
    # so the most specific genres survive the cap regardless of source.
    if combined_genres := gb_genres | ol_genres:
        logger.debug(f"Merged genres for '{book.title}': GB={sorted(gb_genres)}, OL={sorted(ol_genres)}")

        # Sort genres by priority (most specific first)
        prioritized_genres = sorted(
            combined_genres, key=lambda g: GENRE_PRIORITY.index(g) if g in GENRE_PRIORITY else 999
        )

        # Take top 5-6 genres (aim for 5, allow up to 6 if they're all highly specific)
        if len(prioritized_genres) <= 6:
            genres_to_add_limited = prioritized_genres
        elif prioritized_genres[5] in [
            "fantasy",
            "science fiction",
            "thriller",
            "horror",
            "historical fiction",
            "romance",
        ]:
            # If we have 6+ specific fiction genres, take all 6
            genres_to_add_limited = prioritized_genres[:6]
        else:
            # Otherwise, take top 5
            genres_to_add_limited = prioritized_genres[:5]

        # Always clear and replace existing genres with fresh API data.
        # No need to save here - ManyToMany changes are persisted immediately.
        book.genres.clear()
        genre_objs = [Genre.objects.get_or_create(name=g_name)[0] for g_name in genres_to_add_limited]
        book.genres.add(*genre_objs)
        is_updated = True
        logger.debug(
            f"Added {len(genres_to_add_limited)} genres (limited from {len(combined_genres)}): {genres_to_add_limited}"
        )

    # --- Set cover_url from best available source (only if not already set) ---
    if not book.cover_url:
        new_cover_url = None
        if ol_data.get("cover_id"):
            new_cover_url = cover_url_from_olid(ol_data["cover_id"])
        elif book.isbn13:
            new_cover_url = cover_url_from_isbn(book.isbn13)

        if not new_cover_url and gb_data.get("thumbnail_url"):
            new_cover_url = gb_data["thumbnail_url"]

        if new_cover_url:
            book.cover_url = new_cover_url
            is_updated = True

    if is_updated:
        try:
            book.save()
            logger.info(f"Successfully enriched and saved '{book.title}'")
        except IntegrityError as e:
            logger.warning(
                f"Could not save '{book.title}'. An integrity error occurred (e.g., duplicate ISBN). Error: {e}"
            )
    else:
        logger.debug(f"No new data found to update for '{book.title}'")

    # Always refresh from DB to get the current state of genres
    book.refresh_from_db()

    return book, ol_api_calls, gb_api_calls
