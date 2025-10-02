# In core/management/commands/research_publishers.py

import time
import requests
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from datetime import timedelta
from django.db.models import Q

from core.models import Author, Publisher  # Need Author for _normalize
from core.services.publisher_service import research_publisher_identity


class Command(BaseCommand):
    help = "Uses an AI to research and organize publishers."

    def add_arguments(self, parser):
        parser.add_argument("--recheck-all", action="store_true", help="Re-research all publishers.")
        parser.add_argument("--limit", type=int, default=50, help="Limit the number of publishers to check in one run.")

    @transaction.atomic
    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("🚀 Starting AI-powered publisher research..."))

        if options["recheck_all"]:
            publishers_to_check = Publisher.objects.all()
        else:
            # Check publishers that have never been checked and have no parent
            publishers_to_check = Publisher.objects.filter(
                Q(mainstream_last_checked__isnull=True) & Q(parent__isnull=True)
            )

        publishers_to_check = list(publishers_to_check[: options["limit"]])

        if not publishers_to_check:
            self.stdout.write(self.style.SUCCESS("✅ No publishers need researching at this time."))
            return

        self.stdout.write(f"Found {len(publishers_to_check)} publishers to research.")
        updated_count = 0

        with requests.Session() as session:
            session.headers.update({"User-Agent": "BibliotypeApp/1.0 (contact@yourdomain.com)"})

            for publisher in publishers_to_check:
                self.stdout.write(f"  -> Researching: {publisher.name}...")

                findings = research_publisher_identity(publisher.name, session)

                if findings["error"]:
                    self.stdout.write(self.style.ERROR(f"     - Error: {findings['error']}"))
                    # Mark as checked even if it fails, to avoid retrying bad names
                    publisher.mainstream_last_checked = timezone.now()
                else:
                    self.stdout.write(self.style.SUCCESS(f"     - AI Reason: {findings.get('reasoning')}"))

                    is_mainstream_result = findings.get("is_mainstream")

                    # Explicitly check if the result is a boolean. If not, default to False.
                    if isinstance(is_mainstream_result, bool):
                        publisher.is_mainstream = is_mainstream_result
                    else:
                        # This is our safety net. If the AI returns null or something unexpected,
                        # we force the value to False.
                        publisher.is_mainstream = False

                    publisher.mainstream_last_checked = timezone.now()

                    if parent_name := findings.get("parent_company_name"):
                        # Find or create the parent and link it
                        parent_obj, _ = Publisher.objects.get_or_create(
                            normalized_name=Author._normalize(parent_name),
                            defaults={"name": parent_name, "is_mainstream": True},
                        )
                        publisher.parent = parent_obj

                    updated_count += 1

                publisher.save()
                time.sleep(2)  # Be extra polite to the APIs

        self.stdout.write(self.style.SUCCESS(f"\n✅ Finished. Updated {updated_count} publisher entries."))
