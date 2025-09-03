from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from core.models import Book, UserProfile
from core.tasks import claim_anonymous_dna_task, generate_reading_dna_task


# This setting makes Celery tasks run synchronously in the test
@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class TaskIntegrationTests(TestCase):

    # Mock the slow, external network calls
    @patch("core.tasks.get_or_enrich_book_details")
    @patch("core.tasks.generate_vibe_with_llm")
    def test_generate_dna_for_authenticated_user(self, mock_generate_vibe, mock_enrich_details):
        """
        Tests the full DNA generation task for a logged-in user.
        """
        mock_enrich_details.return_value = {"publish_year": 2020, "genres": ["test-genre"]}
        mock_generate_vibe.return_value = ["a cool vibe"]

        user = User.objects.create_user(username="testuser", password="password")

        header = "Title,Author,Exclusive Shelf,My Rating,Number of Pages,Original Publication Year,Date Read,Average Rating,My Review,ISBN13"
        row = "Authenticated Book,Test Author,read,5,150,2021,2023/01/15,4.2,A test review.,9780000000001"
        csv_content = f"{header}\n{row}".encode("utf-8")

        # Run the task directly
        generate_reading_dna_task.delay(csv_content.decode("utf-8"), user.id)

        # Check the results in the database
        user.userprofile.refresh_from_db()
        self.assertIsNotNone(user.userprofile.dna_data)
        self.assertEqual(user.userprofile.reader_type, "Novella Navigator")
        self.assertEqual(user.userprofile.dna_data["top_genres"]["test-genre"], 1)

    @patch("core.tasks.get_or_enrich_book_details")
    @patch("core.tasks.generate_vibe_with_llm")
    def test_generate_dna_for_anonymous_user(self, mock_generate_vibe, mock_enrich_details):
        """
        Tests that anonymous generation saves its result to the cache.
        """
        mock_enrich_details.return_value = {"publish_year": 2020, "genres": ["anon-genre"]}
        mock_generate_vibe.return_value = ["an anonymous vibe"]

        header = "Title,Author,Exclusive Shelf,My Rating,Number of Pages,Original Publication Year,Date Read,Average Rating,My Review,ISBN13"
        row = "Authenticated Book,Test Author,read,5,150,2021,2023/01/15,4.2,A test review.,9780000000001"
        csv_content = f"{header}\n{row}".encode("utf-8")

        # Run the task and get the result object
        result = generate_reading_dna_task.delay(csv_content.decode("utf-8"), None)
        task_id = result.id

        # Check that the result was saved to the cache
        cached_data = cache.get(f"dna_result:{task_id}")
        self.assertIsNotNone(cached_data)
        self.assertEqual(cached_data["top_genres"]["anon-genre"], 1)

    def test_claim_anonymous_dna_task(self):
        """
        Tests the claiming task's ability to fetch from cache and save to a profile.
        """
        user = User.objects.create_user(username="newuser", password="password")
        task_id = "fake-task-id-123"

        # Manually place fake DNA data into the cache
        fake_dna = {"reader_type": "Claimed Reader"}
        cache.set(f"dna_result:{task_id}", fake_dna, timeout=60)

        # Run the claiming task
        claim_anonymous_dna_task.delay(user.id, task_id)

        # Verify the result
        user.userprofile.refresh_from_db()
        self.assertIsNotNone(user.userprofile.dna_data)
        self.assertEqual(user.userprofile.reader_type, "Claimed Reader")

        # Verify the cache key was deleted
        self.assertIsNone(cache.get(f"dna_result:{task_id}"))
