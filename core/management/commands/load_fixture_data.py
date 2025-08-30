from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db.models.signals import post_save

# Import the specific signal receivers you want to disable
from core.models import create_user_profile, save_user_profile


class Command(BaseCommand):
    help = "Loads data from a fixture, temporarily disabling post_save signals for the User model to prevent conflicts."

    def add_arguments(self, parser):
        parser.add_argument("fixture_path", type=str, help="The path to the fixture file (e.g., db_dump.json).")

    def handle(self, *args, **kwargs):
        fixture_path = kwargs["fixture_path"]

        self.stdout.write(self.style.WARNING("Disconnecting post_save signals for User model..."))
        # Disconnect the signals
        post_save.disconnect(create_user_profile, sender=User)
        post_save.disconnect(save_user_profile, sender=User)

        try:
            self.stdout.write(f"Loading data from fixture: {fixture_path}...")
            # Run the original loaddata command
            call_command("loaddata", fixture_path)
            self.stdout.write(self.style.SUCCESS("Successfully loaded data from fixture."))

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"An error occurred during loaddata: {e}"))
            # We still want to reconnect signals even if it fails

        finally:
            self.stdout.write(self.style.SUCCESS("Reconnecting post_save signals for User model..."))
            # CRITICAL: Reconnect the signals so the app works normally again
            post_save.connect(create_user_profile, sender=User)
            post_save.connect(save_user_profile, sender=User)
