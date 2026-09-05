"""Tests for the bulk vs interactive enrichment queue split.

Bulk work (seeding runs, enrich_books backfills) routes to ENRICHMENT_BULK_QUEUE;
interactive uploads stay on the default "celery" queue, which drains first under
queue_order_strategy="sorted".
"""

import tempfile
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings

from core.dna_constants import ENRICHMENT_BULK_QUEUE
from core.models import Author, Book

LOCMEM_CACHE = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "queue-routing-tests",
    }
}


class QueueConfigTests(SimpleTestCase):
    """The settings-level invariants the split depends on."""

    def test_queue_order_strategy_is_sorted(self):
        # NOT "priority": with the Redis transport that strategy's order is
        # hash-randomized per worker start (celery/celery#8673) and can
        # silently invert. "sorted" is deterministic.
        self.assertEqual(settings.CELERY_BROKER_TRANSPORT_OPTIONS["queue_order_strategy"], "sorted")

    def test_bulk_queue_name_sorts_after_default(self):
        # Under "sorted", drain priority IS alphabetical order of queue names.
        # Renaming the bulk queue to anything sorting before "celery" would
        # invert priority for every deploy — this must never pass silently.
        self.assertLess("celery", ENRICHMENT_BULK_QUEUE)

    def test_prefetch_multiplier_is_one(self):
        # Keeps at most ~1 already-reserved bulk task ahead of a newly arrived
        # interactive task on the single prod worker.
        self.assertEqual(settings.CELERY_WORKER_PREFETCH_MULTIPLIER, 1)

    def test_compose_worker_commands_consume_bulk_queue(self):
        # Guards compose/constant drift. A worker whose -Q omits the bulk queue
        # strands its messages in Redis (the celeryd_after_setup guard turns
        # that into a startup crash, but catch it here first).
        for compose in ("docker-compose.local.yml", "docker-compose.prod.yml"):
            content = (Path(settings.BASE_DIR) / compose).read_text()
            self.assertIn(f"-Q celery,{ENRICHMENT_BULK_QUEUE}", content, f"{compose} worker command")

    def test_enrichment_tasks_rate_limit_and_ignore_result(self):
        from core.tasks import check_author_mainstream_status_task, enrich_book_task

        # Single shared task name for bulk + interactive: rate_limit is per
        # task NAME, so the 60/m cap covers total external API throughput.
        self.assertEqual(enrich_book_task.rate_limit, "60/m")
        self.assertEqual(check_author_mainstream_status_task.rate_limit, "30/m")
        self.assertTrue(enrich_book_task.ignore_result)
        self.assertTrue(check_author_mainstream_status_task.ignore_result)


@override_settings(CACHES=LOCMEM_CACHE)
class EnrichBooksCommandRoutingTests(TestCase):
    """enrich_books backfills are bulk work — they must not crowd the default queue."""

    def setUp(self):
        cache.clear()

    @patch("core.management.commands.enrich_books.enrich_book_task.apply_async")
    def test_async_enrich_dispatches_to_bulk_queue(self, mock_apply):
        author = Author.objects.create(name="Backlog Author")
        book = Book.objects.create(title="Backlog Book", author=author)

        call_command("enrich_books")

        # Books leaked by earlier TransactionTestCases can also match the
        # command's queryset, so assert on our book's call and on the queue of
        # every call rather than on a total count.
        calls_by_pk = {call.kwargs["args"][0]: call for call in mock_apply.call_args_list}
        self.assertIn(book.pk, calls_by_pk)
        self.assertEqual(calls_by_pk[book.pk].kwargs["kwargs"], {"force": False})
        for call in mock_apply.call_args_list:
            self.assertEqual(call.kwargs["queue"], ENRICHMENT_BULK_QUEUE)

    @patch("core.management.commands.enrich_books.enrich_book_task.apply_async")
    def test_repeat_dispatch_suppressed_until_force(self, mock_apply):
        """A book already queued (dispatch sentinel set) isn't re-dispatched;
        --force bypasses the sentinel for explicit operator re-runs."""
        author = Author.objects.create(name="Sentinel Author")
        book = Book.objects.create(title="Sentinel Book", author=author)

        call_command("enrich_books")
        first_count = sum(1 for c in mock_apply.call_args_list if c.kwargs["args"][0] == book.pk)
        self.assertEqual(first_count, 1)

        call_command("enrich_books")
        second_count = sum(1 for c in mock_apply.call_args_list if c.kwargs["args"][0] == book.pk)
        self.assertEqual(second_count, 1, "second plain run must not re-dispatch a queued book")

        call_command("enrich_books", "--force")
        third_count = sum(1 for c in mock_apply.call_args_list if c.kwargs["args"][0] == book.pk)
        self.assertEqual(third_count, 2, "--force must dispatch despite the sentinel")


