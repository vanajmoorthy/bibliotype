"""End-to-end integration tests for StoryGraph upload flow.

Tests that the full StoryGraph CSV → DNA pipeline produces correct results:
- Tag-to-genre mapping pre-enrichment
- Mood/pace distributions in DNA
- Read Count drives Comfort Rereader scoring
- Enrichment completion detection via google_books_last_checked
"""

from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import Book, UserBook


SG_HEADER = (
    "Title,Authors,Contributors,ISBN/UID,Format,Read Status,"
    "Date Added,Last Date Read,Dates Read,Read Count,Moods,Pace,"
    "Character- or Plot-Driven?,Strong Character Development?,"
    "Loveable Characters?,Diverse Characters?,Flawed Characters?,"
    "Star Rating,Review,Content Warnings,Content Warning Description,Tags,Owned?"
)


def _sg_row(
    title,
    author,
    isbn="",
    rating="4.0",
    moods="",
    pace="medium",
    read_count="1",
    tags="",
    date_read="2024/06/01",
):
    """Build a StoryGraph CSV row with given values."""
    return (
        f'"{title}","{author}",,{isbn},Paperback,read,2024/01/01,{date_read},,'
        f'{read_count},"{moods}",{pace},,,,,,{rating},,,,"{tags}",Yes'
    )


