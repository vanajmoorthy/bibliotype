from io import StringIO

import numpy as np
import pandas as pd


def generate_reading_dna(csv_file_content: str) -> dict:
    """
    Parses a Goodreads CSV export and generates a dictionary of reading stats.
    """
    try:
        # Use StringIO to treat the string content as a file
        df = pd.read_csv(StringIO(csv_file_content))
    except Exception as e:
        # Handle cases where the CSV is malformed
        raise ValueError(f"Could not parse CSV file. Error: {e}")

    # --- Data Cleaning & Preparation ---
    # We only care about books that have been read.
    df = df[df["Exclusive Shelf"] == "read"].copy()

    if df.empty:
        raise ValueError("No books found on the 'read' shelf in your CSV.")

    # Convert columns to the correct data types
    df["Date Read"] = pd.to_datetime(df["Date Read"], errors="coerce")
    df["My Rating"] = pd.to_numeric(df["My Rating"], errors="coerce")
    df["Average Rating"] = pd.to_numeric(df["Average Rating"], errors="coerce")
    df["Number of Pages"] = pd.to_numeric(df["Number of Pages"], errors="coerce")

    # Drop rows where essential data is missing for analysis
    df.dropna(subset=["Date Read", "My Rating", "Number of Pages"], inplace=True)

    # Extract the year read for grouping
    df["Year Read"] = df["Date Read"].dt.year

    # --- Analytics Generation ---
    total_books_read = len(df)
    total_pages_read = int(df["Number of Pages"].sum())
    average_rating = round(df["My Rating"].mean(), 2)

    # Books read per year
    books_by_year = df["Year Read"].value_counts().sort_index().to_dict()
    books_by_year = {
        str(k): int(v) for k, v in books_by_year.items()
    }  # Ensure JSON serializable

    # Ratings distribution (1-5 stars)
    ratings_dist = df["My Rating"].value_counts().sort_index().to_dict()
    ratings_dist = {str(k): int(v) for k, v in ratings_dist.items()}

    # Highlights
    highest_rated_book = df.loc[df["My Rating"].idxmax()].to_dict()
    lowest_rated_book = df.loc[df["My Rating"].idxmin()].to_dict()
    most_read_author = df["Author"].mode()[0]

    # "Controversial" Book (biggest difference between your rating and the average)
    df["Rating Difference"] = abs(df["My Rating"] - df["Average Rating"])
    most_controversial_book = df.loc[df["Rating Difference"].idxmax()].to_dict()

    df["Date Read"] = df["Date Read"].dt.strftime("%Y-%m-%d")

    # --- Assemble the DNA Dictionary ---
    dna = {
        "total_books_read": total_books_read,
        "total_pages_read": total_pages_read,
        "average_rating": average_rating,
        "books_by_year": books_by_year,
        "ratings_distribution": ratings_dist,
        "most_read_author": most_read_author,
        "highest_rated_book": {
            "title": highest_rated_book.get("Title"),
            "author": highest_rated_book.get("Author"),
            "rating": highest_rated_book.get("My Rating"),
        },
        "lowest_rated_book": {
            "title": lowest_rated_book.get("Title"),
            "author": lowest_rated_book.get("Author"),
            "rating": lowest_rated_book.get("My Rating"),
        },
        "most_controversial_book": {
            "title": most_controversial_book.get("Title"),
            "author": most_controversial_book.get("Author"),
            "my_rating": most_controversial_book.get("My Rating"),
            "avg_rating": round(most_controversial_book.get("Average Rating"), 2),
            "diff": round(most_controversial_book.get("Rating Difference"), 2),
        },
        # Add a raw books list for potential future use or detailed display
        "book_list": df[
            ["Title", "Author", "My Rating", "Number of Pages", "Date Read"]
        ].to_dict("records"),
    }

    # Clean up NaN in the final dict before returning
    # A simple way is to re-convert to pandas and fillna, but we can do it manually for clarity
    def clean_nan(d):
        for k, v in d.items():
            if isinstance(v, dict):
                clean_nan(v)
            elif isinstance(v, float) and np.isnan(v):
                d[k] = None
        return d

    return clean_nan(dna)
