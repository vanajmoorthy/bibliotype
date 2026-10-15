# dna_constants.py

"""
This file stores the constant data used by the Reading DNA generator.
This is the final, comprehensive version incorporating extensive user feedback.
"""

from datetime import timedelta

from .genre_data import (  # noqa: F401 — re-exported for stable import paths
    AMBIGUOUS_FICTION_GENRES,
    AMBIGUOUS_TO_NONFICTION,
    CANONICAL_GENRE_MAP,
    EXCLUDED_GENRES,
    FICTION_GENRES,
    GENRE_ALIASES,
    GENRE_PRIORITY,
    NONFICTION_GENRES,
    STORYGRAPH_TAG_TO_GENRE,
)

# How long a book's enrichment attempt is trusted before it's eligible for a
# retry. A book attempted within this window is skipped (its google_books_last_
# checked timestamp is the "last attempt" marker); once the attempt is older, it
# gets a fresh pass so books that got zero genres can pick up data the APIs may
# have added since. Explicit operator re-runs (`enrich_books --process-all` /
# `--force`) bypass this window.
ENRICHMENT_RETRY_AFTER = timedelta(hours=24)

# Books the APIs can't complete (no genres, page count or publish year after a
# full exchange) would otherwise be re-enriched every ENRICHMENT_RETRY_AFTER
# whenever someone uploads them, spending Google Books quota on lookups that
# keep coming back empty. After this many consecutive incomplete attempts the
# window widens to ENRICHMENT_RETRY_AFTER_INCOMPLETE.
ENRICHMENT_MAX_INCOMPLETE_ATTEMPTS = 3
ENRICHMENT_RETRY_AFTER_INCOMPLETE = timedelta(days=30)


def enrichment_retry_window(book):
    """The retry window for this book: 24h normally, 30 days once it has repeatedly come back incomplete."""
    if book.enrichment_incomplete_attempts >= ENRICHMENT_MAX_INCOMPLETE_ATTEMPTS:
        return ENRICHMENT_RETRY_AFTER_INCOMPLETE
    return ENRICHMENT_RETRY_AFTER


# Enrichment queues. With queue_order_strategy="sorted", priority is
# alphabetical by queue NAME: "celery" (DNA, claims, recommendations) drains
# first, then ENRICHMENT_QUEUE (interactive uploads' per-book and author tasks,
# so one big upload can't stall the next user's DNA), then ENRICHMENT_BULK_QUEUE
# (seeding runs, enrich_books backfills). The order is test-enforced in
# test_enrichment_queue_routing. Lives here rather than core.tasks because
# core.services.dna needs it too and core.tasks imports from core.services
# (circular otherwise).
ENRICHMENT_QUEUE = "enrichment"
ENRICHMENT_BULK_QUEUE = "enrichment_bulk"

