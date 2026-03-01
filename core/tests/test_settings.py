import json
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse


class SettingsViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="testuser", email="test@example.com", password="testpass123!")
        self.client.login(username="testuser", password="testpass123!")

    # --- Update Email ---

    def test_update_email_success(self):
        response = self.client.post(reverse("core:update_email"), {"email": "new@example.com"})
        self.assertRedirects(response, reverse("core:display_dna"))
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "new@example.com")

    def test_update_email_duplicate(self):
        User.objects.create_user(username="other", email="taken@example.com", password="pass123!")
        response = self.client.post(reverse("core:update_email"), {"email": "taken@example.com"})
        self.assertRedirects(response, reverse("core:display_dna"))
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "test@example.com")

    def test_update_email_duplicate_case_insensitive(self):
        User.objects.create_user(username="other", email="Taken@Example.com", password="pass123!")
        response = self.client.post(reverse("core:update_email"), {"email": "taken@example.com"})
        self.assertRedirects(response, reverse("core:display_dna"))
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "test@example.com")

    def test_update_email_invalid_format(self):
        response = self.client.post(reverse("core:update_email"), {"email": "not-an-email"})
        self.assertRedirects(response, reverse("core:display_dna"))
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "test@example.com")

    def test_update_email_requires_login(self):
        self.client.logout()
        response = self.client.post(reverse("core:update_email"), {"email": "new@example.com"})
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response.url)

    def test_update_email_requires_post(self):
        response = self.client.get(reverse("core:update_email"))
        self.assertEqual(response.status_code, 405)

    # --- Change Password ---

    def test_change_password_success(self):
        response = self.client.post(
            reverse("core:change_password"),
            {"old_password": "testpass123!", "new_password1": "newSecure456!", "new_password2": "newSecure456!"},
        )
        self.assertRedirects(response, reverse("core:display_dna"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("newSecure456!"))

    def test_change_password_wrong_old(self):
        response = self.client.post(
            reverse("core:change_password"),
            {"old_password": "wrongpassword", "new_password1": "newSecure456!", "new_password2": "newSecure456!"},
        )
        self.assertRedirects(response, reverse("core:display_dna"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("testpass123!"))

    def test_change_password_mismatch(self):
        response = self.client.post(
            reverse("core:change_password"),
            {"old_password": "testpass123!", "new_password1": "newSecure456!", "new_password2": "different789!"},
        )
        self.assertRedirects(response, reverse("core:display_dna"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("testpass123!"))

    def test_change_password_requires_login(self):
        self.client.logout()
        response = self.client.post(
            reverse("core:change_password"),
            {"old_password": "testpass123!", "new_password1": "newSecure456!", "new_password2": "newSecure456!"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response.url)

    def test_change_password_stays_logged_in(self):
        self.client.post(
            reverse("core:change_password"),
            {"old_password": "testpass123!", "new_password1": "newSecure456!", "new_password2": "newSecure456!"},
        )
        # User should still be logged in after password change
        response = self.client.get(reverse("core:display_dna"))
        self.assertNotEqual(response.status_code, 302)

    # --- Delete Account ---

    def test_delete_account_success(self):
        user_id = self.user.id
        response = self.client.post(
            reverse("core:delete_account"), {"confirmation": "DELETE", "password": "testpass123!"}
        )
        self.assertRedirects(response, reverse("core:home"))
        self.assertFalse(User.objects.filter(id=user_id).exists())

    def test_delete_account_wrong_confirmation(self):
        response = self.client.post(
            reverse("core:delete_account"), {"confirmation": "delete", "password": "testpass123!"}
        )
        self.assertRedirects(response, reverse("core:display_dna"))
        self.assertTrue(User.objects.filter(id=self.user.id).exists())

    def test_delete_account_wrong_password(self):
        response = self.client.post(
            reverse("core:delete_account"), {"confirmation": "DELETE", "password": "wrongpassword"}
        )
        self.assertRedirects(response, reverse("core:display_dna"))
        self.assertTrue(User.objects.filter(id=self.user.id).exists())

    def test_delete_account_missing_fields(self):
        response = self.client.post(reverse("core:delete_account"), {"confirmation": "", "password": ""})
        self.assertRedirects(response, reverse("core:display_dna"))
        self.assertTrue(User.objects.filter(id=self.user.id).exists())

    def test_delete_account_requires_login(self):
        self.client.logout()
        response = self.client.post(
            reverse("core:delete_account"), {"confirmation": "DELETE", "password": "testpass123!"}
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response.url)

    def test_delete_account_cascades_profile(self):
        profile = self.user.userprofile
        profile.dna_data = {"reader_type": "Test Reader"}
        profile.save()
        self.client.post(reverse("core:delete_account"), {"confirmation": "DELETE", "password": "testpass123!"})
        from core.models import UserProfile

        self.assertFalse(UserProfile.objects.filter(user_id=self.user.id).exists())

    # --- Privacy Toggle (AJAX) ---

    def test_privacy_toggle_ajax_make_public(self):
        profile = self.user.userprofile
        profile.is_public = False
        profile.save()
        response = self.client.post(
            reverse("core:update_privacy"),
            json.dumps({"is_public": True}),
            content_type="application/json",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "success")
        self.assertTrue(data["is_public"])
        profile.refresh_from_db()
        self.assertTrue(profile.is_public)

    def test_privacy_toggle_ajax_make_private(self):
        profile = self.user.userprofile
        profile.is_public = True
        profile.save()
        response = self.client.post(
            reverse("core:update_privacy"),
            json.dumps({"is_public": False}),
            content_type="application/json",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "success")
        self.assertFalse(data["is_public"])
        profile.refresh_from_db()
        self.assertFalse(profile.is_public)

    def test_privacy_toggle_form_post_still_works(self):
        response = self.client.post(reverse("core:update_privacy"), {"is_public": "true"})
        self.assertRedirects(response, reverse("core:display_dna"))
        self.user.userprofile.refresh_from_db()
        self.assertTrue(self.user.userprofile.is_public)

    # --- Recommendation Visibility Toggle (AJAX) ---

    def test_recommendation_visibility_ajax_opt_out(self):
        response = self.client.post(
            reverse("core:update_recommendation_visibility"),
            json.dumps({"visible_in_recommendations": False}),
            content_type="application/json",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "success")
        self.assertFalse(data["visible_in_recommendations"])
        self.user.userprofile.refresh_from_db()
        self.assertFalse(self.user.userprofile.visible_in_recommendations)

    def test_recommendation_visibility_ajax_opt_in(self):
        profile = self.user.userprofile
        profile.visible_in_recommendations = False
        profile.save()
        response = self.client.post(
            reverse("core:update_recommendation_visibility"),
            json.dumps({"visible_in_recommendations": True}),
            content_type="application/json",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "success")
        self.assertTrue(data["visible_in_recommendations"])

    def test_recommendation_visibility_form_post_still_works(self):
        response = self.client.post(
            reverse("core:update_recommendation_visibility"), {"visible_in_recommendations": "false"}
        )
        self.assertRedirects(response, reverse("core:display_dna"))
        self.user.userprofile.refresh_from_db()
        self.assertFalse(self.user.userprofile.visible_in_recommendations)


class IsPublicDefaultTests(TestCase):
    def test_new_user_profile_is_public_by_default(self):
        user = User.objects.create_user(username="newuser", email="new@example.com", password="pass123!")
        self.assertTrue(user.userprofile.is_public)
