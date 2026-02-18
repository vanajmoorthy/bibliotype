import logging
import time
from datetime import timedelta

import requests
from django.core.management.base import BaseCommand
from django.db import models
from django.utils import timezone

from core.models import Author
from core.services.author_service import check_author_mainstream_status

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Checks authors against APIs to determine their mainstream status."

    def add_arguments(self, parser):
        parser.add_argument(
            "--recheck-all",
            action="store_true",
            help="Ignores the 'last_checked' date and re-checks all authors.",
        )
        parser.add_argument(
            "--age-days",
            type=int,
            default=90,
            help="Re-check authors whose status was last checked more than this many days ago.",
        )

    def _log(self, msg):
        self.stdout.write(msg)
        logger.info(f"update_author_status: {msg}")

    def _warn(self, msg):
        self.stdout.write(self.style.WARNING(msg))
        logger.warning(f"update_author_status: {msg}")

    def handle(self, *args, **options):
        self._log("Starting author mainstream status update...")

        if options["recheck_all"]:
            self._warn("Re-checking all authors.")
            authors_to_check = Author.objects.all()
        else:
            age_limit = timezone.now() - timedelta(days=options["age_days"])
            authors_to_check = Author.objects.filter(
                models.Q(mainstream_last_checked__isnull=True) | models.Q(mainstream_last_checked__lte=age_limit)
            )

        total_authors = authors_to_check.count()
        if total_authors == 0:
            self._log("No authors need checking at this time.")
            return

        self._log(f"Found {total_authors} authors to check.")
        updated_count = 0

        with requests.Session() as session:
            session.headers.update({"User-Agent": "BibliotypeApp/1.0 (contact@yourdomain.com)"})
            for i, author in enumerate(authors_to_check):
                self._log(f"  ({i+1}/{total_authors}) Checking: {author.name}...")

                status_data = check_author_mainstream_status(author.name, session)

                if status_data["error"]:
                    self._warn(f"    -> API Error for '{author.name}': {status_data['error']}")
                else:
                    if author.is_mainstream != status_data["is_mainstream"]:
                        author.is_mainstream = status_data["is_mainstream"]
                        updated_count += 1
                        self._log(
                            f"    -> Status changed to: {author.is_mainstream}. Reason: {status_data.get('reason', 'N/A')}"
                        )

                    author.mainstream_last_checked = timezone.now()
                    author.save()

                time.sleep(1)

        self._log(f"Finished. Updated the mainstream status for {updated_count} authors.")
