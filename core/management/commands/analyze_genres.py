from collections import Counter

from django.core.management.base import BaseCommand
from django.db.models import Count

from core.dna_constants import CANONICAL_GENRE_MAP
from core.models import Genre


class Command(BaseCommand):
    help = "Analyzes genres in the database, showing which are unmapped and provides an option to delete them."

    def add_arguments(self, parser):
        parser.add_argument(
            "--delete",
            action="store_true",
            help="Deletes all genres from the database that are not found in the CANONICAL_GENRE_MAP.",
        )

    def handle(self, *args, **kwargs):
        self.stdout.write("Analyzing genres in the database...")

        # Get all genres and how many books are associated with them
        all_genres = Genre.objects.annotate(num_books=Count("books")).all()

        unmapped_genres = []
        mapped_count = 0

        for genre in all_genres:
            # Check if the genre name exists as a key in our master map
            if genre.name.lower().strip() not in CANONICAL_GENRE_MAP:
                unmapped_genres.append(genre)
            else:
                mapped_count += 1

        self.stdout.write("\n" + "=" * 50)
        self.stdout.write(f"Found {all_genres.count()} total unique genre strings in the database.")
        self.stdout.write(self.style.SUCCESS(f"  - {mapped_count} are correctly mapped."))
        self.stdout.write(self.style.WARNING(f"  - {len(unmapped_genres)} are UNMAPPED junk/alias genres."))
        self.stdout.write("=" * 50 + "\n")

        if not unmapped_genres:
            self.stdout.write(self.style.SUCCESS("Congratulations! All genres in the database are properly mapped."))
            return

        # Sort by the number of books they're attached to, then by name
        unmapped_genres.sort(key=lambda g: (-g.num_books, g.name))

        self.stdout.write("List of UNMAPPED Genres (and book count):")
        for genre in unmapped_genres:
            self.stdout.write(f"  - '{genre.name}' ({genre.num_books} books)")

        if kwargs["delete"]:
            self.stdout.write("\n" + self.style.WARNING("--- Deleting Unmapped Genres ---"))

            # Get the primary keys of the unmapped genres
            pks_to_delete = [genre.pk for genre in unmapped_genres]

            # This is a safe and efficient bulk delete operation.
            # Django handles removing the links from the book-to-genre relationship table automatically.
            deleted_count, _ = Genre.objects.filter(pk__in=pks_to_delete).delete()

            self.stdout.write(self.style.SUCCESS(f"Successfully deleted {deleted_count} unmapped genre entries."))
        else:
            self.stdout.write(
                self.style.NOTICE(
                    "\nTo clean up the database, review the list above. Add any legitimate aliases to your "
                    "GENRE_ALIASES in dna_constants.py, then run this command again with the --delete flag."
                )
            )
