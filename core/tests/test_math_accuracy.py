"""Math and accuracy tests for DNA computation, scoring, and stats.

These tests verify the *correctness* of arithmetic, scoring, and aggregation
logic — independent of CSV parsing, database state, or external APIs.
"""

from io import StringIO
from unittest.mock import MagicMock

import pandas as pd
from django.test import TestCase

from core.dna_constants import (
    CANONICAL_GENRE_MAP,
    FICTION_GENRES,
    GENRE_ALIASES,
    NONFICTION_GENRES,
    READER_TYPE_DESCRIPTIONS,
)
from core.services.dna import (
    STORYGRAPH_TAG_TO_GENRE,
    _detect_and_normalize_csv,
    _isbn_to_isbn13,
    assign_reader_type,
)


# ────────────────────────────────────────────
# Reader type scoring math
# ────────────────────────────────────────────


class ReaderTypeScoringMathTests(TestCase):
    """Verify reader type scoring produces correct normalized 0-100 values."""

    def _make_df(self, rows, has_read_count=False):
        cols = "Title,Author,Exclusive Shelf,Number of Pages"
        if has_read_count:
            cols += ",Read Count"
        csv_text = cols + "\n" + "\n".join(rows)
        df = pd.read_csv(StringIO(csv_text))
        df.columns = df.columns.str.strip()
        return df

    def _genre_sets(self, n, genre_set):
        """n books all with the same genre set."""
        return [genre_set] * n

    def test_tome_tussler_scores_nonzero_above_floor(self):
        """Books > 490 pages: if fraction > 45% saturation, Tome Tussler scores 100."""
        # 6 of 10 long books = 60%, well above saturation (45%) → score 100
        rows = [
            "Book A,Auth,read,500",
            "Book B,Auth,read,600",
            "Book C,Auth,read,491",
            "Book D,Auth,read,550",
            "Book E,Auth,read,510",
            "Book F,Auth,read,520",
            "Book G,Auth,read,300",  # not long
            "Book H,Auth,read,250",  # not long
            "Book I,Auth,read,200",  # not long
            "Book J,Auth,read,150",  # not long
        ]
        df = self._make_df(rows)
        _, scores = assign_reader_type(df, {}, [])
        self.assertEqual(scores["Tome Tussler"], 100)

    def test_tome_tussler_zero_below_floor(self):
        """2 of 25 books long = 8%, exactly at floor → score 0 (floor exclusive)."""
        rows = ["Book A,Auth,read,500", "Book B,Auth,read,600"] + [f"Book {i},Auth,read,300" for i in range(23)]
        df = self._make_df(rows)
        _, scores = assign_reader_type(df, {}, [])
        # 2/25 = 0.08 = exactly floor → score_ramp returns 0
        self.assertEqual(scores.get("Tome Tussler", 0), 0)

    def test_novella_navigator_scores_above_floor(self):
        """6 of 10 short books (60%) > saturation (45%) → score 100."""
        rows = [
            "Book A,Auth,read,150",
            "Book B,Auth,read,199",
            "Book C,Auth,read,100",
            "Book D,Auth,read,50",
            "Book E,Auth,read,80",
            "Book F,Auth,read,120",
            "Book G,Auth,read,300",
            "Book H,Auth,read,350",
            "Book I,Auth,read,400",
            "Book J,Auth,read,450",
        ]
        df = self._make_df(rows)
        _, scores = assign_reader_type(df, {}, [])
        self.assertEqual(scores["Novella Navigator"], 100)

    def test_fantasy_fanatic_genre_share_above_floor(self):
        """10 of 10 books with fantasy genres → 100% share → score 100."""
        # 10 books required to meet MIN_SIGNAL_BOOKS threshold for genre scoring
        book_genre_sets = [{"fantasy"}] * 5 + [{"science fiction"}] * 3 + [{"dystopian"}] * 2
        rows = [f"B{i},Auth,read,300" for i in range(10)]
        df = self._make_df(rows)
        _, scores = assign_reader_type(df, {}, book_genre_sets)
        self.assertEqual(scores["Fantasy Fanatic"], 100)

    def test_fantasy_fanatic_unique_book_not_double_counted(self):
        """A book with fantasy AND sci-fi counts once for Fantasy Fanatic (not twice)."""
        # 10 books to meet MIN_SIGNAL_BOOKS: 5 fantasy+scifi, 5 romance
        rows = [f"B{i},Auth,read,300" for i in range(10)]
        df = self._make_df(rows)
        book_genre_sets = [{"fantasy", "science fiction"}] * 5 + [{"romance"}] * 5
        _, scores = assign_reader_type(df, {}, book_genre_sets)
        # 5 of 10 books = 0.5 share, floor=0.30, sat=0.70
        # score_ramp(0.5, 0.30, 0.70) = round(100 * (0.5-0.30) / (0.70-0.30)) = round(50.0) = 50
        from core.dna_constants import READER_TYPE_THRESHOLDS, score_ramp
        floor, sat = READER_TYPE_THRESHOLDS["Fantasy Fanatic"]
        expected = score_ramp(0.5, floor, sat)
        self.assertEqual(scores["Fantasy Fanatic"], expected)

    def test_versatile_valedictorian_score_proportional(self):
        """Versatile Valedictorian: 10 solid genres (between floor=6 and sat=14) → intermediate score."""
        df = self._make_df(["Book A,Auth,read,300"] * 30)
        # 30 books, each with a different genre combination to create 10 solid genres
        # Each genre needs >= 2 books and >= 3% of G = 0.03*30 = 0.9 → >=1 books
        book_genre_sets = []
        genres = ["fantasy", "science fiction", "thriller", "horror", "romance",
                  "biography", "history", "philosophy", "psychology", "non-fiction"]
        for i, genre in enumerate(genres):
            # 3 books per genre
            book_genre_sets.extend([{genre}] * 3)
        _, scores = assign_reader_type(df, {}, book_genre_sets)
        # 10 solid genres, floor=6, sat=14 → score_ramp(10, 6, 14) = round(100 * (10-6)/(14-6)) = 50
        self.assertEqual(scores.get("Versatile Valedictorian", 0), 50)

    def test_comfort_rereader_storygraph_reread_detection(self):
        """StoryGraph: Comfort Rereader score > 0 when Read Count > 1."""
        rows = [
            "Book A,Auth,read,200,3",  # reread
            "Book B,Auth,read,200,1",
            "Book C,Auth,read,200,5",  # reread
            "Book D,Auth,read,200,1",
        ]
        df = self._make_df(rows, has_read_count=True)
        _, scores = assign_reader_type(df, {}, [])
        # 2 rereads / 4 total = 0.5 > sat (0.25) → score 100
        self.assertGreater(scores.get("Comfort Rereader", 0), 0)

    def test_comfort_rereader_goodreads_duplicate_titles(self):
        """Goodreads (no Read Count): duplicate titles count as rereads."""
        rows = [
            "Book A,Auth,read,200",
            "Book A,Auth,read,200",  # duplicate → 1 reread
            "Book B,Auth,read,200",
        ]
        df = self._make_df(rows)
        _, scores = assign_reader_type(df, {}, [])
        self.assertGreater(scores.get("Comfort Rereader", 0), 0)

    def test_comfort_rereader_zero_when_no_rereads(self):
        """Zero rereads → Comfort Rereader scores 0."""
        rows = [
            "Book A,Auth,read,200,1",
            "Book B,Auth,read,200,1",
        ]
        df = self._make_df(rows, has_read_count=True)
        _, scores = assign_reader_type(df, {}, [])
        self.assertEqual(scores.get("Comfort Rereader", 0), 0)

    def test_classics_collector_pre_1970(self):
        """Books published before 1970 contribute to Classics Collector."""
        rows2 = ["Book A,Auth,read,300", "Book B,Auth,read,300", "Book C,Auth,read,300",
                 "Book D,Auth,read,300", "Book E,Auth,read,300",
                 "Book F,Auth,read,300", "Book G,Auth,read,300", "Book H,Auth,read,300",
                 "Book I,Auth,read,300", "Book J,Auth,read,300"]
        df2 = self._make_df(rows2)
        enriched2 = {
            "Book A": {"publish_year": 1950, "publisher": None},
            "Book B": {"publish_year": 1960, "publisher": None},
            "Book C": {"publish_year": 1969, "publisher": None},
            "Book D": {"publish_year": 1970, "publisher": None},  # boundary — excluded
            "Book E": {"publish_year": 2000, "publisher": None},
            "Book F": {"publish_year": 2010, "publisher": None},
            "Book G": {"publish_year": 2015, "publisher": None},
            "Book H": {"publish_year": 2020, "publisher": None},
            "Book I": {"publish_year": 2021, "publisher": None},
            "Book J": {"publish_year": 2022, "publisher": None},
        }
        _, scores = assign_reader_type(df2, enriched2, [])
        # 3 pre-1970 of 10 = 30% > floor (10%), below sat (40%) → score > 0 and < 100
        self.assertGreater(scores.get("Classics Collector", 0), 0)
        self.assertLess(scores.get("Classics Collector", 0), 100)

    def test_modern_maverick_rolling_window(self):
        """Modern Maverick uses rolling (current_year - 6) not hardcoded 2018."""
        import datetime
        current_year = datetime.datetime.now().year
        cutoff = current_year - 6
        rows = [f"Book {i},Auth,read,300" for i in range(12)]
        df = self._make_df(rows)
        # Make 10 books at cutoff year (= recent), 2 old
        enriched = {}
        for i in range(12):
            enriched[f"Book {i}"] = {"publish_year": cutoff if i < 10 else 1990, "publisher": None}
        _, scores = assign_reader_type(df, enriched, [])
        self.assertGreater(scores.get("Modern Maverick", 0), 0)

    def test_small_press_supporter(self):
        """Books from non-mainstream publishers contribute to Small Press Supporter."""
        rows = [f"Book {i},Auth,read,300" for i in range(12)]
        df = self._make_df(rows)
        mainstream_pub = MagicMock(is_mainstream=True)
        small_pub = MagicMock(is_mainstream=False)
        enriched = {}
        for i in range(12):
            enriched[f"Book {i}"] = {
                "publish_year": 2020,
                "publisher": small_pub if i < 8 else mainstream_pub,
            }
        _, scores = assign_reader_type(df, enriched, [])
        # 8 small press of 12 = 67% > floor (35%) → score > 0
        self.assertGreater(scores.get("Small Press Supporter", 0), 0)

    def test_eclectic_reader_returned_when_all_low_scores(self):
        """When no reader type reaches MIN_WINNING_SCORE, returns Eclectic Reader."""
        df = self._make_df(["Book A,Auth,read,300"])
        reader_type, _ = assign_reader_type(df, {}, [])
        self.assertEqual(reader_type, "Eclectic Reader")

    def test_not_enough_data_for_empty_df(self):
        """Empty dataframe returns 'Not enough data'."""
        df = self._make_df([])
        df = df.iloc[0:0]
        reader_type, scores = assign_reader_type(df, {}, [])
        self.assertEqual(reader_type, "Not enough data")
        self.assertEqual(len(scores), 0)

    def test_scores_all_in_0_100_range(self):
        """All scores must be integers in [0, 100]."""
        rows = ["Book A,Auth,read,300"] * 20
        df = self._make_df(rows)
        book_genre_sets = [{"fantasy"}] * 20
        _, scores = assign_reader_type(df, {}, book_genre_sets)
        for t, s in scores.items():
            self.assertGreaterEqual(s, 0, f"{t} score below 0: {s}")
            self.assertLessEqual(s, 100, f"{t} score above 100: {s}")


