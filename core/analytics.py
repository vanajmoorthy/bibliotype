import itertools
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from urllib.parse import quote_plus

import pandas as pd
import requests
from django.core.cache import cache
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

GENRE_ALIASES = {
    "fantasy": {
        "fantasy fiction",
        "epic fantasy",
        "high fantasy",
        "discworld (imaginary place)",
        "english fantasy fiction",
        "disque-monde (lieu imaginaire)",
        "wizards",
        "magic",
        "magic, fiction",
        "wizards, fiction",
    },
    "science fiction": {"sci-fi", "speculative fiction"},
    "non-fiction": {"nonfiction"},
    "psychology": {"mental health"},
    "classics": {"classic"},
    "humorous fiction": {"humorous stories", "humor"},
}

# Create a reverse mapping for fast lookups (alias -> canonical).
# This is an efficient way to normalize the genres later on.
CANONICAL_GENRE_MAP = {}

for canonical, aliases in GENRE_ALIASES.items():
    CANONICAL_GENRE_MAP[canonical] = canonical

    for alias in aliases:
        CANONICAL_GENRE_MAP[alias] = canonical


def get_genres_from_open_library(title, author, session):
    """
    Looks up a book's genres. Uses a cache to avoid repeated API calls.
    """
    # 1. Create a unique, clean key for this specific book.
    # We use a prefix to avoid clashes with other cached data.

    LOGIC_VERSION = "v5"

    cache_key = f"genre:{LOGIC_VERSION}:{author}:{title}".lower().replace(" ", "_")

    # 2. Try to get the result from the cache first.
    cached_genres = cache.get(cache_key)

    if cached_genres is not None:
        # If we find it in the cache, return it immediately. No API call needed!
        print(f"   ✅ Found genres for '{title}' in cache.")

        return cached_genres

    # 3. If NOT in cache, perform the slow API call (your existing logic).
    print(f"   📚 Fetching genres for: '{title}' by {author} (from API)")
    clean_title = re.split(r"[:(]", title)[0].strip()
    query_title = quote_plus(clean_title)
    query_author = quote_plus(author)
    search_url = f"https://openlibrary.org/search.json?title={query_title}&author={query_author}"

    try:
        response = session.get(search_url, timeout=5)
        if response.status_code != 200:
            genres = []
        else:
            data = response.json()
            if not data.get("docs"):
                genres = []
            else:
                work_key = data["docs"][0].get("key")
                if not work_key:
                    genres = []
                else:
                    work_url = f"https://openlibrary.org{work_key}.json"
                    work_response = session.get(work_url, timeout=5)

                    if work_response.status_code != 200:
                        genres = []
                    else:
                        work_data = work_response.json()

                        subjects = work_data.get("subjects", [])

                        excluded_genres = {
                            "fiction",
                            "literature",
                            "ficción",
                            "fiction, general",
                            "romans, nouvelles",
                            "fiction, fantasy, general",
                            "fiction, humorous, general",
                            "fiction, humorous",
                            "english literature",
                            "juvenile fiction",
                            "children's fiction",
                            "large type books",
                            "novela",
                        }

                        plausible_genres = [
                            s.lower()
                            for s in subjects
                            if len(s.split()) < 4
                            and "history" not in s.lower()
                            and "accessible" not in s.lower()
                            and s.lower().strip() not in excluded_genres
                        ]

                        genres = plausible_genres[:5]

        # 4. Store the result in the cache for 30 days before returning it.
        # This means we won't have to call the API for this book again for a month.
        cache.set(cache_key, genres, timeout=60 * 60 * 24 * 30)
        print(genres)
        return genres

    except requests.RequestException as e:
        print(f"    ❌ Request failed for '{title}': {str(e)}")
        return []


