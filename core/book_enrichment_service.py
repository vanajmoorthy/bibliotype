import logging
import os
import re
import time

import requests
from django.db import IntegrityError
from django.utils import timezone

from .dna_constants import CANONICAL_GENRE_MAP, EXCLUDED_GENRES
from .models import Author, Book, Genre, Publisher

logger = logging.getLogger(__name__)

# A central place for the API key for cleanliness
GOOGLE_BOOKS_API_KEY = os.getenv("GOOGLE_BOOKS_API_KEY")


def _clean_title_for_api(title):
    """A less aggressive cleaner specifically for API queries."""
    # 1. Remove content in parentheses/brackets
    clean_title = re.sub(r"[\(\[].*?[\)\]]", "", title)
    # 2. Remove subtitles after a colon
    clean_title = clean_title.split(":")[0]
    return clean_title.strip()


def _clean_and_canonicalize_genres(subjects):
    """
    Finds the best, most specific genre match for each subject using
    word boundary regular expressions for precision.
    """
    if not subjects:
        return set()

    canonical_genres = set()

    # Sort aliases by length (desc) to match "science fiction" before "fiction"
    sorted_aliases = sorted(CANONICAL_GENRE_MAP.keys(), key=len, reverse=True)

    for subject in subjects:
        s_lower = subject.lower().strip()

        # Skip if the whole subject is in the exclusion list
        if s_lower in EXCLUDED_GENRES:
            continue

        # --- THIS IS THE CRITICAL CHANGE ---
        # Find all aliases that match as whole words/phrases within the subject
        for alias in sorted_aliases:
            # Create a regex pattern with word boundaries.
            # re.escape handles special characters in aliases like "self-help".
            pattern = r"\b" + re.escape(alias) + r"\b"
            if re.search(pattern, s_lower):
                canonical_name = CANONICAL_GENRE_MAP[alias]
                # Final check to ensure the canonical name itself isn't excluded
                if canonical_name not in EXCLUDED_GENRES:
                    canonical_genres.add(canonical_name)
                    # We break here because we found the most specific match first
                    # due to the list being sorted by length.
                    break

    return canonical_genres


def get_book_details_for_seeder(title: str, author: str, session: requests.Session) -> dict:
    """
    A robust fetcher for the seed_books command that uses a two-step API call
    to reliably get the publisher.
    """
    details = {}
    try:
        # --- Step 1: Search for the book to get its keys ---
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

        # --- Step 2: Use the edition key to get reliable publisher data ---
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


def _fetch_from_open_library(book, session, slow_down=False):
    """
    Fetches metadata by first searching, then querying the specific Work and Edition
    endpoints, mirroring the original reliable logic.
    """
    logger.debug(f"Querying Open Library for '{book.title}'")

    # --- Step 1: Search for the book to get its keys ---
    search_url = "https://openlibrary.org/search.json"
    search_params = {}
    if book.isbn13:
        search_params["q"] = f"isbn:{book.isbn13}"
    else:
        search_params["title"] = _clean_title_for_api(book.title)
        search_params["author"] = book.author.name

    try:
        res = session.get(search_url, params=search_params, timeout=10)

        if slow_down:
            time.sleep(1.2)

        res.raise_for_status()
        search_data = res.json()

        if not search_data.get("docs"):
            logger.debug(f"Open Library: Not found in search for '{book.title}'")
            return {}, 1

        search_result = search_data["docs"][0]
        work_key = search_result.get("key")
        edition_key = search_result.get("cover_edition_key")

        # --- Step 2: Use the keys to get detailed data ---
        book_details = {"genres": [], "publish_year": None, "publisher": None, "page_count": None, "isbn_13": None}
        api_calls = 1  # We already made the search call

        # Fetch from the Work endpoint for subjects (genres)
        if work_key:
            work_url = f"https://openlibrary.org{work_key}.json"
            work_response = session.get(work_url, timeout=5)
            api_calls += 1
            if slow_down:
                time.sleep(1.2)

            if work_response.status_code == 200:
                work_data = work_response.json()
                raw_subjects = work_data.get("subjects", [])
                logger.debug(f"Raw Subjects from API for '{book.title}': {raw_subjects}")
                final_genres = list(_clean_and_canonicalize_genres(raw_subjects))
                logger.debug(f"Canonicalized Genres for '{book.title}': {final_genres}")

                book_details["genres"] = final_genres

        # Fetch from the Edition endpoint for page count, publisher, etc.
        if edition_key:
            edition_url = f"https://openlibrary.org/books/{edition_key}.json"
            edition_response = session.get(edition_url, timeout=5)
            api_calls += 1
            if slow_down:
                time.sleep(1.2)

            if edition_response.status_code == 200:
                edition_data = edition_response.json()

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
                    book_details["isbn_13"] = isbns_10[0]  # Save isbn_10 in the isbn_13 field for simplicity

        return book_details, api_calls

    except requests.RequestException as e:
        logger.error(f"Open Library API Error for '{book.title}': {e}")
        # Even if it fails, we count the initial attempt as one call
        return {}, 1


