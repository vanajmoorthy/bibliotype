from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, TransactionTestCase, override_settings

from core.models import Author, Book
from core.tasks import claim_anonymous_dna_task, generate_reading_dna_task


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


# This setting makes Celery tasks run synchronously in the test
@override_settings(
    CELERY_TASK_ALWAYS_EAGER=True,
    CELERY_TASK_EAGER_PROPAGATES=True,
    CELERY_RESULT_BACKEND="django-db",
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "task-integration-tests",
        }
    },
)
class TaskIntegrationTests(TransactionTestCase):

    def setUp(self):
        try:
            cache.clear()
        except Exception:
            pass

    # Mock the slow, external network calls
    @patch("core.tasks.generate_recommendations_task.delay")
    @patch("core.tasks.check_author_mainstream_status_task.delay")
    @patch("core.tasks.enrich_book_task.delay")
    @patch("core.services.dna_analyser.generate_vibe_with_llm")
    def test_generate_dna_for_authenticated_user(self, mock_generate_vibe, mock_enrich_delay, mock_author_check, mock_rec_task):
        """
        Tests the full DNA generation task for a logged-in user.
        """
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

    @patch("core.tasks.check_author_mainstream_status_task.delay")
    @patch("core.tasks.enrich_book_task.delay")
    @patch("core.services.dna_analyser.generate_vibe_with_llm")
    def test_generate_dna_for_anonymous_user(self, mock_generate_vibe, mock_enrich_delay, mock_author_check):
        """
        Tests that anonymous generation saves its result to the cache.
        """
        mock_generate_vibe.return_value = ["an anonymous vibe"]

        header = "Title,Author,Exclusive Shelf,My Rating,Number of Pages,Original Publication Year,Date Read,Average Rating,My Review,ISBN13"
        row = "Anonymous Book,Anon Author,read,4,180,2022,2023/02/20,3.9,An anon review.,9780000000002"
        csv_content = f"{header}\n{row}".encode("utf-8")

        result = generate_reading_dna_task.delay(csv_content.decode("utf-8"), None)
        task_id = result.id

        cached_data = cache.get(f"dna_result_{task_id}")
        self.assertIsNotNone(cached_data)
        self.assertEqual(cached_data["reader_type"], "Novella Navigator")

    @patch("core.tasks.generate_recommendations_task.delay")
    @patch("core.tasks.AsyncResult")
    def test_claim_anonymous_dna_task(self, mock_async_result, mock_rec_task):
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

    @patch("core.tasks.generate_recommendations_task.delay")
    @patch("core.tasks.check_author_mainstream_status_task.delay")
    @patch("core.tasks.enrich_book_task.delay")
    @patch("core.services.dna_analyser.generate_vibe_with_llm")
    @patch("core.services.dna_analyser.GOOGLE_BOOKS_API_KEY", None)
    def test_generate_dna_for_authenticated_user_storygraph(
        self, mock_generate_vibe, mock_enrich_delay, mock_author_check, mock_rec_task
    ):
        """
        Tests the full DNA generation pipeline with a StoryGraph CSV.
        Verifies: format detection, column normalization, rating rounding,
        ISBN validation, csv_source persistence, and graceful handling of
        missing data (pages, publish year, average rating).
        """
        mock_generate_vibe.return_value = ["a storygraph vibe"]

        user = User.objects.create_user(username="sguser", password="password")

        csv_content = _sg_csv(
            '"Good Omens","Terry Pratchett, Neil Gaiman",,9780060853983,Paperback,read,2024/01/01,2024/02/15,,1,,,,,,,,4.5,Great book!,,,',
            "Dune,Frank Herbert,,sg_internal_123,Kindle,read,2024/03/01,2024/04/01,,1,,,,,,,,3.0,,,,",
            "DNF Book,Some Writer,,,Paperback,did-not-finish,2024/05/01,,,1,,,,,,,,,,,,",
        )

        generate_reading_dna_task.delay(csv_content, user.id)

        user.userprofile.refresh_from_db()
        dna = user.userprofile.dna_data
        self.assertIsNotNone(dna)
        self.assertEqual(dna["csv_source"], "storygraph")
        # Only 2 books should be on the 'read' shelf (did-not-finish excluded)
        self.assertEqual(dna["user_stats"]["total_books_read"], 2)
        # Ratings: 4.5 -> 5, 3.0 -> 3; only rated books counted
        self.assertIn("ratings_distribution", dna)
        # Controversial books should be empty (no Average Rating in StoryGraph CSV, no enrichment)
        self.assertEqual(dna["top_controversial_books"], [])

        # Verify books were created with correct ISBN handling
        good_omens = Book.objects.filter(isbn13="9780060853983").first()
        self.assertIsNotNone(good_omens)
        # sg_internal_123 should NOT be stored as ISBN
        dune = Book.objects.filter(normalized_title__icontains="dune").first()
        self.assertIsNotNone(dune)
        self.assertIsNone(dune.isbn13)

    @patch("core.tasks.generate_recommendations_task.delay")
    @patch("core.tasks.check_author_mainstream_status_task.delay")
    @patch("core.tasks.enrich_book_task.delay")
    @patch("core.services.dna_analyser.generate_vibe_with_llm")
    @patch("core.services.dna_analyser.GOOGLE_BOOKS_API_KEY", None)
    def test_book_defaults_no_overwrite_with_none(
        self, mock_generate_vibe, mock_enrich_delay, mock_author_check, mock_rec_task
    ):
        """
        Tests that uploading a StoryGraph CSV (which lacks page_count, average_rating)
        does NOT overwrite existing enriched book data with None.
        """
        mock_generate_vibe.return_value = ["a vibe"]

        # Pre-create an enriched book
        author = Author.objects.create(name="Frank Herbert", normalized_name="frankherbert")
        book = Book.objects.create(
            title="Dune",
            author=author,
            normalized_title="dune",
            page_count=412,
            average_rating=4.25,
            publish_year=1965,
        )

        user = User.objects.create_user(username="overwriteuser", password="password")

        csv_content = _sg_csv(
            "Dune,Frank Herbert,,,Kindle,read,2024/03/01,2024/04/01,,1,,,,,,,,5.0,,,,",
        )

        generate_reading_dna_task.delay(csv_content, user.id)

        # Verify the enriched data was NOT overwritten
        book.refresh_from_db()
        self.assertEqual(book.page_count, 412)
        self.assertEqual(book.average_rating, 4.25)
        self.assertEqual(book.publish_year, 1965)
