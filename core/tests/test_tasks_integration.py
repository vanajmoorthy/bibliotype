from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, override_settings

from core.tasks import claim_anonymous_dna_task, generate_reading_dna_task


# This setting makes Celery tasks run synchronously in the test
@override_settings(
    CELERY_TASK_ALWAYS_EAGER=True,
    CELERY_RESULT_BACKEND="django-db",
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": "redis://redis:6379/1",
        }
    },
)
class TaskIntegrationTests(TestCase):

    # Mock the slow, external network calls
    @patch("core.services.dna_analyser.enrich_book_from_apis")
    @patch("core.services.dna_analyser.generate_vibe_with_llm")
    def test_generate_dna_for_authenticated_user(self, mock_generate_vibe, mock_enrich_apis):
        """
        Tests the full DNA generation task for a logged-in user.
        """
        mock_enrich_apis.return_value = (None, 0, 0)
        mock_generate_vibe.return_value = ["a cool vibe"]

        user = User.objects.create_user(username="testuser", password="password")

        header = "Title,Author,Exclusive Shelf,My Rating,Number of Pages,Original Publication Year,Date Read,Average Rating,My Review,ISBN13"
        row = "Authenticated Book,Auth Author,read,5,150,2021,2023/01/15,4.2,A test review.,9780000000001"
        csv_content = f"{header}\n{row}".encode("utf-8")

        # Run the task directly
        generate_reading_dna_task.delay(csv_content.decode("utf-8"), user.id)

        # Check the results in the database
        user.userprofile.refresh_from_db()
        self.assertIsNotNone(user.userprofile.dna_data)
        self.assertEqual(user.userprofile.reader_type, "Novella Navigator")

    @patch("core.services.dna_analyser.enrich_book_from_apis")
    @patch("core.services.dna_analyser.generate_vibe_with_llm")
    def test_generate_dna_for_anonymous_user(self, mock_generate_vibe, mock_enrich_apis):
        """
        Tests that anonymous generation saves its result to the cache.
        """
        mock_enrich_apis.return_value = (None, 0, 0)
        mock_generate_vibe.return_value = ["an anonymous vibe"]

        header = "Title,Author,Exclusive Shelf,My Rating,Number of Pages,Original Publication Year,Date Read,Average Rating,My Review,ISBN13"
        row = "Anonymous Book,Anon Author,read,4,180,2022,2023/02/20,3.9,An anon review.,9780000000002"
        csv_content = f"{header}\n{row}".encode("utf-8")

        result = generate_reading_dna_task.delay(csv_content.decode("utf-8"), None)
        task_id = result.id

        cached_data = cache.get(f"dna_result_{task_id}")
        self.assertIsNotNone(cached_data)
        self.assertEqual(cached_data["reader_type"], "Novella Navigator")

    @patch("core.tasks.AsyncResult")
    def test_claim_anonymous_dna_task(self, mock_async_result):
        """
        Tests the claiming task's ability to fetch a result from the Celery backend
        and save it to a profile.
        """
        user = User.objects.create_user(username="newuser", password="password")
        task_id = "fake-task-id-123"
        fake_dna = {"reader_type": "Claimed Reader", "user_stats": {}, "reading_vibe": [], "vibe_data_hash": ""}

        mock_result = mock_async_result.return_value
        mock_result.ready.return_value = True
        mock_result.successful.return_value = True
        mock_result.get.return_value = fake_dna

        claim_anonymous_dna_task.delay(user.id, task_id)

        user.userprofile.refresh_from_db()
        self.assertIsNotNone(user.userprofile.dna_data)
        self.assertEqual(user.userprofile.reader_type, "Claimed Reader")

        mock_async_result.assert_called_with(task_id)
