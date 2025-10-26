import os
import logging

import google.generativeai as genai
from celery import shared_task
from celery.result import AsyncResult
from django.contrib.auth.models import User
from django.core.cache import cache
from dotenv import load_dotenv
from collections import Counter

from .dna_constants import (
    EXCLUDED_GENRES,
)
from .services.dna_analyser import calculate_full_dna, _save_dna_to_profile
import requests
from django.utils import timezone

from .services.author_service import check_author_mainstream_status
from .models import Author

load_dotenv()

logger = logging.getLogger(__name__)

api_key = os.getenv("GEMINI_API_KEY")

if api_key:
    genai.configure(api_key=api_key)
else:
    logger.warning("GEMINI_API_KEY environment variable not found. Vibe generation will be disabled.")


@shared_task
def check_author_mainstream_status_task(author_id: int):
    try:
        author = Author.objects.get(pk=author_id)
        logger.info(f"Running mainstream status check for new author: {author.name}")

        with requests.Session() as session:
            headers = {"User-Agent": "BibliotypeApp/1.0 (contact@yourdomain.com)"}
            session.headers.update(headers)
            status_data = check_author_mainstream_status(author.name, session)

            if status_data["error"]:
                logger.warning(f"API Error for {author.name}: {status_data['error']}")
            else:
                if author.is_mainstream != status_data["is_mainstream"]:
                    author.is_mainstream = status_data["is_mainstream"]
                    logger.info(
                        f"Status updated to: {author.is_mainstream}. Reason: {status_data.get('reason', 'N/A')}"
                    )

                author.mainstream_last_checked = timezone.now()
                author.save()

    except Author.DoesNotExist:
        logger.error(f"Author Status Task Error: Author with ID {author_id} not found")
    except Exception as e:
        logger.error(
            f"Critical error in check_author_mainstream_status_task for author_id {author_id}: {e}", exc_info=True
        )
        raise


@shared_task(bind=True, max_retries=5)
def claim_anonymous_dna_task(self, user_id: int, task_id: str):
    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        logger.error(f"User with id {user_id} not found. Cannot claim task")
        return

    cached_dna = cache.get(f"dna_result_{task_id}")

    if cached_dna:
        logger.info(f"Found cached DNA for task {task_id}. Saving to user {user_id}")
        _save_dna_to_profile(user.userprofile, cached_dna)
        user.userprofile.pending_dna_task_id = None
        user.userprofile.save()
        logger.info(f"Successfully claimed and saved DNA for user {user_id} from task {task_id}")
        return

    result = AsyncResult(task_id)

    if result.ready():
        if result.successful():
            dna_data = result.get()
            _save_dna_to_profile(user.userprofile, dna_data)
            user.userprofile.pending_dna_task_id = None
            user.userprofile.save()
            logger.info(f"Successfully claimed and saved DNA for user {user_id} from task {task_id}")
        else:
            logger.error(f"Task {task_id} failed. Cannot claim DNA for user {user_id}")
            user.userprofile.pending_dna_task_id = None
            user.userprofile.save()
    else:
        logger.info(f"Task {task_id} not ready yet. Retrying claim for user {user_id} in 10s")
        raise self.retry(countdown=10, exc=Exception(f"Task {task_id} not ready"))


def normalize_and_filter_genres(subjects):
    """
    Cleans the raw subject list from the API, using the master EXCLUDED_GENRES set.
    """
    plausible_genres = []
    for s in subjects:
        s_lower = s.lower().strip()
        # Check against the imported exclusion set
        if s_lower in EXCLUDED_GENRES:
            continue
        # Check for junk patterns (e.g., call numbers, NYT lists)
        if "ps35" in s_lower or "nyt:" in s_lower or "b485" in s_lower:
            continue
        # Filter out overly long or non-genre-like subjects
        if len(s.split()) < 4 and "history" not in s_lower and "accessible" not in s_lower:
            plausible_genres.append(s_lower)

    return plausible_genres[:5]


def analyze_and_print_genres(all_raw_genres, canonical_map):
    """
    A helper function to analyze and log the frequency of raw genres,
    separating them into unmapped and already-mapped categories.
    """
    logger.info("=" * 50)
    logger.info("RUNNING GENRE ANALYSIS")
    logger.info("=" * 50)

    if not all_raw_genres:
        logger.info("No genres were found to analyze.")
        return

    raw_genre_counts = Counter(all_raw_genres)
    unmapped_genres = {}

    for genre, count in raw_genre_counts.items():
        if genre not in canonical_map:
            unmapped_genres[genre] = count

    # Sort the unmapped genres by frequency (most common first)
    sorted_unmapped = sorted(unmapped_genres.items(), key=lambda item: item[1], reverse=True)

    logger.info(f"Found {len(raw_genre_counts)} unique raw genre strings in total")
    logger.info(f"Of those, {len(unmapped_genres)} are currently UNMAPPED")

    logger.info("--- UNMAPPED GENRES (Most Common First) ---")

    if not sorted_unmapped:
        logger.info("Great news! All genres are already mapped!")
    else:
        for genre, count in sorted_unmapped:
            logger.info(f"  - '{genre}' (appears {count} times)")

    logger.info("=" * 50)


@shared_task(bind=True)
def generate_reading_dna_task(self, csv_file_content: str, user_id: int | None, session_key: str = None):
    logger.info("Running the latest (refactored) version of the Celery task")
    user = None
    try:
        if user_id is not None:
            user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        logger.error(f"Could not run task. User with id {user_id} not found")
        raise

    try:
        result_data = calculate_full_dna(csv_file_content, user, session_key)

        if not user:
            if self.request.id:
                cache.set(f"dna_result_{self.request.id}", result_data, timeout=3600)
                logger.info(f"DNA result for task {self.request.id} saved to cache")
            return result_data
        else:
            return result_data

    except Exception as e:
        logger.error(f"Task failed due to an error in the analysis engine: {e}", exc_info=True)
        raise


@shared_task
def anonymize_expired_sessions_task():
    """Periodic task to convert expired anonymous sessions to anonymized profiles"""
    from .services.anonymization_service import batch_anonymize_expired_sessions
    
    count = batch_anonymize_expired_sessions()
    logger.info(f"Anonymized {count} expired sessions")
    return count
