from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse


# We need Celery tasks to run synchronously for E2E tests too
@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class ViewE2E_Tests(TestCase):

    def setUp(self):
        self.client = Client()
        self.csv_file = SimpleUploadedFile(
            "goodreads.csv",
            "Title,Author,Exclusive Shelf\nE2E Book,E2E Author,read".encode("utf-8"),
            content_type="text/csv",
        )
        self.sample_dna_data = {"reader_type": "E2E Reader"}

    @patch("core.tasks.generate_reading_dna_task.delay")
    def test_anonymous_upload_to_signup_and_claim_flow(self, mock_generate_task):
        """
        THE CRITICAL PATH TEST:
        1. Anonymous user uploads a file.
        2. They are redirected to a waiting page.
        3. The task finishes, they see their DNA.
        4. They sign up from the waiting page.
        5. The DNA is successfully claimed and saved to their new profile.
        """
        # --- Step 1 & 2: Anonymous Upload ---

        # Mock the task result so we can control the flow
        mock_async_result = MagicMock()
        task_id = "e2e-task-id-456"
        mock_async_result.id = task_id
        mock_generate_task.return_value = mock_async_result

        response = self.client.post(reverse("core:upload"), {"csv_file": self.csv_file})
        self.assertRedirects(response, reverse("core:task_status", kwargs={"task_id": task_id}))

        # --- Step 3: Mock Task Completion and Poll for Result ---

        # The frontend would poll this view. We simulate it directly.
        # Before completion, it should be PENDING
        with patch("core.views.AsyncResult") as mock_async:
            mock_async.return_value.ready.return_value = False
            response = self.client.get(reverse("core:get_task_result", kwargs={"task_id": task_id}))
            self.assertEqual(response.json()["status"], "PENDING")

        # After completion, it should be SUCCESS
        with patch("core.views.AsyncResult") as mock_async:
            mock_async.return_value.ready.return_value = True
            mock_async.return_value.successful.return_value = True
            mock_async.return_value.get.return_value = self.sample_dna_data

            response = self.client.get(reverse("core:get_task_result", kwargs={"task_id": task_id}))
            self.assertEqual(response.json()["status"], "SUCCESS")

        # The API response puts the DNA in the session. Now go to the display page.
        response = self.client.get(reverse("core:display_dna"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "E2E Reader")

        # --- Step 4 & 5: Sign up and Claim ---

        # Manually place the result in the cache, as the real task would
        cache.set(f"dna_result:{task_id}", self.sample_dna_data, timeout=60)

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

        try:
            # Check if the final redirect is to the processing page
            self.assertRedirects(response, reverse("core:display_dna") + "?processing=true")
        except AssertionError:
            # If the redirect fails, print the HTML of the page we got back
            print("\n--- E2E FORM VALIDATION DEBUG ---")
            print("Redirect failed. The server responded with the form page, likely with errors.")
            print("Look for error messages in the HTML below:")
            print(response.content.decode("utf-8"))
            print("--- END DEBUG ---\n")
            # Re-raise the error so the test still fails
            raise

        # The user should now be logged in and on the processing page
        self.assertRedirects(response, reverse("core:display_dna") + "?processing=true")

        # VERIFY THE FINAL RESULT
        new_user = User.objects.get(username="claimeduser")
        self.assertIsNotNone(new_user.userprofile.dna_data)
        self.assertEqual(new_user.userprofile.reader_type, "E2E Reader")
        self.assertIsNone(cache.get(f"dna_result:{task_id}"))  # Cache should be cleared
