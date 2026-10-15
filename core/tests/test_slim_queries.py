"""Query-shape and API-throttle guards from the 2026-10-11 perf audit.

- Similar-user candidate lists are cached in Redis, so they must hold bare
  (id, username) User rows, not users with their whole profile JSON attached.
- The sitemap must not load full UserProfile rows, and lists newest first.
- Gemini calls carry a request timeout.
- Inline enrichment during an upload is capped at 2 concurrent API calls.
"""

import threading
import time
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import Author, Book, Genre, UserBook, UserProfile

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "slim-queries-tests"}}


def _assert_slim_user(test, user):
    test.assertNotIn("userprofile", user._state.fields_cache)
    test.assertIn("password", user.get_deferred_fields())
    test.assertIn("email", user.get_deferred_fields())


@override_settings(CACHES=LOCMEM)
class SimilarUserQueryShapeTests(TestCase):
    def setUp(self):
        cache.clear()
        author = Author.objects.create(name="Slim Author")
        genre = Genre.objects.create(name="fantasy")
        self.book = Book.objects.create(title="Slim Book", author=author, average_rating=4.5)
        self.book.genres.add(genre)
        self.book2 = Book.objects.create(title="Slim Book 2", author=author, average_rating=4.5)
        self.book2.genres.add(genre)

        self.me = User.objects.create_user(username="me", password="pw")
        self.other = User.objects.create_user(username="other", password="pw")
        for u in (self.me, self.other):
            u.userprofile.dna_data = {"top_genres": [("fantasy", 5)], "top_authors": []}
            u.userprofile.is_public = True
            u.userprofile.visible_in_recommendations = True
            u.userprofile.save()
            UserBook.objects.create(user=u, book=self.book, user_rating=5)
        UserBook.objects.create(user=self.other, book=self.book2, user_rating=5, is_top_book=True, top_book_position=1)

    def test_find_similar_users_returns_slim_users(self):
        from core.services.user_similarity_service import _bulk_build_user_contexts, find_similar_users

        ctx = _bulk_build_user_contexts([self.me.id])[self.me.id]
        result = find_similar_users(ctx, self.me.id, top_n=5, min_similarity=0.0)
        self.assertTrue(result)
        for u, _ in result:
            _assert_slim_user(self, u)
        for u, _ in cache.get(f"similar_users_{self.me.id}"):
            _assert_slim_user(self, u)


class SitemapTests(TestCase):
    def test_sitemap_lists_public_profiles_newest_first(self):
        old = User.objects.create_user(username="oldreader", password="pw")
        new = User.objects.create_user(username="newreader", password="pw")
        hidden = User.objects.create_user(username="hiddenreader", password="pw")
        for u, public in ((old, True), (new, True), (hidden, False)):
            UserProfile.objects.filter(user=u).update(dna_data={"x": 1}, is_public=public)
        UserProfile.objects.filter(user=old).update(last_updated=timezone.now() - timedelta(days=5))
        UserProfile.objects.filter(user=new).update(
            last_updated=timezone.now(), recommendations_generated_at=timezone.now() - timedelta(days=2)
        )

        response = self.client.get(reverse("core:sitemap_xml"))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        new_url = reverse("core:public_profile", kwargs={"username": "newreader"})
        old_url = reverse("core:public_profile", kwargs={"username": "oldreader"})
        self.assertIn(new_url, body)
        self.assertIn(old_url, body)
        self.assertNotIn("hiddenreader", body)
        self.assertLess(body.index(new_url), body.index(old_url))
        self.assertIn((timezone.now() - timedelta(days=2)).strftime("%Y-%m-%d"), body)


@override_settings(LLM_MODELS="groq/openai/gpt-oss-120b", GROQ_API_KEY="test-key", LLM_TIMEOUT_SECONDS=30)
class LLMTimeoutTests(TestCase):
    """Both LLM calls carry a request timeout, so a hung provider can't stall a worker."""

    def _response(self, content):
        return MagicMock(choices=[MagicMock(message=MagicMock(content=content))])

    @patch("core.services._llm.completion")
    def test_vibe_call_has_timeout(self, mock_completion):
        from core.services.llm_service import generate_vibe_with_llm

        mock_completion.return_value = self._response('{"vibe_phrases": ["a", "b"]}')
        generate_vibe_with_llm({"top_genres": [], "top_authors": []})
        self.assertEqual(mock_completion.call_args.kwargs["timeout"], 30)

    @patch("core.services._llm.completion")
    def test_publisher_call_has_timeout(self, mock_completion):
        from core.services.publisher_service import research_publisher_identity

        mock_completion.return_value = self._response('{"is_mainstream": true}')
        session = MagicMock()
        session.get.return_value = MagicMock(
            status_code=200, json=lambda: {"extract": "An imprint.", "title": "Viking"}
        )
        research_publisher_identity("Viking Press", session)
        self.assertEqual(mock_completion.call_args.kwargs["timeout"], 30)


