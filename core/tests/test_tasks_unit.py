from io import StringIO
from unittest.mock import MagicMock, patch

import pandas as pd
from django.test import TestCase

from core.models import Book, UserProfile
from core.services.dna_analyser import (
    STORYGRAPH_TAG_TO_GENRE,
    _detect_and_normalize_csv,
    _save_dna_to_profile,
    assign_reader_type,
)


SG_CSV_HEADER = (
    "Title,Authors,Contributors,ISBN/UID,Format,Read Status,"
    "Date Added,Last Date Read,Dates Read,Read Count,Moods,Pace,"
    "Character- or Plot-Driven?,Strong Character Development?,"
    "Loveable Characters?,Diverse Characters?,Flawed Characters?,"
    "Star Rating,Review,Content Warnings,Content Warning Description,Tags,Owned?"
)


def _sg_csv(*rows):
    """Join header + data rows into a single StoryGraph CSV string."""
    return "\n".join([SG_CSV_HEADER] + list(rows))


class TaskUnitTests(TestCase):

    def test_assign_reader_type_logic(self):
        """
        Tests the reader type scoring logic in isolation.
        """
        # Create a sample DataFrame of read books
        csv_data = """Title,Author,Exclusive Shelf,Number of Pages
        Book A,Author X,read,150
        Book B,Author Y,read,150
        Book C,Author Z,read,600
        """
        read_df = pd.read_csv(StringIO(csv_data))

        # Sample list of genres collected during processing
        all_genres = ["fantasy", "fantasy", "fantasy", "science fiction", "non-fiction"]

        reader_type, scores = assign_reader_type(read_df, {}, all_genres)

        # Assert that the scoring logic works as expected
        self.assertEqual(scores["Novella Navigator"], 2)
        self.assertEqual(scores["Tome Tussler"], 2)  # 1 book * 2 points
        self.assertEqual(scores["Fantasy Fanatic"], 4)
        self.assertEqual(scores["Non-Fiction Ninja"], 1)

        # Assert that the correct winner is chosen
        self.assertEqual(reader_type, "Fantasy Fanatic")

    @patch("core.tasks.generate_recommendations_task")
    def test_save_dna_to_profile(self, mock_recommendations_task):
        """
        Tests the helper for saving DNA data to a user profile.
        """
        mock_profile = MagicMock(spec=UserProfile)
        mock_recommendations_task.delay = MagicMock()
        dna_data = {
            "reader_type": "Test Type",
            "user_stats": {"total_books_read": 50},
            "reading_vibe": ["a test vibe"],
            "vibe_data_hash": "testhash123",
        }

        _save_dna_to_profile(mock_profile, dna_data)

        # Assert that the correct attributes were set on the mock profile
        self.assertEqual(mock_profile.reader_type, "Test Type")
        self.assertEqual(mock_profile.total_books_read, 50)
        self.assertEqual(mock_profile.reading_vibe, ["a test vibe"])
        self.assertEqual(mock_profile.vibe_data_hash, "testhash123")
        mock_profile.save.assert_called_once()


