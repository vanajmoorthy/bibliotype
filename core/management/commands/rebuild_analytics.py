import logging

from django.core.management.base import BaseCommand

from core.models import AggregateAnalytics, UserProfile
from core.percentile_engine import update_analytics_from_stats

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Rebuilds the AggregateAnalytics data from all existing UserProfiles."

    def _log(self, msg):
        self.stdout.write(msg)
        logger.info(f"rebuild_analytics: {msg}")

    def _warn(self, msg):
        self.stdout.write(self.style.WARNING(msg))
        logger.warning(f"rebuild_analytics: {msg}")

    def handle(self, *args, **kwargs):
        self._log("Rebuilding aggregate analytics from scratch...")

        self._log("  -> Deleting old aggregate analytics...")
        AggregateAnalytics.objects.all().delete()
        analytics = AggregateAnalytics.get_instance()

        profiles_with_dna = UserProfile.objects.exclude(dna_data__isnull=True)
        total_profiles = profiles_with_dna.count()

        if total_profiles == 0:
            self._log("No user profiles with DNA found. Analytics table is now empty.")
            return

        self._log(f"  -> Found {total_profiles} user profiles with DNA data to process.")

        for i, profile in enumerate(profiles_with_dna):
            user_stats = profile.dna_data.get("user_stats")

            if user_stats:
                if "avg_books_per_year" not in user_stats:
                    stats_by_year = profile.dna_data.get("stats_by_year", [])
                    if stats_by_year:
                        total_books = user_stats.get("total_books_read", 0)
                        num_years = len(stats_by_year)
                        user_stats["avg_books_per_year"] = round(total_books / num_years, 1) if num_years > 0 else 0
                        user_stats["num_reading_years"] = num_years

                update_analytics_from_stats(user_stats)
                self._log(f"     - Processed profile {i+1}/{total_profiles} for user {profile.user.username}")
            else:
                self._warn(f"     - Skipped profile for {profile.user.username} (missing user_stats).")

        self._log(f"Successfully rebuilt aggregate analytics from {total_profiles} profiles.")