@override_settings(CELERY_TASK_ALWAYS_EAGER=True, CELERY_TASK_EAGER_PROPAGATES=True, CACHES=LOCMEM)
class InlineEnrichmentConcurrencyTests(TransactionTestCase):
    @patch("core.tasks.generate_recommendations_task.delay")
    @patch("core.tasks.check_author_mainstream_status_task.apply_async")
    @patch("core.tasks.enrich_book_task.apply_async")
    @patch("core.services.dna.vibe.generate_vibe_with_llm", return_value=["vibe"])
    @patch("core.services.book_enrichment_service.enrich_book_from_apis")
    def test_inline_enrichment_capped_at_two_concurrent(self, mock_enrich, *_):
        from core.services.dna import calculate_full_dna

        lock = threading.Lock()
        state = {"active": 0, "peak": 0}

        def slow_enrich(*args, **kwargs):
            with lock:
                state["active"] += 1
                state["peak"] = max(state["peak"], state["active"])
            time.sleep(0.1)
            with lock:
                state["active"] -= 1

        mock_enrich.side_effect = slow_enrich

        header = "Title,Author,Exclusive Shelf,My Rating,Number of Pages,Original Publication Year,Date Read,Average Rating,My Review,ISBN13"
        rows = [
            f"Throttle Book {i},Throttle Author {i},read,4,200,2020,2023/01/{i + 1:02d},4.0,,97800000001{i:02d}"
            for i in range(8)
        ]
        calculate_full_dna("\n".join([header, *rows]))

        self.assertEqual(mock_enrich.call_count, 8)
        self.assertLessEqual(state["peak"], 2)

    @patch("core.tasks.generate_recommendations_task.delay")
    @patch("core.tasks.check_author_mainstream_status_task.apply_async")
    @patch("core.tasks.enrich_book_task.apply_async")
    @patch("core.services.dna.vibe.generate_vibe_with_llm", return_value=["vibe"])
    @patch("core.services.book_enrichment_service.enrich_book_from_apis")
    @patch("core.services.dna.book_sync._EnrichmentBudget")
    def test_budget_rechecked_after_waiting_for_a_slot(self, mock_budget, mock_enrich, _vibe, mock_enrich_task, *_):
        from core.services.dna import calculate_full_dna

        # Budget has time left when the worker queues, but runs out while it waits.
        mock_budget.return_value.has_remaining.side_effect = [True, False]

        header = "Title,Author,Exclusive Shelf,My Rating,Number of Pages,Original Publication Year,Date Read,Average Rating,My Review,ISBN13"
        calculate_full_dna(f"{header}\nLate Book,Late Author,read,4,200,2020,2023/01/01,4.0,,9780000000999")

        mock_enrich.assert_not_called()
        mock_enrich_task.assert_called_once()


@override_settings(CELERY_TASK_ALWAYS_EAGER=True, CELERY_TASK_EAGER_PROPAGATES=True, CACHES=LOCMEM)
class WorkerConnectionCleanupTests(TransactionTestCase):
    """The DNA row pool's per-thread DB connections are closed when it shuts down,
    not left for the cyclic GC (each upload would otherwise hold up to 8 open)."""

    @staticmethod
    def _open_connections():
        from django.db import connection

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND pid <> pg_backend_pid()"
            )
            return cursor.fetchone()[0]

    @patch("core.tasks.generate_recommendations_task.delay")
    @patch("core.tasks.check_author_mainstream_status_task.apply_async")
    @patch("core.tasks.enrich_book_task.apply_async")
    @patch("core.services.dna.vibe.generate_vibe_with_llm", return_value=["vibe"])
    @patch("core.services.book_enrichment_service.enrich_book_from_apis")
    def test_worker_connections_closed_after_sync(self, *_):
        import gc

        from core.services.dna import calculate_full_dna

        header = "Title,Author,Exclusive Shelf,My Rating,Number of Pages,Original Publication Year,Date Read,Average Rating,My Review,ISBN13"
        rows = [
            f"Pool Book {i},Pool Author {i},read,4,200,2020,2023/01/{i + 1:02d},4.0,,97800000002{i:02d}"
            for i in range(16)
        ]
        before = self._open_connections()
        gc.disable()  # prove the close is explicit, not a lucky GC pass
        try:
            calculate_full_dna("\n".join([header, *rows]))
            # Backends leave pg_stat_activity a moment after the client disconnects.
            deadline = time.monotonic() + 3
            while self._open_connections() > before and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertEqual(self._open_connections(), before)
        finally:
            gc.enable()