@override_settings(
    CELERY_TASK_ALWAYS_EAGER=True,
    CELERY_TASK_STORE_EAGER_RESULT=True,
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "storygraph-tests",
        }
    },
)
class StoryGraphUploadFlowTests(TransactionTestCase):
    """Verify full StoryGraph upload pipeline produces correct DNA output."""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="sg_user", email="sg@test.com", password="testpass123"
        )
        self.client.force_login(self.user)

    def tearDown(self):
        from django.db import connections

        for conn in connections.all():
            if conn.connection is not None:
                conn.close()
        connections.close_all()
        super().tearDown()

    def _upload(self, rows):
        """Helper: upload a StoryGraph CSV with given rows and run the task synchronously."""
        csv_content = (SG_HEADER + "\n" + "\n".join(rows)).encode("utf-8")
        csv_file = SimpleUploadedFile("storygraph.csv", csv_content, content_type="text/csv")
        return self.client.post(reverse("core:upload"), {"csv_file": csv_file})

    @patch("core.services.dna_analyser.generate_vibe_with_llm", return_value=["a vibe"])
    @patch("core.services.book_enrichment_service.enrich_book_from_apis")
    def test_storygraph_csv_creates_dna_with_csv_source(self, mock_enrich, mock_vibe):
        """Uploading a StoryGraph CSV produces DNA with csv_source='storygraph'."""
        self._upload([
            _sg_row("Book A", "Author A", isbn="9780000000001", tags="sci-fi"),
            _sg_row("Book B", "Author B", isbn="9780000000002", tags="fantasy"),
        ])

        self.user.refresh_from_db()
        dna = self.user.userprofile.dna_data
        self.assertIsNotNone(dna)
        self.assertEqual(dna["csv_source"], "storygraph")
        self.assertEqual(dna["user_stats"]["total_books_read"], 2)

    @patch("core.services.dna_analyser.generate_vibe_with_llm", return_value=["a vibe"])
    @patch("core.services.book_enrichment_service.enrich_book_from_apis")
    def test_storygraph_tags_apply_canonical_genres_pre_enrichment(self, mock_enrich, mock_vibe):
        """Tags like 'sci-fi' produce canonical genres on Books before enrichment runs."""
        self._upload([
            _sg_row("SciFi Book", "A1", isbn="9780000001001", tags="sci-fi"),
            _sg_row("Fantasy Book", "A2", isbn="9780000001002", tags="fantasy"),
            _sg_row("Dystopian Book", "A3", isbn="9780000001003", tags="dystopian"),
        ])

        # Verify books got genres from tags (without API enrichment)
        scifi = Book.objects.get(title="SciFi Book")
        fantasy = Book.objects.get(title="Fantasy Book")
        dystopian = Book.objects.get(title="Dystopian Book")

        self.assertIn("science fiction", set(scifi.genres.values_list("name", flat=True)))
        self.assertIn("fantasy", set(fantasy.genres.values_list("name", flat=True)))
        self.assertIn("dystopian", set(dystopian.genres.values_list("name", flat=True)))

    @patch("core.services.dna_analyser.generate_vibe_with_llm", return_value=["a vibe"])
    @patch("core.services.book_enrichment_service.enrich_book_from_apis")
    def test_storygraph_mood_distribution_in_dna(self, mock_enrich, mock_vibe):
        """Mood column populates dna['mood_distribution'] with correct counts."""
        self._upload([
            _sg_row("A", "A1", isbn="9780000002001", moods="dark, reflective"),
            _sg_row("B", "A2", isbn="9780000002002", moods="dark, adventurous"),
            _sg_row("C", "A3", isbn="9780000002003", moods="lighthearted"),
        ])

        self.user.refresh_from_db()
        moods = dict(self.user.userprofile.dna_data.get("mood_distribution", []))
        self.assertEqual(moods["dark"], 2)
        self.assertEqual(moods["reflective"], 1)
        self.assertEqual(moods["adventurous"], 1)
        self.assertEqual(moods["lighthearted"], 1)

    @patch("core.services.dna_analyser.generate_vibe_with_llm", return_value=["a vibe"])
    @patch("core.services.book_enrichment_service.enrich_book_from_apis")
    def test_storygraph_pace_distribution_in_dna(self, mock_enrich, mock_vibe):
        """Pace column populates dna['pace_distribution'] with correct counts."""
        self._upload([
            _sg_row("A", "A1", isbn="9780000003001", pace="slow"),
            _sg_row("B", "A2", isbn="9780000003002", pace="fast"),
            _sg_row("C", "A3", isbn="9780000003003", pace="slow"),
            _sg_row("D", "A4", isbn="9780000003004", pace="medium"),
        ])

        self.user.refresh_from_db()
        pace = dict(self.user.userprofile.dna_data.get("pace_distribution", []))
        self.assertEqual(pace["slow"], 2)
        self.assertEqual(pace["fast"], 1)
        self.assertEqual(pace["medium"], 1)

    @patch("core.services.dna_analyser.generate_vibe_with_llm", return_value=["a vibe"])
    @patch("core.services.book_enrichment_service.enrich_book_from_apis")
    def test_storygraph_read_count_drives_comfort_rereader(self, mock_enrich, mock_vibe):
        """Read Count > 1 contributes 3 points per reread to Comfort Rereader."""
        self._upload([
            _sg_row("A", "A1", isbn="9780000004001", read_count="3", tags="sci-fi"),
            _sg_row("B", "A2", isbn="9780000004002", read_count="2", tags="fantasy"),
            _sg_row("C", "A3", isbn="9780000004003", read_count="1", tags="thriller"),
        ])

        self.user.refresh_from_db()
        scores = self.user.userprofile.dna_data.get("reader_type_scores", {})
        # 2 books with Read Count > 1 → 2 × 3 = 6 points for Comfort Rereader
        self.assertEqual(scores.get("Comfort Rereader"), 6)


