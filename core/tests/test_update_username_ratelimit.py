import json

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from .ratelimit_test_helpers import frozen_ratelimit_window


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "update-username-ratelimit-tests",
        }
    },
    RATELIMIT_ENABLE=True,
)
class UpdateUsernameApiRateLimitTests(TestCase):
    """US-020: update_username_api is rate-limited at 10 POSTs/minute/user via django-ratelimit."""

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.password = "validpassword123!"
        self.user = User.objects.create_user(
            username="ratelimituser",
            email="ratelimit@example.com",
            password=self.password,
        )
        self.client.force_login(self.user)
        self.url = reverse("core:api_update_username")

    def tearDown(self):
        cache.clear()

    def _post_username(self, username):
        return self.client.post(
            self.url,
            data=json.dumps({"username": username}),
            content_type="application/json",
        )

    def test_eleventh_post_in_a_minute_returns_429(self):
        with frozen_ratelimit_window():
            for i in range(10):
                response = self._post_username(f"name_{i}")
                self.assertNotEqual(response.status_code, 429, f"Request {i + 1} unexpectedly 429'd")

            response = self._post_username("over_the_limit")
        self.assertEqual(response.status_code, 429)
        payload = response.json()
        self.assertEqual(payload, {"error": "Too many attempts, try again later."})

    def test_tenth_post_returns_normal_response(self):
        with frozen_ratelimit_window():
            for i in range(9):
                response = self._post_username(f"name_{i}")
                self.assertNotEqual(response.status_code, 429, f"Request {i + 1} unexpectedly 429'd")

            response = self._post_username("tenth_name")
        self.assertNotEqual(response.status_code, 429)
        self.assertIn(response.status_code, (200, 400))
