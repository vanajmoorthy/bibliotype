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

MAJOR_PUBLISHERS = {
    # Original List
    "penguin",
    "random house",
    "harpercollins",
    "simon & schuster",
    "hachette",
    "macmillan",
    "knopf",
    "doubleday",
    "viking",
    "vintage",
    "scribner",
    "atriabooks",
    # Recommended Additions
    "little, brown",  # For "Little, Brown and Company"
    "bloomsbury",
    "farrar, straus & giroux",
    "bantam",
    "scholastic",
    "putnam",  # For "G. P. Putnam's Sons"
    "dutton",
    "hodder",
    "collins",
    "oxford university press",
    "routledge",
    "prentice hall",
    "grove press",
    "virago",
    "gollancz",
    "harper",  # Catches HarperPrism, Harper Paperbacks, etc.
    "harcourt",  # Catches Harcourt, Brace and Company, etc.
    "faber",  # Catches Faber & Faber
    "dover",  # Catches Dover Publications
    "tantor",
    "signet",
    "Atria Books",
    "Ballantine Books",
    "Orion Publishing Group",
    "Berkley",
    "Hogarth",
}


GENRE_ALIASES = {
    # --- FICTION ---
    "fantasy": {
        "fantasy fiction",
        "epic fantasy",
        "high fantasy",
        "wizards",
        "magic",
        "urban fantasy",
        "magical realism",
        "fables",
    },
    "science fiction": {
        "sci-fi",
        "speculative fiction",
        "dystopian fiction",
        "cyberpunk",
        "space opera",
        "science fiction, american",
    },
    "thriller": {
        "thriller",
        "mystery fiction",
        "crime fiction",
        "suspense fiction",
        "detective and mystery stories",
        "thrillers (fiction)",
        "spy stories",
        "crime",
    },
    "horror": {"horror fiction", "ghost stories", "supernatural fiction", "gothic fiction"},
    "historical fiction": {"historical fiction", "roman historique"},
    "romance": {"romance fiction", "love stories", "contemporary romance"},
    "humorous fiction": {"humorous stories", "humor", "satire"},
    "young adult": {"young adult fiction", "juvenile literature"},  # Open Library often uses 'juvenile' for YA
    "children's literature": {"children's stories", "picture books"},
    "classics": {"classic", "classical literature"},
    # --- NON-FICTION ---
    "non-fiction": {"nonfiction", "essays", "journalism", "creative nonfiction"},
    "biography": {"biography", "autobiography", "memoir", "biographies", "memoirs", "diaries"},
    "history": {"history", "world history", "ancient history", "military history"},
    "psychology": {"mental health", "psychology", "psychological fiction"},  # Note: Can be fiction or non-fiction
    "philosophy": {"philosophy", "ethics", "metaphysics", "stoicism"},
    "social science": {
        "sociology",
        "politics",
        "social history",
        "anthropology",
        "current events",
        "political science",
        "economics",
    },
    "nature": {"natural history", "environment", "animals", "outdoors", "nature writing", "biology"},
    "self-help": {"self-help", "personal development", "self-improvement", "productivity", "business"},
    "science": {"science", "popular science", "physics", "astronomy"},
    "travel": {"travel writing", "travelogue", "voyages and travels"},
}


# Create a reverse mapping for fast lookups (alias -> canonical).
CANONICAL_GENRE_MAP = {}
for canonical, aliases in GENRE_ALIASES.items():
    CANONICAL_GENRE_MAP[canonical] = canonical
    for alias in aliases:
        CANONICAL_GENRE_MAP[alias] = canonical


def normalize_and_filter_genres(subjects):
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
    return plausible_genres[:5]


def get_book_details_from_open_library(title, author, session):
    """
    Looks up a book's genres, publish year, and publisher by fetching
    from both the Work (for rich genres) and Edition (for specific details)
    endpoints. Uses a cache to avoid repeated API calls.
    """
    LOGIC_VERSION = "v5_hybrid"  # Bump the version for this new, improved logic
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