@override_settings(
    CELERY_TASK_ALWAYS_EAGER=True,
    CELERY_TASK_STORE_EAGER_RESULT=True,
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "enrichment-completion-tests",
        }
    },
)
class EnrichmentCompletionDetectionTests(TransactionTestCase):
    """Verify the enrichment status endpoint and dashboard view detect completion correctly."""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="ec_user", email="ec@test.com", password="testpass123"
        )
        self.client.force_login(self.user)
        self.profile = self.user.userprofile
        self.profile.dna_data = {"user_stats": {"total_pages_read": 100, "avg_book_length": 100}}
        self.profile.save()

    def tearDown(self):
        from django.db import connections

        for conn in connections.all():
            if conn.connection is not None:
                conn.close()
        connections.close_all()
        super().tearDown()

    def _create_book(self, title, isbn, attempted=False, page_count=None, publish_year=None):
        """Create a book and link to test user via UserBook."""
        from core.models import Author

        author, _ = Author.objects.get_or_create(name=f"Author of {title}")
        book = Book.objects.create(
            title=title,
            normalized_title=Book._normalize_title(title),
            author=author,
            isbn13=isbn,
            google_books_last_checked=timezone.now() if attempted else None,
            page_count=page_count,
            publish_year=publish_year,
        )
        UserBook.objects.create(user=self.user, book=book)
        return book

    def test_pending_when_some_books_unattempted(self):
        """Endpoint returns pending=true when any user book lacks google_books_last_checked."""
        self._create_book("Book A", "9789000000001", attempted=True)
        self._create_book("Book B", "9789000000002", attempted=False)

        response = self.client.get(reverse("core:api_enrichment_status"))
        data = response.json()
        self.assertTrue(data["pending"])
        # 1 of 2 attempted = 50%
        self.assertEqual(data["percent"], 50)

    def test_complete_when_all_books_attempted(self):
        """Endpoint returns pending=false when all user books have google_books_last_checked set."""
        self._create_book("Book A", "9789000000003", attempted=True)
        self._create_book("Book B", "9789000000004", attempted=True)

        response = self.client.get(reverse("core:api_enrichment_status"))
        data = response.json()
        self.assertFalse(data["pending"])

    def test_pending_false_when_no_books(self):
        """Endpoint returns pending=false when user has no books."""
        response = self.client.get(reverse("core:api_enrichment_status"))
        data = response.json()
        self.assertFalse(data["pending"])

    def test_anonymous_user_redirected(self):
        """Endpoint requires login (returns 302 to login)."""
        self.client.logout()
        response = self.client.get(reverse("core:api_enrichment_status"))
        self.assertEqual(response.status_code, 302)

    def test_percent_calculation_uses_attempted_count(self):
        """Percent reflects books with google_books_last_checked set, not just genres present."""
        self._create_book("A", "9789000000010", attempted=True)
        self._create_book("B", "9789000000011", attempted=True)
        self._create_book("C", "9789000000012", attempted=True)
        self._create_book("D", "9789000000013", attempted=False)

        response = self.client.get(reverse("core:api_enrichment_status"))
        data = response.json()
        self.assertEqual(data["percent"], 75)
        self.assertEqual(data["total"], 4)

    def test_pages_any_missing_false_when_all_books_have_page_count(self):
        """Goodreads case: every book has page_count from the CSV — no length banner needed."""
        self._create_book("A", "9789000000020", attempted=False, page_count=300, publish_year=2020)
        self._create_book("B", "9789000000021", attempted=False, page_count=200, publish_year=2021)

        response = self.client.get(reverse("core:api_enrichment_status"))
        data = response.json()
        self.assertTrue(data["pending"])  # global enrichment still running
        self.assertFalse(data["pages_any_missing"])  # but page data is complete

    def test_pages_any_missing_true_when_any_book_missing_page_count(self):
        """One book missing page_count → length banner should show."""
        self._create_book("A", "9789000000022", attempted=False, page_count=300)
        self._create_book("B", "9789000000023", attempted=False, page_count=None)

        response = self.client.get(reverse("core:api_enrichment_status"))
        data = response.json()
        self.assertTrue(data["pages_any_missing"])

    def test_year_any_missing_false_when_all_books_have_publish_year(self):
        """Goodreads case: every book has publish_year from the CSV — no year banner needed."""
        self._create_book("A", "9789000000024", attempted=False, page_count=300, publish_year=1999)
        self._create_book("B", "9789000000025", attempted=False, page_count=200, publish_year=2010)

        response = self.client.get(reverse("core:api_enrichment_status"))
        data = response.json()
        self.assertTrue(data["pending"])
        self.assertFalse(data["year_any_missing"])

    def test_year_any_missing_true_when_any_book_missing_publish_year(self):
        """One book missing publish_year → year banner should show."""
        self._create_book("A", "9789000000026", attempted=False, publish_year=2020)
        self._create_book("B", "9789000000027", attempted=False, publish_year=None)

        response = self.client.get(reverse("core:api_enrichment_status"))
        data = response.json()
        self.assertTrue(data["year_any_missing"])


