"""
Tests for the settings modal endpoints:
  - UpdateEmailForm / update_email_view
  - ChangePasswordForm / change_password_view
  - delete_account_view
  - update_privacy_view  (AJAX + form-POST dual-response)
  - update_recommendation_visibility (AJAX + form-POST dual-response)
  - Auth guards for all new endpoints
"""

from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
)
class UpdateEmailViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.password = "Str0ng-Pass!word"
        self.user = User.objects.create_user(
            username="emailuser",
            email="original@example.com",
            password=self.password,
        )
        self.client.force_login(self.user)
        self.url = reverse("core:update_email")

    def _post(self, email, ajax=True):
        headers = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"} if ajax else {}
        return self.client.post(self.url, {"email": email}, **headers)

    def test_valid_email_change_persists(self):
        response = self._post("new@example.com")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "success")
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "new@example.com")

    def test_duplicate_email_case_insensitive_rejected(self):
        other = User.objects.create_user(username="other", email="taken@example.com", password="pass")
        response = self._post("TAKEN@example.com")
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertEqual(data["status"], "error")
        self.assertIn("already in use", data["message"])
        # Own email must NOT have changed
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "original@example.com")

    def test_changing_to_own_email_is_allowed(self):
        """Changing to the same email (or its uppercase variant) must succeed."""
        response = self._post("ORIGINAL@example.com")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "success")

    def test_invalid_format_rejected(self):
        response = self._post("not-an-email")
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertEqual(data["status"], "error")

    def test_anonymous_user_redirected(self):
        self.client.logout()
        response = self._post("anon@example.com")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])

    @patch("core.views.profile.track_settings_updated")
    def test_analytics_called_on_success(self, mock_track):
        self._post("tracked@example.com")
        mock_track.assert_called_once_with(self.user.id, setting_type="email")


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
    AUTH_PASSWORD_VALIDATORS=[
        {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}}
    ],
)
class ChangePasswordViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.password = "OldP@ssword1"
        self.user = User.objects.create_user(
            username="pwuser",
            email="pw@example.com",
            password=self.password,
        )
        self.client.force_login(self.user)
        self.url = reverse("core:change_password")

    def _post(self, current, new1, new2, ajax=True):
        headers = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"} if ajax else {}
        return self.client.post(
            self.url,
            {"current_password": current, "new_password1": new1, "new_password2": new2},
            **headers,
        )

    def test_valid_change_persists_and_user_stays_logged_in(self):
        new_pw = "NewP@ssword9"
        response = self._post(self.password, new_pw, new_pw)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "success")
        # User must still be authenticated (update_session_auth_hash kept the session)
        check = self.client.get(reverse("core:display_dna"))
        self.assertNotEqual(check.status_code, 302)  # not redirected to login

    def test_wrong_current_password_rejected(self):
        response = self._post("WrongCurrentPw!", "NewP@ssword9", "NewP@ssword9")
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertEqual(data["status"], "error")
        # Password must not have changed
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.password))

    def test_new_password_mismatch_rejected(self):
        response = self._post(self.password, "NewP@ssword9", "DifferentP@ss9")
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertEqual(data["status"], "error")

    def test_too_weak_password_rejected(self):
        """Django's MinimumLengthValidator must reject short passwords."""
        response = self._post(self.password, "short", "short")
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertEqual(data["status"], "error")

    def test_anonymous_user_redirected(self):
        self.client.logout()
        response = self._post("anything", "NewP@ssword9", "NewP@ssword9")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])

    @patch("core.views.profile.track_settings_updated")
    def test_analytics_called_on_success(self, mock_track):
        self._post(self.password, "NewP@ssword9", "NewP@ssword9")
        mock_track.assert_called_once_with(self.user.id, setting_type="password")


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
)
class DeleteAccountViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.password = "Del3te-Me!"
        self.user = User.objects.create_user(
            username="deleteme",
            email="delete@example.com",
            password=self.password,
        )
        self.client.force_login(self.user)
        self.url = reverse("core:delete_account")

    def _post(self, confirmation, password, ajax=True):
        headers = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"} if ajax else {}
        return self.client.post(self.url, {"confirmation": confirmation, "password": password}, **headers)

    @patch("core.views.profile.track_account_deleted")
    def test_correct_confirmation_and_password_deletes_account(self, mock_track):
        uid = self.user.id
        response = self._post("DELETE", self.password)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "success")
        self.assertIn("redirect", data)
        # User and profile must be gone
        self.assertFalse(User.objects.filter(pk=uid).exists())
        # Analytics must have been called before deletion
        mock_track.assert_called_once_with(uid)

    @patch("core.views.profile.track_account_deleted")
    def test_wrong_confirmation_does_not_delete(self, mock_track):
        uid = self.user.id
        response = self._post("delete", self.password)  # lowercase = wrong
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertEqual(data["status"], "error")
        self.assertTrue(User.objects.filter(pk=uid).exists())
        mock_track.assert_not_called()

    @patch("core.views.profile.track_account_deleted")
    def test_wrong_password_does_not_delete(self, mock_track):
        uid = self.user.id
        response = self._post("DELETE", "WrongPassword!")
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertEqual(data["status"], "error")
        self.assertTrue(User.objects.filter(pk=uid).exists())
        mock_track.assert_not_called()

    @patch("core.views.profile.track_account_deleted")
    def test_missing_confirmation_does_not_delete(self, mock_track):
        uid = self.user.id
        response = self._post("", self.password)
        self.assertEqual(response.status_code, 400)
        self.assertTrue(User.objects.filter(pk=uid).exists())
        mock_track.assert_not_called()

    def test_anonymous_user_redirected(self):
        self.client.logout()
        response = self._post("DELETE", self.password)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])

    @patch("core.views.profile.track_account_deleted")
    def test_form_post_redirects_to_home(self, mock_track):
        """Non-AJAX delete must redirect to home."""
        uid = self.user.id
        response = self._post("DELETE", self.password, ajax=False)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(User.objects.filter(pk=uid).exists())


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
)
class UpdatePrivacyDualResponseTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="privacyuser",
            email="privacy@example.com",
            password="pass",
        )
        self.client.force_login(self.user)
        self.url = reverse("core:update_privacy")

    def _post(self, is_public, ajax=True):
        headers = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"} if ajax else {}
        return self.client.post(self.url, {"is_public": "true" if is_public else "false"}, **headers)

    def test_ajax_make_public_returns_json(self):
        response = self._post(True, ajax=True)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "success")
        self.assertTrue(data["is_public"])
        self.user.userprofile.refresh_from_db()
        self.assertTrue(self.user.userprofile.is_public)

    def test_ajax_make_private_returns_json(self):
        self.user.userprofile.is_public = True
        self.user.userprofile.save()
        response = self._post(False, ajax=True)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "success")
        self.assertFalse(data["is_public"])

    def test_form_post_redirects(self):
        response = self._post(True, ajax=False)
        self.assertEqual(response.status_code, 302)
        self.assertIn("dashboard", response["Location"])

    def test_anonymous_user_redirected(self):
        self.client.logout()
        response = self._post(True)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
)
class UpdateRecommendationVisibilityDualResponseTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="recvisuser",
            email="recvis@example.com",
            password="pass",
        )
        self.client.force_login(self.user)
        self.url = reverse("core:update_recommendation_visibility")

    def _post(self, visible, ajax=True):
        headers = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"} if ajax else {}
        return self.client.post(
            self.url,
            {"visible_in_recommendations": "true" if visible else "false"},
            **headers,
        )

    def test_ajax_opt_out_returns_json_and_persists(self):
        response = self._post(False, ajax=True)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "success")
        self.assertFalse(data["visible_in_recommendations"])
        self.user.userprofile.refresh_from_db()
        self.assertFalse(self.user.userprofile.visible_in_recommendations)

    def test_ajax_opt_in_returns_json_and_persists(self):
        self.user.userprofile.visible_in_recommendations = False
        self.user.userprofile.save()
        response = self._post(True, ajax=True)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "success")
        self.assertTrue(data["visible_in_recommendations"])
        self.user.userprofile.refresh_from_db()
        self.assertTrue(self.user.userprofile.visible_in_recommendations)

    def test_form_post_redirects(self):
        response = self._post(False, ajax=False)
        self.assertEqual(response.status_code, 302)

    def test_anonymous_user_redirected(self):
        self.client.logout()
        response = self._post(False)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
)
class AuthGuardTests(TestCase):
    """Every new endpoint must redirect anonymous users to login."""

    def setUp(self):
        self.client = Client()

    def _assert_redirect_to_login(self, url, post_data=None):
        response = self.client.post(url, post_data or {})
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])

    def test_update_email_auth_guard(self):
        self._assert_redirect_to_login(reverse("core:update_email"), {"email": "x@x.com"})

    def test_change_password_auth_guard(self):
        self._assert_redirect_to_login(
            reverse("core:change_password"),
            {"current_password": "a", "new_password1": "b", "new_password2": "b"},
        )

    def test_delete_account_auth_guard(self):
        self._assert_redirect_to_login(
            reverse("core:delete_account"),
            {"confirmation": "DELETE", "password": "pass"},
        )