# ────────────────────────────────────────────
# Genre canonicalization & mapping math
# ────────────────────────────────────────────


class GenreCanonicalizationTests(TestCase):
    """Verify the canonical genre map is consistent and complete."""

    def test_all_canonical_genres_classified_as_fiction_or_nonfiction(self):
        """Every canonical genre key must appear in FICTION_GENRES or NONFICTION_GENRES."""
        all_canonical = set(GENRE_ALIASES.keys())
        classified = FICTION_GENRES | NONFICTION_GENRES
        unclassified = all_canonical - classified
        self.assertEqual(unclassified, set(), f"Unclassified canonical genres: {unclassified}")

    def test_dystopian_is_separate_canonical_genre(self):
        """Dystopian must be its own canonical genre, not aliased to science fiction."""
        self.assertIn("dystopian", GENRE_ALIASES)
        self.assertIn("dystopian", FICTION_GENRES)
        # Must NOT be a science fiction alias anymore
        self.assertNotIn("dystopian", GENRE_ALIASES.get("science fiction", set()))

    def test_canonical_genre_map_self_references(self):
        """Each canonical key maps to itself."""
        for canonical in GENRE_ALIASES.keys():
            self.assertEqual(CANONICAL_GENRE_MAP.get(canonical), canonical, f"{canonical} should map to itself")

    def test_aliases_resolve_to_canonical_genre(self):
        """Each alias maps to its canonical parent."""
        for canonical, aliases in GENRE_ALIASES.items():
            for alias in aliases:
                self.assertEqual(
                    CANONICAL_GENRE_MAP.get(alias),
                    canonical,
                    f"alias '{alias}' should map to canonical '{canonical}'",
                )

    def test_dystopian_aliases_route_correctly(self):
        """All previous dystopian aliases now map to 'dystopian', not 'science fiction'."""
        for alias in ["dystopian fiction", "dystopias", "fiction, dystopian"]:
            self.assertEqual(CANONICAL_GENRE_MAP.get(alias), "dystopian")

    def test_no_alias_collisions(self):
        """No alias appears under multiple canonical genres."""
        seen = {}
        for canonical, aliases in GENRE_ALIASES.items():
            for alias in aliases:
                if alias in seen:
                    self.fail(f"Alias '{alias}' appears in both '{seen[alias]}' and '{canonical}'")
                seen[alias] = canonical


