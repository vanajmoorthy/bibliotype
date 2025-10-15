import re

from django.db import migrations
from django.db.models import Count
from django.db.models.functions import Lower


def _normalize(name):
    """Aggressive cleaning function for the lookup key."""
    name = name.lower()
    name = re.sub(r"[^\w\s]", "", name)
    name = re.sub(r"\s+", "", name)
    return name


def merge_duplicate_authors(apps, schema_editor):
    """
    Finds and merges duplicate authors and their books, then populates the normalized_name field.
    """
    Author = apps.get_model("core", "Author")
    Book = apps.get_model("core", "Book")

    # Use a temporary normalized name to group potential duplicates
    authors_to_check = []
    for author in Author.objects.all():
        authors_to_check.append({"id": author.id, "name": author.name, "normalized": _normalize(author.name)})

    # Group authors by their normalized name
    from collections import defaultdict

    grouped_authors = defaultdict(list)
    for author_data in authors_to_check:
        grouped_authors[author_data["normalized"]].append(author_data)

    # Now iterate through the groups and merge any that have duplicates
    for normalized_name, author_group in grouped_authors.items():
        if len(author_group) > 1:
            # We have duplicates. Sort them to pick a consistent canonical author (e.g., by lowest ID).
            author_group.sort(key=lambda x: x["id"])
            canonical_author_data = author_group.pop(0)
            canonical_author = Author.objects.get(id=canonical_author_data["id"])

            print(f"  Merging authors for normalized name '{normalized_name}' into '{canonical_author.name}':")

            for duplicate_data in author_group:
                duplicate_author = Author.objects.get(id=duplicate_data["id"])
                print(f"    - Processing duplicate: '{duplicate_author.name}'")

                # Now, handle the books for this duplicate author
                for duplicate_book in Book.objects.filter(author=duplicate_author):
                    # Check if a book with the same title already exists for the canonical author
                    canonical_book, created = Book.objects.get_or_create(
                        title=duplicate_book.title, author=canonical_author
                    )

                    if not created:
                        # The book already exists, so we merge and delete the duplicate
                        print(f'      - Merging book: "{duplicate_book.title}"')
                        # Combine lists, ensuring no duplicates
                        canonical_book.canon_lists = list(set(canonical_book.canon_lists + duplicate_book.canon_lists))
                        canonical_book.awards_won = list(set(canonical_book.awards_won + duplicate_book.awards_won))
                        canonical_book.shortlists = list(set(canonical_book.shortlists + duplicate_book.shortlists))
                        # Take the max of numeric fields
                        canonical_book.nyt_bestseller_weeks = max(
                            canonical_book.nyt_bestseller_weeks, duplicate_book.nyt_bestseller_weeks
                        )
                        canonical_book.save()
                        duplicate_book.delete()
                    else:
                        # The book didn't exist for the canonical author, so just re-assign it.
                        # This happens automatically because get_or_create just created it with the right author.
                        # We just need to delete the old one.
                        duplicate_book.delete()

                # After re-assigning/deleting all books, delete the duplicate author
                duplicate_author.delete()

    # Finally, populate the normalized_name for all the clean, unique authors
    print("\nPopulating normalized_name for all remaining authors...")
    for author in Author.objects.all():
        author.normalized_name = _normalize(author.name)
        author.save(update_fields=["normalized_name"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0009_author_normalized_name"),
    ]

    operations = [
        migrations.RunPython(merge_duplicate_authors, migrations.RunPython.noop),
    ]
