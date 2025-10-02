from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count

from core.models import Book, Genre


class Command(BaseCommand):
    help = "Finds and merges duplicate Book entries based on ISBN13."

    @transaction.atomic
    def handle(self, *args, **options):
        self.stdout.write("🔍 Finding books with duplicate ISBNs...")

        # Find all ISBNs that are associated with more than one book
        duplicate_isbns = (
            Book.objects.values("isbn13")
            .annotate(isbn_count=Count("isbn13"))
            .filter(isbn_count__gt=1, isbn13__isnull=False)
        )

        if not duplicate_isbns:
            self.stdout.write(self.style.SUCCESS("✅ No duplicate ISBNs found. Your data is clean!"))
            return

        self.stdout.write(self.style.WARNING(f"Found {len(duplicate_isbns)} ISBNs with duplicate entries. Merging..."))

        total_merged = 0
        for item in duplicate_isbns:
            isbn = item["isbn13"]
            self.stdout.write(f"\nMerging duplicates for ISBN: {isbn}")

            # Get all books sharing this ISBN, oldest first
            duplicate_books = list(Book.objects.filter(isbn13=isbn).order_by("id"))

            # The first book in the list will be our "canonical" one
            canonical_book = duplicate_books[0]
            self.stdout.write(f"  -> Canonical book: '{canonical_book.title}' (ID: {canonical_book.id})")

            # The rest are the ones to be merged and deleted
            books_to_merge = duplicate_books[1:]

            for book in books_to_merge:
                self.stdout.write(f"     - Merging and deleting: '{book.title}' (ID: {book.id})")

                # --- Merge Logic ---
                # Add the bestseller weeks together
                canonical_book.nyt_bestseller_weeks += book.nyt_bestseller_weeks

                # Combine genres without creating duplicates
                existing_genres = set(canonical_book.genres.all())
                new_genres = set(book.genres.all())
                genres_to_add = new_genres - existing_genres
                if genres_to_add:
                    canonical_book.genres.add(*genres_to_add)

                # Add other merge logic here if needed (e.g., take the longest page count)
                if book.page_count and (not canonical_book.page_count or book.page_count > canonical_book.page_count):
                    canonical_book.page_count = book.page_count

                # Delete the duplicate book
                book.delete()
                total_merged += 1

            # Save the updated canonical book
            canonical_book.save()

        self.stdout.write(self.style.SUCCESS(f"\n✅ Finished. Merged {total_merged} duplicate book entries."))
