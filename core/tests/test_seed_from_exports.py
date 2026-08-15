"""Tests for the seed_from_exports management command."""

import json
import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TransactionTestCase, override_settings

GOODREADS_HEADER = (
    "Book Id,Title,Author,Author l-f,Additional Authors,ISBN,ISBN13,My Rating,"
    "Average Rating,Publisher,Binding,Number of Pages,Year Published,"
    "Original Publication Year,Date Read,Date Added,Bookshelves,"
    "Bookshelves with positions,Exclusive Shelf,My Review,Spoiler,Private Notes,"
    "Read Count,Owned Copies"
)


def _row(title, author, isbn13, rating=4, shelf="read", date_read="2024/03/01"):
    if shelf != "read":
        date_read = ""
    return (
        f'123,"{title}","{author}","{author}",,{isbn13},"=""{isbn13}""",{rating},4.05,'
        f'"Test Press",Paperback,320,2015,2015,{date_read},2024/01/15,{shelf},'
        f'"{shelf} (#1)",{shelf},,,,1,0'
    )


def _export_csv(prefix, n_books=6):
    rows = [GOODREADS_HEADER]
    for i in range(n_books):
        rows.append(_row(f"{prefix} Book {i}", f"{prefix} Author {i}", f"97800000{prefix[-1]}{i:03d}"))
    return "\n".join(rows) + "\n"


@override_settings(
    CELERY_TASK_ALWAYS_EAGER=True,
    CELERY_TASK_STORE_EAGER_RESULT=True,
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "seed-from-exports-tests",
        }
    },
)
@patch("core.services.dna.generate_vibe_with_llm", return_value=["a vibe"])
@patch("core.services.book_enrichment_service.enrich_book_from_apis")
class SeedFromExportsTests(TransactionTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.csv_dir = Path(self.tmp.name)
        (self.csv_dir / "001__alice_repo.csv").write_text(_export_csv("a1"), encoding="utf-8")
        (self.csv_dir / "002__bob_repo.csv").write_text(_export_csv("b2"), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()
        from django.db import connections

        connections.close_all()
        super().tearDown()

    def _run(self, *args):
        out = StringIO()
        call_command("seed_from_exports", "--dir", str(self.csv_dir), *args, stdout=out)
        return out.getvalue()

    def test_creates_users_with_dna(self, mock_enrich, mock_vibe):
        output = self._run()

        self.assertIn("Processed: 2, skipped: 0, failed: 0", output)
        alice = User.objects.get(username="seed_001__alice_repo")
        self.assertEqual(alice.email, "seed_001__alice_repo@seed.bibliotype.invalid")
        self.assertFalse(alice.has_usable_password())
        self.assertIsNotNone(alice.userprofile.dna_data)
        self.assertEqual(alice.userprofile.dna_data["user_stats"]["total_books_read"], 6)

    def test_rerun_is_idempotent_and_force_reprocesses(self, mock_enrich, mock_vibe):
        self._run()
        output = self._run()
        self.assertIn("Processed: 0, skipped: 2, failed: 0", output)
        self.assertEqual(User.objects.filter(username__startswith="seed_").count(), 2)

        output = self._run("--force")
        self.assertIn("Processed: 2, skipped: 0, failed: 0", output)

    def test_bad_csv_is_skipped_without_aborting(self, mock_enrich, mock_vibe):
        (self.csv_dir / "000__broken.csv").write_text("not,a,goodreads\nexport,at,all\n", encoding="utf-8")

        output = self._run()

        self.assertIn("Processed: 2, skipped: 0, failed: 1", output)
        # The shell user for the unprocessable CSV must not linger.
        self.assertFalse(User.objects.filter(username="seed_000__broken").exists())

    def test_limit(self, mock_enrich, mock_vibe):
        output = self._run("--limit", "1")
        self.assertIn("Processed: 1", output)
        self.assertEqual(User.objects.filter(username__startswith="seed_").count(), 1)

    def test_purge_deletes_only_seeded_users(self, mock_enrich, mock_vibe):
        self._run()
        organic = User.objects.create_user(username="seed_lookalike", email="real@person.com", password="x")

        out = StringIO()
        call_command("seed_from_exports", "--purge", stdout=out)

        self.assertIn("Purged 2 seeded users", out.getvalue())
        self.assertEqual(User.objects.filter(username__startswith="seed_").count(), 1)
        self.assertTrue(User.objects.filter(pk=organic.pk).exists())

    def test_download_fetches_missing_files_and_tolerates_failures(self, mock_enrich, mock_vibe):
        download_dir = self.csv_dir / "downloaded"
        good_csv = _export_csv("c3").encode("utf-8")
        import hashlib

        manifest = [
            {
                "filename": "001__good.csv",
                "url": "https://raw.githubusercontent.com/x/y/HEAD/a.csv",
                "sha256": hashlib.sha256(good_csv).hexdigest(),
            },
            {
                "filename": "002__gone.csv",
                "url": "https://raw.githubusercontent.com/x/z/HEAD/b.csv",
                "sha256": "0" * 64,
            },
        ]
        manifest_file = self.csv_dir / "manifest.json"
        manifest_file.write_text(json.dumps(manifest))

        import requests as requests_module

        def fake_get(url, timeout=None):
            response = MagicMock()
            if "a.csv" in url:
                response.content = good_csv
                response.raise_for_status = MagicMock()
            else:
                response.raise_for_status.side_effect = requests_module.RequestException("404")
            return response

        with (
            patch("core.management.commands.seed_from_exports.MANIFEST_PATH", manifest_file),
            patch("core.management.commands.seed_from_exports.requests.get", side_effect=fake_get),
        ):
            out = StringIO()
            call_command("seed_from_exports", "--dir", str(download_dir), "--download", stdout=out)

        output = out.getvalue()
        self.assertIn("Downloads: 1 fetched, 0 already present, 1 failed", output)
        self.assertTrue((download_dir / "001__good.csv").is_file())
        self.assertFalse((download_dir / "002__gone.csv").exists())
        # The downloaded file was then seeded.
        self.assertTrue(User.objects.filter(username="seed_001__good").exists())