def generate_reading_dna(csv_file_content: str) -> dict:
    print("🚀 Starting Reading DNA generation...")

    try:
        df = pd.read_csv(StringIO(csv_file_content))
        print(f"✅ Successfully loaded CSV with {len(df)} total entries")
    except Exception as e:
        raise ValueError(f"Could not parse CSV file. Error: {e}")

    # --- NEW STRATEGY: CREATE TWO DATAFRAMES ---
    # read_df: For stats that MUST come from finished books (counts, pages, etc.)
    read_df = df[df["Exclusive Shelf"] == "read"].copy()
    # The original `df` will be used for review analysis, as it contains all books.

    if read_df.empty:
        raise ValueError("No books found on the 'read' shelf in your CSV.")

    print(f"📖 Found {len(read_df)} books marked as 'read' for statistical analysis.")
    print("🧹 Cleaning and processing data...")

    # Process both dataframes
    for temp_df in [df, read_df]:
        temp_df["My Rating"] = pd.to_numeric(temp_df["My Rating"], errors="coerce")
        temp_df["Number of Pages"] = pd.to_numeric(temp_df["Number of Pages"], errors="coerce")
        temp_df["Average Rating"] = pd.to_numeric(temp_df["Average Rating"], errors="coerce")
        temp_df["Date Read"] = pd.to_datetime(temp_df["Date Read"], errors="coerce")
        temp_df.loc[:, "My Review"] = temp_df["My Review"].fillna("")

    print("📊 Calculating reading statistics (from 'read' shelf)...")
    # --- ALL STATS BELOW USE `read_df` FOR ACCURACY ---
    total_books_read = len(read_df)
    total_pages_read = int(read_df["Number of Pages"].dropna().sum())
    print(f"   📚 Total books: {total_books_read}")
    print(f"   📄 Total pages: {total_pages_read:,}")

    ratings_df = read_df[read_df["My Rating"] > 0].dropna(subset=["My Rating"])
    if not ratings_df.empty:
        average_rating_overall = round(ratings_df["My Rating"].mean(), 2)
        ratings_dist = ratings_df["My Rating"].value_counts().sort_index().to_dict()
        ratings_dist = {str(k): int(v) for k, v in ratings_dist.items()}
        print(f"   ⭐ Average rating: {average_rating_overall}")
    else:
        average_rating_overall = "N/A"
        ratings_dist = {}

    yearly_df = read_df.dropna(subset=["Date Read", "My Rating"]).copy()
    stats_by_year_list = []
    if not yearly_df.empty:
        yearly_df.loc[:, "Year Read"] = yearly_df["Date Read"].dt.year
        # ... yearly stats calculation ...
        books_by_year = yearly_df["Year Read"].value_counts().sort_index()
        avg_rating_by_year = yearly_df.groupby("Year Read")["My Rating"].mean().round(2).sort_index()
        stats_by_year = pd.concat([books_by_year, avg_rating_by_year], axis=1)
        stats_by_year.columns = ["count", "avg_rating"]
        for year, data in stats_by_year.iterrows():
            stats_by_year_list.append(
                {
                    "year": int(year),
                    "count": int(data["count"]),
                    "avg_rating": data["avg_rating"],
                }
            )

    top_authors = read_df["Author"].value_counts().head(5).to_dict()

    controversial_df = read_df.dropna(subset=["My Rating", "Average Rating"]).copy()
    controversial_df = controversial_df[controversial_df["My Rating"] > 0]
    top_controversial_list = []
    if not controversial_df.empty:
        controversial_df["Rating Difference"] = abs(controversial_df["My Rating"] - controversial_df["Average Rating"])
        # ... controversial books calculation ...
        top_controversial_books = controversial_df.sort_values(by="Rating Difference", ascending=False).head(3)
        top_controversial_list = top_controversial_books.rename(
            columns={
                "My Rating": "my_rating",
                "Average Rating": "average_rating",
                "Rating Difference": "rating_difference",
            }
        )[["Title", "Author", "my_rating", "average_rating", "rating_difference"]].to_dict("records")

    # --- SENTIMENT ANALYSIS: USES ORIGINAL `df` ---
    print("💭 Analyzing review sentiments (from ALL shelves)...")

    reviews_df = df[
        (df["My Review"].str.strip() != "")
        & (df["My Review"].str.strip() != "nan")
        & (df["My Review"].str.len() > 5)
        & (df["My Rating"].notna())
        & (df["My Rating"] > 0)
    ].copy()

    print(f"   📝 Found {len(reviews_df)} reviews across all shelves to analyze")

    most_positive_review, most_negative_review = None, None
    if not reviews_df.empty:
        print("   🤖 Running sentiment analysis...")
        analyzer = SentimentIntensityAnalyzer()
        reviews_df["sentiment"] = (
            reviews_df["My Review"].str.strip().apply(lambda r: analyzer.polarity_scores(r)["compound"])
        )

        # --- "Human-First" Heuristic for POSITIVE Review ---
        five_star_reviews_df = reviews_df[reviews_df["My Rating"] == 5]
        if not five_star_reviews_df.empty:
            print("   ⭐ Found 5-star reviews! Selecting the most positive from this elite group.")
            pos_review_row = five_star_reviews_df.loc[five_star_reviews_df["sentiment"].idxmax()]
        else:
            print("   ⚠️ No 5-star reviews found. Finding the most positive review from all ratings.")
            pos_review_row = reviews_df.loc[reviews_df["sentiment"].idxmax()]

        # --- NEW "Human-First" Heuristic for NEGATIVE Review ---
        one_star_reviews_df = reviews_df[reviews_df["My Rating"] == 1]
        if not one_star_reviews_df.empty:
            print("   ⭐ Found 1-star reviews! Selecting the most negative from this group.")
            # Use sentiment score as a tie-breaker among your worst books
            neg_review_row = one_star_reviews_df.loc[one_star_reviews_df["sentiment"].idxmin()]
        else:
            # Fallback: If no 1-star reviews, find the most negative overall.
            print("   ⚠️ No 1-star reviews found. Finding the most negative review from all ratings.")
            neg_review_row = reviews_df.loc[reviews_df["sentiment"].idxmin()]

        # --- Final Preparation for Template ---
        reviews_df.rename(columns={"My Review": "my_review"}, inplace=True)
        pos_review_row = reviews_df.loc[pos_review_row.name]
        neg_review_row = reviews_df.loc[neg_review_row.name]

        most_positive_review = pos_review_row[["Title", "Author", "my_review", "sentiment"]].to_dict()
        most_negative_review = neg_review_row[["Title", "Author", "my_review", "sentiment"]].to_dict()
        print(f"   😊 Most positive review is for '{most_positive_review['Title']}'")
        print(f"   😞 Most negative review is for '{most_negative_review['Title']}'")

    # ... (the rest of the file is the same) ...

    print("🎭 Starting genre analysis (from 'read' shelf)...")

    # --- RESTORE THIS INTELLIGENT SAMPLING LOGIC ---
    total_books = len(read_df)

    if total_books > 100:
        print(f"📚 Large library ({total_books} books). Using intelligent sampling...")
        recent_books = read_df.sort_values("Date Read", ascending=False).head(30)
        highly_rated = read_df[read_df["My Rating"] >= 4].sample(n=min(30, len(read_df[read_df["My Rating"] >= 4])))
        random_sample = read_df.sample(n=min(40, total_books))
        book_sample = pd.concat([recent_books, highly_rated, random_sample]).drop_duplicates(subset=["Title", "Author"])
    else:
        print(f"📚 Small library ({total_books} books). Analyzing all books...")
        book_sample = read_df

    print(f"🎭 Starting genre analysis for {len(book_sample)} books...")

    # Now the rest of your code will work with a manageable sample size
    all_genre_lists = []
    with ThreadPoolExecutor(max_workers=10) as executor, requests.Session() as session:
        session.headers.update({"User-Agent": "ReadingDNA/1.0"})
        titles = book_sample["Title"]
        authors = book_sample["Author"]
        results_iterator = executor.map(get_genres_from_open_library, titles, authors, itertools.repeat(session))
        list_of_genre_lists = list(results_iterator)

    all_genres = list(itertools.chain.from_iterable(list_of_genre_lists))
    normalized_genres = [CANONICAL_GENRE_MAP.get(genre, genre) for genre in all_genres]
    top_genres = dict(Counter(normalized_genres).most_common(10)) if normalized_genres else {}

    # --- FINAL ASSEMBLY ---
    dna = {
        "total_books_read": total_books_read,
        "total_pages_read": total_pages_read,
        "average_rating_overall": average_rating_overall,
        "stats_by_year": stats_by_year_list,
        "ratings_distribution": ratings_dist,
        "top_authors": top_authors,
        "top_genres": top_genres,
        "top_controversial_books": top_controversial_list,
        "most_positive_review": most_positive_review,
        "most_negative_review": most_negative_review,
    }

    # ... (clean_dict function and final cleanup) ...
    def clean_dict(d):
        if not isinstance(d, dict):
            return d
        return {k: v for k, v in d.items() if pd.notna(v)}

    dna["top_controversial_books"] = [clean_dict(b) for b in dna["top_controversial_books"]]
    if dna["most_positive_review"]:
        dna["most_positive_review"] = clean_dict(dna["most_positive_review"])
    if dna["most_negative_review"]:
        dna["most_negative_review"] = clean_dict(dna["most_negative_review"])
    return dna
