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
from core.services.dna_analyser import (
    STORYGRAPH_TAG_TO_GENRE,
    _detect_and_normalize_csv,
    _isbn_to_isbn13,
    assign_reader_type,
)


# ────────────────────────────────────────────
# Reader type scoring math
# ────────────────────────────────────────────


class ReaderTypeScoringMathTests(TestCase):
    """Verify reader type scoring formulas produce exact expected values."""

    def _df(self, rows, has_read_count=False):
        cols = "Title,Author,Exclusive Shelf,Number of Pages"
        if has_read_count:
            cols += ",Read Count"
        csv_text = cols + "\n" + "\n".join(rows)
        df = pd.read_csv(StringIO(csv_text))
        df.columns = df.columns.str.strip()
        return df

    def test_tome_tussler_scores_2_points_per_long_book(self):
        """Books with >490 pages give Tome Tussler 2 points each."""
        df = self._df([
            "Book A,Auth,read,500",
            "Book B,Auth,read,600",
            "Book C,Auth,read,491",
            "Book D,Auth,read,490",  # boundary: 490 NOT counted (>490 strict)
            "Book E,Auth,read,200",
        ])
        _, scores = assign_reader_type(df, {}, [])
        self.assertEqual(scores["Tome Tussler"], 6)  # 3 books * 2 pts

    def test_novella_navigator_scores_1_point_per_short_book(self):
        """Books with <200 pages give Novella Navigator 1 point each."""
        df = self._df([
            "Book A,Auth,read,150",
            "Book B,Auth,read,199",
            "Book C,Auth,read,100",
            "Book D,Auth,read,200",  # boundary: 200 NOT counted (<200 strict)
            "Book E,Auth,read,250",
        ])
        _, scores = assign_reader_type(df, {}, [])
        self.assertEqual(scores["Novella Navigator"], 3)

    def test_fantasy_fanatic_combines_fantasy_scifi_dystopian(self):
        """Fantasy Fanatic = fantasy + science fiction + dystopian counts."""
        df = self._df(["Book A,Auth,read,300"])
        genres = ["fantasy", "fantasy", "science fiction", "dystopian", "dystopian", "thriller"]
        _, scores = assign_reader_type(df, {}, genres)
        self.assertEqual(scores["Fantasy Fanatic"], 5)  # 2 + 1 + 2

    def test_versatile_valedictorian_bonus_at_diversity_threshold(self):
        """Versatile Valedictorian gets +15 bonus when ≥10 unique canonical genres."""
        df = self._df(["Book A,Auth,read,300"])
        # Exactly 10 unique canonical genres
        genres = [
            "fantasy", "science fiction", "thriller", "horror", "romance",
            "biography", "history", "philosophy", "psychology", "non-fiction",
        ]
        _, scores = assign_reader_type(df, {}, genres)
        self.assertEqual(scores["Versatile Valedictorian"], 15)

    def test_no_versatile_bonus_below_threshold(self):
        """No diversity bonus with 9 unique canonical genres."""
        df = self._df(["Book A,Auth,read,300"])
        genres = [
            "fantasy", "science fiction", "thriller", "horror", "romance",
            "biography", "history", "philosophy", "psychology",
        ]
        _, scores = assign_reader_type(df, {}, genres)
        self.assertEqual(scores.get("Versatile Valedictorian", 0), 0)

    def test_comfort_rereader_3_points_per_reread_storygraph(self):
        """StoryGraph: Comfort Rereader gets 3 pts per book with Read Count > 1."""
        df = self._df([
            "Book A,Auth,read,200,3",
            "Book B,Auth,read,200,1",
            "Book C,Auth,read,200,5",  # also a reread
            "Book D,Auth,read,200,1",
        ], has_read_count=True)
        _, scores = assign_reader_type(df, {}, [])
        self.assertEqual(scores["Comfort Rereader"], 6)  # 2 rereads * 3

    def test_comfort_rereader_goodreads_duplicate_titles(self):
        """Goodreads (no Read Count): duplicate titles count as rereads."""
        df = self._df([
            "Book A,Auth,read,200",
            "Book A,Auth,read,200",  # duplicate → 1 reread
            "Book B,Auth,read,200",
            "Book B,Auth,read,200",  # duplicate → 1 reread
            "Book C,Auth,read,200",
        ])
        _, scores = assign_reader_type(df, {}, [])
        # 2 titles each read twice → 2 rereads * 3 pts = 6
        self.assertEqual(scores["Comfort Rereader"], 6)

    def test_comfort_rereader_goodreads_three_reads(self):
        """Goodreads: a title read 3 times = 2 rereads = 6 pts (not 1 reread)."""
        df = self._df([
            "Book A,Auth,read,200",
            "Book A,Auth,read,200",
            "Book A,Auth,read,200",
            "Book B,Auth,read,200",  # solo, no rereads
        ])
        _, scores = assign_reader_type(df, {}, [])
        # Book A: 3 reads → 2 rereads. Book B: 0. Total: 2 * 3 = 6.
        self.assertEqual(scores["Comfort Rereader"], 6)

    def test_no_comfort_rereader_when_zero_rereads(self):
        """Zero rereads → no Comfort Rereader score (not even 0 entry)."""
        df = self._df([
            "Book A,Auth,read,200,1",
            "Book B,Auth,read,200,1",
        ], has_read_count=True)
        _, scores = assign_reader_type(df, {}, [])
        self.assertEqual(scores.get("Comfort Rereader", 0), 0)

    def test_classic_collector_pre_1970(self):
        """Books published before 1970 give Classic Collector 1 pt each."""
        df = self._df([
            "Book A,Auth,read,300",
            "Book B,Auth,read,300",
            "Book C,Auth,read,300",
        ])
        enriched = {
            "Book A": {"publish_year": 1950, "publisher": None},
            "Book B": {"publish_year": 1969, "publisher": None},
            "Book C": {"publish_year": 1970, "publisher": None},  # boundary excluded
        }
        _, scores = assign_reader_type(df, enriched, [])
        self.assertEqual(scores["Classic Collector"], 2)

    def test_modern_maverick_post_2018(self):
        """Books published after 2018 give Modern Maverick 1 pt each."""
        df = self._df([
            "Book A,Auth,read,300",
            "Book B,Auth,read,300",
            "Book C,Auth,read,300",
        ])
        enriched = {
            "Book A": {"publish_year": 2019, "publisher": None},
            "Book B": {"publish_year": 2024, "publisher": None},
            "Book C": {"publish_year": 2018, "publisher": None},  # boundary excluded
        }
        _, scores = assign_reader_type(df, enriched, [])
        self.assertEqual(scores["Modern Maverick"], 2)

    def test_small_press_supporter_non_mainstream_publishers(self):
        """Books from non-mainstream publishers give Small Press Supporter 1 pt each."""
        df = self._df([
            "Book A,Auth,read,300",
            "Book B,Auth,read,300",
        ])
        mainstream_pub = MagicMock(is_mainstream=True)
        small_pub = MagicMock(is_mainstream=False)
        small_pub.__str__ = lambda self: "SmallPress"
        enriched = {
            "Book A": {"publish_year": 2000, "publisher": small_pub},
            "Book B": {"publish_year": 2000, "publisher": mainstream_pub},
        }
        _, scores = assign_reader_type(df, enriched, [])
        self.assertEqual(scores["Small Press Supporter"], 1)

    def test_eclectic_reader_returned_when_all_zero_scores(self):
        """When no reader type has positive score, returns Eclectic Reader."""
        df = self._df(["Book A,Auth,read,300"])
        reader_type, _ = assign_reader_type(df, {}, [])
        self.assertEqual(reader_type, "Eclectic Reader")

    def test_not_enough_data_for_empty_df(self):
        """Empty dataframe returns 'Not enough data'."""
        df = self._df([])
        df = df.iloc[0:0]  # ensure empty
        reader_type, scores = assign_reader_type(df, {}, [])
        self.assertEqual(reader_type, "Not enough data")
        self.assertEqual(len(scores), 0)


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

        biography_targets = {STORYGRAPH_TAG_TO_GENRE[t] for t in ["biography", "memoir"]}
        self.assertEqual(biography_targets, {"biography"})

        ya_targets = {STORYGRAPH_TAG_TO_GENRE[t] for t in ["young adult", "ya"]}
        self.assertEqual(ya_targets, {"young adult"})


