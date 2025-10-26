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

GOOGLE_BOOKS_API_KEY = os.getenv("GOOGLE_BOOKS_API_KEY")


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

    # Sort aliases by length (longest first) to match most specific terms first
    # This prevents "science" from matching before "science fiction"
    sorted_aliases = sorted(CANONICAL_GENRE_MAP.keys(), key=len, reverse=True)

    for subject in subjects:
        s_lower = subject.lower().strip()

        # Skip if this entire subject is in our exclusion list
        if s_lower in EXCLUDED_GENRES:
            continue

        matched = False
        for alias in sorted_aliases:
            # Use word boundaries to ensure we match whole phrases
            # This helps avoid matching partial words (e.g., "science" in "social science")
            pattern = r"\b" + re.escape(alias) + r"\b"
            
            if re.search(pattern, s_lower):
                canonical_name = CANONICAL_GENRE_MAP[alias]
                
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
    They come formatted like: "Fiction / Literary" or "History / Ancient / Rome"
    """
    if not categories:
        return set()

    canonical_genres = set()
    sorted_aliases = sorted(CANONICAL_GENRE_MAP.keys(), key=len, reverse=True)

    for category in categories:
        # Google Books categories often have separators like "Fiction / Literary Fiction"
        # We want to check each part
        parts = category.split('/')
        
        for part in parts:
            part_lower = part.strip().lower()
            
            # Skip if excluded
            if part_lower in EXCLUDED_GENRES:
                continue
            
            matched = False
            for alias in sorted_aliases:
                pattern = r"\b" + re.escape(alias) + r"\b"
                
                if re.search(pattern, part_lower):
                    canonical_name = CANONICAL_GENRE_MAP[alias]
                    
                    if canonical_name not in EXCLUDED_GENRES:
                        canonical_genres.add(canonical_name)
                        matched = True
                        logger.debug(f"Matched Google Books category '{part}' to genre '{canonical_name}'")
                        break
            
            # Debug unmatched parts
            if not matched:
                logger.debug(f"Could not match Google Books category part: '{part.strip()}'")

    return canonical_genres


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
            time.sleep(1.2)

        res.raise_for_status()
        data = res.json()

        if data.get("totalItems", 0) == 0:
            logger.debug(f"Google Books: Not found for '{book.title}'")
            return {}, 1

        volume_info = data["items"][0].get("volumeInfo", {})
        
        result = {
            "ratings_count": volume_info.get("ratingsCount"),
            "average_rating": volume_info.get("averageRating"),
        }
        
        # Also fetch categories (Google Books' genre equivalent)
        if categories := volume_info.get("categories"):
            # Google Books categories are often prefixed with something like "Fiction / Literary"
            # We want to canonicalize these
            logger.debug(f"Google Books categories for '{book.title}': {categories}")
            
            # Filter out generic "Fiction" category - not useful for genre classification
            filtered_categories = [cat for cat in categories if cat.lower() not in ['fiction', 'general']]
            
            if filtered_categories:
                result["categories"] = filtered_categories
                logger.debug(f"Filtered Google Books categories: {filtered_categories}")
            else:
                logger.debug(f"All Google Books categories filtered out (too generic)")
        
        return result, 1

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

    # --- Step 1: Always fetch genres from Open Library for re-enrichment ---
    # We want to refresh genres even if other data exists
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
            # Always clear and replace existing genres with fresh API data
            book.genres.clear()
            # No need to save here - ManyToMany changes are persisted immediately
            
            logger.debug(f"Adding genres for '{book.title}': {new_genres}")
            
            # Limit to top 5-6 genres max - prioritize specific over generic
            genre_priority = [
                # Fiction genres (most specific to generic)
                'fantasy', 'science fiction', 'thriller', 'horror', 'historical fiction', 
                'romance', 'humorous fiction', 'young adult', 'short stories',
                # Non-fiction genres  
                'biography', 'philosophy', 'psychology', 'history', 'social science', 
                'non-fiction', 'science', 'nature', 'art & music', 'travel',
                # Generic/classics
                'classics', 'plays & drama', 'children\'s literature'
            ]
            
            # Sort genres by priority (most specific first)
            prioritized_genres = sorted(new_genres, key=lambda g: genre_priority.index(g) if g in genre_priority else 999)
            
            # Take top 5-6 genres (aim for 5, allow up to 6 if they're all highly specific)
            if len(prioritized_genres) <= 6:
                genres_to_add_limited = prioritized_genres
            elif len(prioritized_genres) >= 6 and prioritized_genres[5] in ['fantasy', 'science fiction', 'thriller', 'horror', 'historical fiction', 'romance']:
                # If we have 6+ specific fiction genres, take all 6
                genres_to_add_limited = prioritized_genres[:6]
            else:
                # Otherwise, take top 5
                genres_to_add_limited = prioritized_genres[:5]
            
            # Add the limited genres
            if genres_to_add_limited:
                genre_objs = [Genre.objects.get_or_create(name=g_name)[0] for g_name in genres_to_add_limited]
                book.genres.add(*genre_objs)
                is_updated = True
                logger.debug(f"Added {len(genres_to_add_limited)} genres (limited from {len(new_genres)}): {genres_to_add_limited}")
            else:
                logger.debug(f"No genres to add after limiting")

    # --- Step 2: Get ratings AND categories (genres) from Google Books if needed ---
    # Google Books often has more accurate genres than Open Library
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
            
            # Use Google Books categories as primary source for genres if available
            if google_genres := gb_data.get("categories"):
                canonical_google_genres = list(_canonicalize_google_books_categories(google_genres))
                
                # Only use Google Books genres if we got good results
                if canonical_google_genres:
                    logger.debug(f"Using Google Books categories for '{book.title}': {canonical_google_genres}")
                    
                    # Clear ALL existing genres and replace with Google Books (more accurate)
                    book.genres.clear()
                    
                    genre_priority = [
                        'fantasy', 'science fiction', 'thriller', 'horror', 'historical fiction', 
                        'romance', 'humorous fiction', 'young adult', 'short stories',
                        'biography', 'philosophy', 'psychology', 'history', 'social science', 
                        'non-fiction', 'science', 'nature', 'art & music', 'travel',
                        'classics', 'plays & drama', "children's literature"
                    ]
                    prioritized_genres = sorted(canonical_google_genres, key=lambda g: genre_priority.index(g) if g in genre_priority else 999)
                    
                    # Take top 5 (Google Books categories are usually more accurate)
                    genres_to_add_limited = prioritized_genres[:5]
                    
                    if genres_to_add_limited:
                        genre_objs = [Genre.objects.get_or_create(name=g_name)[0] for g_name in genres_to_add_limited]
                        book.genres.add(*genre_objs)
                        is_updated = True
                        logger.debug(f"Added Google Books genres for '{book.title}': {genres_to_add_limited}")
        
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
    
    # Always refresh from DB to get the current state of genres
    book.refresh_from_db()

    return book, ol_api_calls, gb_api_calls
