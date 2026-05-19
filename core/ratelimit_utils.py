"""Real-client-IP helpers for rate limiting and Turnstile.

Production runs behind Nginx → Gunicorn. `REMOTE_ADDR` at the WSGI layer is
the proxy IP, not the visitor's. Reading it directly buckets all visitors
into one rate-limit slot and feeds the wrong IP to Cloudflare for Turnstile
verification.

`get_real_client_ip()` walks the standard forwarded-header order
(`X-Forwarded-For` → `X-Real-IP` → `REMOTE_ADDR`) via `django-ipware`, which
is the conventional implementation. `client_ip_key()` adapts it to the
callable signature `django-ratelimit` expects.
"""

from ipware import get_client_ip


def get_real_client_ip(request):
    """Return the visitor's real IP as a string, or None if undetectable."""
    client_ip, _is_routable = get_client_ip(request)
    return client_ip


def client_ip_key(_group, request):
    """`django-ratelimit` key callable. Returns the real client IP, or
    `"unknown"` when ipware can't determine one (so misconfigured headers
    bucket together instead of all sharing the empty-string key)."""
    return get_real_client_ip(request) or "unknown"
