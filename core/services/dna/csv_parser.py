import math

import pandas as pd

from .utils import _isbn_to_isbn13

# Goodreads column names serve as the internal canonical schema.
# All CSV sources are normalized to these names so downstream code
# (calculate_full_dna, assign_reader_type, save_anonymous_session_data)
# works identically regardless of source.
STORYGRAPH_TO_GOODREADS = {
    "Authors": "Author",
    "Read Status": "Exclusive Shelf",
    "Star Rating": "My Rating",
    "Last Date Read": "Date Read",
    "Review": "My Review",
    "ISBN/UID": "ISBN13",
    "Format": "Binding",
}


def _detect_and_normalize_csv(df):
    """Detect CSV source (Goodreads vs StoryGraph) and normalize columns to Goodreads schema.

    Always normalizes ISBN13 column to 13-digit ISBNs so cross-platform dedup
    works (Goodreads exports use ISBN-10s, StoryGraph uses ISBN-13s).
    """
    if "Exclusive Shelf" in df.columns:
        if "ISBN13" in df.columns:
            df["ISBN13"] = df["ISBN13"].apply(lambda x: _isbn_to_isbn13(x) if pd.notna(x) else pd.NA)
        return df, "goodreads"
    elif "Read Status" in df.columns:
        df = df.rename(columns=STORYGRAPH_TO_GOODREADS)
        # Multi-author: take first author from comma-separated list
        # StoryGraph uses "First Last" format (not "Last, First"), so comma = author separator
        df["Author"] = df["Author"].str.split(",").str[0].str.strip()
        # Float ratings -> int using round-half-up (not banker's rounding)
        # 4.5 -> 5, 3.5 -> 4, 0.5 -> 1 (preserves user intent for half-star ratings)
        ratings = pd.to_numeric(df["My Rating"], errors="coerce")
        df["My Rating"] = ratings.apply(lambda x: int(math.floor(x + 0.5)) if pd.notna(x) else pd.NA).astype("Int64")
        # Validate ISBN: keep digits (and X for ISBN-10 check digit), length must be 10 or 13.
        # StoryGraph ISBN/UID may contain non-ISBN internal identifiers (rejected by helper).
        # ISBN-10 inputs are upgraded to ISBN-13 for cross-platform dedup with Goodreads.
        if "ISBN13" in df.columns:
            df["ISBN13"] = df["ISBN13"].apply(lambda x: _isbn_to_isbn13(x) if pd.notna(x) else pd.NA)
        # Add missing columns as NaN
        for col in ["Number of Pages", "Original Publication Year", "Average Rating"]:
            if col not in df.columns:
                df[col] = pd.NA
        return df, "storygraph"
    else:
        raise ValueError("Unrecognized CSV format. Please upload a Goodreads or StoryGraph export.")
