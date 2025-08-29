from django.core.management.base import BaseCommand

from core.models import AggregateAnalytics, UserProfile

# We'll reuse the same function that the live app uses!
from core.percentile_engine import update_analytics_from_stats


class Command(BaseCommand):
    help = "Rebuilds the AggregateAnalytics data from all existing UserProfiles."

    def handle(self, *args, **kwargs):
        self.stdout.write(self.style.WARNING("Rebuilding aggregate analytics from scratch..."))

        # Step 1: Clear the old analytics data
        self.stdout.write("  -> Deleting old aggregate analytics...")
        AggregateAnalytics.objects.all().delete()
        # Create a fresh, empty instance
        analytics = AggregateAnalytics.get_instance()

        # Step 2: Get all user profiles that have DNA data
        profiles_with_dna = UserProfile.objects.exclude(dna_data__isnull=True)
        total_profiles = profiles_with_dna.count()

        if total_profiles == 0:
            self.stdout.write(self.style.SUCCESS("No user profiles with DNA found. Analytics table is now empty."))
            return

        self.stdout.write(f"  -> Found {total_profiles} user profiles with DNA data to process.")

        # Step 3: Loop through each profile and re-run the analytics update
        for i, profile in enumerate(profiles_with_dna):
            # Extract the specific `user_stats` dictionary from the larger JSON blob
            user_stats = profile.dna_data.get("user_stats")

            if user_stats:
                # This is the key: we reuse the exact same function as the live app
                # to ensure the logic is identical.
                update_analytics_from_stats(user_stats)
                self.stdout.write(f"     - Processed profile {i+1}/{total_profiles} for user {profile.user.username}")
            else:
                self.stdout.write(
                    self.style.WARNING(f"     - Skipped profile for {profile.user.username} (missing user_stats).")
                )

        self.stdout.write(
            self.style.SUCCESS(f"\nSuccessfully rebuilt aggregate analytics from {total_profiles} profiles.")
        )