# ────────────────────────────────────────────
# Reader type descriptions completeness
# ────────────────────────────────────────────


class ReaderTypeDescriptionsTests(TestCase):
    """Verify every reader type with scoring logic has a description."""

    def test_all_scored_reader_types_have_descriptions(self):
        """Every reader type that can be assigned must have a description in READER_TYPE_DESCRIPTIONS."""
        # Reader types that assign_reader_type can produce
        scored_types = {
            "Rapacious Reader",
            "Tome Tussler",
            "Novella Navigator",
            "Fantasy Fanatic",
            "Non-Fiction Ninja",
            "Philosophical Philomath",
            "Nature Nut Case",
            "Social Savant",
            "Self Help Scholar",
            "Classic Collector",
            "Modern Maverick",
            "Small Press Supporter",
            "Comfort Rereader",
            "Versatile Valedictorian",
            "Eclectic Reader",
        }
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
        from core.services.dna_analyser import calculate_full_dna

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
             patch("core.tasks.enrich_book_task.delay"):
            calculate_full_dna(goodreads_csv, user=user)
            calculate_full_dna(storygraph_csv, user=user)

        # Same physical book → exactly one Book row, with ISBN-13 stored
        gatsby_books = Book.objects.filter(isbn13="9780743273565")
        self.assertEqual(gatsby_books.count(), 1, f"Expected 1 Book row, found {gatsby_books.count()}")
        # And no leftover row keyed only on the ISBN-10 form
        self.assertFalse(Book.objects.filter(isbn13="0743273567").exists())
