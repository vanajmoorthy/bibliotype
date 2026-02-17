import logging
import time

import requests
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.models import Author, Publisher
from core.services.publisher_service import research_publisher_identity

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Uses an AI to research and organize publishers."

    def add_arguments(self, parser):
        parser.add_argument("--recheck-all", action="store_true", help="Re-research all publishers.")
        parser.add_argument("--limit", type=int, default=50, help="Limit the number of publishers to check in one run.")

    def _log(self, msg):
        self.stdout.write(msg)
        logger.info(f"research_publishers: {msg}")

    def _warn(self, msg):
        self.stdout.write(self.style.WARNING(msg))
        logger.warning(f"research_publishers: {msg}")

    @transaction.atomic
    def handle(self, *args, **options):
        self._log("Starting AI-powered publisher research...")

        if options["recheck_all"]:
            publishers_to_check = Publisher.objects.all()
        else:
            publishers_to_check = Publisher.objects.filter(
                Q(mainstream_last_checked__isnull=True) & Q(parent__isnull=True)
            )

        publishers_to_check = list(publishers_to_check[: options["limit"]])

        if not publishers_to_check:
            self._log("No publishers need researching at this time.")
            return

        self._log(f"Found {len(publishers_to_check)} publishers to research.")
        updated_count = 0

        with requests.Session() as session:
            session.headers.update({"User-Agent": "BibliotypeApp/1.0 (contact@yourdomain.com)"})

            for publisher in publishers_to_check:
                self._log(f"  -> Researching: {publisher.name}...")

                findings = research_publisher_identity(publisher.name, session)

                if findings["error"]:
                    self._warn(f"     - Error for '{publisher.name}': {findings['error']}")
                    publisher.mainstream_last_checked = timezone.now()
                else:
                    self._log(f"     - AI Reason: {findings.get('reasoning')}")

                    is_mainstream_result = findings.get("is_mainstream")

                    if isinstance(is_mainstream_result, bool):
                        publisher.is_mainstream = is_mainstream_result
                    else:
                        publisher.is_mainstream = False

                    publisher.mainstream_last_checked = timezone.now()

                    if parent_name := findings.get("parent_company_name"):
                        parent_obj, _ = Publisher.objects.get_or_create(
                            normalized_name=Author._normalize(parent_name),
                            defaults={"name": parent_name, "is_mainstream": True},
                        )
                        publisher.parent = parent_obj

                    updated_count += 1

                publisher.save()
                time.sleep(2)

        self._log(f"Finished. Updated {updated_count} publisher entries.")
