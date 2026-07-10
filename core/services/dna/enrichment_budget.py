"""Wall-clock budget for inline book enrichment during DNA calculation.

Inline enrichment (quick_mode API calls made directly from the book-sync
workers) must not stall an upload indefinitely: after the budget is exhausted,
remaining books fall back to async `enrich_book_task` dispatch.
"""

import threading
import time

# Global wall-clock limit for inline enrichment per upload. After this many
# seconds of inline enrichment, remaining books are enriched async instead.
INLINE_ENRICHMENT_BUDGET_SECONDS = 90


class _EnrichmentBudget:
    """Lazily-started wall-clock budget, safe under the max_workers=8 sync pool.

    The timer starts on the FIRST `has_remaining()` call, not at construction,
    so CSV parsing and DB writes for already-enriched books don't consume the
    budget. A lock guarantees the timer starts exactly once even when several
    workers race the first call. `has_remaining()` itself is only loosely
    ordered after start — at the budget boundary a couple of in-flight workers
    may each let one extra book through, which is acceptable.
    """

    def __init__(self, max_seconds=INLINE_ENRICHMENT_BUDGET_SECONDS):
        self._max = max_seconds
        self._started_at = None  # Lazily initialized on first enrichment attempt
        self._lock = threading.Lock()

    def has_remaining(self):
        if self._started_at is None:
            with self._lock:
                if self._started_at is None:
                    self._started_at = time.monotonic()  # Start timing on first actual enrichment
                    return True
        return (time.monotonic() - self._started_at) < self._max
