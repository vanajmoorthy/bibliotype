import hashlib
import json
import os
import random
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from urllib.parse import quote_plus

import google.generativeai as genai
import pandas as pd
import requests
from celery import shared_task
from django.contrib.auth.models import User
from django.core.cache import cache
from django.db.models import F
from dotenv import load_dotenv
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from .dna_constants import (
    CANONICAL_GENRE_MAP,
    EXCLUDED_GENRES,
    GLOBAL_AVERAGES,
    MAJOR_PUBLISHERS,
    READER_TYPE_DESCRIPTIONS,
)
from .models import Author, Book, Genre, UserProfile
from .percentile_engine import (
    calculate_percentiles_from_aggregates,
    update_analytics_from_stats,
)

# All of your helper functions can live inside this file as well
load_dotenv()

api_key = os.getenv("GEMINI_API_KEY")

if api_key:
    genai.configure(api_key=api_key)
else:
    print("⚠️ WARNING: GEMINI_API_KEY environment variable not found. Vibe generation will be disabled.")


def create_vibe_prompt(dna: dict) -> str:
    """
    Creates a detailed, few-shot prompt for the Gemini API to generate reading vibes.
    """
    # Extract the most salient data points from the DNA to feed the LLM
    reader_type = dna.get("reader_type", "Eclectic Reader")
    top_genres = list(dna.get("top_genres", {}).keys())[:3]
    top_authors = list(dna.get("top_authors", {}).keys())[:2]
    avg_pub_year = dna.get("user_stats", {}).get("avg_publish_year", 2000)

    # Simple logic to determine the era
    era = "classic" if avg_pub_year < 1980 else "modern"

    prompt = f"""
You are a witty, poetic observer who can distill the "vibe" of a person's reading list into short, aesthetic phrases. You are not a robot; you are creative and a little quirky.

Your task is to generate 4 short, evocative, lowercase phrases that capture the feeling of this person's reading DNA.

**RULES:**
- Phrases must be short (2-6 words).
- All lowercase.
- No punctuation at the end of phrases.
- Do NOT describe the user's reading habits directly (e.g., "you read fantasy"). Instead, evoke the *feeling* of those habits.
- Output ONLY a valid JSON object with a single key "vibe_phrases" which is a list of 4 strings.

**User's Reading DNA:**
- Primary Reader Type: "{reader_type}"
- Top Genres: {', '.join(top_genres)}
- Favorite Authors: {', '.join(top_authors)}
- General Era: {era}

**Example of GOOD output for a Fantasy/Classic reader:**
{{
  "vibe_phrases": [
    "dusty maps and forgotten prophecies",
    "the scent of old paper",
    "a quiet corner in a grand library",
    "a story that echoes through ages"
  ]
}}

**Example of BAD output (Do NOT do this):**
{{
  "vibe_phrases": [
    "You enjoy reading fantasy books.",
    "Your favorite author is Brandon Sanderson.",
    "You read a lot of classics.",
    "Your vibe is nerdy."
  ]
}}

Now, generate the JSON for the provided User's Reading DNA.
"""
    return prompt


def generate_vibe_with_llm(dna: dict) -> list:
    """
    Uses the Gemini API to generate a creative "vibe" for the user's DNA.
    """
    if not api_key:
        print("⚠️ Vibe generation skipped because API key is not configured.")
        return ["vibe generation disabled", "please configure api key"]

    prompt = create_vibe_prompt(dna)

    try:
        model = genai.GenerativeModel("gemini-1.5-flash-latest")
        generation_config = genai.GenerationConfig(response_mime_type="application/json")
        response = model.generate_content(prompt, generation_config=generation_config)

        response_json = json.loads(response.text)
        vibe_phrases = response_json.get("vibe_phrases", [])

        if isinstance(vibe_phrases, list) and all(isinstance(p, str) for p in vibe_phrases):
            return vibe_phrases
        else:
            return ["error parsing vibe", "unexpected format received"]

    except json.JSONDecodeError:
        print(f"❌ LLM Error: Failed to decode JSON from response: {response.text}")
        return ["error generating vibe", "invalid json response"]
    except Exception as e:
        print(f"❌ LLM Error: An unexpected error occurred: {e}")
        return ["error generating vibe", "api call failed"]


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