class NormalizationUnitTests(TestCase):

    def test_detect_and_normalize_storygraph_csv(self):
        """Verify StoryGraph CSV: columns renamed, multi-author split, ratings rounded half-up, ISBN validated."""
        csv_text = _sg_csv(
            # Multi-author (comma-separated), half-star rating, valid ISBN
            '"Good Omens","Terry Pratchett, Neil Gaiman",,9780060853983,Paperback,read,2024/01/01,2024/02/15,,1,,,,,,,,4.5,Great book!,,,',
            # Single author, integer rating, non-ISBN UID
            "Dune,Frank Herbert,,sg_internal_id,Kindle,read,2024/03/01,2024/04/01,,1,,,,,,,,3.0,OK book,,,",
            # Half-star edge case: 0.5 should round to 1
            "Tiny Book,Some Author,,1234567890,Paperback,read,2024/05/01,2024/06/01,,1,,,,,,,,0.5,,,,",
            # did-not-finish should be kept (filtering happens downstream)
            "DNF Book,Another Author,,,Paperback,did-not-finish,2024/05/01,,,1,,,,,,,,,,,,",
        )
        df = pd.read_csv(StringIO(csv_text))
        result_df, source = _detect_and_normalize_csv(df)

        self.assertEqual(source, "storygraph")
        # Columns renamed to Goodreads equivalents
        self.assertIn("Author", result_df.columns)
        self.assertIn("Exclusive Shelf", result_df.columns)
        self.assertIn("My Rating", result_df.columns)
        self.assertIn("Date Read", result_df.columns)
        self.assertIn("My Review", result_df.columns)
        self.assertIn("ISBN13", result_df.columns)
        # Missing columns added
        self.assertIn("Number of Pages", result_df.columns)
        self.assertIn("Original Publication Year", result_df.columns)
        self.assertIn("Average Rating", result_df.columns)

        # Multi-author: first author only (split on comma)
        self.assertEqual(result_df.iloc[0]["Author"], "Terry Pratchett")

        # Single author unchanged
        self.assertEqual(result_df.iloc[1]["Author"], "Frank Herbert")

        # Ratings: round-half-up
        self.assertEqual(result_df.iloc[0]["My Rating"], 5)  # 4.5 -> 5
        self.assertEqual(result_df.iloc[1]["My Rating"], 3)  # 3.0 -> 3
        self.assertEqual(result_df.iloc[2]["My Rating"], 1)  # 0.5 -> 1

        # ISBN validation: valid ISBN kept, non-ISBN UID discarded
        self.assertEqual(result_df.iloc[0]["ISBN13"], "9780060853983")
        self.assertTrue(pd.isna(result_df.iloc[1]["ISBN13"]))  # sg_internal_id filtered out
        self.assertEqual(result_df.iloc[2]["ISBN13"], "1234567890")  # 10-digit ISBN kept

        # Read Status mapped to Exclusive Shelf
        self.assertEqual(result_df.iloc[0]["Exclusive Shelf"], "read")
        self.assertEqual(result_df.iloc[3]["Exclusive Shelf"], "did-not-finish")

    def test_detect_and_normalize_goodreads_csv(self):
        """Verify Goodreads CSV passes through unchanged."""
        csv_text = "Title,Author,Exclusive Shelf,My Rating\nSome Book,Some Author,read,4"
        df = pd.read_csv(StringIO(csv_text))
        result_df, source = _detect_and_normalize_csv(df)

        self.assertEqual(source, "goodreads")
        # DataFrame should be unchanged
        self.assertEqual(len(result_df), 1)
        self.assertEqual(result_df.iloc[0]["Title"], "Some Book")
        self.assertEqual(result_df.iloc[0]["My Rating"], 4)

    def test_detect_unrecognized_csv_raises_error(self):
        """Unknown CSV format raises ValueError with clear message."""
        csv_text = "Col1,Col2,Col3\na,b,c"
        df = pd.read_csv(StringIO(csv_text))

        with self.assertRaises(ValueError) as ctx:
            _detect_and_normalize_csv(df)

        self.assertIn("Unrecognized CSV format", str(ctx.exception))
        self.assertIn("Goodreads or StoryGraph", str(ctx.exception))

    def test_bom_handling_storygraph_csv(self):
        """CSV with UTF-8 BOM prefix is parsed correctly after BOM stripping."""
        csv_text = _sg_csv(
            '"The Great Gatsby","F. Scott Fitzgerald",,9780743273565,Paperback,read,2024/01/01,2024/02/01,,1,,,,,,,,4.0,,,,',
        )
        # Simulate BOM-prefixed content (as produced by some StoryGraph exports)
        bom_csv = "\ufeff" + csv_text
        # utf-8-sig decoding strips the BOM — simulate what views.py does
        decoded = bom_csv.encode("utf-8-sig").decode("utf-8-sig")
        df = pd.read_csv(StringIO(decoded))
        result_df, source = _detect_and_normalize_csv(df)

        self.assertEqual(source, "storygraph")
        self.assertEqual(result_df.iloc[0]["Title"], "The Great Gatsby")

    def test_bom_handling_goodreads_csv(self):
        """Goodreads CSV with BOM is handled correctly."""
        csv_text = "Title,Author,Exclusive Shelf,My Rating\nSome Book,Some Author,read,4"
        bom_csv = "\ufeff" + csv_text
        decoded = bom_csv.encode("utf-8-sig").decode("utf-8-sig")
        df = pd.read_csv(StringIO(decoded))
        result_df, source = _detect_and_normalize_csv(df)

        self.assertEqual(source, "goodreads")
        self.assertEqual(result_df.iloc[0]["Title"], "Some Book")

    def test_storygraph_columns_preserved_through_normalization(self):
        """Moods, Pace, Read Count, and Tags columns survive normalization."""
        csv_text = _sg_csv(
            '"Test Book","Test Author",,9780000000001,Paperback,read,2024/01/01,2024/02/01,,3,dark,fast,,,,,,4.0,,,,"dystopian, sci-fi",Yes',
        )
        df = pd.read_csv(StringIO(csv_text))
        result_df, source = _detect_and_normalize_csv(df)

        self.assertEqual(source, "storygraph")
        self.assertIn("Moods", result_df.columns)
        self.assertIn("Pace", result_df.columns)
        self.assertIn("Read Count", result_df.columns)
        self.assertIn("Tags", result_df.columns)
        self.assertEqual(result_df.iloc[0]["Moods"], "dark")
        self.assertEqual(result_df.iloc[0]["Pace"], "fast")
        self.assertEqual(result_df.iloc[0]["Read Count"], 3)