# ────────────────────────────────────────────
# StoryGraph tag → canonical genre mapping
# ────────────────────────────────────────────


class StoryGraphTagMappingTests(TestCase):
    """Verify STORYGRAPH_TAG_TO_GENRE values are valid canonical genres."""

    def test_all_tag_targets_are_canonical_genres(self):
        """Every value in STORYGRAPH_TAG_TO_GENRE must be a canonical genre key."""
        for tag, target in STORYGRAPH_TAG_TO_GENRE.items():
            self.assertIn(
                target,
                GENRE_ALIASES,
                f"Tag '{tag}' maps to '{target}' which is not a canonical genre",
            )

    def test_dystopian_tag_maps_to_dystopian_canonical(self):
        """The 'dystopian' tag must map to 'dystopian', not 'science fiction'."""
        self.assertEqual(STORYGRAPH_TAG_TO_GENRE["dystopian"], "dystopian")

    def test_common_synonym_tags_normalize_consistently(self):
        """Synonym tags like 'sci-fi', 'scifi', 'science fiction' all map to same target."""
        sci_targets = {STORYGRAPH_TAG_TO_GENRE[t] for t in ["sci-fi", "scifi", "science fiction"]}
        self.assertEqual(sci_targets, {"science fiction"})

        # memoir is its own canonical genre now — no longer lumped into biography
        self.assertEqual(STORYGRAPH_TAG_TO_GENRE["biography"], "biography")
        self.assertEqual(STORYGRAPH_TAG_TO_GENRE["memoir"], "memoir")

        ya_targets = {STORYGRAPH_TAG_TO_GENRE[t] for t in ["young adult", "ya"]}
        self.assertEqual(ya_targets, {"young adult fiction"})