GOODREADS_HEADER = (
    "Title,Author,Exclusive Shelf,My Rating,Number of Pages,"
    "Original Publication Year,Date Read,Average Rating,My Review,ISBN13"
)


class SeedFromExportsBulkFlagTests(TestCase):
    """seed_from_exports must run the pipeline in bulk mode."""

    @patch("core.management.commands.seed_from_exports.calculate_full_dna")
    def test_seed_passes_bulk_enrichment_true(self, mock_dna):
        mock_dna.return_value = {"user_stats": {"total_books_read": 1}, "reader_type": "Test"}
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "001__seed.csv"
            csv_path.write_text(
                f"{GOODREADS_HEADER}\nSeeded Book,Seeded Author,read,4,300,2019,2023/05/01,4.1,,\n",
                encoding="utf-8",
            )
            call_command("seed_from_exports", "--dir", tmp)

        self.assertEqual(mock_dna.call_count, 1)
        self.assertTrue(mock_dna.call_args.kwargs.get("bulk_enrichment"))


# One read row (new author -> author-check site; enrichment -> enrich site) and
# one currently-reading row by a DIFFERENT new author (the third dispatch site —
# without a fresh author on this row it silently goes untested).
ROUTING_CSV = (
    f"{GOODREADS_HEADER}\n"
    "Routed Book,Routed Author,read,5,300,2020,2023/01/10,4.0,,\n"
    "CR Book,CR Author,currently-reading,0,200,2021,,4.1,,\n"
)


