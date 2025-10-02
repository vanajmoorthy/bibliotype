import hashlib


from django.db.models import F
from django.core.cache import cache
import random
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from io import StringIO

from core.services.llm_service import generate_vibe_with_llm
import pandas as pd
import requests
from django.db.models import F
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from ..models import Author, Book
from ..percentile_engine import (
    calculate_percentiles_from_aggregates,
    update_analytics_from_stats,
)


from ..book_enrichment_service import enrich_book_from_apis
import hashlib
import random
import time
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
import requests
from ..dna_constants import (
    CANONICAL_GENRE_MAP,
    GLOBAL_AVERAGES,
    READER_TYPE_DESCRIPTIONS,
)


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
    print(genre_counts)

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
            if not publisher.is_mainstream:
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


def calculate_full_dna(csv_file_content: str, user=None):
    try:

        # (The first part of the function with all the analysis logic is unchanged)
        # ...
        df = pd.read_csv(StringIO(csv_file_content))
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
        print("💾 Syncing book data with the database...")

        all_raw_genres, user_book_objects = [], []
        with requests.Session() as session:

            # This is the "worker" function that will be executed in each thread.
            def process_book_row(original_row):
                author_name_from_csv = original_row.get("Author", "").strip()
                title_from_csv = original_row.get("Title", "").strip()

                if not author_name_from_csv or not title_from_csv:
                    return None, []

                normalized_author_name = Author._normalize(author_name_from_csv)
                author, created = Author.objects.get_or_create(
                    normalized_name=normalized_author_name, defaults={"name": author_name_from_csv}
                )

                # If a new author was created, dispatch the background task to check them.
                if created:
                    from ..tasks import check_author_mainstream_status_task

                    print(f"   -> New author found: '{author.name}'. Dispatching background check.")
                    check_author_mainstream_status_task.delay(author.id)

                normalized_book_title = Book._normalize_title(title_from_csv)
                book, created = Book.objects.update_or_create(
                    normalized_title=normalized_book_title,
                    author=author,
                    defaults={
                        "title": title_from_csv,
                        "page_count": int(p) if pd.notna(p := original_row.get("Number of Pages")) else None,
                        "average_rating": float(r) if pd.notna(r := original_row.get("Average Rating")) else None,
                    },
                )

                # If the book was new OR has no genres, enrich it.
                if created or not book.genres.exists():
                    print(f"   -> Enriching '{book.title}'...")
                    enrich_book_from_apis(book, session)
                    time.sleep(1)

                # Important: Update the read count for this user
                Book.objects.filter(pk=book.pk).update(global_read_count=F("global_read_count") + 1)

                # --- THE FIX ---
                # Discard the potentially stale 'book' object and fetch a fresh one
                # from the database to guarantee we get the latest genre relationships.
                fresh_book_instance = Book.objects.get(pk=book.pk)

                # Return the processed book object and its now-current genres
                return fresh_book_instance, [g.name for g in fresh_book_instance.genres.all()]

            # Use the ThreadPoolExecutor to process all rows from the dataframe
            # max_workers=5 is a safe number to avoid overwhelming APIs. You can tune this.
            with ThreadPoolExecutor(max_workers=5) as executor:
                # executor.map runs `process_book_row` for each item in the list
                results = list(executor.map(process_book_row, read_df.to_dict("records")))

        # --- Collect the results from all threads ---
        for book, genres in results:
            if book:  # Ensure we don't process invalid rows
                user_book_objects.append(book)
                all_raw_genres.extend(genres)

        enriched_data_for_scoring = {
            book.title: {
                "publish_year": book.publish_year,
                "publisher": book.publisher,
            }
            for book in user_book_objects
            if book.title
        }

        # --- Personal DNA Calculation ---
        reader_type, reader_type_scores = assign_reader_type(read_df, enriched_data_for_scoring, all_raw_genres)
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
            mainstream_books_count = 0
            total_user_books = len(user_book_objects)

            for book in user_book_objects:
                if book.author.is_mainstream or (book.publisher and book.publisher.is_mainstream):
                    mainstream_books_count += 1

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

        reading_vibe = []
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
            return f"DNA saved for user {user.id}"
        else:
            # For anonymous users, simply return the generated DNA dictionary.
            # The calling Celery task will handle caching it.
            print("🧬 DNA generated for an anonymous user. Returning result for display.")
            return dna

    except Exception as e:
        # FIX: Use the `user` object for logging, not the non-existent user_id.
        user_identifier = user.id if user else "Anonymous"
        print(f"❌❌❌ A critical error occurred in DNA calculation for user_id {user_identifier}: {e}")
        raise