def get_book_details_from_open_library(title, author, session):
    """
    Looks up a book's genres, publish year, publisher, AND page count by
    fetching from both the Work and Edition endpoints.
    Uses a cache to avoid repeated API calls.
    """
    LOGIC_VERSION = "v11_with_pages"  # <-- Updated version to ensure fresh cache
    cache_key = f"book_details:{LOGIC_VERSION}:{author}:{title}".lower().replace(" ", "_")

    cached_data = cache.get(cache_key)
    if cached_data is not None:
        print(f"   ✅ Found details for '{title}' in cache.")
        return cached_data

    print(f"   📚 Fetching details for: '{title}' by {author} (from API)")
    clean_title = re.split(r"[:(]", title)[0].strip()
    query_title = quote_plus(clean_title)
    query_author = quote_plus(author)
    search_url = f"https://openlibrary.org/search.json?title={query_title}&author={query_author}"

    # Default empty structure now includes page_count
    book_details = {
        "genres": [],
        "publish_year": None,
        "publisher": None,
        "page_count": None,  # <-- NEW FIELD
    }

    try:
        response = session.get(search_url, timeout=5)
        if response.status_code != 200:
            return book_details

        data = response.json()
        if not data.get("docs"):
            return book_details

        search_result = data["docs"][0]
        work_key = search_result.get("key")
        edition_key = search_result.get("cover_edition_key")

        if work_key:
            work_url = f"https://openlibrary.org{work_key}.json"
            work_response = session.get(work_url, timeout=5)
            if work_response.status_code == 200:
                work_data = work_response.json()
                subjects = work_data.get("subjects", [])
                book_details["genres"] = normalize_and_filter_genres(subjects)

        if edition_key:
            edition_url = f"https://openlibrary.org/books/{edition_key}.json"
            edition_response = session.get(edition_url, timeout=5)
            if edition_response.status_code == 200:
                edition_data = edition_response.json()

                publish_year_str = edition_data.get("publish_date", "")
                if publish_year_str:
                    match = re.search(r"\d{4}", publish_year_str)
                    if match:
                        try:
                            book_details["publish_year"] = int(match.group())
                        except ValueError:
                            pass

                # === NEW: EXTRACT PAGE COUNT ====================================
                # The API field is typically 'number_of_pages'.
                # We add a check to ensure it's a valid integer before storing.
                raw_page_count = edition_data.get("number_of_pages")
                if raw_page_count:
                    try:
                        book_details["page_count"] = int(raw_page_count)
                    except (ValueError, TypeError):
                        # If the value is not a clean integer, ignore it.
                        book_details["page_count"] = None
                # ================================================================

                book_details["publisher"] = edition_data.get("publishers", [None])[0]

        cache.set(cache_key, book_details, timeout=60 * 60 * 24 * 30)
        return book_details

    except requests.RequestException as e:
        print(f"    ❌ Request failed for '{title}': {str(e)}")
        return book_details


