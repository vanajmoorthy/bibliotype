import time
import requests
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from core.models import Author
from django.db import models
from core.services.author_service import check_author_mainstream_status


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

    def handle(self, *args, **options):
        self.stdout.write("🚀 Starting author mainstream status update...")

        if options["recheck_all"]:
            self.stdout.write(self.style.WARNING("Re-checking all authors."))
            authors_to_check = Author.objects.all()
        else:
            # Check authors that have never been checked OR were checked long ago
            age_limit = timezone.now() - timedelta(days=options["age_days"])
            authors_to_check = Author.objects.filter(
                models.Q(mainstream_last_checked__isnull=True) | models.Q(mainstream_last_checked__lte=age_limit)
            )

        total_authors = authors_to_check.count()
        if total_authors == 0:
            self.stdout.write(self.style.SUCCESS("✅ No authors need checking at this time."))
            return

        self.stdout.write(f"Found {total_authors} authors to check.")
        updated_count = 0

        with requests.Session() as session:
            headers = {
                # Replace with your app name and a contact email/URL.
                # This is crucial for being a good API citizen.
                "User-Agent": "BibliotypeApp/1.0 (contact@yourdomain.com)"
            }
            session.headers.update(headers)
            for i, author in enumerate(authors_to_check):
                self.stdout.write(f"  ({i+1}/{total_authors}) Checking: {author.name}...")

                status_data = check_author_mainstream_status(author.name, session)

                if status_data["error"]:
                    self.stdout.write(self.style.ERROR(f"    -> API Error: {status_data['error']}"))
                else:
                    if author.is_mainstream != status_data["is_mainstream"]:
                        author.is_mainstream = status_data["is_mainstream"]
                        updated_count += 1
                        self.stdout.write(
                            self.style.NOTICE(
                                f"    -> Status changed to: {author.is_mainstream}. Reason: {status_data.get('reason', 'N/A')}"
                            )
                        )

                    # Always update the last_checked timestamp
                    author.mainstream_last_checked = timezone.now()
                    author.save()

                # Be a polite API consumer
                time.sleep(1)  # 1-second delay between authors

        self.stdout.write("\n" + self.style.SUCCESS("✅ Finished."))
        self.stdout.write(f"Updated the mainstream status for {updated_count} authors.")
