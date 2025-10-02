# In core/management/commands/organize_publishers.py

from django.core.management.base import BaseCommand
from django.db import transaction
from core.models import Author, Publisher  # Import Author for the _normalize function
from core.dna_constants import MAINSTREAM_PUBLISHERS_HIERARCHY


class Command(BaseCommand):
    help = "Organizes publishers by assigning parents and flagging mainstream status based on dna_constants."

    @transaction.atomic
    def handle(self, *args, **kwargs):
        self.stdout.write(self.style.NOTICE("Organizing publishers... Building reference map."))

        # --- Step 1: Create a reverse map for fast lookups ---
        # This maps a normalized subsidiary name directly to its parent's normalized name.
        # e.g., {'vikingpress': 'penguinrandomhouse', 'torbooks': 'macmillanpublishers'}
        subsidiary_to_parent_map = {}
        for parent_name, subsidiaries in MAINSTREAM_PUBLISHERS_HIERARCHY.items():
            normalized_parent = Author._normalize(parent_name)
            for sub_name in subsidiaries:
                normalized_sub = Author._normalize(sub_name)
                subsidiary_to_parent_map[normalized_sub] = normalized_parent

        # Cache the parent publisher objects to avoid repeated database queries
        parent_publishers = {
            p.normalized_name: p
            for p in Publisher.objects.filter(normalized_name__in=subsidiary_to_parent_map.values())
        }

        # --- Step 2: Iterate through all publishers and organize them ---
        self.stdout.write("Processing all publishers in the database...")
        updated_count = 0
        publishers_to_process = list(Publisher.objects.all())

        for publisher in publishers_to_process:
            is_updated = False

            # Check if this publisher is a known subsidiary
            if publisher.normalized_name in subsidiary_to_parent_map:
                # It's a subsidiary, so let's set its parent and mainstream status
                if not publisher.is_mainstream:
                    publisher.is_mainstream = True
                    is_updated = True

                parent_normalized_name = subsidiary_to_parent_map[publisher.normalized_name]
                if parent_normalized_name in parent_publishers:
                    parent_obj = parent_publishers[parent_normalized_name]
                    if publisher.parent != parent_obj:
                        publisher.parent = parent_obj
                        is_updated = True

            # Also check if the publisher is a parent company itself
            elif publisher.normalized_name in parent_publishers:
                if not publisher.is_mainstream:
                    publisher.is_mainstream = True
                    is_updated = True

            if is_updated:
                publisher.save()
                updated_count += 1
                self.stdout.write(
                    f"  -> Updated '{publisher.name}' (Parent: {publisher.parent}, Mainstream: {publisher.is_mainstream})"
                )

        self.stdout.write(self.style.SUCCESS(f"\n✅ Finished. Updated {updated_count} publisher entries."))
        self.stdout.write("Review any remaining un-parented publishers in the Django Admin.")