def assign_reader_type(read_df, enriched_data, all_genres):
    """
    Calculates scores for reader traits using the final, equitable bonus-based logic.
    """
    scores = Counter()
    total_books = len(read_df)
    if total_books == 0:
        return "Not enough data", Counter()

    # --- HEURISTICS & SCORE CALCULATION ---
    if "Date Read" in read_df.columns:
        books_per_year = read_df.dropna(subset=["Date Read"])["Date Read"].dt.year.value_counts().mean()
        if total_books > 75 and books_per_year > 40:
            scores["Rapacious Reader"] = 100

    if "Number of Pages" in read_df.columns:
        long_books = read_df[read_df["Number of Pages"] > 490].shape[0]
        short_books = read_df[read_df["Number of Pages"] < 200].shape[0]
        scores["Tome Tussler"] += long_books * 2
        scores["Novella Navigator"] += short_books

    # Use the CANONICAL_GENRE_MAP to group aliases into their canonical form
    mapped_genres = [CANONICAL_GENRE_MAP.get(g, g) for g in all_genres]
    genre_counts = Counter(mapped_genres)

    # These scores are now based on CLEAN, CANONICAL genre counts
    scores["Fantasy Fanatic"] += genre_counts.get("fantasy", 0) + genre_counts.get("science fiction", 0)
    scores["Non-Fiction Ninja"] += genre_counts.get("non-fiction", 0)
    scores["Philosophical Philomath"] += genre_counts.get("philosophy", 0)
    scores["Nature Nut Case"] += genre_counts.get("nature", 0)
    scores["Social Savant"] += genre_counts.get("social science", 0)
    scores["Self Help Scholar"] += genre_counts.get("self-help", 0)

    for index, book in read_df.iterrows():
        details = enriched_data.get(book["Title"])
        if not details:
            continue

        if details.get("publish_year"):
            if details["publish_year"] < 1970:
                scores["Classic Collector"] += 1
            elif details["publish_year"] > 2018:
                scores["Modern Maverick"] += 1

        publisher = details.get("publisher")

        if publisher:
            is_major = any(major.lower() in publisher.lower() for major in MAJOR_PUBLISHERS)
            if not is_major:
                # --- THIS IS THE CORRECTED LINE ---
                # It now correctly prints the publisher's name, which is available inside the loop.
                print(f"   ℹ️ Found non-major publisher: {publisher}")
                scores["Small Press Supporter"] += 1

    # --- THE DEFINITIVE FIX FOR EQUITY ---
    # Give a flat bonus for high variety instead of a runaway score.
    DIVERSITY_THRESHOLD = 10  # Number of UNIQUE CANONICAL genres to qualify
    DIVERSITY_BONUS = 15  # Flat score bonus for meeting the threshold

    # The number of unique keys in genre_counts is now much smaller and more accurate
    unique_canonical_genres = len(genre_counts)

    print(f"   ℹ️ Found {unique_canonical_genres} unique CANONICAL genres.")

    if unique_canonical_genres >= DIVERSITY_THRESHOLD:
        scores["Versatile Valedictorian"] += DIVERSITY_BONUS

    # --- Determine Winner ---
    if not scores or all(s == 0 for s in scores.values()):
        return "Eclectic Reader", scores

    if scores.get("Rapacious Reader", 0) > 0:
        return "Rapacious Reader", scores

    primary_type = scores.most_common(1)[0][0]
    return primary_type, scores


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