# ────────────────────────────────────────────
# Reader type descriptions completeness
# ────────────────────────────────────────────


class ReaderTypeDescriptionsTests(TestCase):
    """Verify every reader type with scoring logic has a description."""

    def test_all_scored_reader_types_have_descriptions(self):
        """Every reader type that can be assigned must have a description in READER_TYPE_DESCRIPTIONS."""
        from core.dna_constants import READER_TYPE_TIEBREAK_ORDER
        # All 20 types in tiebreak order plus the fallbacks
        scored_types = set(READER_TYPE_TIEBREAK_ORDER) | {"Eclectic Reader"}
        missing = scored_types - set(READER_TYPE_DESCRIPTIONS.keys())
        self.assertEqual(missing, set(), f"Reader types with no descriptions: {missing}")

    def test_description_lists_are_non_empty(self):
        """Every reader type description must have at least one phrase."""
        for reader_type, descriptions in READER_TYPE_DESCRIPTIONS.items():
            self.assertGreater(len(descriptions), 0, f"{reader_type} has no descriptions")
            for desc in descriptions:
                self.assertIsInstance(desc, str)
                self.assertGreater(len(desc.strip()), 10, f"{reader_type} has too-short description")


# ────────────────────────────────────────────
# Mood / pace distribution math (StoryGraph)
# ────────────────────────────────────────────


