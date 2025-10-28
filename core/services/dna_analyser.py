import hashlib
import logging

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

logger = logging.getLogger(__name__)

from ..models import Author, Book, Genre
from ..percentile_engine import (
    calculate_percentiles_from_aggregates,
    update_analytics_from_stats,
)
from ..services.top_books_service import calculate_and_store_top_books


from ..book_enrichment_service import enrich_book_from_apis
import hashlib
import random
import time
from collections import Counter
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
    logger.debug(f"Genre counts: {genre_counts}")

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
                logger.debug(f"Found non-major publisher: {publisher}")
                scores["Small Press Supporter"] += 1

    DIVERSITY_THRESHOLD = 10
    DIVERSITY_BONUS = 15
    unique_canonical_genres = len(genre_counts)

    logger.debug(f"Found {unique_canonical_genres} unique CANONICAL genres")

    if unique_canonical_genres >= DIVERSITY_THRESHOLD:
        scores["Versatile Valedictorian"] += DIVERSITY_BONUS
    if not scores or all(s == 0 for s in scores.values()):

        return "Eclectic Reader", scores

    if scores.get("Rapacious Reader", 0) > 0:
        return "Rapacious Reader", scores

    primary_type = scores.most_common(1)[0][0]
    return primary_type, scores


def _save_dna_to_profile(profile, dna_data):
    profile.dna_data = dna_data
    profile.reader_type = dna_data.get("reader_type")
    profile.total_books_read = dna_data.get("user_stats", {}).get("total_books_read")
    profile.reading_vibe = dna_data.get("reading_vibe")
    profile.vibe_data_hash = dna_data.get("vibe_data_hash")
    
    # Clear the pending task ID since we've completed the regeneration
    profile.pending_dna_task_id = None

    try:
        # Explicitly save all fields including pending_dna_task_id
        profile.save(update_fields=['dna_data', 'reader_type', 'total_books_read', 'reading_vibe', 'vibe_data_hash', 'pending_dna_task_id'])
    except Exception as e:
        logger.error(f"Error saving profile for user {profile.user.username}: {e}")
        raise


def save_anonymous_session_data(session_key, dna_data, user_book_objects, read_df):
    """Save anonymous user data to temporary session storage"""
    from ..models import AnonymousUserSession
    from django.utils import timezone
    from datetime import timedelta
    
    # Extract books
    books_data = [book.id for book in user_book_objects if book]
    
    # Calculate top books for anonymous users based on ratings and reviews
    book_scores = []
    analyzer = SentimentIntensityAnalyzer()
    
    for idx, row_dict in enumerate(read_df.to_dict('records')):
        if idx < len(user_book_objects) and user_book_objects[idx]:
            book = user_book_objects[idx]
            score = 0
            
            rating = row_dict.get('My Rating')
            if pd.notna(rating) and rating > 0:
                score += rating * 20
            
            review = str(row_dict.get('My Review', '')).strip()
            if review and len(review) > 15:
                sentiment = analyzer.polarity_scores(review)["compound"]
                score += sentiment * 30
            
            book_scores.append((book.id, score))
    
    book_scores.sort(key=lambda x: x[1], reverse=True)
    top_books_data = [book_id for book_id, score in book_scores[:5]]
    
    # Extract distributions from DNA
    genre_dist = {}
    for genre, count in dna_data.get('top_genres', []):
        genre_dist[genre] = count
    
    author_dist = {}
    for author, count in dna_data.get('top_authors', [])[:20]:
        normalized = Author._normalize(author)
        author_dist[normalized] = count
    
    # Save or update session
    AnonymousUserSession.objects.update_or_create(
        session_key=session_key,
        defaults={
            'dna_data': dna_data,
            'books_data': books_data,
            'top_books_data': top_books_data,
            'genre_distribution': genre_dist,
            'author_distribution': author_dist,
            'expires_at': timezone.now() + timedelta(days=7),
        }
    )