# THIS IS THE NEW CELERY TASK
@shared_task
def generate_reading_dna_task(csv_file_content: str, user_id: int | None):
    """
    The Celery task that wraps the original DNA generation logic.
    """
    print("✅✅✅ RUNNING THE LATEST VERSION OF THE CELERY TASK ✅✅✅")
    user = None  # Default user to None for the anonymous case

    try:
        # --- FIX: Conditionally fetch the user ---
        # Only try to get a user object if a user_id was actually passed.
        if user_id is not None:
            try:
                user = User.objects.get(pk=user_id)
            except User.DoesNotExist:
                # This is a critical error if an ID was passed but is invalid.
                print(f"❌ Error: Could not run task. User with id {user_id} not found.")
                # Stop execution if the user is invalid.
                raise
        else:
            print("👤 Processing request for an anonymous user.")

        try:
            df = pd.read_csv(StringIO(csv_file_content))
        except Exception as e:
            raise ValueError(f"Could not parse CSV file. Error: {e}")

        read_df = df[df["Exclusive Shelf"] == "read"].copy()

        if read_df.empty:
            raise ValueError("No books found on the 'read' shelf in your CSV.")

        # Create the hash from the user's book list
        book_fingerprint_list = sorted([f"{row['Title']}{row['Author']}" for _, row in read_df.iterrows()])
        fingerprint_string = "".join(book_fingerprint_list)
        new_data_hash = hashlib.sha256(fingerprint_string.encode()).hexdigest()

        print(f"📖 Found {len(read_df)} books marked as 'read' for statistical analysis.")
        # --- Data cleaning ---
        for temp_df in [df, read_df]:
            temp_df["My Rating"] = pd.to_numeric(temp_df["My Rating"], errors="coerce")
            temp_df["Number of Pages"] = pd.to_numeric(temp_df["Number of Pages"], errors="coerce")
            temp_df["Average Rating"] = pd.to_numeric(temp_df["Average Rating"], errors="coerce")
            temp_df["Date Read"] = pd.to_datetime(temp_df["Date Read"], errors="coerce")

            if "Original Publication Year" in temp_df.columns:
                temp_df["Original Publication Year"] = pd.to_numeric(
                    temp_df["Original Publication Year"], errors="coerce"
                )
            else:
                temp_df["Original Publication Year"] = None
            temp_df.loc[:, "My Review"] = temp_df["My Review"].fillna("")

        # --- Phase 1 & 2: API and DB Sync ---
        print("🎭 Fetching book data from Open Library API (in parallel)...")

        with ThreadPoolExecutor(max_workers=10) as executor, requests.Session() as session:

            def fetch_book_details(book_row):
                return (book_row, get_book_details_from_open_library(book_row["Title"], book_row["Author"], session))

            api_results = list(executor.map(fetch_book_details, [row for _, row in read_df.iterrows()]))

        print("💾 Syncing book data with the database (serially)...")

        all_raw_genres, user_book_objects = [], []

        for original_row, api_details in api_results:
            author, _ = Author.objects.get_or_create(name=original_row["Author"])

            # === FIX IS HERE =========================================================
            # Get the raw values from the pandas row (which might be NaN)
            raw_page_count = original_row.get("Number of Pages")
            raw_avg_rating = original_row.get("Average Rating")

            # Convert NaN to None, otherwise cast to the correct type (int/float).
            # This ensures the data is clean before hitting the database.
            clean_page_count = int(raw_page_count) if pd.notna(raw_page_count) else None
            clean_avg_rating = float(raw_avg_rating) if pd.notna(raw_avg_rating) else None
            # =========================================================================

            book, _ = Book.objects.get_or_create(
                title=original_row["Title"],
                author=author,
                defaults={
                    # Use the cleaned variables here
                    "page_count": clean_page_count,
                    "average_rating": clean_avg_rating,
                    "publish_year": api_details.get("publish_year"),
                    "publisher": api_details.get("publisher"),
                },
            )

            Book.objects.filter(pk=book.pk).update(global_read_count=F("global_read_count") + 1)
            book.refresh_from_db()

            raw_genres_for_book = api_details.get("genres", [])

            # 1. Map raw genres to their canonical form.
            mapped_genres = [CANONICAL_GENRE_MAP.get(g) for g in raw_genres_for_book]

            # 2. Filter out any that didn't have a mapping (are None) and get unique values.
            final_canonical_genres = set(g for g in mapped_genres if g)

            # 3. Only save the clean, canonical genres to the database.
            if final_canonical_genres:
                genre_pks = [Genre.objects.get_or_create(name=g)[0].pk for g in final_canonical_genres]
                book.genres.set(genre_pks)
            # =========================================================================

            user_book_objects.append(book)
            all_raw_genres.extend(raw_genres_for_book)

        # --- Personal DNA Calculation ---
        reader_type, reader_type_scores = assign_reader_type(read_df, {}, all_raw_genres)
        explanation = random.choice(READER_TYPE_DESCRIPTIONS.get(reader_type, [""]))
        top_types_list = [{"type": t, "score": s} for t, s in reader_type_scores.most_common(3) if s > 0]
        mapped_genres = [CANONICAL_GENRE_MAP.get(g, g) for g in all_raw_genres]
        top_genres = dict(Counter(mapped_genres).most_common(10))

        # --- Community Analytics Calculation ---
        print("📈 Calculating base user statistics...")

        user_base_stats = {
            "total_books_read": int(len(read_df)),
            "total_pages_read": int(read_df["Number of Pages"].dropna().sum()),
            "avg_book_length": (
                int(round(read_df["Number of Pages"].dropna().mean()))
                if not read_df["Number of Pages"].dropna().empty
                else 0
            ),
            "avg_publish_year": (
                int(round(read_df["Original Publication Year"].dropna().mean()))
                if "Original Publication Year" in read_df.columns
                and not read_df["Original Publication Year"].dropna().empty
                else 0
            ),
        }

        print("🌍 Calculating community stats...")

        update_analytics_from_stats(user_base_stats)

        percentiles = calculate_percentiles_from_aggregates(user_base_stats)

        # --- Other Personal Stats Calculations ---
        most_niche_book = None

        if user_book_objects:
            user_book_objects.sort(key=lambda b: b.global_read_count)
            most_niche_book = {
                "title": user_book_objects[0].title,
                "author": user_book_objects[0].author.name,
                "read_count": user_book_objects[0].global_read_count,
            }

        top_authors = {k: int(v) for k, v in read_df["Author"].value_counts().head(10).to_dict().items()}

        ratings_df = read_df[read_df["My Rating"] > 0].dropna(subset=["My Rating"])
        average_rating_overall = float(round(ratings_df["My Rating"].mean(), 2)) if not ratings_df.empty else "N/A"
        ratings_dist = {
            str(k): int(v) for k, v in ratings_df["My Rating"].value_counts().sort_index().to_dict().items()
        }

        controversial_df = read_df.dropna(subset=["My Rating", "Average Rating"]).copy()
        controversial_df = controversial_df[controversial_df["My Rating"] > 0]

        top_controversial_list = []

        if not controversial_df.empty:
            controversial_df["Rating Difference"] = abs(
                controversial_df["My Rating"] - controversial_df["Average Rating"]
            )
            top_books = controversial_df.sort_values(by="Rating Difference", ascending=False).head(3)

            top_books["my_rating"] = top_books["My Rating"].astype(float)
            top_books["average_rating"] = top_books["Average Rating"].astype(float)
            top_books["rating_difference"] = top_books["Rating Difference"].astype(float)

            top_controversial_list = top_books.rename(columns={"Title": "Title", "Author": "Author"})[
                ["Title", "Author", "my_rating", "average_rating", "rating_difference"]
            ].to_dict("records")

        reviews_df = df[
            (df["My Review"].str.strip().ne(""))
            & (df["My Review"].str.len() > 15)
            & (df["My Rating"].notna())
            & (df["My Rating"] > 0)
        ].copy()

        most_positive_review, most_negative_review = None, None

        if not reviews_df.empty:
            analyzer = SentimentIntensityAnalyzer()

            reviews_df["sentiment"] = reviews_df["My Review"].apply(lambda r: analyzer.polarity_scores(r)["compound"])

            pos_candidate = (
                reviews_df[reviews_df["My Rating"] == 5]
                if not reviews_df[reviews_df["My Rating"] == 5].empty
                else reviews_df
            )

            pos_review_row = pos_candidate.loc[pos_candidate["sentiment"].idxmax()]

            neg_candidate = (
                reviews_df[reviews_df["My Rating"] == 1]
                if not reviews_df[reviews_df["My Rating"] == 1].empty
                else reviews_df
            )

            neg_review_row = neg_candidate.loc[neg_candidate["sentiment"].idxmin()]

            most_positive_review = pos_review_row.rename({"My Review": "my_review"})[
                ["Title", "Author", "my_review", "sentiment"]
            ].to_dict()

            most_positive_review["sentiment"] = float(most_positive_review["sentiment"])

            most_negative_review = neg_review_row.rename({"My Review": "my_review"})[
                ["Title", "Author", "my_review", "sentiment"]
            ].to_dict()

            most_negative_review["sentiment"] = float(most_negative_review["sentiment"])

        stats_by_year_list = []

        if "Date Read" in read_df.columns and not read_df["Date Read"].dropna().empty:
            yearly_df = read_df.dropna(subset=["Date Read"]).copy()
            yearly_df["year"] = yearly_df["Date Read"].dt.year

            yearly_stats = (
                yearly_df.groupby("year").agg(count=("Title", "size"), avg_rating=("My Rating", "mean")).reset_index()
            )

            yearly_stats["avg_rating"] = yearly_stats["avg_rating"].fillna(0).round(2)
            stats_by_year_list = yearly_stats.to_dict("records")

            # Ensure all types are native Python types for JSON serialization
            for item in stats_by_year_list:
                item["year"] = int(item["year"])
                item["count"] = int(item["count"])
                item["avg_rating"] = float(item["avg_rating"])

        mainstream_score = 0

        if user_book_objects:
            # A book is "mainstream" if its read count is over 50.
            mainstream_books_count = sum(1 for book in user_book_objects if book.global_read_count > 50)
            total_user_books = len(user_book_objects)

            if total_user_books > 0:
                mainstream_score = round((mainstream_books_count / total_user_books) * 100)

        dna = {
            "user_stats": user_base_stats,
            "bibliotype_percentiles": percentiles,
            "global_averages": GLOBAL_AVERAGES,
            "most_niche_book": most_niche_book,
            "reader_type": reader_type,
            "reader_type_explanation": explanation,
            "top_reader_types": top_types_list,
            "reader_type_scores": dict(reader_type_scores),
            "top_genres": top_genres,
            "top_authors": top_authors,
            "average_rating_overall": average_rating_overall,
            "ratings_distribution": ratings_dist,
            "top_controversial_books": top_controversial_list,
            "most_positive_review": most_positive_review,
            "most_negative_review": most_negative_review,
            "stats_by_year": stats_by_year_list,
            "mainstream_score_percent": mainstream_score,
        }

        # === VIBE GENERATION & SAVING (LLM VERSION) ===============================
        reading_vibe = []
        # Check if we have a logged-in user
        if user:
            profile = user.userprofile
            if profile.vibe_data_hash == new_data_hash and profile.reading_vibe:
                print("✅ Vibe data is unchanged. Using cached vibe from database.")
                reading_vibe = profile.reading_vibe
            else:
                print("✨ Vibe data has changed. Generating a new vibe with LLM...")
                reading_vibe = generate_vibe_with_llm(dna)
                profile.reading_vibe = reading_vibe
                profile.vibe_data_hash = new_data_hash
        else:
            # For anonymous users, always generate it fresh.
            print("✨ Anonymous user. Generating a new vibe with LLM...")
            reading_vibe = generate_vibe_with_llm(dna)

        dna["reading_vibe"] = reading_vibe
        dna["vibe_data_hash"] = new_data_hash

        # --- Final cleanup (remains the same) ---
        def clean_dict(d):
            if not isinstance(d, dict):
                return d
            return {k: v for k, v in d.items() if pd.notna(v)}

        dna["top_controversial_books"] = [clean_dict(b) for b in dna.get("top_controversial_books", [])]
        dna["most_positive_review"] = clean_dict(dna.get("most_positive_review"))
        dna["most_negative_review"] = clean_dict(dna.get("most_negative_review"))

        # --- MODIFIED: Final save/return logic ---
        if user:
            # For logged-in users, save to the profile and return a success message.
            _save_dna_to_profile(user.userprofile, dna)
            print(f"✅ Saved DNA for user: {user.username}")
            return f"DNA saved for user {user_id}"  # Celery needs a serializable return value
        else:
            # For anonymous users, RETURN the final DNA dictionary.
            print("🧬 DNA generated for an anonymous user. Returning result.")
            return dna

    except Exception as e:
        # This generic catch-all is important. It ensures that if anything
        # goes wrong (CSV parsing, API call, etc.), the task fails gracefully.
        print(f"❌❌❌ A critical error occurred in generate_reading_dna_task for user_id {user_id}: {e}")
        # Re-raise the exception to mark the Celery task as FAILED.
        # This is crucial for the frontend to show the error message.
        raise


def _save_dna_to_profile(profile, dna_data):
    """
    A reusable helper to correctly save all parts of the DNA dictionary
    to the user's profile, populating both the main JSON blob and the
    optimized, separate fields.
    """
    print(f"🔍 DEBUG: Saving DNA to profile for user: {profile.user.username}")
    print(f"🔍 DEBUG: DNA data keys: {list(dna_data.keys()) if dna_data else 'None'}")

    profile.dna_data = dna_data
    profile.reader_type = dna_data.get("reader_type")
    profile.total_books_read = dna_data.get("user_stats", {}).get("total_books_read")
    profile.reading_vibe = dna_data.get("reading_vibe")
    profile.vibe_data_hash = dna_data.get("vibe_data_hash")

    try:
        profile.save()
        print(f"✅ [DB] Successfully saved DNA data for user: {profile.user.username}")
        print(f"🔍 DEBUG: Profile.dna_data is now: {profile.dna_data is not None}")
    except Exception as e:
        print(f"❌ [DB] Error saving profile: {e}")
        raise
