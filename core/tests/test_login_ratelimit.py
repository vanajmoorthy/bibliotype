from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from .ratelimit_test_helpers import frozen_ratelimit_window


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "login-ratelimit-tests",
        }
    },
    RATELIMIT_ENABLE=True,
)
class LoginRateLimitTests(TestCase):
    """US-016: login_view is rate-limited at 5 POSTs/minute/IP via django-ratelimit."""

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.password = "validpassword123!"
        self.user = User.objects.create_user(
            username="ratelimituser",
            email="ratelimit@example.com",
            password=self.password,
        )

    def tearDown(self):
        cache.clear()

    def test_sixth_login_post_returns_429_with_template_error(self):
        login_url = reverse("core:login")
        bad_payload = {"username": "ratelimit@example.com", "password": "wrongpassword"}

        with frozen_ratelimit_window():
            for _ in range(5):
                response = self.client.post(login_url, bad_payload)
                self.assertEqual(response.status_code, 200)

            response = self.client.post(login_url, bad_payload)
        self.assertEqual(response.status_code, 429)
        self.assertContains(response, "Too many attempts", status_code=429)

    def test_fifth_login_post_with_valid_credentials_still_authenticates(self):
        login_url = reverse("core:login")
        bad_payload = {"username": "ratelimit@example.com", "password": "wrongpassword"}

        with frozen_ratelimit_window():
            for _ in range(4):
                response = self.client.post(login_url, bad_payload)
                self.assertEqual(response.status_code, 200)

            good_payload = {"username": "ratelimit@example.com", "password": self.password}
            response = self.client.post(login_url, good_payload)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.id)
