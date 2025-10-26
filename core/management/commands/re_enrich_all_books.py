"""Simple command to re-enrich all books with updated genre logic"""
from django.core.management.base import BaseCommand
from core.models import Book
from core.book_enrichment_service import enrich_book_from_apis
import requests


class Command(BaseCommand):
    help = 'Re-enrich all books with improved genre fetching'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be done without making changes'
        )
        parser.add_argument(
            '--limit',
            type=int,
            help='Limit number of books to process'
        )
        parser.add_argument(
            '--yes',
            action='store_true',
            help='Skip confirmation prompt'
        )

    def handle(self, *args, **options):
        total_books = Book.objects.count()
        
        if total_books == 0:
            self.stdout.write(self.style.WARNING("No books in database."))
            return

        if options['dry_run']:
            self.stdout.write(self.style.WARNING("\n🔍 DRY RUN MODE - No changes will be made\n"))

        # Show current state
        books_without_genres = Book.objects.filter(genres__isnull=True).count()
        books_with_genres = total_books - books_without_genres
        
        self.stdout.write(f"Total books: {total_books}")
        self.stdout.write(f"Books with genres: {books_with_genres}")
        self.stdout.write(f"Books without genres: {books_without_genres}")

        if not options['dry_run'] and not options.get('yes'):
            self.stdout.write(self.style.WARNING(
                f"\n⚠️  This will make API calls to Google Books and Open Library "
                f"for {total_books} books."
            ))
            confirm = input("Continue? (yes/no): ")
            if confirm.lower() != 'yes':
                self.stdout.write("Cancelled.")
                return

        # Get books to process
        books = Book.objects.all()
        if options['limit']:
            books = books[:options['limit']]
        
        self.stdout.write(f"\nRe-enriching {books.count()} books...")
        self.stdout.write("=" * 70)

        with requests.Session() as session:
            updated_count = 0
            error_count = 0
            
            for i, book in enumerate(books, 1):
                try:
                    if not options['dry_run']:
                        # Clear existing genres to start fresh
                        old_genres = list(book.genres.all().values_list('name', flat=True))
                        book.genres.clear()
                        # Must save to persist the cleared ManyToMany relationships
                        book.save()
                        
                        # Force re-fetch by resetting flags
                        original_google_check = book.google_books_last_checked
                        book.google_books_last_checked = None
                        
                        # Re-enrich with fresh data
                        enrich_book_from_apis(book, session, slow_down=True)
                        
                        # Restore the google_books timestamp to avoid re-checking
                        book.google_books_last_checked = original_google_check
                        
                        # Refresh from DB to get the actual current genres
                        book.refresh_from_db()
                        genres = list(book.genres.all().values_list('name', flat=True))
                        
                        if genres:
                            self.stdout.write(f"[{i}/{books.count()}] ✓ {book.title}")
                            if old_genres:
                                self.stdout.write(f"    Old genres: {old_genres}")
                            self.stdout.write(f"    New genres: {genres}")
                            updated_count += 1
                        else:
                            self.stdout.write(f"[{i}/{books.count()}] ⚠  {book.title}")
                            if old_genres:
                                self.stdout.write(f"    Had genres: {old_genres}")
                            self.stdout.write(f"    No genres found from API")
                    else:
                        self.stdout.write(f"[{i}/{books.count()}] Would process: {book.title}")
                        
                except Exception as e:
                    error_count += 1
                    self.stdout.write(self.style.ERROR(f"[{i}/{books.count()}] ✗ Error with {book.title}: {e}"))

        self.stdout.write("\n" + "=" * 70)
        if not options['dry_run']:
            self.stdout.write(self.style.SUCCESS(
                f"\n✅ Completed! Updated {updated_count} books, {error_count} errors"
            ))
        else:
            self.stdout.write(self.style.WARNING("\n🔍 DRY RUN - No changes made"))

