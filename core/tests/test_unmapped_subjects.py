"""Unmapped API subjects are recorded so analyze_genres can surface missing genre aliases."""

from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from core.models import UnmappedSubject
from core.services.book_enrichment_service import _clean_and_canonicalize_genres, _record_unmapped_subjects


class UnmappedSubjectTests(TestCase):
    def test_collects_only_subjects_with_no_alias_or_exclusion(self):
        unmatched = set()
        genres = _clean_and_canonicalize_genres(["Fantasy", "Fiction", "Dementors"], unmatched)
        self.assertIn("fantasy", genres)
        self.assertEqual(unmatched, {"dementors"})

    def test_record_creates_then_increments(self):
        _record_unmapped_subjects({"dementors"}, "Book One")
        _record_unmapped_subjects({"dementors", "house elves"}, "Book Two")
        dementors = UnmappedSubject.objects.get(name="dementors")
        self.assertEqual(dementors.seen_count, 2)
        self.assertEqual(dementors.example_title, "Book One")
        self.assertEqual(UnmappedSubject.objects.get(name="house elves").seen_count, 1)

    def test_command_lists_unmapped_and_prunes_covered(self):
        UnmappedSubject.objects.create(name="dementors", seen_count=5, example_title="Book One")
        UnmappedSubject.objects.create(name="fantasy", seen_count=9)  # covered by an alias now

        out = StringIO()
        call_command("analyze_genres", stdout=out)
        self.assertIn("'dementors'", out.getvalue())
        self.assertNotIn("'fantasy'", out.getvalue())

        call_command("analyze_genres", "--prune", stdout=StringIO())
        self.assertEqual(list(UnmappedSubject.objects.values_list("name", flat=True)), ["dementors"])
