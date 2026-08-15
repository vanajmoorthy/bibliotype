"""Tests for the reject-while-pending concurrent-upload guard (PR C).

While an authenticated user's DNA upload is in flight, a TTL'd in-flight marker
(dna_task_inflight_{user_id}) is live and a new upload is rejected — no second
task, no Postgres row-lock race. The task deletes the marker on completion; the
TTL bounds it so a hard worker crash can't lock the user out.

The guard consults only the marker (not AsyncResult): eager tasks report their
Celery state inconsistently across environments depending on whether a result
backend is configured, so a state-based guard would be flaky.
"""

from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TransactionTestCase, override_settings
from django.urls import reverse

from core.cache_utils import safe_cache_get, safe_cache_set


def _csv_file():
    header = (
        "Title,Author,Exclusive Shelf,My Rating,Number of Pages,"
        "Original Publication Year,Date Read,Average Rating,My Review,ISBN13"
    )
    row = "Guard Book,Guard Author,read,5,150,2021,2023/01/15,4.2,A review.,9780000000009"
    return SimpleUploadedFile("goodreads.csv", f"{header}\n{row}".encode("utf-8"), content_type="text/csv")


@override_settings(
    CELERY_TASK_ALWAYS_EAGER=True,
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
)
class ConcurrentUploadGuardTests(TransactionTestCase):
    def setUp(self):
        from django.core.cache import cache

        cache.clear()  # locmem persists across tests in a class; isolate the marker
        self.client = Client()
        self.user = User.objects.create_user(username="guard", password="x", email="guard@example.com")
        self.client.force_login(self.user)
        self.marker_key = f"dna_task_inflight_{self.user.id}"

    def tearDown(self):
        from django.db import connections

        for conn in connections.all():
            if conn.connection is not None:
                conn.close()
        connections.close_all()

    @patch("core.views.upload.generate_reading_dna_task.delay")
    def test_rejects_upload_while_marker_present(self, mock_delay):
        safe_cache_set(self.marker_key, "1", timeout=900)  # prior upload in flight

        response = self.client.post(reverse("core:upload"), {"csv_file": _csv_file()})

        self.assertEqual(response.status_code, 302)
        self.assertIn("processing=true", response.url)
        mock_delay.assert_not_called()  # no second task dispatched

    @patch("core.views.upload.generate_reading_dna_task.delay")
    def test_allows_upload_when_marker_absent(self, mock_delay):
        mock_delay.return_value = MagicMock(id="new-task-id")

        response = self.client.post(reverse("core:upload"), {"csv_file": _csv_file()})

        self.assertEqual(response.status_code, 302)
        mock_delay.assert_called_once()

    @patch("core.views.upload.generate_reading_dna_task.delay")
    def test_dispatch_sets_inflight_marker(self, mock_delay):
        """A fresh upload sets the marker so a concurrent one would be blocked.
        delay is mocked so the (would-be eager) task can't clear it here."""
        mock_delay.return_value = MagicMock(id="new-task-id")

        self.client.post(reverse("core:upload"), {"csv_file": _csv_file()})

        self.assertEqual(safe_cache_get(self.marker_key), "1")

    @patch("core.views.upload.generate_reading_dna_task.delay")
    def test_stale_pending_without_marker_not_locked_out(self, mock_delay):
        """A leftover pending_dna_task_id whose marker has expired (worker died
        mid-task) must NOT block: the marker is the sole gate, so the upload
        proceeds and the user is never permanently locked out."""
        self.user.userprofile.pending_dna_task_id = "phantom-task-id"
        self.user.userprofile.save(update_fields=["pending_dna_task_id"])
        # No marker in cache (expired).
        mock_delay.return_value = MagicMock(id="new-task-id")

        response = self.client.post(reverse("core:upload"), {"csv_file": _csv_file()})

        self.assertEqual(response.status_code, 302)
        mock_delay.assert_called_once()

    @patch("core.services.dna.generate_vibe_with_llm", return_value=["a vibe"])
    @patch("core.services.book_enrichment_service.enrich_book_from_apis")
    def test_completed_upload_clears_marker(self, mock_enrich, mock_vibe):
        """A real (eager) upload clears the marker on completion, so a follow-up
        upload is allowed."""
        mock_enrich.return_value = (None, 0, 0)

        self.client.post(reverse("core:upload"), {"csv_file": _csv_file()})

        # Task ran inline and deleted the marker as part of saving the DNA.
        self.assertIsNone(safe_cache_get(self.marker_key))
