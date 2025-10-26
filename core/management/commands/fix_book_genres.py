"""Management command to analyze and fix problematic genre assignments"""
from django.core.management.base import BaseCommand
from core.models import Book, Genre
from core.book_enrichment_service import enrich_book_from_apis
import requests
from collections import defaultdict


class Command(BaseCommand):
    help = 'Analyze and fix books with problematic genre assignments'

    def add_arguments(self, parser):
        parser.add_argument(
            '--analyze',
            action='store_true',
            help='Just analyze without making changes'
        )
        parser.add_argument(
            '--fix-all',
            action='store_true',
            help='Fix ALL books with problematic genres'
        )
        parser.add_argument(
            '--fix-specific',
            type=str,
            help='Fix a specific book by title (partial match)'
        )

    def handle(self, *args, **options):
        if options['analyze']:
            self._analyze_genres()
        elif options['fix_all']:
            self._fix_all_books()
        elif options['fix_specific']:
            self._fix_specific_book(options['fix_specific'])
        else:
            self.stdout.write(self.style.WARNING("Use --analyze to see problematic books"))
            self.stdout.write(self.style.WARNING("Use --fix-all to fix all books"))
            self.stdout.write(self.style.WARNING("Use --fix-specific='title' to fix a specific book"))

    def _analyze_genres(self):
        """Analyze genre distribution and identify problematic books"""
        total_books = Book.objects.count()
        self.stdout.write(f"\nTotal books in database: {total_books}")
        
        if total_books == 0:
            self.stdout.write(self.style.WARNING("⚠️  No books in database"))
            return

        # Get all books with their genre counts
        books_with_genres = Book.objects.prefetch_related('genres').all()
        
        genre_counts = defaultdict(list)
        books_without_genres = []
        books_with_too_many_genres = []
        
        for book in books_with_genres:
            genres = list(book.genres.all())
            genre_names = [g.name for g in genres]
            
            # Book without genres
            if not genre_names:
                books_without_genres.append(book)
            
            # Book with too many genres (likely problematic)
            elif len(genre_names) > 5:
                books_with_too_many_genres.append((book, genre_names))
            
            # Track genre distribution
            for genre in genres:
                genre_counts[genre.name].append(book)

        # Display statistics
        self.stdout.write(f"\n{'=' * 70}")
        self.stdout.write("Genre Distribution:")
        self.stdout.write(f"{'=' * 70}")
        for genre_name, books in sorted(genre_counts.items(), key=lambda x: len(x[1]), reverse=True):
            if books:
                self.stdout.write(f"  {genre_name}: {len(books)} books")

        # Show problematic books
        if books_with_too_many_genres:
            self.stdout.write(f"\n{'=' * 70}")
            self.stdout.write(f"Books with >5 genres ({len(books_with_too_many_genres)} total):")
            self.stdout.write(f"{'=' * 70}")
            for book, genres in books_with_too_many_genres[:20]:  # Show first 20
                self.stdout.write(f"\n{book.title} by {book.author.name}")
                self.stdout.write(f"  Genres ({len(genres)}): {', '.join(genres)}")

        if books_without_genres:
            self.stdout.write(f"\n{'=' * 70}")
            self.stdout.write(f"Books without genres ({len(books_without_genres)} total)")
            self.stdout.write(f"{'=' * 70}")

    def _fix_all_books(self):
        """Re-enrich all books to fix genre assignments"""
        self.stdout.write(self.style.WARNING("\n⚠️  This will re-enrich all books with genres, making API calls."))
        confirm = input("Continue? (yes/no): ")
        if confirm.lower() != 'yes':
            self.stdout.write("Cancelled.")
            return

        books = Book.objects.all()
        self.stdout.write(f"\nRe-enriching {books.count()} books...")

        with requests.Session() as session:
            for i, book in enumerate(books, 1):
                try:
                    # Clear existing genres
                    book.genres.clear()
                    
                    # Re-enrich
                    enrich_book_from_apis(book, session, slow_down=True)
                    
                    genres = list(book.genres.all().values_list('name', flat=True))
                    self.stdout.write(f"[{i}/{books.count()}] {book.title}: {genres or 'No genres found'}")
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"Error with {book.title}: {e}"))

        self.stdout.write(self.style.SUCCESS("\n✅ Finished re-enriching books"))

    def _fix_specific_book(self, title_query):
        """Fix a specific book's genres"""
        books = Book.objects.filter(title__icontains=title_query)
        
        if books.count() == 0:
            self.stdout.write(self.style.ERROR(f"No books found matching '{title_query}'"))
            return
        
        if books.count() > 10:
            self.stdout.write(self.style.WARNING(f"Found {books.count()} books matching '{title_query}'. Too many. Be more specific."))
            return

        for book in books:
            self.stdout.write(f"\nFound: {book.title} by {book.author.name}")
            genres = list(book.genres.all().values_list('name', flat=True))
            self.stdout.write(f"Current genres: {genres or 'None'}")
            
            confirm = input(f"Re-enrich this book? (yes/no): ")
            if confirm.lower() == 'yes':
                try:
                    with requests.Session() as session:
                        book.genres.clear()
                        enrich_book_from_apis(book, session, slow_down=False)
                        new_genres = list(book.genres.all().values_list('name', flat=True))
                        self.stdout.write(self.style.SUCCESS(f"✅ Updated genres: {new_genres}"))
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"Error: {e}"))