class MoodPaceAggregationTests(TestCase):
    """Verify mood and pace aggregation produces correct counts."""

    def test_mood_distribution_counts_comma_separated_moods(self):
        """Multi-mood entries split correctly and accumulate counts."""
        from collections import Counter

        moods_series = pd.Series([
            "dark, reflective",
            "dark, adventurous",
            "lighthearted",
            None,  # NaN should be skipped
            "DARK, EMOTIONAL",  # case-insensitive
        ])
        all_moods = []
        for m_str in moods_series.dropna():
            all_moods.extend([m.strip().lower() for m in str(m_str).split(",") if m.strip()])
        result = dict(Counter(all_moods).most_common(10))
        self.assertEqual(result["dark"], 3)  # case-insensitive merge
        self.assertEqual(result["reflective"], 1)
        self.assertEqual(result["adventurous"], 1)
        self.assertEqual(result["lighthearted"], 1)
        self.assertEqual(result["emotional"], 1)

    def test_pace_distribution_strips_and_lowercases(self):
        """Pace values normalize via strip + lowercase."""
        from collections import Counter

        pace = pd.Series(["  Slow  ", "fast", "MEDIUM", "Slow", "medium"])
        normalized = pace.dropna().str.strip().str.lower()
        result = dict(Counter(normalized).most_common())
        self.assertEqual(result["slow"], 2)
        self.assertEqual(result["medium"], 2)
        self.assertEqual(result["fast"], 1)

    def test_empty_moods_column_produces_empty_distribution(self):
        """If Moods column is all NaN, distribution is empty."""
        from collections import Counter

        moods = pd.Series([None, None, None])
        all_moods = []
        for m_str in moods.dropna():
            all_moods.extend([m.strip().lower() for m in str(m_str).split(",") if m.strip()])
        self.assertEqual(list(Counter(all_moods).most_common()), [])


# ────────────────────────────────────────────
# CSV detection & normalization edge cases
# ────────────────────────────────────────────


class CSVDetectionEdgeCasesTests(TestCase):
    """Edge cases for CSV format detection."""

    def test_storygraph_with_minimum_columns_detected(self):
        """StoryGraph CSV is detected by 'Read Status' column alone."""
        csv_text = "Title,Authors,Read Status,Star Rating\nBook,Auth,read,4.0"
        df = pd.read_csv(StringIO(csv_text))
        result_df, source = _detect_and_normalize_csv(df)
        self.assertEqual(source, "storygraph")

    def test_round_half_up_ratings(self):
        """StoryGraph half-star ratings round half-up (4.5→5, 3.5→4, 0.5→1)."""
        csv_text = (
            "Title,Authors,Read Status,Star Rating\n"
            "A,X,read,4.5\n"
            "B,X,read,3.5\n"
            "C,X,read,2.5\n"
            "D,X,read,1.5\n"
            "E,X,read,0.5\n"
            "F,X,read,5.0\n"
            "G,X,read,1.0\n"
        )
        df = pd.read_csv(StringIO(csv_text))
        result_df, _ = _detect_and_normalize_csv(df)
        self.assertEqual(result_df.iloc[0]["My Rating"], 5)
        self.assertEqual(result_df.iloc[1]["My Rating"], 4)
        self.assertEqual(result_df.iloc[2]["My Rating"], 3)
        self.assertEqual(result_df.iloc[3]["My Rating"], 2)
        self.assertEqual(result_df.iloc[4]["My Rating"], 1)
        self.assertEqual(result_df.iloc[5]["My Rating"], 5)
        self.assertEqual(result_df.iloc[6]["My Rating"], 1)

    def test_isbn_validation_rejects_non_numeric(self):
        """ISBN validation: 10/13-digit values kept (ISBN-10 → ISBN-13); others become NaN."""
        csv_text = (
            "Title,Authors,Read Status,Star Rating,ISBN/UID\n"
            "A,X,read,4,9780743273565\n"  # 13-digit valid (passthrough)
            "B,X,read,4,0743273567\n"     # ISBN-10 valid → upgraded to ISBN-13
            "C,X,read,4,sg_internal_id\n"  # invalid
            "D,X,read,4,12345\n"            # too short
            "E,X,read,4,123456789012345\n"  # too long
        )
        df = pd.read_csv(StringIO(csv_text))
        result_df, _ = _detect_and_normalize_csv(df)
        self.assertEqual(result_df.iloc[0]["ISBN13"], "9780743273565")
        # 0743273567 (ISBN-10) → 9780743273565 (same physical book)
        self.assertEqual(result_df.iloc[1]["ISBN13"], "9780743273565")
        self.assertTrue(pd.isna(result_df.iloc[2]["ISBN13"]))
        self.assertTrue(pd.isna(result_df.iloc[3]["ISBN13"]))
        self.assertTrue(pd.isna(result_df.iloc[4]["ISBN13"]))

    def test_multi_author_takes_first(self):
        """StoryGraph multi-author CSV: take only the first author after comma split."""
        csv_text = (
            "Title,Authors,Read Status,Star Rating\n"
            "Good Omens,\"Terry Pratchett, Neil Gaiman\",read,5\n"
            "Solo,Single Author,read,4\n"
        )
        df = pd.read_csv(StringIO(csv_text))
        result_df, _ = _detect_and_normalize_csv(df)
        self.assertEqual(result_df.iloc[0]["Author"], "Terry Pratchett")
        self.assertEqual(result_df.iloc[1]["Author"], "Single Author")

    def test_unrecognized_csv_raises_value_error(self):
        """Unknown CSV format raises ValueError with helpful message."""
        csv_text = "ColA,ColB\nx,y"
        df = pd.read_csv(StringIO(csv_text))
        with self.assertRaises(ValueError) as ctx:
            _detect_and_normalize_csv(df)
        self.assertIn("Unrecognized CSV format", str(ctx.exception))