READER_TYPE_DESCRIPTIONS = {
    "Fantasy Fanatic": [
        "You have a deep love for worlds of magic, myth, and epic quests. Your reading history is filled with fantasy and science fiction.",
        "Your library is a gateway to other realms. You consistently choose books in the fantasy and sci-fi genres.",
        "From dragons to distant galaxies, your reading habits show a clear preference for the imaginative and the speculative.",
    ],
    "Novella Navigator": [
        "You're a master of the short-form narrative, frequently choosing books under 200 pages. You appreciate a story that gets straight to the point.",
        "Your reading data shows a strong preference for shorter books. You enjoy concise, impactful stories that can be read in a single sitting.",
        "You've read a significant number of novellas and shorter works, making you an expert in the art of brevity.",
    ],
    "Small Press Supporter": [
        "You champion the underdogs of the publishing world. A significant portion of your reading comes from independent and small presses.",
        "Your library is filled with hidden gems from outside the major publishing houses. You have a keen eye for unique, independent voices.",
        "By frequently reading books from small presses, you actively support a more diverse literary landscape.",
    ],
    "Tome Tussler": [
        "You aren't afraid of commitment. Your reading history is filled with massive books over 500 pages long.",
        "You love to get lost in sprawling epics and complex narratives, as shown by the number of lengthy tomes you've conquered.",
        "Your results show a clear pattern of tackling the biggest books on the shelf. You're a true heavyweight reader.",
    ],
    "Classics Collector": [
        "You have a great appreciation for the classics. A notable part of your reading list was published before 1970.",
        "Your reading habits show a journey through literary history, with a strong focus on timeless, classic works.",
        "You frequently read books that have stood the test of time, building a library of foundational literary classics.",
    ],
    "Versatile Valedictorian": [
        "Your reading tastes are incredibly diverse. You've explored a wide array of genres, from fiction to non-fiction and everything in between.",
        "You are a true literary explorer. Your results show you've read from more than ten distinct canonical genres.",
        "Your library is a testament to your curiosity, spanning a broad and impressive range of different literary categories.",
    ],
    "Non-Fiction Ninja": [
        "You read to learn and understand the world. Your library is heavily weighted towards non-fiction titles.",
        "From history to science to biography, your reading habits show a strong desire for facts, insights, and real-world knowledge.",
    ],
    "Modern Maverick": [
        "You have your finger on the pulse of modern literature. You consistently read new releases and books published in the last few years.",
        "Your bookshelf is a snapshot of the current literary moment, showing a strong preference for contemporary authors and recent publications.",
    ],
    "Philosophical Philomath": [
        "You enjoy pondering the big questions. Your reading history shows a recurring interest in philosophical works.",
        "Your library is a space for deep thought, filled with books that explore ethics, existence, and the nature of reality.",
    ],
    "Rapacious Reader": [
        "You devour books at an extraordinary pace. With over 40 books a year under your belt, reading isn't just a hobby — it's a way of life.",
        "Your reading velocity is remarkable. You tear through books at a rate most readers can only dream of.",
    ],
    "Author Loyalist": [
        "When you find an author you love, you commit. A striking share of your library comes from a single writer's pen.",
        "You don't just read books — you follow careers. One author's name appears on your shelves again and again.",
        "Completionist energy: your reading history shows deep devotion to one author's entire body of work.",
    ],
    "Self Help Scholar": [
        "You read with purpose and self-improvement in mind. Your library is rich with books on personal development, productivity, and growth.",
        "You believe in the power of books to change lives — starting with your own. Self-help and personal development titles dominate your reading list.",
    ],
    "Comfort Rereader": [
        "You find joy in returning to books you love. Your reading history shows a pattern of revisiting favourites rather than always chasing the new.",
        "Some readers race through new titles — you prefer to deepen your relationship with books that matter to you. Your rereads speak volumes.",
        "Your library isn't just a collection of books read once. You return to stories like old friends, finding new meaning with each visit.",
    ],
    "Mystery Maven": [
        "You live for the plot twist. Your library is packed with mysteries and thrillers that keep you guessing until the final page.",
        "Clues, red herrings, and locked rooms — your reading history reads like a detective's case file.",
        "You have a nose for suspense. Whodunits and thrillers dominate your shelves.",
    ],
    "Romance Reveller": [
        "You believe every great story deserves a great love story. Romance is the beating heart of your library.",
        "Meet-cutes, slow burns, and happily-ever-afters — your reading history shows a devotion to romance in all its forms.",
        "Your shelves are full of butterflies. You consistently choose books where love takes centre stage.",
    ],
    "History Hound": [
        "You read to time travel. Histories, biographies, and historical fiction fill your library with other eras.",
        "Your bookshelf is a museum. You're drawn to the stories of real people and vanished worlds.",
        "From ancient empires to lives lived a century ago, your reading shows a deep fascination with the past.",
    ],
    "Series Slayer": [
        "Why read one book when there are seven? Your library shows a serious commitment to finishing entire series.",
        "You don't just visit fictional worlds — you move in. Sequels and sagas dominate your reading history.",
        "Book one is never enough. You binge series the way other people binge TV.",
    ],
    "Literary Luminary": [
        "You gravitate toward the beautifully written. Literary fiction is the backbone of your library.",
        "Prize lists and prose stylists — your reading history shows a taste for serious fiction.",
        "Your shelves favour character over plot and sentences worth rereading. A true literary reader.",
    ],
    "Sonnet Slinger": [
        "You make space for verse in a prose-heavy world. Poetry collections appear again and again in your library.",
        "Line breaks, imagery, and compression — your reading history shows a rare devotion to poetry.",
    ],
    "Eclectic Reader": [
        "Your reading habits are balanced and wide-ranging, with no single trait dominating the others. You are a true eclectic reader!",
    ],
}


