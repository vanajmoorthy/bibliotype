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

# --- NEW: PUBLISHER & TYPE DEFINITIONS ---
MAJOR_PUBLISHERS = {
    "penguin", "random house", "harpercollins", "simon & schuster", "hachette",
    "macmillan", "knopf", "doubleday", "viking", "vintage", "scribner", "atriabooks"
}

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
    Looks up a book's genres, publish year, and publisher. Uses a cache.
    """
    LOGIC_VERSION = "v4_details" # Bump the version for the new logic
    cache_key = f"book_details:{LOGIC_VERSION}:{author}:{title}".lower().replace(" ", "_")
    
    cached_data = cache.get(cache_key)
    if cached_data is not None:
        return cached_data

    clean_title = re.split(r"[:(]", title)[0].strip()
    query_title = quote_plus(clean_title)
    query_author = quote_plus(author)
    search_url = f"https://openlibrary.org/search.json?title={query_title}&author={query_author}"
    
    try:
        response = session.get(search_url, timeout=5)
        if response.status_code != 200: return {}
        data = response.json()
        if not data.get("docs"): return {}

        # Get the key for the most relevant edition
        edition_key = data["docs"][0].get("cover_edition_key")
        if not edition_key: return {}
        
        # Fetch the edition details, which has the data we need
        edition_url = f"https://openlibrary.org/books/{edition_key}.json"
        edition_response = session.get(edition_url, timeout=5)
        if edition_response.status_code != 200: return {}
        
        edition_data = edition_response.json()
        subjects = edition_data.get("subjects", [])
        
        genres = normalize_and_filter_genres(subjects)
        
        # Extract the new data points
        publish_year_str = edition_data.get("publish_date", "")
        publish_year = None
        if publish_year_str:
            match = re.search(r'\d{4}', publish_year_str)
            if match:
                try:
                    publish_year = int(match.group())
                except ValueError:
                    publish_year = None

        publisher = edition_data.get("publishers", [None])[0]

        book_details = {
            "genres": genres,
            "publish_year": publish_year,
            "publisher": publisher,
        }
        cache.set(cache_key, book_details, timeout=60 * 60 * 24 * 30)
        return book_details

    except requests.RequestException:
        return {}

def assign_reader_type(read_df, enriched_data, all_genres):
    """
    Calculates scores for various reader traits and determines the primary type.
    """
    scores = Counter()
    total_books = len(read_df)
    if total_books == 0:
        return "Not enough data"

    # --- SCORE CALCULATION HEURISTICS ---

    # 1. Volume-based scores
    if 'Date Read' in read_df.columns:
        books_per_year = read_df.dropna(subset=['Date Read'])['Date Read'].dt.year.value_counts().mean()
        if total_books > 75 and books_per_year > 40:
            scores['Rapacious Reader'] = 100 # Strong signal

    # 2. Page length-based scores
    if 'Number of Pages' in read_df.columns:
        long_books = read_df[read_df["Number of Pages"] > 600].shape[0]
        short_books = read_df[read_df["Number of Pages"] < 200].shape[0]
        scores['Tome Tussler'] += long_books * 2
        scores['Novella Navigator'] += short_books
    
    # 3. Genre-based scores
    genre_counts = Counter([CANONICAL_GENRE_MAP.get(g, g) for g in all_genres])
    scores['Fantasy Fanatic'] += genre_counts.get('fantasy', 0) + genre_counts.get('science fiction', 0)
    scores['Non-Fiction Ninja'] += genre_counts.get('non-fiction', 0)
    scores['Philosophical Philomath'] += genre_counts.get('philosophy', 0)
    
    # 4. Publication Year & Publisher scores (using enriched data)
    for index, book in read_df.iterrows():
        details = enriched_data.get(book['Title'])
        if not details: continue
        
        if details.get('publish_year'):
            if details['publish_year'] < 1970:
                scores['Classic Collector'] += 1
            elif details['publish_year'] > 2018:
                scores['Modern Maverick'] += 1
        
        if details.get('publisher'):
            if details['publisher'] is not None:
                is_major = any(major in details['publisher'].lower() for major in MAJOR_PUBLISHERS)
                if not is_major:
                    scores['Small Press Supporter'] += 2 # Give extra weight

    # 5. Diversity score
    if len(genre_counts) > 10:
        scores['Versatile Valedictorian'] = len(genre_counts) # Reward variety

    # 6. StoryGraph-specific scores
    if 'Moods' in read_df.columns:
        mood_count = read_df['Moods'].dropna().count()
        if (mood_count / total_books) > 0.5: # If over half of books have moods
            scores['Mood Maven'] += 5

    # Determine the winner
    if not scores:
        return "Eclectic Reader" # A nice default
    
    # Prioritize strong signals
    if scores['Rapacious Reader'] > 0:
        return 'Rapacious Reader'
        
    # Find the highest score among the rest
    primary_type = scores.most_common(1)[0][0]
    
    return primary_type

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

    # --- NEW: DATA ENRICHMENT STEP ---
    print("🎭 Enriching book data via Open Library API...")
    enriched_data = {}
    with ThreadPoolExecutor(max_workers=10) as executor, requests.Session() as session:
        titles = read_df["Title"]
        authors = read_df["Author"]
        results = executor.map(get_book_details_from_open_library, titles, authors, itertools.repeat(session))
        
        for title, details in zip(titles, results):
            if details:
                enriched_data[title] = details
    
    all_genres = list(itertools.chain.from_iterable(
        d.get('genres', []) for d in enriched_data.values()
    ))
    top_genres = dict(Counter([CANONICAL_GENRE_MAP.get(g, g) for g in all_genres]).most_common(10))

    # --- NEW: ASSIGN READER TYPE ---
    print("🧠 Assigning Reader Type...")
    reader_type = assign_reader_type(read_df, enriched_data, all_genres)
    print(f"   🏆 Determined Reader Type: {reader_type}")

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