import logging

from django.core.management.base import BaseCommand

from core.models import UserProfile
from core.tasks import generate_recommendations_task

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Regenerate recommendations for users with DNA data. Dispatches async Celery tasks."

    def _log(self, msg):
        self.stdout.write(msg)
        logger.info(f"regenerate_recommendations: {msg}")

    def _warn(self, msg):
        self.stdout.write(self.style.WARNING(msg))
        logger.warning(f"regenerate_recommendations: {msg}")

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would happen without dispatching tasks.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            help="Max profiles to process.",
        )
        parser.add_argument(
            "--username",
            type=str,
            help="Process a single user by username.",
        )

    def handle(self, *args, **options):
        profiles = UserProfile.objects.filter(dna_data__isnull=False).select_related("user")

        if options["username"]:
            profiles = profiles.filter(user__username=options["username"])

        if options["limit"]:
            profiles = profiles[: options["limit"]]

        profiles = list(profiles)

        if not profiles:
            self._log("No profiles with DNA data found.")
            return

        self._log(f"Found {len(profiles)} profiles to process.")
        dispatched = 0

        for profile in profiles:
            user = profile.user
            rec_count = len(profile.recommendations_data) if profile.recommendations_data else 0

            if options["dry_run"]:
                self._log(f"  {user.username}: current recommendations = {rec_count}")
                continue

            generate_recommendations_task.delay(user.id)
            self._log(f"  {user.username}: dispatched (had {rec_count} recommendations)")
            dispatched += 1

        if options["dry_run"]:
            self._warn("Dry run complete. No tasks dispatched.")
        else:
            self._log(f"Dispatched recommendation generation for {dispatched} users.")