def calculate_full_dna(csv_file_content: str, user=None, session_key=None, progress_cb=None):
    try:
        df = pd.read_csv(StringIO(csv_file_content))
        read_df = df[df["Exclusive Shelf"] == "read"].copy()

        total_books = int(len(read_df))
        if progress_cb:
            progress_cb(0, total_books, "Parsing your library")

        if read_df.empty:
            raise ValueError("No books found on the 'read' shelf in your CSV.")

        book_fingerprint_list = sorted([f"{row['Title']}{row['Author']}" for _, row in read_df.iterrows()])
        fingerprint_string = "".join(book_fingerprint_list)
        new_data_hash = hashlib.sha256(fingerprint_string.encode()).hexdigest()

        logger.info(f"Found {len(read_df)} books marked as 'read' for statistical analysis")
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

        logger.info("Syncing book data with the database...")

        all_raw_genres, user_book_objects = [], []
        with requests.Session() as session:

            def process_book_row(original_row):
                author_name_from_csv = original_row.get("Author", "").strip()
                title_from_csv = original_row.get("Title", "").strip()

                if not author_name_from_csv or not title_from_csv:
                    return None, [], original_row

                normalized_author_name = Author._normalize(author_name_from_csv)
                author, created = Author.objects.get_or_create(
                    normalized_name=normalized_author_name, defaults={"name": author_name_from_csv}
                )

                if created:
                    from ..tasks import check_author_mainstream_status_task

                    logger.info(f"New author found: '{author.name}'. Dispatching background check.")
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

                # Check if the book already has genres by querying the database directly
                # This ensures we get the actual current state, not cached relationship data
                # If created=True, we know it's new and needs enrichment, so skip the DB check
                if created:
                    has_genres = False
                else:
                    # Use a direct database query that bypasses any instance caching
                    # Query from the Genre side of the relationship to ensure fresh data
                    has_genres = Genre.objects.filter(books__id=book.pk).exists()
                
                # Skip enrichment during upload to avoid database locks
                # Enrichment should be done in a separate background task
                # if created or not has_genres:
                #     logger.debug(f"Enriching '{book.title}' (created={created}, has_genres={has_genres})...")
                #     enrich_book_from_apis(book, session)
                # else:
                #     logger.debug(f"Book '{book.title}' already exists with genres. Skipping enrichment.")
                
                # Just log the enrichment status
                if created or not has_genres:
                    logger.debug(f"Book '{book.title}' will be enriched later (created={created}, has_genres={has_genres})")
                else:
                    logger.debug(f"Book '{book.title}' already exists with genres.")

                Book.objects.filter(pk=book.pk).update(global_read_count=F("global_read_count") + 1)

                fresh_book_instance = Book.objects.get(pk=book.pk)

                return fresh_book_instance, [g.name for g in fresh_book_instance.genres.all()], original_row

            # Use single worker to avoid SQLite database lock issues
            with ThreadPoolExecutor(max_workers=1) as executor:
                results = []
                processed = 0
                for res in executor.map(process_book_row, read_df.to_dict("records")):
                    results.append(res)
                    processed += 1
                    if progress_cb:
                        progress_cb(processed, total_books, "Syncing books")

        for book, genres, original_row in results:
            if book:
                user_book_objects.append(book)
                all_raw_genres.extend(genres)
        
        # Store UserBook entries for registered users
        if user and results:
            from ..models import UserBook
            
            # Store book data with ratings and reviews - now we have the original row
            for book, genres, original_row in results:
                if book:
                    rating_value = None
                    review_value = ''
                    
                    if pd.notna(original_row.get('My Rating')) and original_row['My Rating'] > 0:
                        try:
                            rating_value = int(original_row['My Rating'])
                        except (ValueError, TypeError):
                            rating_value = None
                    
                    if pd.notna(original_row.get('My Review')):
                        review_value = str(original_row['My Review']).strip()
                    
                    date_read_value = None
                    if pd.notna(original_row.get('Date Read')):
                        date_read_value = pd.to_datetime(original_row['Date Read'], errors='coerce')
                        if pd.isna(date_read_value):
                            date_read_value = None
                    
                    # Use update_or_create to handle duplicates better
                    UserBook.objects.update_or_create(
                        user=user,
                        book=book,
                        defaults={
                            'user_rating': rating_value,
                            'user_review': review_value,
                            'date_read': date_read_value,
                        }
                    )

        enriched_data_for_scoring = {
            book.title: {
                "publish_year": book.publish_year,
                "publisher": book.publisher,
            }
            for book in user_book_objects
            if book.title
        }

        reader_type, reader_type_scores = assign_reader_type(read_df, enriched_data_for_scoring, all_raw_genres)
        explanation = random.choice(READER_TYPE_DESCRIPTIONS.get(reader_type, [""]))
        top_types_list = [{"type": t, "score": s} for t, s in reader_type_scores.most_common(3) if s > 0]
        mapped_genres = [CANONICAL_GENRE_MAP.get(g, g) for g in all_raw_genres]
        top_genres = Counter(mapped_genres).most_common(10)

        if progress_cb:
            progress_cb(total_books, total_books, "Crunching stats")
        logger.info("Calculating base user statistics...")

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

        logger.info("Calculating community stats...")

        update_analytics_from_stats(user_base_stats)

        percentiles = calculate_percentiles_from_aggregates(user_base_stats)

        most_niche_book = None

        if user_book_objects:
            user_book_objects.sort(key=lambda b: b.global_read_count)
            most_niche_book = {
                "title": user_book_objects[0].title,
                "author": user_book_objects[0].author.name,
                "read_count": user_book_objects[0].global_read_count,
            }

        top_authors = list(read_df["Author"].value_counts().head(10).items())

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
            yearly_df = read_df.dropna(subset=["Date Read"])
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
        logger.debug(f"DNA data generated")

        reading_vibe = []
        if user:
            profile = user.userprofile
            if profile.vibe_data_hash == new_data_hash and profile.reading_vibe:
                logger.debug("Vibe data is unchanged. Using cached vibe from database")
                reading_vibe = profile.reading_vibe
            else:
                logger.info("Vibe data has changed. Generating a new vibe with LLM...")
                reading_vibe = generate_vibe_with_llm(dna)
                profile.reading_vibe = reading_vibe
                profile.vibe_data_hash = new_data_hash
        else:
            logger.info("Anonymous user. Generating a new vibe with LLM...")
            reading_vibe = generate_vibe_with_llm(dna)

        dna["reading_vibe"] = reading_vibe
        dna["vibe_data_hash"] = new_data_hash

        def clean_dict(d):
            if not isinstance(d, dict):
                return d
            return {k: v for k, v in d.items() if pd.notna(v)}

        dna["top_controversial_books"] = [clean_dict(b) for b in dna.get("top_controversial_books", [])]
        dna["most_positive_review"] = clean_dict(dna.get("most_positive_review"))
        dna["most_negative_review"] = clean_dict(dna.get("most_negative_review"))

        if progress_cb:
            # Final update to help smooth the last jump in the UI
            progress_cb(total_books, total_books, "Finishing up")

        if user:
            # Calculate and store top books
            calculate_and_store_top_books(user, limit=5)
            
            _save_dna_to_profile(user.userprofile, dna)
            logger.info(f"Saved DNA for user: {user.username}")
            return f"DNA saved for user {user.id}"
        else:
            # Save anonymous session data
            if session_key:
                save_anonymous_session_data(session_key, dna, user_book_objects, read_df)
                logger.info(f"Saved anonymous session data for session: {session_key}")
            logger.info("DNA generated for an anonymous user. Returning result for display")
            return dna

    except Exception as e:
        user_identifier = user.id if user else "Anonymous"
        logger.error(f"A critical error occurred in DNA calculation for user_id {user_identifier}: {e}", exc_info=True)
        raise


def normalize_and_filter_genres(subjects):
    plausible_genres = []
    for s in subjects:
        s_lower = s.lower().strip()
        if s_lower in EXCLUDED_GENRES:
            continue
        if "ps35" in s_lower or "nyt:" in s_lower or "b485" in s_lower:
            continue
        if len(s.split()) < 4 and "history" not in s_lower and "accessible" not in s_lower:
            plausible_genres.append(s_lower)

    return plausible_genres[:5]


def analyze_and_print_genres(all_raw_genres, canonical_map):
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
