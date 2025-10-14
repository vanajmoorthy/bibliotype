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
    @patch("core.services.dna_analyser.enrich_book_from_apis")
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
