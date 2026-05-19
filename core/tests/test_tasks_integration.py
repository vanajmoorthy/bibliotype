from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, TransactionTestCase, override_settings

from core.tasks import claim_anonymous_dna_task, generate_reading_dna_task


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
        and save it to a profile when the supplied session_key matches the
        cached task owner (the happy path established by US-001 + US-003).
        """
        user = User.objects.create_user(username="newuser", password="password")
        task_id = "fake-task-id-123"
        legit_session_key = "legit-uploader-session-key"
        # Cache the binding the same way upload_view does (US-001).
        cache.set(f"task_owner_{task_id}", legit_session_key, 3600)
        fake_dna = {"reader_type": "Claimed Reader", "user_stats": {}, "reading_vibe": [], "vibe_data_hash": ""}

        mock_result = mock_async_result.return_value
        mock_result.ready.return_value = True
        mock_result.successful.return_value = True
        mock_result.get.return_value = fake_dna

        claim_anonymous_dna_task.delay(user.id, task_id, legit_session_key)

        user.userprofile.refresh_from_db()
        self.assertIsNotNone(user.userprofile.dna_data)
        self.assertEqual(user.userprofile.reader_type, "Claimed Reader")

        mock_async_result.assert_called_with(task_id)

    @patch("core.tasks.generate_recommendations_task.delay")
    @patch("core.tasks.AsyncResult")
    def test_claim_anonymous_dna_task_fails_closed_on_cache_miss(self, mock_async_result, mock_rec_task):
        """
        Post-review hardening: when `task_owner_<task_id>` is absent from cache
        (TTL expired or task_id never bound), the claim task MUST reject — the
        previous behavior silently passed because owner was None, which re-
        opened the hijack window after the 1-hour TTL.

        Also verifies pending_dna_task_id is cleared so the user's dashboard
        doesn't poll forever.
        """
        user = User.objects.create_user(username="cachemiss", password="password")
        user.userprofile.pending_dna_task_id = "expired-task-id"
        user.userprofile.save()

        task_id = "expired-task-id"
        # NOTE: deliberately do NOT cache.set(f"task_owner_{task_id}", ...)

        with self.assertLogs("core.tasks", level="WARNING") as log_capture:
            claim_anonymous_dna_task.delay(user.id, task_id, "any-session-key")

        self.assertTrue(
            any("task_owner cache miss" in msg for msg in log_capture.output),
            f"expected cache-miss warning not found: {log_capture.output}",
        )
        user.userprofile.refresh_from_db()
        self.assertIsNone(user.userprofile.dna_data)
        self.assertIsNone(user.userprofile.pending_dna_task_id)
        mock_async_result.assert_not_called()

    @patch("core.tasks.generate_recommendations_task.delay")
    @patch("core.tasks.AsyncResult")
    def test_claim_anonymous_dna_task_fails_closed_on_missing_session_key(
        self, mock_async_result, mock_rec_task
    ):
        """
        Post-review hardening: empty/missing session_key (e.g. direct broker
        publish) MUST refuse the claim. The previous default `session_key=None`
        silently bypassed the ownership check entirely.
        """
        user = User.objects.create_user(username="nokeyuser", password="password")
        user.userprofile.pending_dna_task_id = "some-task"
        user.userprofile.save()

        task_id = "some-task"
        cache.set(f"task_owner_{task_id}", "the-real-owner-key", 3600)

        with self.assertLogs("core.tasks", level="WARNING") as log_capture:
            claim_anonymous_dna_task.delay(user.id, task_id, "")

        self.assertTrue(
            any("missing session_key" in msg for msg in log_capture.output),
            f"expected missing-key warning not found: {log_capture.output}",
        )
        user.userprofile.refresh_from_db()
        self.assertIsNone(user.userprofile.dna_data)
        self.assertIsNone(user.userprofile.pending_dna_task_id)
        mock_async_result.assert_not_called()

    @patch("core.tasks.generate_recommendations_task.delay")
    @patch("core.tasks.AsyncResult")
    def test_claim_anonymous_dna_task_rejects_session_key_mismatch(self, mock_async_result, mock_rec_task):
        """
        US-003 security: when the caller-supplied session_key does NOT match
        the `task_owner_<task_id>` value cached at upload time, the task must
        log a warning and return early — no DNA writes, no UserBooks.
        """
        user = User.objects.create_user(username="mismatchuser", password="password")
        user.userprofile.pending_dna_task_id = "owner-task-789"
        user.userprofile.save()

        task_id = "owner-task-789"
        cache.set(f"task_owner_{task_id}", "real-owner-session-key", 3600)

        with self.assertLogs("core.tasks", level="WARNING") as log_capture:
            claim_anonymous_dna_task.delay(user.id, task_id, "attacker-session-key")

        self.assertTrue(
            any("claim rejected: session_key mismatch" in msg for msg in log_capture.output),
            f"expected warning not found in log output: {log_capture.output}",
        )

        user.userprofile.refresh_from_db()
        self.assertIsNone(user.userprofile.dna_data)
        # Post-review hardening: pending_dna_task_id must be cleared so the
        # legitimate user's dashboard doesn't poll a doomed task forever.
        self.assertIsNone(user.userprofile.pending_dna_task_id)
        from core.models import UserBook

        self.assertEqual(UserBook.objects.filter(user=user).count(), 0)
        mock_async_result.assert_not_called()