# REVISED AND MORE EQUITABLE FUNCTION
def assign_reader_type(read_df, enriched_data, all_genres):
    """
    Calculates scores for various reader traits and determines the primary type.
    This version re-balances the "Versatile Valedictorian" score to be more equitable.
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
        long_books = read_df[read_df["Number of Pages"] > 600].shape[0]
        short_books = read_df[read_df["Number of Pages"] < 200].shape[0]
        scores["Tome Tussler"] += long_books * 2
        scores["Novella Navigator"] += short_books

    genre_counts = Counter([CANONICAL_GENRE_MAP.get(g, g) for g in all_genres])
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
                print(f"   Found non-major publisher: {publisher}")
                scores["Small Press Supporter"] += 1

    # --- THE CRUCIAL CHANGE FOR EQUITY ---
    # Now, this score only starts counting AFTER 15 distinct genres.
    # This prevents it from unfairly dominating the results.
    scores["Versatile Valedictorian"] = max(0, len(genre_counts) - 15)

    if not scores:
        return "Eclectic Reader", scores
    if scores["Rapacious Reader"] > 0:
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


def generate_reading_dna(csv_file_content: str) -> dict:
    print("🚀 Starting Reading DNA generation...")

    try:
        df = pd.read_csv(StringIO(csv_file_content))
        print(f"✅ Successfully loaded CSV with {len(df)} total entries")
    except Exception as e:
        raise ValueError(f"Could not parse CSV file. Error: {e}")

    read_df = df[df["Exclusive Shelf"] == "read"].copy()
    if read_df.empty:
        raise ValueError("No books found on the 'read' shelf in your CSV.")

    print(f"📖 Found {len(read_df)} books marked as 'read' for statistical analysis.")
    print("🧹 Cleaning and processing data...")

    for temp_df in [df, read_df]:
        temp_df["My Rating"] = pd.to_numeric(temp_df["My Rating"], errors="coerce")
        temp_df["Number of Pages"] = pd.to_numeric(temp_df["Number of Pages"], errors="coerce")
        temp_df["Average Rating"] = pd.to_numeric(temp_df["Average Rating"], errors="coerce")
        temp_df["Date Read"] = pd.to_datetime(temp_df["Date Read"], errors="coerce")
        temp_df.loc[:, "My Review"] = temp_df["My Review"].fillna("")

    print("🎭 Enriching book data via Open Library API...")
    enriched_data = {}
    with ThreadPoolExecutor(max_workers=10) as executor, requests.Session() as session:
        titles, authors = read_df["Title"], read_df["Author"]
        results = executor.map(get_book_details_from_open_library, titles, authors, itertools.repeat(session))
        for title, details in zip(titles, results):
            if details:
                enriched_data[title] = details

    all_genres = list(itertools.chain.from_iterable(d.get("genres", []) for d in enriched_data.values()))

    analyze_and_print_genres(all_genres, CANONICAL_GENRE_MAP)

    top_genres = dict(Counter([CANONICAL_GENRE_MAP.get(g, g) for g in all_genres]).most_common(10))

    print("🧠 Assigning Reader Type...")
    reader_type, reader_type_scores = assign_reader_type(read_df, enriched_data, all_genres)
    print(f"   🏆 Determined Reader Type: {reader_type}")
    print(f"   📊 Reader Type Scores: {reader_type_scores}")

    # --- NEW: Get Top 3 Reader Types for Display ---
    top_types_list = [
        {"type": r_type, "score": score} for r_type, score in reader_type_scores.most_common(3) if score > 0
    ]

    print("📊 Calculating reading statistics (from 'read' shelf)...")
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

    top_authors = read_df["Author"].value_counts().head(10).to_dict()

    controversial_df = read_df.dropna(subset=["My Rating", "Average Rating"]).copy()
    controversial_df = controversial_df[controversial_df["My Rating"] > 0]
    top_controversial_list = []
    if not controversial_df.empty:
        controversial_df["Rating Difference"] = abs(controversial_df["My Rating"] - controversial_df["Average Rating"])
        top_controversial_books = controversial_df.sort_values(by="Rating Difference", ascending=False).head(3)
        top_controversial_list = top_controversial_books.rename(
            columns={
                "My Rating": "my_rating",
                "Average Rating": "average_rating",
                "Rating Difference": "rating_difference",
            }
        )[["Title", "Author", "my_rating", "average_rating", "rating_difference"]].to_dict("records")

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

        five_star_reviews_df = reviews_df[reviews_df["My Rating"] == 5]
        if not five_star_reviews_df.empty:
            print("   ⭐ Found 5-star reviews! Selecting the most positive from this elite group.")
            pos_review_row = five_star_reviews_df.loc[five_star_reviews_df["sentiment"].idxmax()]
        else:
            print("   ⚠️ No 5-star reviews found. Finding the most positive review from all ratings.")
            pos_review_row = reviews_df.loc[reviews_df["sentiment"].idxmax()]

        one_star_reviews_df = reviews_df[reviews_df["My Rating"] == 1]
        if not one_star_reviews_df.empty:
            print("   ⭐ Found 1-star reviews! Selecting the most negative from this group.")
            neg_review_row = one_star_reviews_df.loc[one_star_reviews_df["sentiment"].idxmin()]
        else:
            print("   ⚠️ No 1-star reviews found. Finding the most negative review from all ratings.")
            neg_review_row = reviews_df.loc[reviews_df["sentiment"].idxmin()]

        reviews_df.rename(columns={"My Review": "my_review"}, inplace=True)
        pos_review_row = reviews_df.loc[pos_review_row.name]
        neg_review_row = reviews_df.loc[neg_review_row.name]

        most_positive_review = pos_review_row[["Title", "Author", "my_review", "sentiment"]].to_dict()
        most_negative_review = neg_review_row[["Title", "Author", "my_review", "sentiment"]].to_dict()
        print(f"   😊 Most positive review is for '{most_positive_review['Title']}'")
        print(f"   😞 Most negative review is for '{most_negative_review['Title']}'")

    dna = {
        "reader_type": reader_type,
        "reader_type_scores": reader_type_scores,
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

