# In the new file, e.g., core/migrations/000Y_populate_normalized_titles.py

import re

from django.db import migrations


def _normalize_title_for_migration(title):
    """A standalone version of the normalization function for the migration."""
    if not title:
        return ""
    title = title.lower()
    title = re.sub(r"[\(\[].*?[\)\]]", "", title)
    title = re.sub(r"[^\w\s]", "", title)
    title = re.sub(r"\s+", "", title)
    return title.strip()


def populate_normalized_titles(apps, schema_editor):
    """
    We get the Book model from the versioned app registry and populate
    the normalized_title for all existing books.
    """
    Book = apps.get_model("core", "Book")
    for book in Book.objects.all():
        book.normalized_title = _normalize_title_for_migration(book.title)
        book.save(update_fields=["normalized_title"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0014_book_normalized_title"),  # Make sure this matches the previous migration's name!
    ]

    operations = [
        migrations.RunPython(populate_normalized_titles, migrations.RunPython.noop),
    ]
