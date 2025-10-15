from io import StringIO
from unittest.mock import MagicMock, patch

import pandas as pd
from django.test import TestCase

from core.models import Book, UserProfile
from core.services.dna_analyser import _save_dna_to_profile, assign_reader_type


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

    

    def test_save_dna_to_profile(self):
        """
        Tests the helper for saving DNA data to a user profile.
        """
        mock_profile = MagicMock(spec=UserProfile)
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