# Normalized 0-100 scoring constants for assign_reader_type v2
MIN_WINNING_SCORE = 30
MIN_SIGNAL_BOOKS = 10


def score_ramp(value: float, floor: float, saturation: float) -> int:
    """Linearly map value in [floor, saturation] to 0-100, clamped."""
    if saturation <= floor:
        return 0
    return round(100 * min(max((value - floor) / (saturation - floor), 0.0), 1.0))


# (floor, saturation) tuples for each reader type's normalized 0-100 score
READER_TYPE_THRESHOLDS = {
    "Fantasy Fanatic":         (0.30, 0.70),   # floor raised: 30%+ speculative fiction needed
    "Mystery Maven":           (0.20, 0.60),
    "Romance Reveller":        (0.15, 0.50),
    "History Hound":           (0.15, 0.55),   # floor lowered so genuine history buffs surface
    "Literary Luminary":       (0.10, 0.40),
    "Sonnet Slinger":          (0.05, 0.25),
    "Non-Fiction Ninja":       (0.15, 0.50),
    "Philosophical Philomath": (0.05, 0.25),
    "Self Help Scholar":       (0.08, 0.30),
    "Author Loyalist":         (0.12, 0.35),   # largest single-author share; wins at ~19%
    "Tome Tussler":            (0.08, 0.45),
    "Novella Navigator":       (0.08, 0.45),
    "Classics Collector":      (0.15, 0.45),   # floor raised: 15%+ pre-1970 needed
    "Modern Maverick":         (0.45, 0.85),
    "Small Press Supporter":   (0.35, 0.75),
    "Comfort Rereader":        (0.03, 0.25),
    "Series Slayer":           (0.25, 0.70),
    "Rapacious Reader":        (25.0, 80.0),   # raw books/year, not fraction
    "Versatile Valedictorian": (6.0, 14.0),    # raw count of solid genres
}

# Tiebreak order: most-specific/niche first, so a rarer identity beats a broad one on exact ties
READER_TYPE_TIEBREAK_ORDER = [
    "Sonnet Slinger",
    "Philosophical Philomath",
    "Self Help Scholar",
    "Comfort Rereader",
    "Author Loyalist",
    "Series Slayer",
    "Novella Navigator",
    "Tome Tussler",
    "Classics Collector",
    "Small Press Supporter",
    "Literary Luminary",
    "Mystery Maven",
    "Romance Reveller",
    "History Hound",
    "Fantasy Fanatic",
    "Non-Fiction Ninja",
    "Modern Maverick",
    "Rapacious Reader",
    "Versatile Valedictorian",
]

READER_TYPE_COLORS = {
    "Fantasy Fanatic":         "purple",
    "Philosophical Philomath": "purple",
    "Literary Luminary":       "purple",
    "Eclectic Reader":         "purple",
    "Rapacious Reader":        "yellow",
    "Modern Maverick":         "yellow",
    "Series Slayer":           "yellow",
    "Versatile Valedictorian": "yellow",
    "Tome Tussler":            "orange",
    "History Hound":           "orange",
    "Classics Collector":      "orange",
    "Classic Collector":       "orange",   # legacy name mapping
    "Romance Reveller":        "pink",
    "Comfort Rereader":        "pink",
    "Sonnet Slinger":          "pink",
    "Mystery Maven":           "cyan",
    "Non-Fiction Ninja":       "cyan",
    "Social Savant":           "cyan",    # retired type — legacy mapping for stored profiles
    "Novella Navigator":       "cyan",
    "Nature Nut Case":         "green",   # retired type — legacy mapping for stored profiles
    "Author Loyalist":         "green",
    "Self Help Scholar":       "green",
    "Small Press Supporter":   "green",
}


GLOBAL_AVERAGES = {
    # Median across the major reading surveys — see GLOBAL_AVERAGES_SOURCES below
    # for the sources and the mean-vs-median reasoning. Flows live into every
    # profile's Comparative Analytics card via _enrich_dna_for_display.
    "avg_books_per_year": 4,
    "avg_book_length_pages": 375,
    "avg_publish_year": 2009,
}

