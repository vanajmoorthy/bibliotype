"""Tests for the reject-while-pending concurrent-upload guard (PR C).

When an authenticated user's previous DNA task is still processing, a new
upload is rejected (rather than revoking the old task mid-flight and racing on
Postgres row locks). A finished/failed task clears pending_dna_task_id as part
of its own completion, so this only blocks genuinely in-flight uploads.
"""

from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TransactionTestCase, override_settings
from django.urls import reverse

from core.cache_utils import safe_cache_set


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
        self.client = Client()
        self.user = User.objects.create_user(username="guard", password="x", email="guard@example.com")
        self.client.force_login(self.user)

    def tearDown(self):
        from django.db import connections

        for conn in connections.all():
            if conn.connection is not None:
                conn.close()
        connections.close_all()

    def _set_pending(self, task_id, *, inflight=True):
        self.user.userprofile.pending_dna_task_id = task_id
        self.user.userprofile.save(update_fields=["pending_dna_task_id"])
        if inflight:
            # Mirror what upload_view sets at dispatch — the guard only blocks
            # while this TTL'd marker is live.
            safe_cache_set(f"dna_task_inflight_{self.user.id}", task_id, timeout=900)

    @patch("core.views.upload.generate_reading_dna_task.delay")
    @patch("core.views.upload.AsyncResult")
    def test_rejects_upload_while_prior_task_running(self, mock_async_result, mock_delay):
        self._set_pending("running-task-id")
        mock_async_result.return_value.ready.return_value = False  # still processing

        response = self.client.post(reverse("core:upload"), {"csv_file": _csv_file()})

        # Redirected back to the processing dashboard, no new task dispatched.
        self.assertEqual(response.status_code, 302)
        self.assertIn("processing=true", response.url)
        mock_delay.assert_not_called()
        # Pending id is unchanged — the old task keeps running.
        self.user.userprofile.refresh_from_db()
        self.assertEqual(self.user.userprofile.pending_dna_task_id, "running-task-id")

    @patch("core.views.upload.generate_reading_dna_task.delay")
    @patch("core.views.upload.AsyncResult")
    def test_allows_upload_when_prior_task_finished(self, mock_async_result, mock_delay):
        self._set_pending("finished-task-id")
        mock_async_result.return_value.ready.return_value = True  # done
        mock_delay.return_value = MagicMock(id="new-task-id")

        response = self.client.post(reverse("core:upload"), {"csv_file": _csv_file()})

        self.assertEqual(response.status_code, 302)
        mock_delay.assert_called_once()
        self.user.userprofile.refresh_from_db()
        self.assertEqual(self.user.userprofile.pending_dna_task_id, "new-task-id")

    @patch("core.views.upload.generate_reading_dna_task.delay")
    @patch("core.views.upload.AsyncResult")
    def test_fails_open_when_task_state_unknown(self, mock_async_result, mock_delay):
        """If AsyncResult errors (result backend hiccup), let the upload through
        rather than locking the user out."""
        self._set_pending("unknowable-task-id")
        mock_async_result.side_effect = Exception("backend down")
        mock_delay.return_value = MagicMock(id="new-task-id")

        response = self.client.post(reverse("core:upload"), {"csv_file": _csv_file()})

        self.assertEqual(response.status_code, 302)
        mock_delay.assert_called_once()

    @patch("core.views.upload.generate_reading_dna_task.delay")
    @patch("core.views.upload.AsyncResult")
    def test_self_heals_when_inflight_marker_expired(self, mock_async_result, mock_delay):
        """A stale pending_dna_task_id (worker died mid-task) whose in-flight
        marker has expired must NOT lock the user out — the upload proceeds even
        though Celery still reports the phantom task as not-ready."""
        self._set_pending("phantom-task-id", inflight=False)  # no marker
        mock_async_result.return_value.ready.return_value = False  # phantom PENDING
        mock_delay.return_value = MagicMock(id="new-task-id")

        response = self.client.post(reverse("core:upload"), {"csv_file": _csv_file()})

        self.assertEqual(response.status_code, 302)
        mock_delay.assert_called_once()

    @patch("core.views.upload.generate_reading_dna_task.delay")
    @patch("core.views.upload.AsyncResult")
    def test_allows_upload_when_no_prior_task(self, mock_async_result, mock_delay):
        # pending_dna_task_id is None by default.
        mock_delay.return_value = MagicMock(id="first-task-id")

        response = self.client.post(reverse("core:upload"), {"csv_file": _csv_file()})

        self.assertEqual(response.status_code, 302)
        mock_delay.assert_called_once()
        mock_async_result.assert_not_called()  # no prior id → never checked
