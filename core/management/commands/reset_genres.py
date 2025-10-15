from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import Book, Genre


class Command(BaseCommand):
    help = "Deletes all Genres and removes their relationships from all Books, without deleting the books themselves."

    def add_arguments(self, parser):
        # Add a --no-input argument to bypass the confirmation prompt for automated scripts
        parser.add_argument(
            "--no-input",
            action="store_true",
            help="Do not prompt for confirmation before deleting.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        self.stdout.write(self.style.WARNING("Starting the genre reset process..."))

        confirm = options["no_input"]

        if not confirm:
            self.stdout.write(self.style.ERROR("\nWARNING: This is a destructive operation. It will permanently:"))
            self.stdout.write("1. Remove ALL genres from the 'Genre' table.")
            self.stdout.write("2. Clear ALL genre relationships from every book.")
            self.stdout.write("The Book data itself will NOT be deleted.")

            response = input("\nAre you absolutely sure you want to continue? [y/N]: ")
            if response.lower() != "y":
                self.stdout.write(self.style.WARNING("Operation cancelled by user."))
                return

        self.stdout.write("\nProceeding with deletion...")

        # --- The Logic ---
        # The most efficient way to achieve both goals is to simply delete all Genre objects.
        # Because of the ManyToMany relationship, Django will automatically and efficiently
        # delete all corresponding records from the hidden join table (`core_book_genres`).
        # This is much faster than looping through every book and calling .clear().

        genres_to_delete = Genre.objects.all()

        # .delete() returns a tuple: (number_of_objects_deleted, {type: count})
        count, _ = genres_to_delete.delete()

        self.stdout.write(
            self.style.SUCCESS(
                f"\n✅ COMPLETE: Successfully deleted {count} genres and all their relationships to books."
            )
        )
