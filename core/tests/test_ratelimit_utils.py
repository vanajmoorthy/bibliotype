"""Tests for `core.ratelimit_utils` — confirms the real-client-IP helpers
read forwarded headers correctly behind Nginx and produce distinct cache
keys for distinct clients."""

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse

from core.ratelimit_utils import client_ip_key, get_real_client_ip


class RealClientIpTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_reads_x_forwarded_for(self):
        request = self.factory.get("/", HTTP_X_FORWARDED_FOR="203.0.113.7, 10.0.0.1")
        self.assertEqual(get_real_client_ip(request), "203.0.113.7")

    def test_prefers_x_real_ip_over_x_forwarded_for(self):
        # Defends against XFF spoofing: a client can prepend a forged value
        # to X-Forwarded-For because Nginx *appends* rather than replaces.
        # X-Real-IP is set by Nginx to $remote_addr (the TCP peer) and
        # cannot be spoofed through the proxy.
        request = self.factory.get(
            "/",
            HTTP_X_FORWARDED_FOR="1.2.3.4, 10.0.0.1",  # attacker-forged leftmost
            HTTP_X_REAL_IP="203.0.113.99",              # Nginx-set, trustworthy
        )
        self.assertEqual(get_real_client_ip(request), "203.0.113.99")

    def test_falls_back_to_remote_addr_when_no_forwarded_headers(self):
        request = self.factory.get("/", REMOTE_ADDR="198.51.100.42")
        self.assertEqual(get_real_client_ip(request), "198.51.100.42")

    def test_client_ip_key_returns_string_for_ratelimit(self):
        request = self.factory.get("/", HTTP_X_FORWARDED_FOR="203.0.113.7")
        # django-ratelimit signature: key_fn(group, request) -> str
        self.assertEqual(client_ip_key("login", request), "203.0.113.7")

    def test_client_ip_key_returns_unknown_sentinel_when_ip_undetectable(self):
        # No headers + no REMOTE_ADDR — ipware returns None
        request = self.factory.get("/")
        request.META.pop("REMOTE_ADDR", None)
        self.assertEqual(client_ip_key("login", request), "unknown")


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "real-ip-ratelimit-tests",
        }
    },
    RATELIMIT_ENABLE=True,
)
class LoginRateLimitKeyedOnRealIpTests(TestCase):
    """End-to-end: two distinct `X-Forwarded-For` IPs get two separate
    rate-limit buckets even when sharing the same `REMOTE_ADDR` (the
    Nginx proxy IP). Without `client_ip_key`, both would share one
    bucket and the 5/m limit becomes site-global."""

    def setUp(self):
        cache.clear()
        self.password = "validpassword123!"
        self.user = User.objects.create_user(
            username="realipuser",
            email="realip@example.com",
            password=self.password,
        )

    def tearDown(self):
        cache.clear()

    def test_two_distinct_real_ips_get_separate_buckets(self):
        login_url = reverse("core:login")
        bad_payload = {"username": "realip@example.com", "password": "wrongpassword"}

        client_a = Client(HTTP_X_FORWARDED_FOR="203.0.113.10")
        client_b = Client(HTTP_X_FORWARDED_FOR="203.0.113.20")

        # Client A burns its bucket with 5 misses, then trips the limit on the 6th.
        for _ in range(5):
            self.assertEqual(client_a.post(login_url, bad_payload).status_code, 200)
        self.assertEqual(client_a.post(login_url, bad_payload).status_code, 429)

        # Client B (different real IP, same upstream proxy) must still get 200,
        # confirming the bucket is per-real-IP, not per-REMOTE_ADDR.
        self.assertEqual(client_b.post(login_url, bad_payload).status_code, 200)
