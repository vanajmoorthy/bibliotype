import uuid
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils.html import escape

from .ratelimit_test_helpers import frozen_ratelimit_window


@override_settings(
    CELERY_TASK_ALWAYS_EAGER=True,
    CELERY_TASK_STORE_EAGER_RESULT=True,
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "unique-snowflake",
        }
    },
    RATELIMIT_ENABLE=False,
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

    @patch("core.services.dna.generate_vibe_with_llm")
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
        self.assertTrue(response.url.startswith("/task/"))
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
        # The anonymous snapshot must not outlive the claim
        self.assertNotIn("dna_data", self.client.session)

    def test_task_status_rejects_non_uuid_task_id(self):
        """A task_id that breaks out of the poller's inline JS string must not reach the template."""
        self.assertEqual(self.client.get("/task/x'),alert(document.domain),('/").status_code, 404)
        self.assertEqual(self.client.get("/api/task-result/x'),alert(1),('/").status_code, 404)

        task_id = str(uuid.uuid4())
        response = self.client.get(reverse("core:task_status", kwargs={"task_id": task_id}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"taskPoller('/api/task-result/{task_id}/')")
        self.assertContains(response, f"/signup/?task_id={task_id}")

    @patch("core.services.dna.generate_vibe_with_llm")
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

    @patch("core.services.dna.generate_vibe_with_llm")
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

    @patch("core.services.dna.generate_vibe_with_llm")
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

    @patch("core.services.dna.generate_vibe_with_llm")
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
        # Flash messages are autoescaped, so the apostrophe arrives as &#x27;.
        self.assertContains(
            response, escape("We couldn't verify that this Bibliotype belongs to your current session.")
        )
        self.assertFalse(User.objects.filter(username="attacker").exists())

    @patch("core.services.dna.generate_vibe_with_llm")
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
    @patch("core.services.dna.generate_vibe_with_llm")
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

        from core.services.dna import _save_dna_to_profile

        _save_dna_to_profile(user.userprofile, new_dna_data)

        # Verify pending_dna_task_id is cleared and new DNA is saved
        user.userprofile.refresh_from_db()
        self.assertIsNone(user.userprofile.pending_dna_task_id)
        self.assertIsNotNone(user.userprofile.dna_data)
        self.assertEqual(user.userprofile.reader_type, "New Reader")
        self.assertEqual(user.userprofile.total_books_read, 10)

    @patch("core.tasks.generate_recommendations_task")
    @patch("core.services.dna.generate_vibe_with_llm")
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

        from core.services.dna import _save_dna_to_profile

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

        response = self.client.get(reverse("core:api_status", args=["dna"]))
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

        response = self.client.get(reverse("core:api_status", args=["dna"]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "SUCCESS")

    @patch("core.tasks.generate_recommendations_task.delay")
    def test_empty_recommendations_are_done_not_pending(self, mock_rec_delay):
        """A finished run that found nothing ([]) shows the empty state instead of polling and re-dispatching."""
        user = User.objects.create_user(username="norecs", password="password")
        user.userprofile.dna_data = {"reader_type": "Completed Reader"}
        user.userprofile.recommendations_data = []
        user.userprofile.save()
        self.client.login(username="norecs", password="password")

        response = self.client.get(reverse("core:display_dna"))
        self.assertFalse(response.context["recommendations_pending"])
        self.assertContains(response, "No recommended books yet")
        mock_rec_delay.assert_not_called()

        response = self.client.get(reverse("core:api_check_recommendations_status"))
        self.assertEqual(response.json()["status"], "ready")

    def _poll_failed_anonymous_task(self, csv_bytes):
        """Upload anonymously (task runs eagerly and fails), then poll the result view."""
        upload = SimpleUploadedFile("goodreads.csv", csv_bytes, content_type="text/csv")
        response = self.client.post(reverse("core:upload"), {"csv_file": upload})
        task_id = response.url.split("/")[-2]
        with patch("core.views.upload.AsyncResult", return_value=MagicMock(state="FAILURE")):
            response = self.client.get(reverse("core:get_task_result", kwargs={"task_id": task_id}))
        data = response.json()
        self.assertEqual(data["status"], "FAILURE")
        return data["error"]

    def test_task_validation_error_reaches_the_user(self):
        """A CSV with no books on the read shelf fails in the task with a message the user can act on."""
        error = self._poll_failed_anonymous_task(b"Title,Author,Exclusive Shelf\nSome Book,Someone,to-read\n")
        self.assertIn('any books on your "read" shelf', error)

    @patch("core.tasks.dna.calculate_full_dna", side_effect=RuntimeError("secret internals"))
    def test_task_unexpected_error_stays_generic(self, _mock_calc):
        """Unknown exceptions never leak their text to the user."""
        from core.views.upload import GENERIC_TASK_ERROR

        error = self._poll_failed_anonymous_task(self.csv_file.read())
        self.assertEqual(error, GENERIC_TASK_ERROR)

    def test_status_check_returns_failure_when_nothing_pending_and_no_data(self):
        """
        No task in flight and no DNA (e.g. a signup claim gave up): stop polling.
        """
        user = User.objects.create_user(username="testuser5", password="password")
        user.userprofile.save()

        self.client.login(username="testuser5", password="password")

        response = self.client.get(reverse("core:api_status", args=["dna"]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "FAILURE")

    def test_status_check_returns_failure_when_task_failed(self):
        """
        Test that the dna status poll returns FAILURE status when Celery task fails
        and clears pending_dna_task_id.
        """
        user = User.objects.create_user(username="testuser6", password="password")
        user.userprofile.pending_dna_task_id = "failed-task-id"
        user.userprofile.save()

        self.client.login(username="testuser6", password="password")

        mock_result = MagicMock()
        mock_result.state = "FAILURE"
        mock_result.info = Exception("Something went wrong")

        with patch("core.views.upload.AsyncResult", return_value=mock_result):
            response = self.client.get(reverse("core:api_status", args=["dna"]))

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

    @patch("core.tasks.generate_recommendations_task")
    def test_logged_in_dashboard_ignores_stale_session_dna(self, mock_recs):
        user = User.objects.create_user(username="profilereader", email="p@test.com", password="a-Strong-p4ssword!")
        user.userprofile.dna_data = {"reader_type": "Profile Reader"}
        user.userprofile.save()
        self.client.force_login(user)
        session = self.client.session
        session["dna_data"] = {"reader_type": "Stale Session Reader"}
        session.save()

        response = self.client.get(reverse("core:display_dna"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["dna"]["reader_type"], "Profile Reader")


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
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "session-dna-claim"}},
)
@patch("core.tasks.generate_recommendations_task.delay")
@patch("core.tasks.dna.track_anonymous_dna_claimed")
class SessionDnaClaimTests(TransactionTestCase):
    """Signup/login from the dashboard link (no ?task_id) claims the session DNA:
    DNA + anonymous library as UserBooks, and one anonymous_dna_claimed event."""

    PASSWORD = "Brand-New-p4ssword!"

    def setUp(self):
        from datetime import timedelta

        from django.utils import timezone

        from core.models import AnonymousUserSession, Author, Book

        self.client = Client()
        author = Author.objects.create(name="Session Author")
        self.book_a = Book.objects.create(title="Session A", author=author)
        self.book_b = Book.objects.create(title="Session B", author=author)

        session = self.client.session
        session["dna_data"] = {"reader_type": "Session Reader", "user_stats": {}}
        session.save()
        self.session_key = session.session_key
        AnonymousUserSession.objects.create(
            session_key=self.session_key,
            dna_data={"reader_type": "Session Reader"},
            books_data=[self.book_a.id, self.book_b.id],
            book_ratings={str(self.book_a.id): 5},
            expires_at=timezone.now() + timedelta(days=7),
        )

    def tearDown(self):
        from django.db import connections

        connections.close_all()
        super().tearDown()

    def _login(self, email):
        return self.client.post(reverse("core:login"), {"username": email, "password": self.PASSWORD})

    def test_signup_claims_dna_and_library(self, mock_track, mock_recs):
        from core.models import UserBook

        response = self.client.post(
            reverse("core:signup"),
            {
                "username": "sessionuser",
                "email": "session@test.com",
                "password1": self.PASSWORD,
                "password2": self.PASSWORD,
            },
        )

        self.assertRedirects(response, reverse("core:display_dna"), fetch_redirect_response=False)
        user = User.objects.get(username="sessionuser")
        self.assertEqual(user.userprofile.reader_type, "Session Reader")
        self.assertEqual(UserBook.objects.filter(user=user).count(), 2)
        self.assertEqual(UserBook.objects.get(user=user, book=self.book_a).user_rating, 5)
        mock_track.assert_called_once_with(user_id=user.id, task_id=None, session_key=self.session_key)

    def test_login_without_dna_claims_dna_and_library(self, mock_track, mock_recs):
        from core.models import UserBook

        user = User.objects.create_user(username="returning", email="r@test.com", password=self.PASSWORD)

        self._login("r@test.com")

        user.userprofile.refresh_from_db()
        self.assertEqual(user.userprofile.reader_type, "Session Reader")
        self.assertEqual(UserBook.objects.filter(user=user).count(), 2)
        mock_track.assert_called_once_with(user_id=user.id, task_id=None, session_key=self.session_key)

    def test_login_with_existing_dna_keeps_it_and_claims_nothing(self, mock_track, mock_recs):
        from core.models import UserBook

        user = User.objects.create_user(username="veteran", email="v@test.com", password=self.PASSWORD)
        user.userprofile.dna_data = {"reader_type": "Existing Reader"}
        user.userprofile.save()
        UserBook.objects.create(user=user, book=self.book_b, user_rating=1)

        self._login("v@test.com")

        user.userprofile.refresh_from_db()
        self.assertEqual(user.userprofile.dna_data, {"reader_type": "Existing Reader"})
        self.assertEqual(
            list(UserBook.objects.filter(user=user).values_list("book_id", "user_rating")), [(self.book_b.id, 1)]
        )
        mock_track.assert_not_called()


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "upload-validation-tests",
        }
    },
    RATELIMIT_ENABLE=False,
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

    @patch("core.views.upload.generate_reading_dna_task")
    def test_oversize_row_count_is_truncated_not_rejected(self, mock_task):
        """50_001-row CSV is accepted; csv_content passed to the task has at
        most MAX_UPLOAD_ROWS data rows (header + 50000 = 50001 lines)."""
        from core.views import MAX_UPLOAD_ROWS

        mock_task.delay.return_value = MagicMock(id=str(uuid.uuid4()))

        header = "Title,Author,Exclusive Shelf,My Rating,ISBN13"
        rows = "\n".join(f"Book {i},Author {i},read,4,978000000{i:04d}" for i in range(MAX_UPLOAD_ROWS + 1))
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

    @patch("core.views.upload.generate_reading_dna_task")
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

    @patch("core.views.upload.generate_reading_dna_task")
    def test_missing_schema_columns_is_rejected(self, mock_task):
        """A CSV with none of the expected columns is rejected, naming what's missing."""
        bad = b"FooBar,Baz\n1,2\n3,4\n"
        upload = self._make_csv("not-goodreads.csv", bad)

        response = self.client.post(reverse("core:upload"), {"csv_file": upload}, follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, escape('missing the "Title", "Author", "Exclusive Shelf" columns'))
        mock_task.delay.assert_not_called()

    @patch("core.views.upload.generate_reading_dna_task")
    def test_goodreads_csv_missing_exclusive_shelf_names_the_column(self, mock_task):
        """A Goodreads export without "Exclusive Shelf" is rejected before the task runs."""
        upload = self._make_csv("goodreads.csv", b"Title,Author,My Rating\nSome Book,Someone,4\n")

        response = self.client.post(reverse("core:upload"), {"csv_file": upload}, follow=True)

        self.assertContains(
            response,
            escape(
                'Your CSV is missing the "Exclusive Shelf" column. '
                "Please upload an unedited Goodreads or StoryGraph export."
            ),
        )
        mock_task.delay.assert_not_called()

    @patch("core.views.upload.generate_reading_dna_task")
    def test_storygraph_csv_missing_read_status_names_the_column(self, mock_task):
        """StoryGraph exports are checked against their own column names."""
        upload = self._make_csv("storygraph.csv", b"Title,Authors,Star Rating\nSome Book,Someone,4.5\n")

        response = self.client.post(reverse("core:upload"), {"csv_file": upload}, follow=True)

        self.assertContains(response, escape('missing the "Read Status" column'))
        mock_task.delay.assert_not_called()

    @patch("core.views.upload.generate_reading_dna_task")
    def test_valid_goodreads_csv_still_uploads(self, mock_task):
        """Positive control: a Goodreads-shaped CSV passes validation and the
        task is dispatched with the original csv_content."""
        mock_task.delay.return_value = MagicMock(id=str(uuid.uuid4()))

        good = b"Title,Author,Exclusive Shelf,My Rating,ISBN13\n" b"Dune,Frank Herbert,read,5,9780441172719\n"
        upload = self._make_csv("goodreads.csv", good)

        response = self.client.post(reverse("core:upload"), {"csv_file": upload})

        self.assertEqual(response.status_code, 302)
        mock_task.delay.assert_called_once()

    @patch("core.views.upload.generate_reading_dna_task")
    def test_valid_storygraph_csv_still_uploads(self, mock_task):
        """Positive control: a StoryGraph-shaped CSV (Authors, plural) passes
        validation. Schema check accepts either Goodreads or StoryGraph
        signature column sets."""
        mock_task.delay.return_value = MagicMock(id=str(uuid.uuid4()))

        sg = b"Title,Authors,Read Status,Star Rating,ISBN/UID\n" b"Project Hail Mary,Andy Weir,read,5,9780593135204\n"
        upload = self._make_csv("storygraph.csv", sg)

        response = self.client.post(reverse("core:upload"), {"csv_file": upload})

        self.assertEqual(response.status_code, 302)
        mock_task.delay.assert_called_once()


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "upload-ratelimit"}},
    RATELIMIT_ENABLE=True,
)
class UploadRateLimitTests(TransactionTestCase):
    """Anonymous uploads are capped at 5 per 10 minutes per IP; logged-in users are not counted."""

    CSV = b"Title,Author,Exclusive Shelf,My Rating,ISBN13\nBook,Author,read,4,9780000000001\n"

    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def _post(self):
        upload = SimpleUploadedFile("goodreads.csv", self.CSV, content_type="text/csv")
        return self.client.post(reverse("core:upload"), {"csv_file": upload})

    @patch("core.views.upload.generate_reading_dna_task")
    def test_sixth_anonymous_upload_is_rejected(self, mock_task):
        mock_task.delay.return_value = MagicMock(id="anon-task-id")
        with frozen_ratelimit_window():
            for _ in range(5):
                self._post()
            response = self._post()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("core:home"))
        self.assertEqual(mock_task.delay.call_count, 5)

    @patch("core.views.upload.generate_reading_dna_task")
    def test_logged_in_uploads_are_not_limited(self, mock_task):
        mock_task.delay.return_value = MagicMock(id="auth-task-id")
        user = User.objects.create_user("ratelimited", "rl@example.com", "pw")
        self.client.force_login(user)
        with frozen_ratelimit_window():
            for _ in range(6):
                cache.delete(f"dna_task_inflight_{user.id}")  # sidestep the separate in-flight guard
                self._post()

        self.assertEqual(mock_task.delay.call_count, 6)


class LoginUrlRedirectTests(TransactionTestCase):
    """Anonymous hits to @login_required endpoints must redirect to our real
    login page, not Django's default /accounts/login/ (which 404s here)."""

    def test_anonymous_enrichment_status_redirects_to_login(self):
        response = self.client.get(reverse("core:api_status", args=["enrichment"]))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            response.url.startswith(reverse("core:login")),
            f"expected redirect into {reverse('core:login')}, got {response.url}",
        )

    def test_anonymous_recommendations_status_redirects_to_login(self):
        response = self.client.get(reverse("core:api_status", args=["recommendations"]))
        self.assertEqual(response.status_code, 302)

    def test_anonymous_dna_status_is_served(self):
        response = self.client.get(reverse("core:api_status", args=["dna"]))
        self.assertEqual(response.json(), {"status": "PENDING"})

    def test_unknown_status_part_404s(self):
        self.assertEqual(self.client.get("/api/status/nope/").status_code, 404)

    def test_anonymous_logout_redirects_to_login(self):
        response = self.client.get(reverse("core:logout"))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(reverse("core:login")))
