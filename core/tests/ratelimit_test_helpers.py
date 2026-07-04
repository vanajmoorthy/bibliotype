"""Helpers for django-ratelimit tests.

django-ratelimit counts requests inside fixed clock windows (e.g. `5/m` uses
the current minute bucket, computed from `time.time()` in
`django_ratelimit.core`). On a slow CI runner a burst of test requests can
straddle a minute boundary, silently resetting the counter and flaking the
"Nth request returns 429" assertion (seen on main CI run 28718182397).

`frozen_ratelimit_window()` pins the module's clock so every request in the
`with` block deterministically lands in the same window. It swaps only the
`time` name inside django_ratelimit.core's namespace — the real `time` module
is untouched for everything else.
"""

from types import SimpleNamespace
from unittest.mock import patch

_FROZEN_CLOCK = SimpleNamespace(time=lambda: 1_700_000_000.0)


def frozen_ratelimit_window():
    return patch("django_ratelimit.core.time", _FROZEN_CLOCK)
