from unittest.mock import patch

from django.contrib.auth.models import User
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponse
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse

from core.views.pages import about_view


class FlashMessageEscapingTests(TestCase):
    """base.html autoescapes flash messages; only messages built as safe HTML render as markup."""

    def test_plain_message_markup_is_escaped(self):
        request = RequestFactory().get("/about/")
        SessionMiddleware(lambda r: HttpResponse()).process_request(request)
        request.user = User(username="anon")
        request._messages = FallbackStorage(request)
        request._messages.add(20, "<script>alert(1)</script>")

        html = about_view(request).content.decode()

        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)

    @patch("core.views.profile.track_profile_made_public")
    def test_public_profile_link_message_still_renders_as_link(self, _track):
        user = User.objects.create_user(username="linkuser", email="link@example.com", password="pw-123456!")
        client = Client()
        client.force_login(user)

        client.post(reverse("core:update_privacy"), {"is_public": "true"})
        response = client.get(reverse("core:about"))
        html = response.content.decode()

        self.assertIn("Your profile is now public!", html)
        self.assertIn(f'href="{reverse("core:public_profile", kwargs={"username": "linkuser"})}"', html)
        self.assertNotIn("&lt;a", html)
