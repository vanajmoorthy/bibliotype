"""
Seed synthetic users from real Goodreads export CSVs.

Runs each CSV in the seed corpus through the same pipeline as a real upload
(`calculate_full_dna`), so AggregateAnalytics, percentiles, and the
recommendations candidate pool are populated with realistic reading data.

The corpus is 197 `goodreads_library_export.csv` files that their owners
committed to public GitHub repos. The CSVs themselves are NOT in this repo
(they live in the gitignored `csv/github_exports/`); a committed manifest of
raw.githubusercontent URLs lets any environment fetch them with `--download`.

Typical usage:
    manage.py seed_from_exports --download --limit 20   # first probe
    manage.py seed_from_exports --download              # full corpus
    manage.py seed_from_exports --purge                 # remove seeded users

Seeded users are identified by the `seed_` username prefix and the
`@seed.bibliotype.invalid` email domain — there is no model flag.
"""

import hashlib
import json
import re
import time
from pathlib import Path

import requests
from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError

from core.services.dna import calculate_full_dna

MANIFEST_PATH = Path(settings.BASE_DIR) / "core" / "seed_data" / "github_exports_manifest.json"
DEFAULT_CSV_DIR = "csv/github_exports"
USERNAME_PREFIX = "seed_"
SEED_EMAIL_DOMAIN = "seed.bibliotype.invalid"
# Mirrors the cap in core.tasks.generate_reading_dna_task / core.views.MAX_UPLOAD_ROWS.
MAX_ROWS = 50000
DOWNLOAD_TIMEOUT_SECONDS = 30


class Command(BaseCommand):
    help = "Seed synthetic users by running real Goodreads export CSVs through the upload pipeline."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dir",
            type=str,
            default=DEFAULT_CSV_DIR,
            help=f"Directory of export CSVs (default: {DEFAULT_CSV_DIR}, relative to BASE_DIR).",
        )
        parser.add_argument(
            "--download",
            action="store_true",
            help="Fetch CSVs listed in the committed manifest into --dir before seeding.",
        )
        parser.add_argument("--limit", type=int, default=0, help="Only process the first N files (0 = all).")
        parser.add_argument(
            "--force",
            action="store_true",
            help="Reprocess users that already have DNA (default: skip them).",
        )
        parser.add_argument(
            "--purge",
            action="store_true",
            help="Delete all previously seeded users and exit.",
        )
        parser.add_argument(
            "--delay",
            type=float,
            default=0.0,
            help="Seconds to sleep between users (be gentle on enrichment APIs).",
        )

    def handle(self, *args, **options):
        if options["purge"]:
            self._purge()
            return

        csv_dir = Path(options["dir"])
        if not csv_dir.is_absolute():
            csv_dir = Path(settings.BASE_DIR) / csv_dir

        if options["download"]:
            self._download(csv_dir)

        if not csv_dir.is_dir():
            raise CommandError(f"CSV directory {csv_dir} does not exist. Run with --download to fetch the corpus.")

        files = sorted(csv_dir.glob("*.csv"))
        if options["limit"]:
            files = files[: options["limit"]]
        if not files:
            raise CommandError(f"No CSV files found in {csv_dir}.")

        self.stdout.write(f"Seeding from {len(files)} CSV files in {csv_dir}...")
        processed = skipped = failed = 0

        for i, path in enumerate(files, start=1):
            username = self._username_for(path)
            user, created = User.objects.get_or_create(
                username=username,
                defaults={"email": f"{username}@{SEED_EMAIL_DOMAIN}"},
            )
            if created:
                user.set_unusable_password()
                user.save(update_fields=["password"])

            if not options["force"] and not created and user.userprofile.dna_data:
                skipped += 1
                self.stdout.write(f"[{i}/{len(files)}] {username}: already has DNA, skipping")
                continue

            try:
                csv_content = path.read_text(encoding="utf-8-sig", errors="replace")
                self._validate(csv_content, path)
                # bulk_enrichment: route all enrichment through the rate-limited
                # bulk queue instead of enriching inline or crowding the
                # interactive queue (a full corpus run enqueues thousands).
                dna = calculate_full_dna(csv_content, user, bulk_enrichment=True)
                processed += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f"[{i}/{len(files)}] {username}: "
                        f"{dna.get('user_stats', {}).get('total_books_read', '?')} books read, "
                        f"reader type: {dna.get('reader_type', '?')}"
                    )
                )
            except Exception as exc:  # noqa: BLE001 — one bad CSV must not abort the seeding run
                failed += 1
                self.stdout.write(self.style.WARNING(f"[{i}/{len(files)}] {username}: FAILED — {exc}"))
                if created:
                    # Don't leave an empty shell user behind for a CSV that can't be processed.
                    user.delete()

            if options["delay"] and i < len(files):
                time.sleep(options["delay"])

        self.stdout.write(self.style.SUCCESS(f"\nDone. Processed: {processed}, skipped: {skipped}, failed: {failed}."))
        if processed:
            self.stdout.write(
                "Enrichment and recommendations tasks were dispatched to Celery and will drain in the background."
            )

    # --- helpers -----------------------------------------------------------

    def _username_for(self, path):
        stem = re.sub(r"[^a-z0-9_]+", "_", path.stem.lower()).strip("_")
        return (USERNAME_PREFIX + stem)[:150]

    def _validate(self, csv_content, path):
        header, _, _ = csv_content.partition("\n")
        if "Title" not in header:
            raise ValueError(f"{path.name} does not look like a Goodreads export (no Title column)")
        if csv_content.count("\n") > MAX_ROWS + 1:
            raise ValueError(f"{path.name} exceeds the {MAX_ROWS}-row cap")

    def _download(self, csv_dir):
        if not MANIFEST_PATH.is_file():
            raise CommandError(f"Manifest not found at {MANIFEST_PATH}")
        manifest = json.loads(MANIFEST_PATH.read_text())
        csv_dir.mkdir(parents=True, exist_ok=True)

        fetched = present = failures = 0
        for entry in manifest:
            dest = csv_dir / entry["filename"]
            if dest.is_file():
                present += 1
                continue
            try:
                response = requests.get(entry["url"], timeout=DOWNLOAD_TIMEOUT_SECONDS)
                response.raise_for_status()
            except requests.RequestException as exc:
                failures += 1
                self.stdout.write(self.style.WARNING(f"Download failed for {entry['filename']}: {exc}"))
                continue
            if hashlib.sha256(response.content).hexdigest() != entry["sha256"]:
                # Upstream repo moved on since the manifest was built — the file is
                # still a valid export, just newer than the recorded snapshot.
                self.stdout.write(f"Note: {entry['filename']} differs from the manifest snapshot (upstream changed)")
            dest.write_bytes(response.content)
            fetched += 1

        self.stdout.write(f"Downloads: {fetched} fetched, {present} already present, {failures} failed.")

    def _purge(self):
        seeded = User.objects.filter(username__startswith=USERNAME_PREFIX, email__endswith=f"@{SEED_EMAIL_DOMAIN}")
        count = seeded.count()
        seeded.delete()
        self.stdout.write(self.style.SUCCESS(f"Purged {count} seeded users."))
        self.stdout.write("Run `manage.py rebuild_analytics` to recompute aggregates without them.")
