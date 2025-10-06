import os


import google.generativeai as genai
from celery import shared_task
from celery.exceptions import Ignore
from celery.result import AsyncResult
from django.contrib.auth.models import User
from django.core.cache import cache
from dotenv import load_dotenv

from .dna_constants import (
    EXCLUDED_GENRES,
)
from .services.dna_analyser import calculate_full_dna, _save_dna_to_profile
import requests
from django.utils import timezone

# Make sure to import your service and model
from .services.author_service import check_author_mainstream_status
from .models import Author, UserProfile  # Add Author here

# All of your helper functions can live inside this file as well
load_dotenv()

api_key = os.getenv("GEMINI_API_KEY")

if api_key:
    genai.configure(api_key=api_key)
else:
    print("⚠️ WARNING: GEMINI_API_KEY environment variable not found. Vibe generation will be disabled.")


@shared_task
def check_author_mainstream_status_task(author_id: int):
    """
    A dedicated background task to check and update the mainstream status
    for a single author.
    """
    try:
        author = Author.objects.get(pk=author_id)
        print(f"-> Running mainstream status check for new author: {author.name}...")

        with requests.Session() as session:
            headers = {"User-Agent": "BibliotypeApp/1.0 (contact@yourdomain.com)"}
            session.headers.update(headers)
            status_data = check_author_mainstream_status(author.name, session)

            if status_data["error"]:
                print(f"    -> API Error for {author.name}: {status_data['error']}")
            else:
                if author.is_mainstream != status_data["is_mainstream"]:
                    author.is_mainstream = status_data["is_mainstream"]
                    print(
                        f"    -> Status updated to: {author.is_mainstream}. Reason: {status_data.get('reason', 'N/A')}"
                    )

                # Always update the last_checked timestamp
                author.mainstream_last_checked = timezone.now()
                author.save()

    except Author.DoesNotExist:
        print(f"❌ Author Status Task Error: Author with ID {author_id} not found.")
    except Exception as e:
        print(f"❌❌❌ Critical error in check_author_mainstream_status_task for author_id {author_id}: {e}")
        raise  # Re-raise to let Celery know the task failed


@shared_task(bind=True)
def claim_anonymous_dna_task(self, user_id: int, task_id: str):
    """
    Checks if an anonymous DNA task is complete. If so, saves the result.
    If not, retries itself until the task is complete. This is non-blocking.
    """
    try:
        # Get the result object for the original DNA generation task
        original_task_result = AsyncResult(task_id)

        if original_task_result.ready():
            # The task is finished! We can now safely get the result.
            print(f"Task {task_id} is ready. Claiming for user {user_id}.")

            if original_task_result.successful():
                dna_data = original_task_result.get()
                user = User.objects.get(pk=user_id)
                profile = user.userprofile

                # Save the DNA data using your helper
                _save_dna_to_profile(profile, dna_data)

                # Clear the pending task ID from the profile
                profile.pending_dna_task_id = None
                profile.save()

                print(f"✅ Successfully claimed and saved DNA for user {user_id} from task {task_id}.")
            else:
                # The original task failed
                print(f"❌ Claiming Error: Original task {task_id} failed.")
        else:
            # The task is not ready. Retry this claiming task in 10 seconds.
            print(f"Task {task_id} not ready yet. Retrying claim for user {user_id} in 10s...")
            # This tells Celery to re-queue this task. The worker is now free to do other work.
            raise self.retry(countdown=10, max_retries=120)  # Retry every 10s for up to 20 mins

    except User.DoesNotExist:
        print(f"❌ Claiming Error: User with ID {user_id} not found. Stopping retries.")
        raise Ignore()  # Stop retrying if the user doesn't exist
    except Exception as e:
        print(f"❌❌❌ An error occurred while claiming task {task_id} for user {user_id}: {e}")
        # Retry on other unexpected errors
        raise self.retry(countdown=60, max_retries=5)


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
    A helper function to analyze and print the frequency of raw genres,
    separating them into unmapped and already-mapped categories.
    """
    print("\n" + "=" * 50)
    print("🔬 RUNNING GENRE ANALYSIS 🔬")
    print("=" * 50)

    if not all_raw_genres:
        print("No genres were found to analyze.")
        return

    raw_genre_counts = Counter(all_raw_genres)
    unmapped_genres = {}

    for genre, count in raw_genre_counts.items():
        if genre not in canonical_map:
            unmapped_genres[genre] = count

    # Sort the unmapped genres by frequency (most common first)
    sorted_unmapped = sorted(unmapped_genres.items(), key=lambda item: item[1], reverse=True)

    print(f"\nFound {len(raw_genre_counts)} unique raw genre strings in total.")
    print(f"Of those, {len(unmapped_genres)} are currently UNMAPPED.")

    print("\n--- UNMAPPED GENRES (Most Common First) ---")

    if not sorted_unmapped:
        print("✅ Great news! All genres are already mapped!")
    else:
        for genre, count in sorted_unmapped:
            print(f"  - '{genre}' (appears {count} times)")

    print("\n" + "=" * 50 + "\n")


@shared_task(bind=True)
def generate_reading_dna_task(self, csv_file_content: str, user_id: int | None):
    """
    Celery task wrapper for generating Reading DNA.
    It fetches the user and calls the main analysis engine.
    """
    print("✅✅✅ RUNNING THE LATEST (REFACTORED) VERSION OF THE CELERY TASK ✅✅✅")
    user = None
    try:
        if user_id is not None:
            user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        print(f"❌ Error: Could not run task. User with id {user_id} not found.")
        raise  # Fail the task if the user is invalid

    # Call the main analysis function from our service
    # The `calculate_full_dna` function now contains all the heavy logic.
    # We re-wrap it in a try/except block to handle task-specific outcomes.
    try:
        result_data = calculate_full_dna(csv_file_content, user)

        if not user:
            # For anonymous users, the result_data is the DNA dict.
            # We must save it to the cache for the 'claim' task or the polling view.
            if self.request.id:
                cache.set(f"dna_result:{self.request.id}", result_data, timeout=3600)
                print(f"🧬 DNA result for task {self.request.id} saved to cache.")
            return result_data
        else:
            # For logged-in users, the data is already saved. The function returns a success message.
            return result_data

    except Exception as e:
        # If calculate_full_dna raises an error, this block catches it
        # and ensures the Celery task is marked as FAILED.
        print(f"❌ Task failed due to an error in the analysis engine: {e}")
        raise  # Re-raise to mark the task as failed
