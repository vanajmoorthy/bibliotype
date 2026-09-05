"""Tests for the regenerate_dna management command's genre handling.

The command rebuilds reader types from stored Genre rows, which may predate
genre renames (e.g. "classics" → "classic fiction"). The per-book genre sets
fed to assign_reader_type must be canonicalized, or old-vocab rows silently
stop counting toward genre-share reader types.
"""

from datetime import date, timedelta

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase

from core.models import Author, Book, Genre, UserBook


class RegenerateDnaGenreCanonicalizationTests(TestCase):
    def test_old_vocab_genre_rows_drive_reader_type(self):
        """A library of pre-rename "classics" rows must still score Literary Luminary.

        Raw {"classics"} sets don't intersect the canonical {"literary fiction",
        "classic fiction"} target, so without canonicalization the share is 0 and
        the user falls back to Eclectic Reader.
        """
        user = User.objects.create_user(username="regen", email="regen@example.com", password="x")
        profile = user.userprofile
        profile.dna_data = {"reader_type": "Eclectic Reader", "user_stats": {}}
        profile.save()

        author = Author.objects.create(name="Old Vocab Author")
        classics = Genre.objects.create(name="classics")  # pre-rename Genre row
        for i in range(12):
            book = Book.objects.create(title=f"Classic Tome {i}", author=author, page_count=320)
            book.genres.add(classics)
            UserBook.objects.create(
                user=user,
                book=book,
                user_rating=4,
                date_read=date(2022, 1, 1) + timedelta(days=30 * i),
            )

        call_command("regenerate_dna", "--username", "regen")

        profile.refresh_from_db()
        self.assertEqual(profile.dna_data["reader_type"], "Literary Luminary")
        self.assertGreater(profile.dna_data["reader_type_scores"].get("Literary Luminary", 0), 0)
        # top_genres was already canonicalized before this fix — guard it stays so
        self.assertEqual(profile.dna_data["top_genres"][0][0], "classic fiction")
