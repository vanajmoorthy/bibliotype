from unittest.mock import patch

from django.contrib.auth.models import User
from django.core import mail
from django.test import Client, TestCase, override_settings
from django.urls import reverse


@override_settings(
    TURNSTILE_SECRET_KEY="test-secret-key",
    TURNSTILE_SITE_KEY="test-site-key",
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "password-reset-tests",
        }
    },
)
class PasswordResetTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="testuser",
            email="test@example.com",
            password="testpassword123",
        )

    def test_password_reset_form_renders(self):
        response = self.client.get(reverse("password_reset"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Reset Password")
        self.assertContains(response, 'name="email"')

    def test_password_reset_form_has_turnstile_widget(self):
        response = self.client.get(reverse("password_reset"))
        self.assertContains(response, "cf-turnstile")
        self.assertContains(response, "challenges.cloudflare.com/turnstile")

    @patch("core.turnstile.verify_turnstile_token", return_value=True)
    def test_password_reset_valid_email_sends_email(self, mock_turnstile):
        response = self.client.post(
            reverse("password_reset"),
            {"email": "test@example.com", "cf-turnstile-response": "valid-token"},
        )
        self.assertRedirects(response, reverse("password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].subject, "Reset your Bibliotype password")
        self.assertIn("test@example.com", mail.outbox[0].to)

    @patch("core.turnstile.verify_turnstile_token", return_value=True)
    def test_password_reset_nonexistent_email_still_redirects(self, mock_turnstile):
        """Non-existent email should still redirect to done page to prevent enumeration."""
        response = self.client.post(
            reverse("password_reset"),
            {"email": "nobody@example.com", "cf-turnstile-response": "valid-token"},
        )
        self.assertRedirects(response, reverse("password_reset_done"))
        self.assertEqual(len(mail.outbox), 0)

    @patch("core.turnstile.verify_turnstile_token", return_value=False)
    def test_password_reset_without_turnstile_fails(self, mock_turnstile):
        response = self.client.post(
            reverse("password_reset"),
            {"email": "test@example.com", "cf-turnstile-response": ""},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "CAPTCHA verification failed")
        self.assertEqual(len(mail.outbox), 0)

    def test_password_reset_done_renders(self):
        response = self.client.get(reverse("password_reset_done"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Check Your Email")
        self.assertContains(response, "expires in 3 days")

    def test_password_reset_complete_renders(self):
        response = self.client.get(reverse("password_reset_complete"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Password Updated")

    def test_login_page_has_forgot_password_link(self):
        response = self.client.get(reverse("core:login"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Forgot Password?")
        self.assertContains(response, reverse("password_reset"))


@override_settings(
    TURNSTILE_SECRET_KEY="test-secret-key",
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "turnstile-tests",
        }
    },
)
class TurnstileVerificationTests(TestCase):
    @patch("core.turnstile.requests.post")
    def test_turnstile_verify_success(self, mock_post):
        from core.turnstile import verify_turnstile_token

        mock_post.return_value.json.return_value = {"success": True}
        result = verify_turnstile_token("valid-token")
        self.assertTrue(result)
        mock_post.assert_called_once()

    @patch("core.turnstile.requests.post")
    def test_turnstile_verify_failure(self, mock_post):
        from core.turnstile import verify_turnstile_token

        mock_post.return_value.json.return_value = {"success": False}
        result = verify_turnstile_token("invalid-token")
        self.assertFalse(result)

    @override_settings(TURNSTILE_SECRET_KEY="")
    def test_turnstile_dev_mode_skip(self):
        """When TURNSTILE_SECRET_KEY is empty, verification should pass (dev mode)."""
        from core.turnstile import verify_turnstile_token

        result = verify_turnstile_token("")
        self.assertTrue(result)

    def test_turnstile_empty_token_with_secret_key(self):
        from core.turnstile import verify_turnstile_token

        result = verify_turnstile_token("")
        self.assertFalse(result)

    @patch("core.turnstile.requests.post", side_effect=Exception("Network error"))
    def test_turnstile_network_error_returns_false(self, mock_post):
        from core.turnstile import verify_turnstile_token

        result = verify_turnstile_token("some-token")
        self.assertFalse(result)


@override_settings(
    TURNSTILE_SECRET_KEY="test-secret-key",
    TURNSTILE_SITE_KEY="test-site-key",
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "signup-turnstile-tests",
        }
    },
)
class SignupTurnstileTests(TestCase):
    def setUp(self):
        self.client = Client()

    @patch("core.turnstile.verify_turnstile_token", return_value=True)
    def test_signup_with_valid_turnstile(self, mock_turnstile):
        response = self.client.post(
            reverse("core:signup"),
            {
                "username": "newuser",
                "email": "new@example.com",
                "password1": "complexpass123!",
                "password2": "complexpass123!",
                "cf-turnstile-response": "valid-token",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(User.objects.filter(username="newuser").exists())

    @patch("core.turnstile.verify_turnstile_token", return_value=False)
    def test_signup_with_failed_turnstile(self, mock_turnstile):
        response = self.client.post(
            reverse("core:signup"),
            {
                "username": "newuser",
                "email": "new@example.com",
                "password1": "complexpass123!",
                "password2": "complexpass123!",
                "cf-turnstile-response": "invalid-token",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username="newuser").exists())
        self.assertContains(response, "CAPTCHA verification failed")

    def test_signup_page_has_turnstile_widget(self):
        response = self.client.get(reverse("core:signup"))
        self.assertContains(response, "cf-turnstile")


@override_settings(
    TURNSTILE_SECRET_KEY="",
    TURNSTILE_SITE_KEY="",
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "dev-mode-tests",
        }
    },
)
class DevModeTurnstileTests(TestCase):
    """Tests verifying Turnstile is fully bypassed when keys are empty (local dev)."""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="devuser",
            email="dev@example.com",
            password="testpassword123",
        )

    def test_signup_page_hides_turnstile_widget_in_dev(self):
        response = self.client.get(reverse("core:signup"))
        self.assertNotContains(response, "cf-turnstile")
        self.assertNotContains(response, "challenges.cloudflare.com/turnstile")

    def test_password_reset_page_hides_turnstile_widget_in_dev(self):
        response = self.client.get(reverse("password_reset"))
        self.assertNotContains(response, "cf-turnstile")
        self.assertNotContains(response, "challenges.cloudflare.com/turnstile")

    def test_password_reset_works_without_turnstile_in_dev(self):
        """Password reset should succeed without any Turnstile token in dev mode."""
        response = self.client.post(
            reverse("password_reset"),
            {"email": "dev@example.com"},
        )
        self.assertRedirects(response, reverse("password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].subject, "Reset your Bibliotype password")

    def test_signup_works_without_turnstile_in_dev(self):
        """Signup should succeed without any Turnstile token in dev mode."""
        response = self.client.post(
            reverse("core:signup"),
            {
                "username": "devnewuser",
                "email": "devnew@example.com",
                "password1": "complexpass123!",
                "password2": "complexpass123!",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(User.objects.filter(username="devnewuser").exists())

    def test_password_reset_confirm_sets_new_password(self):
        """Full flow: request reset, use token from email, set new password."""
        # Request reset
        self.client.post(reverse("password_reset"), {"email": "dev@example.com"})
        self.assertEqual(len(mail.outbox), 1)

        # Extract token and uid from email body
        email_body = mail.outbox[0].body
        import re

        reset_link = re.search(r"/password-reset-confirm/([^/]+)/([^/]+)/", email_body)
        self.assertIsNotNone(reset_link, "Reset link not found in email body")
        uid = reset_link.group(1)
        token = reset_link.group(2)

        # GET the confirm page (Django redirects to set-password token internally)
        response = self.client.get(
            reverse("password_reset_confirm", kwargs={"uidb64": uid, "token": token}),
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Set New Password")

        # POST new password — Django's confirm view uses the internal "set-password" token after GET
        response = self.client.post(
            reverse("password_reset_confirm", kwargs={"uidb64": uid, "token": "set-password"}),
            {"new_password1": "newstrongpass456!", "new_password2": "newstrongpass456!"},
        )
        self.assertRedirects(response, reverse("password_reset_complete"))

        # Verify the password actually changed
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("newstrongpass456!"))
