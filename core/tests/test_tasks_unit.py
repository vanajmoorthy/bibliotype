from io import StringIO
from unittest.mock import MagicMock, patch

import pandas as pd
from django.test import TestCase

from core.models import Book, UserProfile
from core.tasks import _save_dna_to_profile, assign_reader_type, get_or_enrich_book_details


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

    @patch("core.tasks.cache")
    @patch("core.tasks.requests.Session")
    @patch("core.tasks.Book.objects")
    def test_get_or_enrich_book_details(self, mock_book_manager, mock_session, mock_cache):
        """
        Tests the three-tiered data fetching logic (cache -> db -> api).
        """
        title = "Dune"
        author = "Frank Herbert"

        # Scenario 1: Data is found in the cache
        mock_cache.get.return_value = {"publish_year": 1965, "genres": ["science fiction"]}
        result = get_or_enrich_book_details(title, author, None, mock_session)
        self.assertEqual(result["publish_year"], 1965)
        mock_book_manager.get.assert_not_called()  # Database was not hit
        mock_session.get.assert_not_called()  # API was not hit

        # Scenario 2: Data is found in the database
        mock_cache.get.return_value = None  # Cache miss
        mock_book = MagicMock()
        mock_book.publish_year = 1965
        mock_book.genres.all.return_value = []
        mock_book_manager.get.return_value = mock_book

        result = get_or_enrich_book_details(title, author, None, mock_session)
        self.assertEqual(result["publish_year"], 1965)
        mock_cache.set.assert_called_once()  # Result was cached
        mock_session.get.assert_not_called()  # API was not hit

        # Scenario 3: Data is fetched from the API
        mock_cache.get.return_value = None
        mock_book_manager.get.side_effect = Book.DoesNotExist  # DB miss

        mock_api_response = MagicMock()
        mock_api_response.status_code = 200
        # A simplified Open Library response
        mock_api_response.json.return_value = {"docs": [{"cover_edition_key": "OL12345M"}]}
        mock_session.get.return_value = mock_api_response  # Mock the API call

        get_or_enrich_book_details(title, author, None, mock_session)
        self.assertTrue(mock_session.get.call_count > 0)  # API was called

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