class IsbnNormalizationTests(TestCase):
    """ISBN-10 → ISBN-13 conversion for cross-platform dedup."""

    def test_isbn_10_converts_to_isbn_13(self):
        """Known conversion: 0306406152 → 9780306406157."""
        self.assertEqual(_isbn_to_isbn13("0306406152"), "9780306406157")

    def test_isbn_10_with_x_check_digit(self):
        """X check digit (= value 10) is accepted on ISBN-10 input."""
        # 080442957X is a valid ISBN-10. Conversion drops the X (it's just a
        # check digit) and computes a fresh EAN-13 check.
        self.assertEqual(_isbn_to_isbn13("080442957X"), "9780804429573")
        # Lowercase x is accepted too.
        self.assertEqual(_isbn_to_isbn13("080442957x"), "9780804429573")

    def test_isbn_13_passthrough(self):
        """13-digit input is returned unchanged (function is idempotent)."""
        self.assertEqual(_isbn_to_isbn13("9780743273565"), "9780743273565")

    def test_goodreads_wrapper_stripped(self):
        """Goodreads-style ="..." wrapping is stripped before conversion."""
        self.assertEqual(_isbn_to_isbn13('="0306406152"'), "9780306406157")

    def test_invalid_isbn_returns_none(self):
        """Garbage input returns None instead of raising."""
        for bad in [None, "", "  ", "abc", "12345", "sg_internal_id", "123456789012345"]:
            self.assertIsNone(_isbn_to_isbn13(bad), f"Expected None for {bad!r}")

    def test_pandas_nan_returns_none(self):
        """pandas NaN floats don't blow up the helper."""
        import math as _math
        self.assertIsNone(_isbn_to_isbn13(_math.nan))
        self.assertIsNone(_isbn_to_isbn13(pd.NA))

    def test_round_trip_dedup_goodreads_then_storygraph(self):
        """Same physical book uploaded as Goodreads ISBN-10 + StoryGraph ISBN-13 dedupes to one DB row."""
        from django.contrib.auth.models import User

        from core.models import Book
        from core.services.dna import calculate_full_dna

        user = User.objects.create_user(username="isbn_dedup_user", password="x")

        goodreads_csv = (
            'Title,Author,Exclusive Shelf,Number of Pages,Date Read,My Rating,My Review,'
            'Original Publication Year,Average Rating,ISBN13,Binding\n'
            '"The Great Gatsby","F. Scott Fitzgerald",read,180,2024/01/01,5,Loved it,'
            '1925,4.0,="0743273567",Paperback\n'
        )
        storygraph_csv = (
            "Title,Authors,Read Status,Star Rating,ISBN/UID,Format,Last Date Read,Read Count,"
            "Number of Pages,Original Publication Year,Average Rating\n"
            '"The Great Gatsby","F. Scott Fitzgerald",read,5,9780743273565,Paperback,'
            "2024/02/01,1,180,1925,4.0\n"
        )

        from unittest.mock import patch

        with patch("core.services.dna.generate_vibe_with_llm", return_value=["a", "b"]), \
             patch("core.tasks.enrich_book_task.apply_async"), \
             patch("core.services.book_enrichment_service.enrich_book_from_apis"):
            calculate_full_dna(goodreads_csv, user=user)
            calculate_full_dna(storygraph_csv, user=user)

        # Same physical book → exactly one Book row, with ISBN-13 stored
        gatsby_books = Book.objects.filter(isbn13="9780743273565")
        self.assertEqual(gatsby_books.count(), 1, f"Expected 1 Book row, found {gatsby_books.count()}")
        # And no leftover row keyed only on the ISBN-10 form
        self.assertFalse(Book.objects.filter(isbn13="0743273567").exists())
