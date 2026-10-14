import logging

from django.core.management.base import BaseCommand

from core.models import UnmappedSubject
from core.services.book_enrichment_service import is_subject_covered

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Lists the most common API subjects that matched no genre alias, so missing aliases can be added."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=50, help="How many subjects to list")
        parser.add_argument(
            "--prune", action="store_true", help="Delete recorded subjects that an alias or exclusion now covers"
        )

    def _log(self, msg):
        self.stdout.write(msg)
        logger.info(f"analyze_genres: {msg}")

    def handle(self, *args, **kwargs):
        subjects = UnmappedSubject.objects.order_by("-seen_count", "name")

        if kwargs["prune"]:
            covered = [s.pk for s in subjects.only("pk", "name") if is_subject_covered(s.name)]
            UnmappedSubject.objects.filter(pk__in=covered).delete()
            self._log(f"Pruned {len(covered)} subjects now covered by an alias or exclusion.")

        self._log(f"{subjects.count()} unmapped subjects recorded.")
        shown = 0
        for subject in subjects.iterator():
            if shown >= kwargs["limit"]:
                break
            if is_subject_covered(subject.name):
                continue  # already handled in code; --prune removes these
            self._log(f"  {subject.seen_count:>6}  '{subject.name}'  (e.g. '{subject.example_title}')")
            shown += 1

        if shown:
            self._log(
                "Add genuine aliases to GENRE_ALIASES and junk to EXCLUDED_GENRES in dna_constants.py, "
                "then run with --prune."
            )