class ReReadDetectionTests(TestCase):

    def test_comfort_rereader_scoring_with_read_count(self):
        """StoryGraph Read Count > 1 awards 3 points per reread to Comfort Rereader."""
        csv_data = """Title,Author,Exclusive Shelf,Number of Pages,Read Count
        Book A,Author X,read,200,3
        Book B,Author Y,read,200,1
        Book C,Author Z,read,200,2
        Book D,Author W,read,200,1
        """
        read_df = pd.read_csv(StringIO(csv_data))
        read_df.columns = read_df.columns.str.strip()

        reader_type, scores = assign_reader_type(read_df, {}, [])

        # Books A (Read Count=3) and C (Read Count=2) are re-reads → 2 rereads × 3 pts = 6
        self.assertEqual(scores["Comfort Rereader"], 6)

    def test_comfort_rereader_scoring_goodreads_fallback(self):
        """Goodreads uses duplicate title detection when Read Count column is absent."""
        csv_data = """Title,Author,Exclusive Shelf,Number of Pages
        Book A,Author X,read,200
        Book A,Author X,read,200
        Book B,Author Y,read,200
        """
        read_df = pd.read_csv(StringIO(csv_data))
        read_df.columns = read_df.columns.str.strip()

        reader_type, scores = assign_reader_type(read_df, {}, [])

        # Book A appears twice → 2 duplicated rows // 2 = 1 reread × 3 pts = 3
        self.assertEqual(scores["Comfort Rereader"], 3)

    def test_no_comfort_rereader_when_no_rereads(self):
        """No re-reads means no Comfort Rereader score."""
        csv_data = """Title,Author,Exclusive Shelf,Number of Pages,Read Count
        Book A,Author X,read,200,1
        Book B,Author Y,read,200,1
        """
        read_df = pd.read_csv(StringIO(csv_data))
        read_df.columns = read_df.columns.str.strip()

        reader_type, scores = assign_reader_type(read_df, {}, [])

        self.assertEqual(scores.get("Comfort Rereader", 0), 0)


