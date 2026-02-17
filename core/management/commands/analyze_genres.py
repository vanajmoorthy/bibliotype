import logging

from django.core.management.base import BaseCommand
from django.db.models import Count

from core.dna_constants import CANONICAL_GENRE_MAP
from core.models import Genre

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Analyzes genres in the database, showing which are unmapped and provides an option to delete them."

    def add_arguments(self, parser):
        parser.add_argument(
            "--delete",
            action="store_true",
            help="Deletes all genres from the database that are not found in the CANONICAL_GENRE_MAP.",
        )

    def _log(self, msg):
        self.stdout.write(msg)
        logger.info(f"analyze_genres: {msg}")

    def _warn(self, msg):
        self.stdout.write(self.style.WARNING(msg))
        logger.warning(f"analyze_genres: {msg}")

    def handle(self, *args, **kwargs):
        self._log("Analyzing genres in the database...")

        all_genres = Genre.objects.annotate(num_books=Count("books")).all()

        unmapped_genres = []
        mapped_count = 0

        for genre in all_genres:
            if genre.name.lower().strip() not in CANONICAL_GENRE_MAP:
                unmapped_genres.append(genre)
            else:
                mapped_count += 1

        self._log(f"Found {all_genres.count()} total unique genre strings in the database.")
        self._log(f"  - {mapped_count} are correctly mapped.")
        self._warn(f"  - {len(unmapped_genres)} are UNMAPPED junk/alias genres.")

        if not unmapped_genres:
            self._log("All genres in the database are properly mapped.")
            return

        unmapped_genres.sort(key=lambda g: (-g.num_books, g.name))

        self._log("List of UNMAPPED Genres (and book count):")
        for genre in unmapped_genres:
            self._log(f"  - '{genre.name}' ({genre.num_books} books)")

        if kwargs["delete"]:
            self._warn("--- Deleting Unmapped Genres ---")

            pks_to_delete = [genre.pk for genre in unmapped_genres]
            deleted_count, _ = Genre.objects.filter(pk__in=pks_to_delete).delete()

            self._log(f"Successfully deleted {deleted_count} unmapped genre entries.")
        else:
            self._log(
                "To clean up the database, review the list above. Add any legitimate aliases to your "
                "GENRE_ALIASES in dna_constants.py, then run this command again with the --delete flag."
            )
