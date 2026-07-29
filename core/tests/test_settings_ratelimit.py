"""Rate-limit coverage for the settings-modal write endpoints.

update_email / change_password / delete_account are password/email oracles, so
each is throttled to 5 POSTs/minute/user via django-ratelimit (mirrors
update_username_api). The 6th POST inside a minute must return 429.
"""

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from .ratelimit_test_helpers import frozen_ratelimit_window

AJAX = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "settings-ratelimit-tests",
        }
    },
    RATELIMIT_ENABLE=True,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)
class SettingsEndpointsRateLimitTests(TestCase):
    """Each write endpoint allows 5 POSTs/minute/user, then 429s."""

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.password = "Str0ng-Pass!word"
        self.user = User.objects.create_user(
            username="rluser",
            email="rl@example.com",
            password=self.password,
        )
        self.client.force_login(self.user)

    def tearDown(self):
        cache.clear()

    def _assert_sixth_post_429s(self, url, body):
        with frozen_ratelimit_window():
            for i in range(5):
                response = self.client.post(url, body, **AJAX)
                self.assertNotEqual(response.status_code, 429, f"Request {i + 1} unexpectedly 429'd")

            response = self.client.post(url, body, **AJAX)
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["status"], "error")

    def test_update_email_rate_limited(self):
        # Wrong password keeps every attempt a 400 (no side effects) until the 429.
        self._assert_sixth_post_429s(
            reverse("core:update_email"),
            {"email": "new@example.com", "current_password": "WrongPass!1"},
        )

    def test_change_password_rate_limited(self):
        self._assert_sixth_post_429s(
            reverse("core:change_password"),
            {"current_password": "WrongPass!1", "new_password1": "NewP@ss9x", "new_password2": "NewP@ss9x"},
        )

    def test_delete_account_rate_limited(self):
        # Wrong confirmation keeps the account intact while we exhaust the limit.
        self._assert_sixth_post_429s(
            reverse("core:delete_account"),
            {"confirmation": "nope", "password": self.password},
        )
