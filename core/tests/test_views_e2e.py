from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TransactionTestCase, override_settings
from django.urls import reverse


@override_settings(
    CELERY_TASK_ALWAYS_EAGER=True,
    CELERY_TASK_STORE_EAGER_RESULT=True,
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "unique-snowflake",
        }
    },
)
class ViewE2E_Tests(TransactionTestCase):

    def setUp(self):
        self.client = Client()
        csv_header = "Title,Author,Exclusive Shelf,My Rating,Number of Pages,Original Publication Year,Date Read,Average Rating,My Review,ISBN13"
        csv_row = "E2E Book,E2E Author,read,5,150,2021,2023/01/15,4.2,A test review.,9780000000003"
        csv_content = f"{csv_header}\n{csv_row}".encode("utf-8")
        self.csv_file = SimpleUploadedFile(
            "goodreads.csv",
            csv_content,
            content_type="text/csv",
        )
        self.sample_dna_data = {"reader_type": "E2E Reader"}

    def tearDown(self):
        """Clean up database connections after each test."""
        from django.db import connections

        # Simple, effective cleanup
        for conn in connections.all():
            if conn.connection is not None:
                conn.close()

        connections.close_all()
        super().tearDown()

    @patch("core.services.dna_analyser.generate_vibe_with_llm")
    @patch("core.services.book_enrichment_service.enrich_book_from_apis")
    def test_anonymous_upload_to_signup_and_claim_flow(self, mock_enrich_book, mock_generate_vibe):
        """
        Critical path test:
        1. Anonymous user uploads a file. The task runs synchronously.
        2. They are redirected to a waiting page with a real task ID.
        3. The frontend polls the result view, which now finds the completed task.
        4. They sign up from the waiting page.
        5. The claim task runs, gets the result from the eager backend, and saves it.
        """
        # Configure mocks for the services called *inside* the task
        mock_enrich_book.return_value = (None, 0, 0)  # Return dummy values
        mock_generate_vibe.return_value = ["an e2e vibe"]

        # Anonymous Upload
        response = self.client.post(reverse("core:upload"), {"csv_file": self.csv_file})

        # The view should redirect to the status page, extracting the task_id from the response
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("core:task_status", kwargs={"task_id": "dummy"})[:-6], response.url)
        task_id = response.url.split("/")[-2]

        # Poll for Result
        # With ALWAYS_EAGER=True, the task is already complete.
        # The client can now poll the result view.
        response = self.client.get(reverse("core:get_task_result", kwargs={"task_id": task_id}))

        # The view should return SUCCESS and the URL to the display page
        self.assertEqual(response.status_code, 200)
        json_response = response.json()
        self.assertEqual(json_response["status"], "SUCCESS")
        self.assertEqual(json_response["redirect_url"], reverse("core:display_dna"))

        # The result is now in the session. Go to the display page to confirm.
        response = self.client.get(reverse("core:display_dna"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "an e2e vibe")  # Check for some content from the result

        # Sign up and Claim
        signup_url = reverse("core:signup") + f"?task_id={task_id}"
        response = self.client.get(signup_url)
        self.assertContains(response, task_id)  # Check the hidden field is there

        # Post the signup form
        response = self.client.post(
            signup_url,
            {
                "username": "claimeduser",
                "email": "claimed@test.com",
                "password1": "a-Strong-p4ssword!",
                "password2": "a-Strong-p4ssword!",
                "task_id_to_claim": task_id,
            },
            follow=True,
        )

        # The user is created, logged in, and the claim task runs synchronously.
        # The final redirect should be to the display page with a processing signal.
        self.assertRedirects(response, reverse("core:display_dna") + "?processing=true")

        new_user = User.objects.get(username="claimeduser")
        new_user.userprofile.refresh_from_db()
        self.assertIsNotNone(new_user.userprofile.dna_data)
        self.assertIn("an e2e vibe", new_user.userprofile.reading_vibe)

    @patch("core.services.dna_analyser.generate_vibe_with_llm")
    @patch("core.services.book_enrichment_service.enrich_book_from_apis")
    def test_anonymous_upload_binds_task_owner_to_session(self, mock_enrich_book, mock_generate_vibe):
        """
        US-001: After an anonymous upload, the task_id must be bound to the
        caller's session via both `session["anonymous_task_id"]` and the
        `task_owner_<task_id>` cache entry, so downstream views can refuse
        cross-session lookups.
        """
        mock_enrich_book.return_value = (None, 0, 0)
        mock_generate_vibe.return_value = ["bind-test vibe"]

        response = self.client.post(reverse("core:upload"), {"csv_file": self.csv_file})

        self.assertEqual(response.status_code, 302)
        task_id = response.url.rstrip("/").split("/")[-1]

        session = self.client.session
        self.assertEqual(session["anonymous_task_id"], task_id)
        self.assertIsNotNone(session.session_key)
        self.assertEqual(cache.get(f"task_owner_{task_id}"), session.session_key)

    @patch("core.services.dna_analyser.generate_vibe_with_llm")
    @patch("core.services.book_enrichment_service.enrich_book_from_apis")
    def test_task_result_owner_can_fetch_own_task(self, mock_enrich_book, mock_generate_vibe):
        """
        US-002 positive: the session that uploaded a CSV is able to poll its
        own task_id and gets a SUCCESS response.
        """
        mock_enrich_book.return_value = (None, 0, 0)
        mock_generate_vibe.return_value = ["owner vibe"]

        response = self.client.post(reverse("core:upload"), {"csv_file": self.csv_file})
        task_id = response.url.rstrip("/").split("/")[-1]

        response = self.client.get(reverse("core:get_task_result", kwargs={"task_id": task_id}))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "SUCCESS")

    @patch("core.services.dna_analyser.generate_vibe_with_llm")
    @patch("core.services.book_enrichment_service.enrich_book_from_apis")
    def test_task_result_rejects_foreign_session(self, mock_enrich_book, mock_generate_vibe):
        """
        US-002 negative: a second client that did not upload the file must
        receive a 403 when polling someone else's task_id, AND no DNA must
        leak into the attacker's session. The view fails closed on cache
        mismatch (post-review hardening — the original warn-and-allow path
        leaked DNA into the requester's session, which combined with the
        signup view to re-open the hijack).
        """
        mock_enrich_book.return_value = (None, 0, 0)
        mock_generate_vibe.return_value = ["enforced vibe"]

        uploader = self.client
        upload_response = uploader.post(reverse("core:upload"), {"csv_file": self.csv_file})
        task_id = upload_response.url.rstrip("/").split("/")[-1]

        attacker = Client()
        with self.assertLogs("core.views", level="WARNING") as log_capture:
            response = attacker.get(reverse("core:get_task_result", kwargs={"task_id": task_id}))

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["status"], "FORBIDDEN")
        self.assertTrue(
            any("task_owner mismatch" in msg for msg in log_capture.output),
            f"expected mismatch warning not found: {log_capture.output}",
        )
        # Threat-model assertion: the attacker's session MUST NOT contain
        # any leaked DNA data after the 403 response. (Original buggy path
        # wrote `request.session["dna_data"] = cached_result` even on
        # mismatch — this test would have failed before the fix.)
        self.assertNotIn("dna_data", attacker.session)
        self.assertNotIn("book_ids", attacker.session)

    @patch("core.services.dna_analyser.generate_vibe_with_llm")
    @patch("core.services.book_enrichment_service.enrich_book_from_apis")
    def test_signup_rejects_cross_session_task_id_claim(self, mock_enrich_book, mock_generate_vibe):
        """
        US-003 negative: session A uploads and gets a task_id. Session B (a
        different Client) attempts to sign up using A's task_id. The signup
        form must render with a validation error and no user must be created.
        """
        mock_enrich_book.return_value = (None, 0, 0)
        mock_generate_vibe.return_value = ["cross-session vibe"]

        uploader = self.client
        upload_response = uploader.post(reverse("core:upload"), {"csv_file": self.csv_file})
        task_id = upload_response.url.rstrip("/").split("/")[-1]

        attacker = Client()
        response = attacker.post(
            reverse("core:signup") + f"?task_id={task_id}",
            {
                "username": "attacker",
                "email": "attacker@test.com",
                "password1": "a-Strong-p4ssword!",
                "password2": "a-Strong-p4ssword!",
                "task_id_to_claim": task_id,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "We couldn't verify that this Bibliotype belongs to your current session.")
        self.assertFalse(User.objects.filter(username="attacker").exists())

    @patch("core.services.dna_analyser.generate_vibe_with_llm")
    @patch("core.services.book_enrichment_service.enrich_book_from_apis")
    def test_signup_positive_claim_after_session_rotation(self, mock_enrich_book, mock_generate_vibe):
        """
        US-003 positive: same Client uploads then signs up. login() rotates
        the session_key, but the view captured the pre-login key and passes
        it to the claim task, so the claim should still succeed. Explicitly
        verifies that the session_key did rotate between upload and post-login.
        """
        mock_enrich_book.return_value = (None, 0, 0)
        mock_generate_vibe.return_value = ["rotation vibe"]

        upload_response = self.client.post(reverse("core:upload"), {"csv_file": self.csv_file})
        task_id = upload_response.url.rstrip("/").split("/")[-1]

        pre_login_session_key = self.client.session.session_key
        self.assertIsNotNone(pre_login_session_key)

        response = self.client.post(
            reverse("core:signup") + f"?task_id={task_id}",
            {
                "username": "claimer",
                "email": "claimer@test.com",
                "password1": "a-Strong-p4ssword!",
                "password2": "a-Strong-p4ssword!",
                "task_id_to_claim": task_id,
            },
            follow=True,
        )

        self.assertRedirects(response, reverse("core:display_dna") + "?processing=true")

        post_login_session_key = self.client.session.session_key
        self.assertIsNotNone(post_login_session_key)
        self.assertNotEqual(pre_login_session_key, post_login_session_key)

        user = User.objects.get(username="claimer")
        user.userprofile.refresh_from_db()
        self.assertIsNotNone(user.userprofile.dna_data)

    @patch("core.tasks.generate_recommendations_task")
    @patch("core.services.dna_analyser.generate_vibe_with_llm")
    @patch("core.services.book_enrichment_service.enrich_book_from_apis")
    @patch("core.tasks.check_author_mainstream_status_task")
    def test_authenticated_user_dna_regeneration_flow(
        self, mock_author_check, mock_enrich_book, mock_generate_vibe, mock_recommendations_task
    ):
        """
        Test complete DNA regeneration flow for authenticated users:
        1. Create user with initial DNA
        2. Simulate regeneration by directly calling DNA save logic
        3. Verify pending_dna_task_id is cleared after save
        4. Verify new DNA data is saved correctly
        """
        mock_enrich_book.return_value = (None, 0, 0)
        mock_generate_vibe.return_value = ["new regenerated vibe"]
        mock_author_check.delay = MagicMock()
        mock_recommendations_task.delay = MagicMock()

        # Create user with initial DNA
        user = User.objects.create_user(username="dnatestuser", password="testpass123")
        user.userprofile.dna_data = {
            "reader_type": "Original Reader",
            "user_stats": {"total_books_read": 5},
            "reading_vibe": ["old vibe"],
        }
        user.userprofile.pending_dna_task_id = "fake-regeneration-task-id"
        user.userprofile.save()

        # Verify initial state
        user.userprofile.refresh_from_db()
        self.assertIsNotNone(user.userprofile.pending_dna_task_id)
        self.assertEqual(user.userprofile.pending_dna_task_id, "fake-regeneration-task-id")

        # Simulate DNA save after task completes
        new_dna_data = {
            "reader_type": "New Reader",
            "user_stats": {"total_books_read": 10},
            "reading_vibe": ["new regenerated vibe"],
            "vibe_data_hash": "newhash",
        }

        from core.services.dna_analyser import _save_dna_to_profile

        _save_dna_to_profile(user.userprofile, new_dna_data)

        # Verify pending_dna_task_id is cleared and new DNA is saved
        user.userprofile.refresh_from_db()
        self.assertIsNone(user.userprofile.pending_dna_task_id)
        self.assertIsNotNone(user.userprofile.dna_data)
        self.assertEqual(user.userprofile.reader_type, "New Reader")
        self.assertEqual(user.userprofile.total_books_read, 10)

    @patch("core.tasks.generate_recommendations_task")
    @patch("core.services.dna_analyser.generate_vibe_with_llm")
    @patch("core.services.book_enrichment_service.enrich_book_from_apis")
    def test_pending_dna_task_id_cleared_on_save(self, mock_enrich_book, mock_generate_vibe, mock_recommendations_task):
        """
        Test that pending_dna_task_id is properly cleared when DNA is saved to profile.
        This is critical for the status polling mechanism to work correctly.
        """
        mock_enrich_book.return_value = (None, 0, 0)
        mock_generate_vibe.return_value = ["test vibe"]
        mock_recommendations_task.delay = MagicMock()

        # Create user with pending task
        user = User.objects.create_user(username="testuser2", password="password")
        user.userprofile.pending_dna_task_id = "fake-task-id-12345"
        user.userprofile.save()

        # Verify it's set
        user.userprofile.refresh_from_db()
        self.assertEqual(user.userprofile.pending_dna_task_id, "fake-task-id-12345")

        # Save new DNA data
        dna_data = {
            "reader_type": "Test Reader",
            "user_stats": {"total_books_read": 10},
            "reading_vibe": ["test vibe"],
            "vibe_data_hash": "testhash",
        }

        from core.services.dna_analyser import _save_dna_to_profile

        _save_dna_to_profile(user.userprofile, dna_data)

        # Verify pending_dna_task_id is cleared
        user.userprofile.refresh_from_db()
        self.assertIsNone(user.userprofile.pending_dna_task_id)
        self.assertIsNotNone(user.userprofile.dna_data)
        self.assertEqual(user.userprofile.reader_type, "Test Reader")

    def test_status_check_returns_pending_while_task_running(self):
        """
        Test that status check returns PENDING when pending_dna_task_id is set.
        """
        user = User.objects.create_user(username="testuser3", password="password")
        user.userprofile.pending_dna_task_id = "in-progress-task-id"
        user.userprofile.dna_data = {"reader_type": "Old Data"}
        user.userprofile.save()

        self.client.login(username="testuser3", password="password")

        response = self.client.get(reverse("core:api_check_dna_status"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "PENDING")

    def test_status_check_returns_success_when_no_pending_task(self):
        """
        Test that status check returns SUCCESS when no pending task and DNA exists.
        """
        user = User.objects.create_user(username="testuser4", password="password")
        user.userprofile.dna_data = {"reader_type": "Completed Reader"}
        user.userprofile.save()

        self.client.login(username="testuser4", password="password")

        response = self.client.get(reverse("core:api_check_dna_status"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "SUCCESS")

    def test_status_check_returns_pending_when_no_data(self):
        """
        Test that status check returns PENDING when there's no DNA data.
        """
        user = User.objects.create_user(username="testuser5", password="password")
        user.userprofile.save()

        self.client.login(username="testuser5", password="password")

        response = self.client.get(reverse("core:api_check_dna_status"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "PENDING")

    def test_status_check_returns_failure_when_task_failed(self):
        """
        Test that check_dna_status_view returns FAILURE status when Celery task fails
        and clears pending_dna_task_id.
        """
        user = User.objects.create_user(username="testuser6", password="password")
        user.userprofile.pending_dna_task_id = "failed-task-id"
        user.userprofile.save()

        self.client.login(username="testuser6", password="password")

        mock_result = MagicMock()
        mock_result.state = "FAILURE"
        mock_result.info = Exception("Something went wrong")

        with patch("core.views.AsyncResult", return_value=mock_result):
            response = self.client.get(reverse("core:api_check_dna_status"))

        data = response.json()
        self.assertEqual(data["status"], "FAILURE")
        self.assertIn("error", data)

        user.userprofile.refresh_from_db()
        self.assertIsNone(user.userprofile.pending_dna_task_id)

    def test_anonymous_session_recreation_is_idempotent(self):
        """
        US-024b: when ``display_dna_view`` reaches the AnonymousUserSession
        recreation branch repeatedly for the same session_key (e.g. a race
        where two dashboard requests fire before either commits), it MUST
        not raise IntegrityError on the unique session_key constraint. The
        view uses update_or_create, so the second call refreshes the row
        in place instead of trying to insert a duplicate.
        """
        from datetime import timedelta

        from django.utils import timezone

        from core.models import AnonymousUserSession

        # Anonymous client with dna_data and a real session_key.
        session = self.client.session
        session["dna_data"] = {
            "reader_type": "Recreate Reader",
            "top_genres": [["Fiction", 3]],
            "top_authors": [["Some Author", 2]],
        }
        session.save()
        session_key = session.session_key
        self.assertIsNotNone(session_key)

        # Pre-create a row so the second call would collide on session_key
        # under the old ``create(...)`` path. The first call's update_or_create
        # path should then update this row in place.
        AnonymousUserSession.objects.create(
            session_key=session_key,
            dna_data={"reader_type": "Stale Reader"},
            books_data=[],
            top_books_data=[],
            genre_distribution={},
            author_distribution={},
            book_ratings={},
            expires_at=timezone.now() + timedelta(days=7),
        )

        # Force the recreate branch on every call by making the ``.get()``
        # lookup miss, then mock out the recommendation engine so we isolate
        # the unique-constraint behavior under test.
        with patch.object(
            AnonymousUserSession.objects,
            "get",
            side_effect=AnonymousUserSession.DoesNotExist,
        ), patch(
            "core.services.recommendation_service.get_recommendations_for_anonymous",
            return_value=[],
        ):
            with self.assertNoLogs("core.views", level="ERROR"):
                response_one = self.client.get(reverse("core:display_dna"))
                response_two = self.client.get(reverse("core:display_dna"))

        self.assertEqual(response_one.status_code, 200)
        self.assertEqual(response_two.status_code, 200)

        # The unique-constraint contract: there is exactly one row for
        # this session_key, and it carries the refreshed dna_data — not the
        # pre-seeded "Stale Reader" payload.
        rows = AnonymousUserSession.objects.filter(session_key=session_key)
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.first().dna_data["reader_type"], "Recreate Reader")


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "signup-duplicate-email-tests",
        }
    },
)
class SignupDuplicateEmailTests(TransactionTestCase):
    """US-017: signup must not reveal whether an email is already registered.

    On duplicate: short-circuit to the generic "check your inbox" page AND
    send a password-reset email to the legitimate account. On a new email:
    proceed normally.
    """

    def setUp(self):
        from django.core import mail

        self.client = Client()
        self.mail = mail
        mail.outbox = []

    def tearDown(self):
        from django.db import connections

        for conn in connections.all():
            if conn.connection is not None:
                conn.close()
        connections.close_all()
        super().tearDown()

    def test_signup_with_duplicate_email_short_circuits_and_sends_reset(self):
        """Duplicate email: no new user created, password-reset email dispatched,
        redirect to the same generic 'check your inbox' page used by the
        password-reset flow."""
        existing = User.objects.create_user(
            username="existing",
            email="taken@test.com",
            password="ExistingP4ssword!",
        )
        user_count_before = User.objects.count()

        response = self.client.post(
            reverse("core:signup"),
            {
                "username": "newcomer",
                "email": "taken@test.com",
                "password1": "Brand-New-p4ssword!",
                "password2": "Brand-New-p4ssword!",
            },
        )

        self.assertRedirects(response, reverse("password_reset_done"))
        self.assertEqual(User.objects.count(), user_count_before)
        self.assertFalse(User.objects.filter(username="newcomer").exists())
        self.assertEqual(len(self.mail.outbox), 1)
        reset_email = self.mail.outbox[0]
        self.assertIn(existing.email, reset_email.to)
        self.assertEqual(reset_email.subject, "Reset your Bibliotype password")

    def test_signup_duplicate_email_is_case_insensitive(self):
        """Duplicate detection must be case-insensitive to match the rest of
        the email-based auth path (`email__iexact`)."""
        User.objects.create_user(
            username="lowercase",
            email="mixedcase@test.com",
            password="ExistingP4ssword!",
        )

        response = self.client.post(
            reverse("core:signup"),
            {
                "username": "newcomer",
                "email": "MixedCase@Test.com",
                "password1": "Brand-New-p4ssword!",
                "password2": "Brand-New-p4ssword!",
            },
        )

        self.assertRedirects(response, reverse("password_reset_done"))
        self.assertFalse(User.objects.filter(username="newcomer").exists())
        self.assertEqual(len(self.mail.outbox), 1)

    def test_signup_with_fresh_email_creates_user_normally(self):
        """Negative control: a never-before-seen email completes signup,
        logs the user in, and dispatches NO reset email."""
        response = self.client.post(
            reverse("core:signup"),
            {
                "username": "freshuser",
                "email": "fresh@test.com",
                "password1": "Brand-New-p4ssword!",
                "password2": "Brand-New-p4ssword!",
            },
        )

        # No DNA / no task to claim → redirect to home (default branch).
        self.assertRedirects(response, reverse("core:home"))
        self.assertTrue(User.objects.filter(username="freshuser").exists())
        self.assertEqual(len(self.mail.outbox), 0)


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "upload-validation-tests",
        }
    },
)
class UploadValidationTests(TransactionTestCase):
    """US-018: upload_view must reject pathological CSVs before queueing a task.

    Validation reads only the head (nrows=MAX_UPLOAD_ROWS) so a pathological
    file can't OOM the validation pass. Files exceeding the row cap are
    truncated; column-count overflow or missing schema columns are rejected
    outright.
    """

    def setUp(self):
        self.client = Client()

    def tearDown(self):
        from django.db import connections

        for conn in connections.all():
            if conn.connection is not None:
                conn.close()
        connections.close_all()
        super().tearDown()

    @staticmethod
    def _make_csv(name, content_bytes):
        return SimpleUploadedFile(name, content_bytes, content_type="text/csv")

    @patch("core.views.generate_reading_dna_task")
    def test_oversize_row_count_is_truncated_not_rejected(self, mock_task):
        """50_001-row CSV is accepted; csv_content passed to the task has at
        most MAX_UPLOAD_ROWS data rows (header + 50000 = 50001 lines)."""
        from core.views import MAX_UPLOAD_ROWS

        mock_task.delay.return_value = MagicMock(id="truncated-task-id")

        header = "Title,Author,Exclusive Shelf,My Rating,ISBN13"
        rows = "\n".join(
            f"Book {i},Author {i},read,4,978000000{i:04d}" for i in range(MAX_UPLOAD_ROWS + 1)
        )
        csv_content = (header + "\n" + rows).encode("utf-8")
        upload = self._make_csv("big.csv", csv_content)

        response = self.client.post(reverse("core:upload"), {"csv_file": upload})

        self.assertEqual(response.status_code, 302)
        # Upload should NOT redirect back to home with an error.
        self.assertNotEqual(response.url, reverse("core:home"))
        mock_task.delay.assert_called_once()

        sent_csv = mock_task.delay.call_args[0][0]
        sent_data_rows = sent_csv.strip().split("\n")[1:]  # drop header
        self.assertLessEqual(len(sent_data_rows), MAX_UPLOAD_ROWS)

    @patch("core.views.generate_reading_dna_task")
    def test_too_many_columns_is_rejected(self, mock_task):
        """101-column CSV is rejected outright; no task is dispatched."""
        from core.views import MAX_UPLOAD_COLUMNS

        bad_headers = ["Title", "Author"] + [f"Col{i}" for i in range(MAX_UPLOAD_COLUMNS)]
        bad_row = ["t", "a"] + ["x"] * MAX_UPLOAD_COLUMNS
        csv_content = (",".join(bad_headers) + "\n" + ",".join(bad_row)).encode("utf-8")
        upload = self._make_csv("wide.csv", csv_content)

        response = self.client.post(reverse("core:upload"), {"csv_file": upload}, follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "too many columns")
        mock_task.delay.assert_not_called()

    @patch("core.views.generate_reading_dna_task")
    def test_missing_schema_columns_is_rejected(self, mock_task):
        """A CSV without Title + Author/Authors is rejected with the schema error."""
        bad = b"FooBar,Baz\n1,2\n3,4\n"
        upload = self._make_csv("not-goodreads.csv", bad)

        response = self.client.post(reverse("core:upload"), {"csv_file": upload}, follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "does not look like a Goodreads or StoryGraph export")
        mock_task.delay.assert_not_called()

    @patch("core.views.generate_reading_dna_task")
    def test_valid_goodreads_csv_still_uploads(self, mock_task):
        """Positive control: a Goodreads-shaped CSV passes validation and the
        task is dispatched with the original csv_content."""
        mock_task.delay.return_value = MagicMock(id="happy-task-id")

        good = (
            b"Title,Author,Exclusive Shelf,My Rating,ISBN13\n"
            b"Dune,Frank Herbert,read,5,9780441172719\n"
        )
        upload = self._make_csv("goodreads.csv", good)

        response = self.client.post(reverse("core:upload"), {"csv_file": upload})

        self.assertEqual(response.status_code, 302)
        mock_task.delay.assert_called_once()

    @patch("core.views.generate_reading_dna_task")
    def test_valid_storygraph_csv_still_uploads(self, mock_task):
        """Positive control: a StoryGraph-shaped CSV (Authors, plural) passes
        validation. Schema check accepts either Goodreads or StoryGraph
        signature column sets."""
        mock_task.delay.return_value = MagicMock(id="sg-task-id")

        sg = (
            b"Title,Authors,Read Status,Star Rating,ISBN/UID\n"
            b"Project Hail Mary,Andy Weir,read,5,9780593135204\n"
        )
        upload = self._make_csv("storygraph.csv", sg)

        response = self.client.post(reverse("core:upload"), {"csv_file": upload})

        self.assertEqual(response.status_code, 302)
        mock_task.delay.assert_called_once()