def _fetch_ratings_from_google_books(book, session, slow_down=False):
    """
    Fetches only ratingsCount and averageRating from Google Books.
    This is our secondary, specialized data source.
    Returns a dictionary of data and the number of API calls made (always 1 or 0).
    """
    if not GOOGLE_BOOKS_API_KEY:
        return {}, 0  # No API key, so no call is made

    logger.debug(f"Querying Google Books for ratings for '{book.title}'")
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
            time.sleep(1.2)

        res.raise_for_status()
        data = res.json()

        if data.get("totalItems", 0) == 0:
            logger.debug(f"Google Books: Not found for '{book.title}'")
            return {}, 1

        volume_info = data["items"][0].get("volumeInfo", {})
        return {
            "ratings_count": volume_info.get("ratingsCount"),
            "average_rating": volume_info.get("averageRating"),
        }, 1

    except requests.RequestException as e:
        logger.error(f"Google Books API Error for '{book.title}': {e}")
        return {}, 1


def enrich_book_from_apis(book, session, slow_down=False):
    """
    The main public function to enrich a single Book object.
    It orchestrates the calls to the different APIs, but only if data is missing.
    Returns the updated book object and the total number of API calls made.
    """
    ol_api_calls = 0
    gb_api_calls = 0
    is_updated = False

    # --- Step 1: Get general data from Open Library if needed ---
    # Only run if we are missing key data like publisher, page count, or genres.
    if not book.publisher or not book.page_count or not book.genres.exists():
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

            if new_genres := ol_data.get("genres"):
                current_genre_names = set(book.genres.values_list("name", flat=True))
                genres_to_add = [g_name for g_name in new_genres if g_name not in current_genre_names]
                if genres_to_add:
                    logger.debug(f"Adding new genres for '{book.title}': {genres_to_add}")
                    genre_objs = [Genre.objects.get_or_create(name=g_name)[0] for g_name in genres_to_add]
                    book.genres.add(*genre_objs)
                    is_updated = True

    # --- Step 2: Get ratings data from Google Books if needed ---
    # Only run if the book has never been checked with Google Books before.
    if book.google_books_last_checked is None:
        gb_data, calls_made = _fetch_ratings_from_google_books(book, session, slow_down)
        gb_api_calls += calls_made

        if gb_data:
            if gb_data.get("ratings_count") is not None:
                book.google_books_ratings_count = gb_data["ratings_count"]
                is_updated = True
            if gb_data.get("average_rating") is not None:
                book.google_books_average_rating = gb_data["average_rating"]
                is_updated = True
        
        # Mark as checked *after* the API call is attempted.
        book.google_books_last_checked = timezone.now()
        is_updated = True # Always true if we ran the check

    # --- Step 3: Finalize and save ---
    if is_updated:
        try:
            book.save()
            logger.info(f"Successfully enriched and saved '{book.title}'")
        except IntegrityError as e:
            logger.warning(f"Could not save '{book.title}'. An integrity error occurred (e.g., duplicate ISBN). Error: {e}")
    else:
        logger.debug(f"No new data found to update for '{book.title}'")

    return book, ol_api_calls, gb_api_calls
