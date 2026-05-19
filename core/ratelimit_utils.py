"""Real-client-IP helpers for rate limiting and Turnstile.

Production runs behind Nginx → Gunicorn. `REMOTE_ADDR` at the WSGI layer is
the proxy IP, not the visitor's. Reading it directly buckets all visitors
into one rate-limit slot and feeds the wrong IP to Cloudflare for Turnstile
verification.

`get_real_client_ip()` prefers `X-Real-IP` (Nginx sets it to `$remote_addr`,
the TCP peer — clients can't spoof it through the proxy). Falls back to
`django-ipware` (which walks `X-Forwarded-For` → `REMOTE_ADDR`) for
environments where `X-Real-IP` isn't set. Important: `X-Forwarded-For`
alone is spoofable behind Nginx because Nginx *appends* via
`proxy_add_x_forwarded_for` rather than replacing — an attacker can prepend
their own value to defeat a per-IP rate limit. Preferring `X-Real-IP`
closes that channel.

`client_ip_key()` adapts the helper to the callable signature
`django-ratelimit` expects.
"""

from ipware import get_client_ip


def get_real_client_ip(request):
    """Return the visitor's real IP as a string, or None if undetectable.

    Order:
      1. `X-Real-IP` (unspoofable when set by trusted Nginx upstream)
      2. ipware fallback (`X-Forwarded-For` → `REMOTE_ADDR`)
    """
    real_ip = request.META.get("HTTP_X_REAL_IP")
    if real_ip:
        return real_ip
    client_ip, _is_routable = get_client_ip(request)
    return client_ip


def client_ip_key(_group, request):
    """`django-ratelimit` key callable. Returns the real client IP, or
    `"unknown"` when ipware can't determine one (so misconfigured headers
    bucket together instead of all sharing the empty-string key)."""
    return get_real_client_ip(request) or "unknown"
