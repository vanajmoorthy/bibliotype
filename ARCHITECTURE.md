# How Bibliotype Works: A Deep Dive into the Architecture

Bibliotype is a web application that takes a CSV export of your reading history from Goodreads or StoryGraph and generates a personalised "Reading DNA" dashboard. It tells you what kind of reader you are, generates poetic AI vibes for your reading taste, recommends books based on readers who share your literary DNA, and compares your habits against the community.

This document is a comprehensive walkthrough of every system, algorithm, and design decision that makes it work.

---

## Table of Contents

1. [The Upload Pipeline](#1-the-upload-pipeline)
2. [The DNA Analysis Engine](#2-the-dna-analysis-engine)
3. [Reader Type Classification](#3-reader-type-classification)
4. [Book Syncing and Normalization](#4-book-syncing-and-normalization)
5. [Book Enrichment: Open Library and Google Books](#5-book-enrichment-open-library-and-google-books)
6. [Genre Canonicalization](#6-genre-canonicalization)
7. [Author Mainstream Detection](#7-author-mainstream-detection)
8. [Publisher Research](#8-publisher-research)
9. [AI Vibe Generation](#9-ai-vibe-generation)
10. [The Percentile Engine](#10-the-percentile-engine)
11. [Top Books Scoring](#11-top-books-scoring)
12. [The Recommendation Engine](#12-the-recommendation-engine)
13. [User Similarity Algorithm](#13-user-similarity-algorithm)
14. [Anonymous vs Authenticated Flows](#14-anonymous-vs-authenticated-flows)
15. [The Anonymization Pipeline](#15-the-anonymization-pipeline)
16. [Caching Architecture](#16-caching-architecture)
17. [Analytics and Observability](#17-analytics-and-observability)
18. [The Frontend: Neobrutalist Design](#18-the-frontend-neobrutalist-design)
19. [Infrastructure and Deployment](#19-infrastructure-and-deployment)
20. [Testing Strategy](#20-testing-strategy)

---

## 1. The Upload Pipeline

Everything starts with a CSV file. The user drags their Goodreads or StoryGraph export onto a drop zone (or clicks to browse), and the frontend submits it as a multipart POST to `upload_view`.

### Validation

The server performs two checks before anything else:

- **File extension**: Must end in `.csv`. Anything else gets a flash message and a redirect.
- **File size**: Must be under 10MB. Goodreads exports for even prolific readers rarely exceed a few hundred kilobytes, so this is a generous ceiling that mostly protects against abuse.

### Branching: Authenticated vs Anonymous

After validation, the flow diverges based on whether the user is logged in.

**Authenticated users:**
1. Any stale `dna_data` in the Django session is cleared (so the dashboard will pull from the database, not the session).
2. The CSV content is decoded to UTF-8 and dispatched as a Celery task: `generate_reading_dna_task.delay(csv_content, user.id)`.
3. The task ID is saved on the user's profile as `pending_dna_task_id` so the frontend can poll for progress.
4. The user is redirected to `/dashboard/?processing=true`, which shows a loading screen with a progress bar.

**Anonymous users:**
1. The CSV is dispatched as a Celery task with `user_id=None` and the session key: `generate_reading_dna_task.delay(csv_content, None, session_key)`.
2. The task ID is saved in the session as `anonymous_task_id`.
3. The user is redirected to `/task/<task_id>/`, a dedicated polling page that checks for results.

### Progress Tracking

The Celery task reports progress back to the frontend through Celery's result backend. Inside `generate_reading_dna_task`, a callback function is passed into the analysis engine:

```python
def progress_cb(current: int, total: int, stage: str):
    self.update_state(state="PROGRESS", meta={
        "current": current, "total": total, "stage": stage
    })
```

The frontend polls every 3 seconds. The progress bar uses a tweening algorithm that smoothly animates toward the target percentage (with a 0.2 multiplier per frame), preventing the jarring jumps that come from polling at irregular intervals. If no exact percentage is available, the frontend estimates based on the stage name: "Syncing books" maps to ~60%, "Crunching stats" to ~70%, "Finishing up" to ~90%.

Stages cycle through four animated headings: "Sequencing Your Bibliotype...", "Analyzing Your Reading Patterns...", "Discovering Your Literary DNA...", and "Mapping Your Book Preferences..." with scale and fade transitions every 4 seconds.

---

## 2. The DNA Analysis Engine

The heart of the application is `calculate_full_dna()` in `core/services/dna_analyser.py`. This function takes a CSV string and produces a large dictionary (the "DNA") containing everything the dashboard needs.

### CSV Parsing

The CSV is loaded into a pandas DataFrame. The first filter is critical: only books on the "read" shelf are kept. Goodreads exports include "to-read", "currently-reading", and custom shelves, but DNA analysis only makes sense for books you've actually finished.

```python
df = pd.read_csv(StringIO(csv_file_content))
read_df = df[df["Exclusive Shelf"] == "read"].copy()
```

If the read shelf is empty, the function raises a `ValueError` immediately.

### Data Fingerprinting

Before doing any work, the engine generates a SHA256 hash of the user's library. It sorts all `title + author` strings alphabetically, concatenates them, and hashes the result. This fingerprint is used later to determine whether the AI-generated vibe needs regenerating. If a user re-uploads the same library, the hash won't change and the expensive LLM call is skipped entirely.

```python
book_fingerprint_list = sorted([f"{row['Title']}{row['Author']}" for _, row in read_df.iterrows()])
fingerprint_string = "".join(book_fingerprint_list)
new_data_hash = hashlib.sha256(fingerprint_string.encode()).hexdigest()
```

### Column Preparation

Several columns are coerced to their proper types: `My Rating` and `Number of Pages` become numeric (with `errors="coerce"` to handle blanks gracefully), `Date Read` becomes a datetime, and `Original Publication Year` is extracted if present. The `My Review` column is filled with empty strings where null.

### The Pipeline

After parsing, the function runs through these stages:

1. **Book syncing** — Creates or updates Author, Book, and UserBook records in the database.
2. **Reader type assignment** — Scores the user against 11 reader archetypes.
3. **Statistics calculation** — Total books, pages, average length, average publication year.
4. **Community percentile calculation** — Where the user stands relative to all other users.
5. **Genre and author analysis** — Top 10 genres (canonicalized) and top 10 authors.
6. **Ratings analysis** — Distribution, average, controversial books.
7. **Sentiment analysis** — VADER sentiment on reviews to find most positive/negative.
8. **Yearly reading stats** — Books per year, average rating per year.
9. **Mainstream score** — Percentage of books from mainstream authors/publishers.
10. **Vibe generation** — AI-generated reading vibe (or cached version).
11. **Top books calculation** — Top 5 books by combined rating + sentiment score.

The output is a dictionary with over 20 keys. Here's the shape:

```python
{
    "user_stats": {"total_books_read", "total_pages_read", "avg_book_length", "avg_publish_year"},
    "bibliotype_percentiles": {...},
    "global_averages": {...},
    "most_niche_book": {"title", "author", "read_count"},
    "reader_type": "Fantasy Fanatic",
    "reader_type_explanation": "...",
    "top_reader_types": [{"type", "score"}, ...],
    "top_genres": [("fantasy", 42), ("thriller", 28), ...],
    "top_authors": [("Brandon Sanderson", 8), ...],
    "average_rating_overall": 3.87,
    "ratings_distribution": {"1": 2, "2": 5, "3": 18, "4": 45, "5": 30},
    "top_controversial_books": [...],
    "most_positive_review": {...},
    "most_negative_review": {...},
    "stats_by_year": [{"year": 2023, "count": 42, "avg_rating": 3.9}, ...],
    "mainstream_score_percent": 67,
    "reading_vibe": ["dusty maps and forgotten prophecies", ...],
    "vibe_data_hash": "a1b2c3d4...",
}
```

---

## 3. Reader Type Classification

Every user is assigned one of 11 reader types. The system uses a scoring approach: a `Counter` accumulates points for each archetype based on the user's reading patterns, and the type with the highest score wins.

### The 11 Reader Types

| Type | How You Earn Points |
|---|---|
| **Rapacious Reader** | 75+ total books AND 40+ books/year average. Gets an automatic score of 100 (always wins if triggered). |
| **Tome Tussler** | +2 points per book over 490 pages. |
| **Novella Navigator** | +1 point per book under 200 pages. |
| **Fantasy Fanatic** | Points from "fantasy" + "science fiction" genre counts. |
| **Non-Fiction Ninja** | Points from "non-fiction" genre count. |
| **Philosophical Philomath** | Points from "philosophy" genre count. |
| **Nature Nut Case** | Points from "nature" genre count. |
| **Social Savant** | Points from "social science" genre count. |
| **Self Help Scholar** | Points from "self-help" genre count. |
| **Classic Collector** | +1 per book published before 1970. |
| **Modern Maverick** | +1 per book published after 2018. |
| **Small Press Supporter** | +1 per book from a non-mainstream publisher. |
| **Versatile Valedictorian** | +15 bonus if 10+ unique canonical genres. |
| **Eclectic Reader** | Fallback type if all scores are zero. |

Genre-based scoring uses the canonicalized genre counts, not raw API subjects. This means "urban fantasy", "wizards", and "magic" all count toward the Fantasy Fanatic score because they all map to the canonical "fantasy" genre.

The Rapacious Reader type has a special override: if a user has read 75+ books total and averages 40+ books per year (calculated from the `Date Read` column), the score is set to 100, which always beats everything else. This recognises truly voracious readers.

Each user also gets explanatory text, randomly selected from 1-2 pre-written descriptions per type stored in `READER_TYPE_DESCRIPTIONS`.

---

## 4. Book Syncing and Normalization

For every book in the CSV, the engine creates or updates database records. This is where the application builds its shared knowledge base.

### Author Normalization

Author names are normalized using `Author._normalize()`: lowercased, special characters removed, whitespace stripped. "J.R.R. Tolkien" and "JRR Tolkien" both become "jrr tolkien". The system uses `get_or_create` on `normalized_name` to prevent duplicates.

When a new author is created, the system immediately dispatches a background task to check their mainstream status: `check_author_mainstream_status_task.delay(author.id)`. This happens asynchronously so it doesn't slow down the CSV processing.

### Book Normalization

Book titles go through a similar normalization: lowercased, brackets and parentheses removed (to strip edition markers like "(Paperback)"), special characters stripped, all whitespace removed. "The Lord of the Rings: The Fellowship of the Ring" and "the lord of the rings the fellowship of the ring" both normalize to the same string.

Books use `update_or_create` keyed on `(normalized_title, author)`, ensuring that different editions of the same book by the same author don't create duplicates.

### Threading Model

Book processing uses a `ThreadPoolExecutor` with a single worker. This might seem pointless, but it's deliberate: SQLite (used in local development) doesn't handle concurrent writes well. A single-threaded executor provides the structure for future parallelism while avoiding database lock issues today.

### Read Count Tracking

Every time a book appears in someone's upload, its `global_read_count` is atomically incremented using Django's `F()` expression: `Book.objects.filter(pk=book.pk).update(global_read_count=F("global_read_count") + 1)`. This count powers the "Most Niche Read" feature — the book with the lowest `global_read_count` in a user's library.

---

## 5. Book Enrichment: Open Library and Google Books

Books uploaded via CSV arrive with minimal metadata: title, author, page count (sometimes), rating, and publication year (sometimes). The enrichment pipeline fills in the gaps using two external APIs.

### Open Library (First Pass)

Three endpoints are queried in sequence:

1. **Search** (`/search.json`): Finds the book by title and author, returning a `cover_edition_key` and `first_publish_year`.
2. **Work** (`/[work_key].json`): Fetches the canonical work record, which contains raw `subjects` (genres).
3. **Edition** (`/books/[edition_key].json`): Fetches edition-specific data: page count, publisher, publish date, ISBN-13, and ISBN-10.

The publish date is extracted from a free-text field using regex (`\d{4}`) since Open Library stores dates in inconsistent formats like "January 1, 2005" or "2005" or "c2005".

### Google Books (Second Pass)

If a Google Books API key is configured, a second enrichment pass runs:

- **By ISBN** (preferred): `isbn:{isbn13}` query.
- **By title/author** (fallback): `intitle:{title}+inauthor:{author}`.

Google Books provides `ratingsCount`, `averageRating`, and `categories`. The categories are generally more accurate than Open Library's subjects, so when Google provides genre data, it takes priority.

A `google_books_last_checked` timestamp prevents redundant API calls — once a book has been checked, it won't be re-queried unless explicitly requested.

### Rate Limiting

Both APIs use a `slow_down` parameter. When enabled, a `time.sleep(1.2)` is inserted after each call. This is used during batch enrichment operations (management commands) to stay within API rate limits. During normal user uploads, enrichment is currently deferred to separate background tasks.

---

## 6. Genre Canonicalization

Raw genre data from APIs is messy. Open Library might tag a book with "Fiction, fantasy, epic", "Wizards", "Imaginary wars and battles", and "Middle Earth (Imaginary place)". Google Books might label it "Fiction / Fantasy / Epic". The canonicalization system maps this chaos to a clean set of 35 canonical genres.

### The Canonical Genre Map

`CANONICAL_GENRE_MAP` is built from `GENRE_ALIASES`, a dictionary mapping each canonical genre to a set of aliases. There are 300+ alias entries across 35 genres. Examples:

- `"fantasy"` includes: "urban fantasy", "wizards", "dragons", "magic", "dark fantasy", "epic fantasy", "fairy tales", "mythical creatures"
- `"science fiction"` includes: "cyberpunk", "space opera", "dystopian fiction", "robots", "time travel"
- `"thriller"` includes: "mystery", "detective fiction", "spy stories", "assassins", "criminal investigation", "suspense"
- `"social science"` includes: "feminist theory", "race relations", "popular culture", "current events", "anthropology"

### The Matching Algorithm

For each raw subject string:

1. Check if it's in `EXCLUDED_GENRES` (~980 entries). This set filters out Library of Congress codes ("813/.54"), character names ("Harry Potter", "Frodo Baggins"), fictional places ("Narnia", "Middle Earth"), format tags ("audiobooks", "large print"), award metadata ("Hugo Award"), and extremely broad terms ("fiction", "literature", "general").

2. Sort all aliases by length, longest first. This prevents "science" from matching before "science fiction".

3. For each alias, build a word-boundary regex (`\b{alias}\b`) and test it against the subject. Word boundaries prevent partial matches — "science" won't match inside "social science".

4. On the first match, map to the canonical genre and stop. One match per subject.

### Genre Priority

After canonicalization, books often have too many genres. A priority list determines which survive:

```
fantasy > science fiction > thriller > horror > historical fiction > romance >
humorous fiction > young adult > short stories > biography > philosophy >
psychology > history > social science > non-fiction > science > nature >
art & music > travel > classics > plays & drama > children's literature
```

The system takes the top 5 genres by priority. There's one exception: if the 6th genre is a highly specific fiction genre (fantasy, sci-fi, thriller, horror, historical fiction, or romance), it keeps 6 genres. The rationale is that specific fiction genres carry more signal than generic non-fiction ones.

---

## 7. Author Mainstream Detection

The mainstream score on a user's dashboard shows what percentage of their library comes from mainstream authors and publishers. But how does the system decide who's mainstream?

### The Algorithm

For each author, two data points are collected:

1. **Work count** from Open Library's author search API — how many works are attributed to this author.
2. **Monthly Wikipedia pageviews** — the 90-day total divided by 3, queried from Wikimedia's pageview API.

### Two Paths to Mainstream

**Path 1: Prolific and Popular.** An author with 10+ works AND 2,000+ average monthly Wikipedia pageviews is mainstream. This captures working commercial authors like Stephen King, James Patterson, or Colleen Hoover.

**Path 2: Cultural Icon.** An author with 50,000+ average monthly pageviews is mainstream regardless of work count. This exists for the Harper Lee problem — authors who wrote very few books but are so culturally significant that their Wikipedia pages get enormous traffic.

The result includes a human-readable reason: "Prolific author (10+ works, 2k+ views)" or "Cultural icon (50k+ avg monthly views)" or "Did not meet prolific or cultural icon thresholds".

---

## 8. Publisher Research

Publisher mainstream detection is more complex because publisher names in book metadata are inconsistent. "Penguin Books", "Penguin Random House", "Penguin Classics", and "Penguin Press" are all the same parent company.

### The Hierarchy

The codebase maintains a hardcoded hierarchy of the "Big 5" publishers and their subsidiaries:

- **Penguin Random House**: Penguin Books, Random House, Viking Press, Knopf Doubleday, Crown Publishing, Ballantine Books, Bantam Dell, Dutton, Berkley, Riverhead, Vintage, Anchor, Signet, G.P. Putnam's Sons
- **Hachette Livre**: Grand Central Publishing, Little Brown and Company, Orbit Books, Orion, Perseus
- **HarperCollins**: William Morrow, Avon Books, Harlequin, Ecco Press, Harvill Secker
- **Macmillan Publishers**: Farrar Straus and Giroux, St. Martin's Press, Tor Books, Picador
- **Simon & Schuster**: Scribner, Atria Books, Gallery Books

Plus smaller notable publishers: Bloomsbury, Scholastic, Oxford University Press, Pearson, Wiley, Routledge, Grove Atlantic.

### LLM-Assisted Research

For publishers not in the hardcoded list, the system tries a multi-step research process:

1. **Wikipedia search** with three query variations: `"{name} (publisher)"`, `"{name} (imprint)"`, and just `"{name}"`. The first non-disambiguation page wins.

2. **Gemini LLM analysis** of the Wikipedia summary. The prompt asks the model to determine whether the publisher is one of the Big 5 or an imprint of one, and to identify the parent company. The response is structured JSON with `is_mainstream`, `parent_company_name`, and `reasoning`.

This two-step approach (structured data lookup + LLM reasoning) handles edge cases that a simple lookup can't: obscure imprints, recently acquired publishers, and regional variants.

---

## 9. AI Vibe Generation

The "reading vibe" is the most visible AI feature — four short, evocative phrases displayed at the top of the dashboard. "Dusty maps and forgotten prophecies" or "the scent of old paper" rather than "you read a lot of fantasy".

### The Prompt

The system sends a few-shot prompt to Gemini 2.5 Flash with structured JSON output. The prompt includes:

- The user's primary reader type
- Top 3 genres
- Top 3 authors
- A general "era" (classic if average publication year < 1980, modern otherwise)

The instructions are specific: phrases must be 2-6 words, all lowercase, no punctuation, and they must evoke the *feeling* of the reading habits rather than describing them directly. The prompt includes both a good example and a bad example to guide the model.

### Caching

Vibe generation is the most expensive operation (in terms of both latency and API cost), so it's aggressively cached. The system compares the SHA256 hash of the current library against the stored `vibe_data_hash` on the user's profile. If the hashes match, the cached vibe is returned without touching the API.

For anonymous users, there's no profile to cache against, so vibes are always generated fresh.

### Fallback

If the API key isn't configured, the function returns `["vibe generation disabled", "please configure api key"]`. If the API call fails or returns malformed JSON, it returns `["error generating vibe", "api call failed"]`. The dashboard gracefully handles these placeholder values.

---

## 10. The Percentile Engine

The dashboard shows how each user compares to the community: "Your average book length is longer than 73.2% of other Bibliotype readers."

### Distribution Tracking

Rather than storing individual user statistics and computing percentiles on the fly (which would be O(n) per request), the system maintains pre-computed distributions in a singleton `AggregateAnalytics` model.

Three distributions are tracked, each as a JSON dictionary of bucket counts:

- **Average book length**: Buckets of 50 pages. "200-249": 15 means 15 users have an average book length between 200 and 249 pages.
- **Average publication year**: Buckets of 10 years. "2010-2019": 45.
- **Total books read**: Buckets of 25 books. "50-74": 22.

### Updating

Every time a user's DNA is generated, `update_analytics_from_stats()` is called. It increments the `total_profiles_counted` and adds 1 to the appropriate bucket for each metric. This is a simple O(1) update.

### Percentile Calculation

The percentile formula uses the "percentage of users below" approach, with a half-bucket correction for the user's own bucket:

```
percentile = ((users_below + same_bucket_count / 2) / total_other_users) * 100
```

The half-bucket correction assumes uniform distribution within a bucket. If 20 users are in the "200-249" bucket and you're also in that bucket, 10 of them are counted as below you.

A minimum of 10 other users is required before percentiles are shown. Below that threshold, the dashboard displays a "not enough data yet" message.

---

## 11. Top Books Scoring

Each user's top 5 books are determined by a composite score that weighs explicit ratings, review sentiment, and fallback heuristics.

### The Formula

For each book in the user's library:

| Component | Score | Condition |
|---|---|---|
| 5-star rating | +100 | `user_rating == 5` |
| 4-star rating | +80 | `user_rating == 4` |
| 3-star rating | +45 | `user_rating == 3` |
| 2-star rating | +30 | `user_rating == 2` |
| 1-star rating | +15 | `user_rating == 1` |
| Review sentiment | `compound * 30` | Review exists and length > 15 characters |
| No data fallback | +10 | No rating AND no review |

The sentiment score uses VADER (Valence Aware Dictionary and sEntiment Reasoner), which returns a compound score from -1.0 to +1.0. At maximum positive sentiment, a review contributes +30 points. A scathing review contributes -30.

This means a 5-star book with a glowing review can score up to 130 points, while an unrated book with no review gets only 10 points. The top 5 by score are marked as `is_top_book=True` with positions 1-5.

---

## 12. The Recommendation Engine

The recommendation system is the most algorithmically complex part of the application. It's built as a class (`RecommendationEngine`) with a multi-stage pipeline.

### Candidate Collection: Three Sources

**Source 1: Similar Registered Users (Highest Quality)**

The engine finds up to 30 similar users using the similarity algorithm (described in the next section). For each similar user, it collects books that are either marked as top books OR rated 4+ stars. Each book gets a weight equal to the similarity score, with a 1.5x multiplier if it's one of the recommender's top books.

**Source 2: Anonymized Profiles (Medium Quality)**

Up to 100 anonymized profiles (from expired anonymous sessions) are sampled. Similarity is calculated against each, and top books from matching profiles are collected. These get a 0.8x weight multiplier, reflecting the lower confidence of anonymized data.

**Source 3: Fallback Candidates**

If the first two sources don't provide enough candidates:
- Books by the user's favourite non-oversaturated authors (3 books per author, from top 5 authors with fewer than 3 books already read).
- Highly-rated books (4.0+) from favourite genres (5 books per genre, from top 3 genres).

Fallback candidates get fixed low weights (0.4 for author-based, 0.3 for genre-based).

### Scoring Formula

Each candidate book's final score is a composite of five factors:

```
final_score = sqrt(total_weight) + ln(recommender_count + 1) * 0.1
            + max(0, rating - 3.5) * 0.15
            + genre_alignment * 0.3
            + recency_factor * 0.1
```

**Base score** uses the square root of accumulated weight. This implements diminishing returns: two moderately similar users recommending the same book don't outweigh one highly similar user. The sublinear accumulation prevents the algorithm from being dominated by popularity.

**Popularity boost** uses the natural log of recommender count. Moving from 1 to 2 recommenders helps more than moving from 9 to 10.

**Quality factor** rewards books rated above 3.5 stars. A 4.5-star book gets a 0.15 bonus; a 3.5-star book gets nothing.

**Genre alignment** measures how well the book's genres match the user's preferences. The user's genre weights are normalized, and the book's genres are summed against those weights. A perfect match (book has user's top genres) scores 1.0; a book with no genre data scores 0.3.

**Recency factor** gives a small boost to newer books: 0.15 for books 0-3 years old, 0.10 for 4-10 years, 0.05 for 11-20 years, 0 for older. Classics aren't penalised; they just don't get the boost.

### Quality Filters

Before scoring, candidates pass through five filters:

1. **Already read** — Excluded if in user's reading history.
2. **Disliked** — Excluded if user rated the book 2 stars or below.
3. **Series saturation** — Excluded if the user has 3+ books from the same series. Series are detected by splitting titles on common indicators (" book ", " vol ", "#", ":", " - ") and comparing the first 2-3 significant words.
4. **Author saturation** — If 4+ books from the same author, only recommend if the book is rated 4.3+ stars.
5. **Minimum quality** — Excluded if average rating is below 3.5 stars.

### Diversity Filter

After scoring and ranking, a diversity filter prevents monotonous recommendations:

- **Max 3 books per genre** (enforced after the first 50% of results are filled).
- **Max 2 books per author** (same enforcement point).
- **Exception**: A book can bypass diversity constraints if its score is at least 80% of the top-scoring candidate. Exceptional recommendations override diversity rules.

### Explanation Generation

Each recommendation gets human-readable explanation components:

- **Shared books**: "You share 7 books in common" (if from a similar user with shared books).
- **Genre match**: "Matches your interest in Science Fiction, Fantasy" (if genre alignment > 0.6).
- **Popularity**: "Loved by 5 similar readers" (if 3+ recommenders).
- **Rating**: "Highly rated (4.7 stars)" (if average rating >= 4.2).

### Confidence Score

Separate from the final score, a confidence value tells the user how reliable the recommendation is:

```
confidence = min(max_similarity + ln(recommender_count + 1) * 0.1, 1.0)
```

This is capped at 1.0 and displayed as a percentage badge on the recommendation card.

---

## 13. User Similarity Algorithm

The similarity system uses seven components, each measuring a different aspect of reading taste. The weights are adaptive — they shift based on available data.

### The Seven Components

**1. Shared Book Correlation (weight: up to 0.35)**

Pearson correlation on ratings for books both users have read. Requires at least 5 shared books with ratings on both sides. The correlation (ranging from -1 to +1) is normalized to 0-1 space: `(correlation + 1) / 2`.

The weight scales with confidence: `0.35 * min(shared_rated_count / 20, 1.0)`. With 5 shared books, the weight is only ~0.09. At 20+ shared books, it reaches the full 0.35. This prevents small sample sizes from dominating.

**2. Jaccard Overlap (weight: 0.15 or 0.25)**

Classic set overlap: `|intersection| / |union|` of all book IDs. If correlation data is unavailable (too few shared rated books), the Jaccard weight increases from 0.15 to 0.25 to compensate.

**3. Top Books Overlap (weight: 0.20)**

Same as Jaccard but only on top-rated books. This captures the idea that two readers who share the same *favourite* books are more similar than two who share the same mediocre reads.

**4. Genre Similarity (weight: 0.15)**

Cosine similarity on genre weight vectors. Each user's genres are represented as a Counter where the weight is the user's rating of each book (or 3 if unrated). The cosine similarity measures the angle between these vectors in high-dimensional genre space.

**5. Author Similarity (weight: 0.15)**

Same cosine similarity approach, but on author weight vectors.

**6. Rating Pattern Similarity (weight: 0.08)**

Cosine similarity on rating distributions. If both users are "harsh raters" (lots of 2s and 3s) or "generous raters" (lots of 4s and 5s), this component will be high. It captures rating behaviour independent of what they read.

**7. Reading Era Similarity (weight: 0.07)**

Compares the decade distribution of publication years. Each user's book years are grouped into decades (2020s, 2010s, 2000s, etc.), weighted by rating, and normalized into proportions. Similarity is `1 - (Manhattan distance / 2)`. Two users who both read mostly modern books will score high here.

### Adaptive Weighting

If a component can't be calculated (e.g., no shared rated books for correlation, or no publication year data for era), its weight drops to zero and the remaining weights are renormalized to sum to 1.0. This ensures the final similarity score always uses whatever data is available.

### Match Quality Labels

The raw similarity score (0-1) maps to human-readable labels:

| Score | Label |
|---|---|
| 0.80+ | Literary twin |
| 0.65-0.79 | Kindred reader |
| 0.50-0.64 | Some shared tastes |
| 0.35-0.49 | Some overlap |
| 0.20-0.34 | Different preferences |
| Below 0.20 | Opposite tastes |

### Performance Optimizations

Computing similarity is expensive when comparing against hundreds of users. The system uses several optimizations:

- **Pre-built contexts**: All user data (book IDs, ratings, genre weights, author weights, decade distributions) is pre-computed into a flat dictionary. No database queries during comparison.
- **Bulk loading**: `_bulk_build_user_contexts(user_ids)` fetches all books for all candidate users in a single query with `select_related` and `prefetch_related`, then groups by user ID in Python. This reduces database round-trips from O(n) to O(1).
- **Candidate filtering**: Only users who share at least one book are considered. The query `User.objects.filter(user_books__book_id__in=current_user_book_ids).distinct()[:500]` narrows the pool before any similarity computation.

---

## 14. Anonymous vs Authenticated Flows

The application supports two parallel user experiences. The key design goal is that anonymous users get a full experience without signing up, but their data is ephemeral.

### Storage Differences

| Aspect | Authenticated | Anonymous |
|---|---|---|
| DNA data | `UserProfile.dna_data` (JSONField, persistent) | `request.session["dna_data"]` + `AnonymousUserSession` (7-day expiry) |
| Book records | `UserBook` rows with ratings, reviews, dates | Book IDs stored in `AnonymousUserSession.books_data` as a JSON array |
| Top books | `UserBook.is_top_book` flag + `top_book_position` | IDs in `AnonymousUserSession.top_books_data` |
| Recommendations | Pre-generated async, stored in `profile.recommendations_data` | Generated on-the-fly when viewing dashboard |
| Vibe caching | Hash-based — reuses if library unchanged | Always regenerated |

### The Claiming Flow

When an anonymous user signs up, their data needs to be transferred. This is handled by `claim_anonymous_dna_task`, a Celery task with up to 5 retries and 10-second countdown between attempts.

The retry mechanism exists because the DNA generation task might still be running when the user clicks "Sign Up". The claim task checks Redis cache first (`dna_result_{task_id}`), then falls back to `AsyncResult(task_id).get()`. If neither is ready, it retries.

On successful claim:
1. DNA is saved to the new `UserProfile` via `_save_dna_to_profile()`.
2. `UserBook` records are created from the `AnonymousUserSession` — converting the flat list of book IDs into proper relational records.
3. Top book flags are set on the corresponding `UserBook` entries.
4. `pending_dna_task_id` is cleared.
5. Recommendation generation is triggered asynchronously.

### Session Data on Login

If a user generates DNA anonymously and then logs into an existing account, the `login_view` checks for `dna_data` in the session. If found and the user doesn't already have DNA, it's saved directly to their profile.

---

## 15. The Anonymization Pipeline

Anonymous sessions expire after 7 days. Rather than simply deleting them, the system extracts useful aggregate data into permanent `AnonymizedReadingProfile` records. These profiles feed into the recommendation engine as a secondary data source.

### What's Preserved

- Total books read, reader type
- Genre and author distributions (as JSON dictionaries)
- Average rating, average book length, average publication year
- Mainstream score, genre diversity count
- Top book IDs

### What's Discarded

- Session key, IP address, user agent (privacy)
- Individual book read dates and timestamps
- Raw review text (only sentiment analysis results were used)
- The full DNA data dictionary

### Quality Gate

Sessions with fewer than 10 books are not anonymized. The reading patterns from a tiny library aren't meaningful enough to help the recommendation engine, and they'd dilute the quality of anonymized profiles.

### Schedule

The `anonymize_expired_sessions_task` runs daily at 2:00 AM UTC via Celery Beat. It queries for sessions where `expires_at < now()` and `anonymized = False`, processes each one, marks it as anonymized, and cleans up sessions that have been anonymized for more than 30 days.

---

## 16. Caching Architecture

The caching strategy is multi-layered, combining Redis, database fields, and hash-based invalidation.

### Redis Cache Layer

| Key Pattern | TTL | Purpose |
|---|---|---|
| `dna_result_{task_id}` | 1 hour | Anonymous DNA results (bridge between task completion and session retrieval) |
| `session_key_{task_id}` | 1 hour | Maps task IDs to session keys for the claiming flow |
| `user_recommendations_{user_id}_{limit}` | 15 min | Computed recommendations |
| `similar_users_{user_id}_{top_n}_{min_similarity}` | 30 min | User similarity results |
| `anon_profiles_sample_{user_id}` | 1 hour | Sampled anonymized profiles (100 profiles) |
| `public_users_for_recs_sample` | 30 min | List of public, recommendation-eligible users |

### Database Cache Layer

| Field | Model | Invalidation |
|---|---|---|
| `dna_data` | `UserProfile` | Overwritten on re-upload |
| `reading_vibe` + `vibe_data_hash` | `UserProfile` | Hash comparison — only regenerated when book fingerprint changes |
| `recommendations_data` + `recommendations_generated_at` | `UserProfile` | Cleared by `_save_dna_to_profile()`, regenerated by async task |

### Graceful Degradation

Every Redis operation goes through `safe_cache_get()` and `safe_cache_set()`, which catch all exceptions, log a warning, and track the error via PostHog analytics. If Redis goes down, the application continues functioning — queries just run from scratch instead of hitting cache. No user-facing errors, just higher latency.

```python
def safe_cache_get(key, default=None):
    try:
        return cache.get(key, default)
    except Exception as e:
        logger.warning(f"Cache get failed for key '{key}': {e}")
        track_redis_cache_error(operation="get", key=key, error_type=type(e).__name__)
        return default
```

---

## 17. Analytics and Observability

The application tracks user behaviour through PostHog (EU instance) with two custom middleware classes and event tracking calls scattered throughout views and tasks.

### Event Taxonomy

Events use snake_case names and fall into categories:

- **Upload lifecycle**: `file_upload_started`, `dna_generation_started`, `dna_generation_completed`, `dna_generation_failed`
- **User lifecycle**: `user_signed_up` (with sources: "with_task_claim", "with_session_dna", "before_dna"), `user_logged_in`, `anonymous_dna_claimed`
- **Display events**: `dna_displayed`, `anonymous_dna_displayed`, `public_profile_viewed`
- **Feature events**: `recommendations_generated`, `profile_made_public`, `settings_updated`
- **Error events**: `recommendation_error`, `redis_cache_error`, `exception`

### Identity Strategy

- **Authenticated users**: `str(user.id)` as the PostHog distinct ID.
- **Anonymous users**: `session.session_key`.
- **System events**: `"system"` (for background task errors).

### Privacy

All events include an `environment` property ("production" or "development") to separate dev traffic. Error messages are sanitized before sending: truncated to 500 characters, and a regex strips anything matching `(api[_-]?key|password|secret|token)\s*[:=]\s*[\w-]+`.

### Middleware

`PostHogPageviewMiddleware` runs before every view (except `/admin/`, `/static/`, `/api/`, and `/silk/`) and tracks a `$pageview` event with path, method, referrer, and user agent.

`PostHogExceptionMiddleware` catches unhandled exceptions, but only tracks them in production. Development exceptions are left to Django's debug page.

---

## 18. The Frontend: Neobrutalist Design

Bibliotype uses a neobrutalist aesthetic — a design philosophy characterized by raw, bold visual elements that reject the polished minimalism of most modern web apps.

### Design System

Every UI element follows these rules:

- **Borders**: 2px solid in the text colour (`border-brand-text border-2`). No rounded corners anywhere.
- **Shadows**: 4px offset shadows (`shadow-neo: 4px 4px 0px 0px`) that disappear on hover and shift on active (simulating a button press).
- **Font**: VT323, a retro monospace typeface loaded as the default `--font-sans`. It gives everything a terminal/computer aesthetic.
- **Colors**: High-saturation flat colours with no gradients. Yellow (`#fde047`), cyan (`#67e8f9`), pink (`#f9a8d4`), purple (`#c4b5fd`), green (`#86efac`), orange (`#ffa75e`).
- **Grid background**: The page body uses a repeating linear-gradient pattern creating a subtle grid.

### The Dashboard

The dashboard is a vertical stack of cards, each with the standard border + shadow treatment:

1. **Editable username** — Click to edit, powered by Alpine.js with AJAX submission.
2. **Vibe display** — Four phrases in coloured pills with `·` separators. Separators that fall on line breaks are hidden via JavaScript resize detection.
3. **Reader type card** — Purple background, large type name, explanation text.
4. **Top reader traits** — Leaderboard with gold/silver/bronze medal colours.
5. **Key stats row** — Three-column grid: total books (yellow), total pages (pink), average rating (cyan).
6. **Comparative analytics** — Number line visualizations comparing user stats to community averages with percentile data.
7. **Most niche read + Mainstream gauge** — Side-by-side: the book with the lowest read count, and an animated semicircle gauge showing mainstream percentage.
8. **Books per year chart** — Chart.js bar chart with yellow bars.
9. **Top genres and authors** — Doughnut charts with rainbow-cycling colour palettes, plus ordered lists in coloured badges.
10. **Review analysis** — Most positive and most negative review by VADER sentiment.
11. **Controversial ratings** — Books where user rating differs most from Goodreads average, with number line visualizations.
12. **Recommendations grid** — 3-column grid with confidence badges, source user information, and explanation components.
13. **Privacy controls** — Toggle between public/private, shareable profile link.

### The Mainstream Gauge

The mainstream gauge is the most complex frontend component. It's a semicircle with five colour zones (green to red, representing niche to mainstream), an animated needle, and a percentage display.

The animation uses Intersection Observer to trigger when 50% visible, then a 1000ms easing function (cosine-based) animates the needle from 0 to the target value. After the main animation completes, a continuous wiggle effect kicks in — two overlapping sine waves (`sin(timestamp / 280) * 0.4` and `sin(timestamp / 110) * 0.25`) create a subtle, organic oscillation. The wiggle intensity scales with the main animation progress, so it ramps up smoothly.

### Number Line Component

The number line is a reusable partial template used across comparative analytics and controversial ratings. It displays two markers (user value and comparison value) on a horizontal scale with a coloured band between them.

Smart label positioning prevents overlap: labels right-align if they're past 75% of the track, left-align if before 25%, and centre otherwise. The difference band is a semi-transparent fill (40% opacity) that spans between the two markers.

### Interactive Features

All interactivity uses Alpine.js (loaded via CDN, no build step):

- **Drag-and-drop upload**: Visual feedback on dragover, filename display, loading state.
- **Instructions modal**: Toggle visibility, escape key to close, scroll lock on body.
- **Progress polling**: 3-second interval, tweened progress bar, stage-based headings.
- **Username editing**: Click-to-edit with validation (max 15 chars), AJAX save.
- **Message dismissal**: Auto-dismiss after 8 seconds with fade transition.

### No Build Step for JavaScript

There are no separate JavaScript files. All JS is inline in templates or script tags. Alpine.js handles component state and DOM manipulation. Chart.js handles data visualisation. Both are loaded via CDN. This is a deliberate choice: the application is server-rendered with sprinkles of interactivity, not a single-page app.

---

## 19. Infrastructure and Deployment

### Local Development

Running `honcho start` launches two processes from the Procfile:
- `web`: Django development server (`python manage.py runserver`)
- `tailwind`: CSS watcher (`pnpm run dev`) that compiles `static/src/input.css` to `static/dist/output.css`

The Tailwind source file defines the custom `@theme` block with all brand colours, shadow definitions, and the VT323 font. The compiled output is committed to the repo so deployment doesn't need Node.js in production.

### Docker Architecture

**Local development** uses `docker-compose.local.yml` with four services:
- PostgreSQL 15 with healthcheck
- Redis 7 (Alpine) with healthcheck
- Web: Runs both Django dev server and Tailwind watcher in parallel via shell command
- Celery worker: Waits for PostgreSQL before starting

**Production** uses `docker-compose.prod.yml`:
- PostgreSQL and Redis (internal network only, no exposed ports)
- Web: Gunicorn (`gunicorn bibliotype.wsgi:application --bind 0.0.0.0:8000`) bound to `127.0.0.1` (Nginx sits in front)
- Celery worker: Same image, different command
- Static files: Volume mount at `./staticfiles:/app/staticfiles` shared between Django (collectstatic) and Nginx

The Docker entrypoint script (`docker-entrypoint.sh`) waits for PostgreSQL, runs migrations, and in production mode runs `collectstatic --noinput` followed by ownership and permission fixes for Nginx (`chown -R 33:33`, `chmod -R 775`).

### Single Settings File

Rather than separate settings files for development and production, `bibliotype/settings.py` uses environment variables to toggle behaviour:

- `DEBUG` controls Django debug mode, Silk profiling, logging levels, and security headers.
- `DJANGO_ENV` controls PostHog environment tagging.
- `DATABASE_URL` selects PostgreSQL or SQLite fallback via `dj-database-url`.
- Production-only settings (`SECURE_SSL_REDIRECT`, `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`, `SECURE_PROXY_SSL_HEADER`) are enabled when `DEBUG=False`.

### CI/CD

**On pull requests and pushes to main**, GitHub Actions runs the test suite inside Docker containers. It creates a `.env` with test credentials, builds the containers, waits for PostgreSQL readiness (60-second timeout), and runs `python manage.py test` inside the web container.

**On pushes to main only**, a second workflow builds the Docker image, pushes it to Docker Hub with two tags (`:latest` and `:sha_short`), then SSH deploys to a DigitalOcean VPS. The deployment pulls the new image, recreates containers with `--force-recreate`, fixes static file permissions, and prunes old images.

The production stack is Nginx (with SSL via Certbot) reverse proxying to Gunicorn, with a separate Celery worker container.

### The Custom Test Runner

A quirk of PostgreSQL: you can't drop a database while there are active connections. Django's default test runner sometimes fails to fully disconnect before teardown, causing "database is being accessed by other users" errors.

`ForceDisconnectTestRunner` in `bibliotype/runner.py` solves this with a four-step escalation:

1. Close all Django connections aggressively (5 iterations with 200ms delays).
2. Connect to the `postgres` system database and query `pg_stat_activity` for stray connections.
3. Terminate them with `pg_terminate_backend(pid)`.
4. Close everything one more time and proceed with normal database destruction.

---

## 20. Testing Strategy

### Test Organisation

Tests live in `core/tests/` with files named by scope:

- `test_views_e2e.py` — End-to-end tests that exercise full user flows (upload, signup, claim).
- `test_tasks_integration.py` — Integration tests for Celery task chains with real database operations.
- `test_tasks_unit.py` — Unit tests for isolated task logic (reader type assignment, genre scoring).
- `test_recommendations.py` — Recommendation engine and similarity calculation tests.
- `test_profile_and_recommendations.py` — Profile privacy and recommendation display tests.

### Key Patterns

**Celery in tests**: `@override_settings(CELERY_TASK_ALWAYS_EAGER=True)` makes all Celery tasks run synchronously, avoiding the need for a running worker during tests.

**Cache in tests**: `@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})` replaces Redis with an in-memory cache.

**Mocking external services**: LLM calls (`generate_vibe_with_llm`), API calls (`enrich_book_from_apis`), and background tasks are always mocked. This keeps tests fast, deterministic, and free of API key requirements.

**E2E tests use `TransactionTestCase`** instead of `TestCase` because the upload flow involves real Celery task execution (via eager mode) and database transactions that `TestCase`'s transaction wrapping interferes with.

---

## Closing Thoughts

Bibliotype is a project where the complexity hides behind a simple interface. The user uploads a CSV and sees a colourful dashboard. Behind that:

- A CSV parsing pipeline processes free-text data with pandas.
- Two external APIs enrich book metadata.
- A 300+ entry genre canonicalization map reduces chaos to 35 clean categories.
- Wikipedia and Open Library determine which authors are mainstream.
- An LLM generates poetic reading vibes with hash-based caching.
- A seven-component similarity algorithm compares readers across multiple dimensions.
- A multi-source recommendation engine with diminishing returns scoring, diversity filtering, and explanation generation suggests books.
- A percentile engine places each user in the community distribution.
- An anonymization pipeline preserves aggregate value from expired sessions.
- Celery tasks orchestrate the entire flow with progress tracking and retry logic.
- Redis caching with graceful degradation keeps things fast.
- And a neobrutalist frontend with animated gauges and number line visualisations makes it all feel fun.

Every piece exists because the data demanded it. Goodreads exports are messy, genre metadata is noisy, and reader taste is multidimensional. The architecture is a response to those realities.