@override_settings(
    CELERY_TASK_ALWAYS_EAGER=True,
    CELERY_TASK_STORE_EAGER_RESULT=True,
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "upload-nonce-tests",
        }
    },
)
class UploadNonceTests(TransactionTestCase):
    """Verify upload nonce is set in cache before task dispatch (prevents race condition)."""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="nonce_user", email="nonce@test.com", password="testpass123"
        )
        self.client.force_login(self.user)

    def tearDown(self):
        from django.db import connections

        for conn in connections.all():
            if conn.connection is not None:
                conn.close()
        connections.close_all()
        super().tearDown()

    @patch("core.services.dna_analyser.generate_vibe_with_llm", return_value=["a vibe"])
    @patch("core.services.book_enrichment_service.enrich_book_from_apis")
    def test_upload_sets_nonce_in_cache(self, mock_enrich, mock_vibe):
        """Upload writes upload_nonce_{user_id} to cache."""
        from core.cache_utils import safe_cache_get

        csv_content = (
            "Title,Author,Exclusive Shelf,My Rating\n"
            "Test Book,Test Author,read,4\n"
        ).encode("utf-8")
        csv_file = SimpleUploadedFile("goodreads.csv", csv_content, content_type="text/csv")

        self.client.post(reverse("core:upload"), {"csv_file": csv_file})

        nonce = safe_cache_get(f"upload_nonce_{self.user.id}")
        self.assertIsNotNone(nonce)
        # nonce must be a uuid4 string
        self.assertEqual(len(nonce), 36)  # uuid4 hex is 36 chars including dashes

    @patch("core.services.dna_analyser.generate_vibe_with_llm", return_value=["a vibe"])
    @patch("core.services.book_enrichment_service.enrich_book_from_apis")
    def test_reupload_replaces_nonce(self, mock_enrich, mock_vibe):
        """Re-uploading replaces the cached nonce so old enrichment tasks exit early."""
        from core.cache_utils import safe_cache_get

        def upload():
            csv_content = (
                "Title,Author,Exclusive Shelf,My Rating\n"
                "Test Book,Test Author,read,4\n"
            ).encode("utf-8")
            csv_file = SimpleUploadedFile("g.csv", csv_content, content_type="text/csv")
            self.client.post(reverse("core:upload"), {"csv_file": csv_file})

        upload()
        first_nonce = safe_cache_get(f"upload_nonce_{self.user.id}")
        upload()
        second_nonce = safe_cache_get(f"upload_nonce_{self.user.id}")

        self.assertIsNotNone(first_nonce)
        self.assertIsNotNone(second_nonce)
        self.assertNotEqual(first_nonce, second_nonce)