@override_settings(CACHES=LOCMEM_CACHE)
class CalculateFullDnaRoutingTests(TransactionTestCase):
    """All three async dispatch sites in calculate_full_dna honour bulk_enrichment.

    TransactionTestCase: the pipeline's ThreadPoolExecutor workers use their own
    DB connections and must see each other's committed rows.
    """

    def setUp(self):
        cache.clear()

    def tearDown(self):
        from django.db import connections

        connections.close_all()

    def _run(self, user, **kwargs):
        from core.services.dna import calculate_full_dna

        return calculate_full_dna(ROUTING_CSV, user=user, **kwargs)

    @patch("core.services.book_enrichment_service.enrich_book_from_apis")
    @patch("core.services.dna.generate_vibe_with_llm", return_value=["vibe"])
    @patch("core.tasks.generate_recommendations_task.delay")
    @patch("core.tasks.check_author_mainstream_status_task.apply_async")
    @patch("core.tasks.enrich_book_task.apply_async")
    def test_bulk_routes_all_three_sites_and_skips_inline(
        self, mock_enrich, mock_author, mock_recs, mock_vibe, mock_inline
    ):
        user = User.objects.create_user(username="bulk_user", password="pw")
        from core.cache_utils import safe_cache_set

        safe_cache_set(f"upload_nonce_{user.id}", "nonce-bulk")

        self._run(user, bulk_enrichment=True)

        # Inline enrichment must be fully skipped: bulk API traffic belongs on
        # the rate-limited queue, not in-process.
        mock_inline.assert_not_called()

        # Site 1: enrich_book_task for the read book.
        self.assertEqual(mock_enrich.call_count, 1)
        enrich_call = mock_enrich.call_args
        self.assertEqual(enrich_call.kwargs["queue"], ENRICHMENT_BULK_QUEUE)
        self.assertEqual(
            enrich_call.kwargs["kwargs"], {"user_id": user.id, "upload_nonce": "nonce-bulk"}
        )

        # Sites 2 + 3: author checks for the read row's new author AND the
        # currently-reading row's new author.
        self.assertEqual(mock_author.call_count, 2)
        for call in mock_author.call_args_list:
            self.assertEqual(call.kwargs["queue"], ENRICHMENT_BULK_QUEUE)
            self.assertEqual(call.kwargs["kwargs"], {"user_id": user.id, "upload_nonce": "nonce-bulk"})
        dispatched_author_ids = {call.kwargs["args"][0] for call in mock_author.call_args_list}
        expected_author_ids = set(
            Author.objects.filter(name__in=["Routed Author", "CR Author"]).values_list("id", flat=True)
        )
        self.assertEqual(dispatched_author_ids, expected_author_ids)

        # Bulk runs never dispatch recommendations: seeded users are candidates,
        # not consumers, and the task would land on the interactive queue.
        mock_recs.assert_not_called()

    @patch("core.services.book_enrichment_service.enrich_book_from_apis")
    @patch("core.services.dna.generate_vibe_with_llm", return_value=["vibe"])
    @patch("core.tasks.generate_recommendations_task.delay")
    @patch("core.tasks.check_author_mainstream_status_task.apply_async")
    @patch("core.tasks.enrich_book_task.apply_async")
    def test_bulk_duplicate_dispatch_suppressed_across_runs(
        self, mock_enrich, mock_author, mock_recs, mock_vibe, mock_inline
    ):
        """Overlapping seed CSVs share books; only the first bulk run may
        dispatch enrichment for a given book (the sentinel suppresses the rest
        until the queued task runs and stamps the retry window)."""
        user_a = User.objects.create_user(username="bulk_a", password="pw")
        user_b = User.objects.create_user(username="bulk_b", password="pw")

        self._run(user_a, bulk_enrichment=True)
        self.assertEqual(mock_enrich.call_count, 1)

        self._run(user_b, bulk_enrichment=True)
        self.assertEqual(
            mock_enrich.call_count, 1, "second bulk run must not re-dispatch the still-queued book"
        )

        # An interactive upload of the same book is NOT suppressed — the
        # sentinel is bulk-only (interactive supersede semantics own that path).
        with patch(
            "core.services.dna.enrichment_budget._EnrichmentBudget.has_remaining", return_value=False
        ):
            user_c = User.objects.create_user(username="interactive_c", password="pw")
            self._run(user_c)
        self.assertEqual(mock_enrich.call_count, 2, "interactive dispatch must ignore the bulk sentinel")

    @patch("core.services.dna.enrichment_budget._EnrichmentBudget.has_remaining", return_value=False)
    @patch("core.services.book_enrichment_service.enrich_book_from_apis")
    @patch("core.services.dna.generate_vibe_with_llm", return_value=["vibe"])
    @patch("core.tasks.generate_recommendations_task.delay")
    @patch("core.tasks.check_author_mainstream_status_task.apply_async")
    @patch("core.tasks.enrich_book_task.apply_async")
    def test_interactive_default_stays_on_default_queue(
        self, mock_enrich, mock_author, mock_recs, mock_vibe, mock_inline, mock_budget
    ):
        user = User.objects.create_user(username="interactive_user", password="pw")
        from core.cache_utils import safe_cache_set

        safe_cache_set(f"upload_nonce_{user.id}", "nonce-int")

        self._run(user)

        # user_id/upload_nonce survived the .delay -> .apply_async conversion...
        self.assertEqual(mock_enrich.call_count, 1)
        self.assertEqual(
            mock_enrich.call_args.kwargs["kwargs"], {"user_id": user.id, "upload_nonce": "nonce-int"}
        )
        self.assertEqual(mock_author.call_count, 2)
        for call in mock_author.call_args_list:
            self.assertEqual(call.kwargs["kwargs"], {"user_id": user.id, "upload_nonce": "nonce-int"})

        # ...and nothing carried a queue override: interactive work stays on
        # the default "celery" queue.
        for call in [mock_enrich.call_args] + mock_author.call_args_list:
            self.assertNotIn("queue", call.kwargs)

        # Interactive saves DO dispatch recommendations (bulk is the exception).
        mock_recs.assert_called_once()
