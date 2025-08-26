import itertools
import random
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from urllib.parse import quote_plus

import pandas as pd
import requests
from django.core.cache import cache
from django.db.models import F
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from .dna_constants import (
    CANONICAL_GENRE_MAP,
    EXCLUDED_GENRES,
    GLOBAL_AVERAGES,
    MAJOR_PUBLISHERS,
    READER_TYPE_DESCRIPTIONS,
)
from .models import Author, Book, Genre, UserProfile
from .percentile_engine import calculate_percentiles_from_aggregates, update_analytics_from_stats


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
    Looks up a book's genres, publish year, and publisher by fetching
    from both the Work (for rich genres) and Edition (for specific details)
    endpoints. Uses a cache to avoid repeated API calls.
    """
    LOGIC_VERSION = "v10_hybrid"
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

    # Default empty structure
    book_details = {
        "genres": [],
        "publish_year": None,
        "publisher": None,
    }

    try:
        # --- Step 1: Search for the book ---
        response = session.get(search_url, timeout=5)
        if response.status_code != 200:
            return book_details

        data = response.json()
        if not data.get("docs"):
            return book_details

        search_result = data["docs"][0]
        work_key = search_result.get("key")
        edition_key = search_result.get("cover_edition_key")

        # --- Step 2: Get rich genre data from the WORK endpoint (File 1's method) ---
        if work_key:
            work_url = f"https://openlibrary.org{work_key}.json"
            work_response = session.get(work_url, timeout=5)
            if work_response.status_code == 200:
                work_data = work_response.json()
                subjects = work_data.get("subjects", [])
                book_details["genres"] = normalize_and_filter_genres(subjects)  # Using the helper from File 2

        # --- Step 3: Get specific data from the EDITION endpoint (File 2's method) ---
        if edition_key:
            edition_url = f"https://openlibrary.org/books/{edition_key}.json"
            edition_response = session.get(edition_url, timeout=5)
            if edition_response.status_code == 200:
                edition_data = edition_response.json()

                # Extract publish year
                publish_year_str = edition_data.get("publish_date", "")
                if publish_year_str:
                    match = re.search(r"\d{4}", publish_year_str)
                    if match:
                        try:
                            book_details["publish_year"] = int(match.group())
                        except ValueError:
                            pass  # Keep it as None

                # Extract publisher
                book_details["publisher"] = edition_data.get("publishers", [None])[0]

        # --- Step 4: Cache the combined result and return ---
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


def generate_reading_dna(csv_file_content: str, user) -> dict:
    print("🚀 Starting Reading DNA generation...")
    try:
        df = pd.read_csv(StringIO(csv_file_content))
    except Exception as e:
        raise ValueError(f"Could not parse CSV file. Error: {e}")

    read_df = df[df["Exclusive Shelf"] == "read"].copy()
    if read_df.empty:
        raise ValueError("No books found on the 'read' shelf in your CSV.")

    print(f"📖 Found {len(read_df)} books marked as 'read' for statistical analysis.")
    # --- Data cleaning ---
    for temp_df in [df, read_df]:
        temp_df["My Rating"] = pd.to_numeric(temp_df["My Rating"], errors="coerce")
        temp_df["Number of Pages"] = pd.to_numeric(temp_df["Number of Pages"], errors="coerce")
        temp_df["Average Rating"] = pd.to_numeric(temp_df["Average Rating"], errors="coerce")
        temp_df["Date Read"] = pd.to_datetime(temp_df["Date Read"], errors="coerce")
        if "Original Publication Year" in temp_df.columns:
            temp_df["Original Publication Year"] = pd.to_numeric(temp_df["Original Publication Year"], errors="coerce")
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
        if raw_genres_for_book:
            book.genres.set([Genre.objects.get_or_create(name=g)[0].pk for g in raw_genres_for_book])
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
    ratings_dist = {str(k): int(v) for k, v in ratings_df["My Rating"].value_counts().sort_index().to_dict().items()}

    controversial_df = read_df.dropna(subset=["My Rating", "Average Rating"]).copy()
    controversial_df = controversial_df[controversial_df["My Rating"] > 0]
    top_controversial_list = []
    if not controversial_df.empty:
        controversial_df["Rating Difference"] = abs(controversial_df["My Rating"] - controversial_df["Average Rating"])
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

    # =========================================================================
    # === NEW CODE BLOCK TO FIX THE CHART =====================================
    # =========================================================================
    # This calculates the statistics needed for the yearly charts in the template.
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
    # =========================================================================
    # === END OF NEW CODE BLOCK ===============================================
    # =========================================================================

    # --- Assemble final DNA dictionary ---
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
        "stats_by_year": stats_by_year_list,  # <-- Add the new data here
    }

    # Final cleanup of NaN values for JSON serialization
    def clean_dict(d):
        if not isinstance(d, dict):
            return d
        return {k: v for k, v in d.items() if pd.notna(v)}

    dna["top_controversial_books"] = [clean_dict(b) for b in dna.get("top_controversial_books", [])]
    dna["most_positive_review"] = clean_dict(dna.get("most_positive_review"))
    dna["most_negative_review"] = clean_dict(dna.get("most_negative_review"))

    return dna
