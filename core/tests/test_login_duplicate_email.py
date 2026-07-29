"""Login must not 500 when duplicate case-variant emails exist (F3).

User.email is not unique at the DB level, so historical data can hold two
accounts with case-variant addresses. login_view now selects the earliest
match deterministically instead of calling .get() (which raised
MultipleObjectsReturned → HTTP 500 for both accounts).
"""

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
)
class LoginDuplicateEmailTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client()
        self.url = reverse("core:login")
        # Two accounts whose emails differ only by case.
        User.objects.create_user(username="dupfirst", email="Dup@example.com", password="firstpw123!")
        User.objects.create_user(username="dupsecond", email="dup@example.com", password="secondpw123!")

    def tearDown(self):
        cache.clear()

    def test_login_page_survives_duplicate_emails(self):
        response = self.client.post(self.url, {"username": "dup@example.com", "password": "wrongpassword"})
        # Re-renders the form with an error rather than 500-ing on MultipleObjectsReturned.
        self.assertEqual(response.status_code, 200)

    def test_earliest_account_can_still_log_in(self):
        response = self.client.post(self.url, {"username": "dup@example.com", "password": "firstpw123!"})
        # The earliest (ordered by id) account authenticates and is redirected.
        self.assertEqual(response.status_code, 302)