class StoryGraphTagMappingTests(TestCase):

    def test_tag_to_genre_mapping_covers_common_tags(self):
        """STORYGRAPH_TAG_TO_GENRE maps common user tags to canonical genres."""
        self.assertEqual(STORYGRAPH_TAG_TO_GENRE["sci-fi"], "science fiction")
        self.assertEqual(STORYGRAPH_TAG_TO_GENRE["fantasy"], "fantasy")
        self.assertEqual(STORYGRAPH_TAG_TO_GENRE["dystopian"], "dystopian")
        self.assertEqual(STORYGRAPH_TAG_TO_GENRE["classic"], "classics")
        self.assertEqual(STORYGRAPH_TAG_TO_GENRE["non-fiction"], "non-fiction")
        self.assertEqual(STORYGRAPH_TAG_TO_GENRE["ya"], "young adult")
        self.assertEqual(STORYGRAPH_TAG_TO_GENRE["memoir"], "biography")

    def test_unknown_tags_are_not_in_mapping(self):
        """Tags not in the mapping are silently ignored during processing."""
        self.assertNotIn("page-turner", STORYGRAPH_TAG_TO_GENRE)
        self.assertNotIn("beach-read", STORYGRAPH_TAG_TO_GENRE)
        self.assertNotIn("", STORYGRAPH_TAG_TO_GENRE)


class MoodPaceDistributionTests(TestCase):

    def test_mood_distribution_from_storygraph_csv(self):
        """Mood distribution is computed from StoryGraph Moods column."""
        from collections import Counter

        csv_text = _sg_csv(
            '"Book A","Author A",,9780000000001,Paperback,read,2024/01/01,2024/02/01,,1,"dark, reflective",slow,,,,,,4.0,,,,,Yes',
            '"Book B","Author B",,9780000000002,Paperback,read,2024/01/01,2024/02/01,,1,"dark, adventurous",fast,,,,,,3.0,,,,,Yes',
            '"Book C","Author C",,9780000000003,Paperback,read,2024/01/01,2024/02/01,,1,lighthearted,medium,,,,,,5.0,,,,,Yes',
        )
        df = pd.read_csv(StringIO(csv_text))
        result_df, _ = _detect_and_normalize_csv(df)

        # Simulate the mood distribution logic from dna_analyser.py
        all_moods = []
        for moods_str in result_df["Moods"].dropna():
            all_moods.extend([m.strip().lower() for m in str(moods_str).split(",") if m.strip()])
        mood_distribution = Counter(all_moods).most_common(10)

        mood_dict = dict(mood_distribution)
        self.assertEqual(mood_dict["dark"], 2)
        self.assertEqual(mood_dict["reflective"], 1)
        self.assertEqual(mood_dict["adventurous"], 1)
        self.assertEqual(mood_dict["lighthearted"], 1)

    def test_pace_distribution_from_storygraph_csv(self):
        """Pace distribution is computed from StoryGraph Pace column."""
        from collections import Counter

        csv_text = _sg_csv(
            '"Book A","Author A",,9780000000001,Paperback,read,2024/01/01,2024/02/01,,1,,slow,,,,,,4.0,,,,,Yes',
            '"Book B","Author B",,9780000000002,Paperback,read,2024/01/01,2024/02/01,,1,,fast,,,,,,3.0,,,,,Yes',
            '"Book C","Author C",,9780000000003,Paperback,read,2024/01/01,2024/02/01,,1,,slow,,,,,,5.0,,,,,Yes',
        )
        df = pd.read_csv(StringIO(csv_text))
        result_df, _ = _detect_and_normalize_csv(df)

        pace_values = result_df["Pace"].dropna().str.strip().str.lower()
        pace_distribution = Counter(pace_values).most_common()

        pace_dict = dict(pace_distribution)
        self.assertEqual(pace_dict["slow"], 2)
        self.assertEqual(pace_dict["fast"], 1)

    def test_goodreads_csv_has_empty_mood_pace(self):
        """Goodreads CSVs lack Moods/Pace columns — distribution should be empty."""
        csv_text = "Title,Author,Exclusive Shelf,My Rating\nBook,Author,read,4"
        df = pd.read_csv(StringIO(csv_text))
        result_df, source = _detect_and_normalize_csv(df)

        self.assertEqual(source, "goodreads")
        self.assertNotIn("Moods", result_df.columns)
        self.assertNotIn("Pace", result_df.columns)