# Sourcing for the GLOBAL_AVERAGES constants above.
#
# Honesty note: these are benchmark *estimates*, separate from the community
# percentiles (those come from real reader data in AggregateAnalytics). The
# books-per-year figure is a median aggregated from live, named surveys; the
# other two are working estimates because no authoritative source measures them.
# Every entry documents where its number really comes from and links live
# context, and no entry links a dead URL.
#
# Schema per key: {url, archived_url, accessed, note, citations}
#   url          live page for the source / further reading, or None if none exists
#   archived_url Wayback snapshot, or None
#   accessed     ISO date the url was last checked (see git history for the audit)
#   note         plain-language explanation of where the number comes from
#   citations    list of named studies the note refers to, each {label, url};
#                may be empty when the note has no specific studies to name
# Every GLOBAL_AVERAGES key must have an entry here (enforced by test).
GLOBAL_AVERAGES_SOURCES = {
    "avg_book_length_pages": {
        "url": "https://prowritingaid.com/average-book-length",
        "archived_url": None,
        "accessed": "2026-07-29",
        "note": (
            "This is a rough figure rather than a measured one. Most adult novels "
            "are somewhere between 250 and 400 pages, so we picked a round number in "
            "the middle. ProWritingAid has a good breakdown of typical book lengths."
        ),
        "citations": [],
    },
    "avg_publish_year": {
        "url": "https://ammar-alyousfi.com/2024/exploring-goodreads-data-an-analysis-of-10-million-books",
        "archived_url": None,
        "accessed": "2026-07-29",
        "note": (
            "Nobody surveys the average publication year of the books people read, "
            "so this one is a guess. Most people read fairly recent books with some "
            "older ones mixed in, and this is a rough middle point. This analysis of "
            "10 million Goodreads books shows how they spread out by decade."
        ),
        "citations": [],
    },
    "avg_books_per_year": {
        "url": "https://www.pewresearch.org/internet/2016/09/01/book-reading-2016/",
        "archived_url": None,
        "accessed": "2026-07-29",
        "note": (
            "This is the median from the big reading surveys. The mean is much "
            "higher (Gallup says 12.6 books, Pew says 12 to 14), but that gets pulled "
            "up by a small group of people who read loads, while about half of adults "
            "read no books at all. The medians are 4 to 5 from Pew, and 2 in the US "
            "and 3 in the UK from YouGov, so we went with the middle of those."
        ),
        "citations": [
            {
                "label": "Gallup (2021 mean)",
                "url": "https://news.gallup.com/poll/388541/americans-reading-fewer-books-past.aspx",
            },
            {
                "label": "Pew (2016 mean & median)",
                "url": "https://www.pewresearch.org/internet/2016/09/01/book-reading-2016/",
            },
            {
                "label": "YouGov (2025 median)",
                "url": "https://today.yougov.com/entertainment/articles/48239-54-percent-of-americans-read-a-book-this-year",
            },
        ],
    },
}


NICHE_THRESHOLD = 5

CONTRARINESS_SCALE = [
    (1.5, "Wildly contrarian", "bg-brand-pink"),
    (1.0, "Very contrarian", "bg-brand-orange"),
    (0.6, "Moderately contrarian", "bg-brand-yellow"),
    (0.3, "Slightly contrarian", "bg-brand-cyan"),
    (0.0, "Aligned with consensus", "bg-brand-green"),
]


def compute_contrariness(avg_diff):
    """Return (label, color) for the given average rating difference."""
    for threshold, label, color in CONTRARINESS_SCALE:
        if avg_diff >= threshold:
            return label, color
    return "Aligned with consensus", "bg-brand-green"


# Best-match similarity below this means no matched reader shares much taste
UNIQUENESS_WEAK_MATCH_THRESHOLD = 0.40


def compute_uniqueness(similar_users_count, max_similarity):
    """Return (label, color) for how unique a reader's taste is, or None for no badge."""
    if similar_users_count == 0:
        return "One of a kind", "bg-brand-pink"
    if max_similarity < UNIQUENESS_WEAK_MATCH_THRESHOLD:
        return "Pretty unique", "bg-brand-cyan"
    return None
