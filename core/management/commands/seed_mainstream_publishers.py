# In core/management/commands/seed_mainstream_publishers.py

from django.core.management.base import BaseCommand
from django.db import transaction
from core.models import Author, Publisher  # Import Author for the _normalize function
from core.dna_constants import MAINSTREAM_PUBLISHERS_HIERARCHY


class Command(BaseCommand):
    help = "Seeds the database with mainstream publishers and their subsidiaries."

    @transaction.atomic
    def handle(self, *args, **kwargs):
        self.stdout.write(self.style.NOTICE("Seeding mainstream publishers and their imprints..."))
        created_count = 0

        for parent_name, subsidiaries in MAINSTREAM_PUBLISHERS_HIERARCHY.items():
            # Create or update the parent publisher, ensuring it's marked as mainstream
            parent_pub, created = Publisher.objects.update_or_create(
                normalized_name=Author._normalize(parent_name), defaults={"name": parent_name, "is_mainstream": True}
            )
            if created:
                created_count += 1

            # Create/update the subsidiaries and link them to the parent
            for sub_name in subsidiaries:
                sub_pub, created = Publisher.objects.update_or_create(
                    normalized_name=Author._normalize(sub_name),
                    defaults={"name": sub_name, "is_mainstream": True, "parent": parent_pub},
                )
                if created:
                    created_count += 1

        self.stdout.write(self.style.SUCCESS(f"✅ Finished. Created or updated {created_count} publisher entries."))