@override_settings(
    CELERY_TASK_ALWAYS_EAGER=True,
    CELERY_TASK_STORE_EAGER_RESULT=True,
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "concurrent-upload-tests",
        }
    },
)
class ConcurrentUploadRevokeTests(TransactionTestCase):
    """Re-uploading while a prior DNA task is still pending revokes the prior task.

    Without this, the prior task keeps running and contends on Postgres row locks
    with the new task, stalling the new task's progress bar at ~50%.
    """

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="revoke_user", email="revoke@test.com", password="testpass123"
        )
        self.client.force_login(self.user)

    def tearDown(self):
        from django.db import connections

        for conn in connections.all():
            if conn.connection is not None:
                conn.close()
        connections.close_all()
        super().tearDown()

    def _upload(self):
        csv_content = (
            "Title,Author,Exclusive Shelf,My Rating\n"
            "Test Book,Test Author,read,4\n"
        ).encode("utf-8")
        csv_file = SimpleUploadedFile("g.csv", csv_content, content_type="text/csv")
        return self.client.post(reverse("core:upload"), {"csv_file": csv_file})

    @patch("core.views.AsyncResult")
    @patch("core.services.dna_analyser.generate_vibe_with_llm", return_value=["a vibe"])
    @patch("core.services.book_enrichment_service.enrich_book_from_apis")
    def test_upload_revokes_prior_pending_task(self, mock_enrich, mock_vibe, mock_async_result):
        """If a prior DNA task is still pending, the new upload revokes it."""
        prior_id = "prior-task-id-123"
        self.user.userprofile.pending_dna_task_id = prior_id
        self.user.userprofile.save()

        prior_result = mock_async_result.return_value
        prior_result.ready.return_value = False

        self._upload()

        mock_async_result.assert_any_call(prior_id)
        prior_result.revoke.assert_called_once_with(terminate=True, signal="SIGTERM")

    @patch("core.views.AsyncResult")
    @patch("core.services.dna_analyser.generate_vibe_with_llm", return_value=["a vibe"])
    @patch("core.services.book_enrichment_service.enrich_book_from_apis")
    def test_upload_does_not_revoke_completed_task(self, mock_enrich, mock_vibe, mock_async_result):
        """If the prior task has already finished, no revoke is attempted."""
        prior_id = "prior-task-id-456"
        self.user.userprofile.pending_dna_task_id = prior_id
        self.user.userprofile.save()

        prior_result = mock_async_result.return_value
        prior_result.ready.return_value = True

        self._upload()

        prior_result.revoke.assert_not_called()

    @patch("core.views.AsyncResult")
    @patch("core.services.dna_analyser.generate_vibe_with_llm", return_value=["a vibe"])
    @patch("core.services.book_enrichment_service.enrich_book_from_apis")
    def test_upload_succeeds_when_revoke_raises(self, mock_enrich, mock_vibe, mock_async_result):
        """A failure to revoke the prior task must not block the new upload."""
        prior_id = "prior-task-id-789"
        self.user.userprofile.pending_dna_task_id = prior_id
        self.user.userprofile.save()

        prior_result = mock_async_result.return_value
        prior_result.ready.return_value = False
        prior_result.revoke.side_effect = RuntimeError("broker unavailable")

        response = self._upload()

        # Upload still redirects to the dashboard processing view
        self.assertEqual(response.status_code, 302)
        self.user.userprofile.refresh_from_db()
        self.assertIsNotNone(self.user.userprofile.pending_dna_task_id)
        self.assertNotEqual(self.user.userprofile.pending_dna_task_id, prior_id)

    @patch("core.services.dna_analyser.generate_vibe_with_llm", return_value=["a vibe"])
    @patch("core.services.book_enrichment_service.enrich_book_from_apis")
    def test_reupload_clears_stale_userbooks(self, mock_enrich, mock_vibe):
        """Books from a prior upload that aren't in the new CSV are removed (handhles
        orphans from a revoked task — calculate_full_dna deletes UserBooks not in the
        current upload)."""
        # First upload: one book
        first_csv = (
            "Title,Author,Exclusive Shelf,My Rating\n"
            "Old Book,Old Author,read,5\n"
        ).encode("utf-8")
        self.client.post(
            reverse("core:upload"),
            {"csv_file": SimpleUploadedFile("a.csv", first_csv, content_type="text/csv")},
        )
        self.assertTrue(UserBook.objects.filter(user=self.user, book__title="Old Book").exists())

        # Second upload: different book
        second_csv = (
            "Title,Author,Exclusive Shelf,My Rating\n"
            "New Book,New Author,read,4\n"
        ).encode("utf-8")
        self.client.post(
            reverse("core:upload"),
            {"csv_file": SimpleUploadedFile("b.csv", second_csv, content_type="text/csv")},
        )

        self.assertFalse(UserBook.objects.filter(user=self.user, book__title="Old Book").exists())
        self.assertTrue(UserBook.objects.filter(user=self.user, book__title="New Book").exists())
