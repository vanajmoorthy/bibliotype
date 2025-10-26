"""
Seed the database with books from existing CSV files for testing
"""
import csv
import os
from django.core.management.base import BaseCommand
from core.models import Book, Author, Publisher, Genre


class Command(BaseCommand):
    help = 'Seed database with books from existing CSV files'

    def add_arguments(self, parser):
        parser.add_argument(
            '--csv-file',
            type=str,
            help='Specific CSV file to use'
        )

    def handle(self, *args, **options):
        csv_file = options.get('csv_file')
        
        if not csv_file:
            csv_file = 'csv/goodreads_library_export anya.csv'
        
        if not os.path.exists(csv_file):
            self.stdout.write(self.style.ERROR(f"CSV file not found: {csv_file}"))
            return
        
        self.stdout.write(f"Reading books from {csv_file}...")
        
        with open(csv_file, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            count = 0
            
            for row in reader:
                title = row.get('Title', '').strip()
                author_name = row.get('Author', '').strip()
                
                if not title or not author_name:
                    continue
                
                # Get or create author
                author, created = Author.objects.get_or_create(name=author_name)
                
                # Normalize title for lookup
                normalized_title = Book._normalize_title(title)
                
                # Get or create book
                try:
                    book = Book.objects.get(normalized_title=normalized_title, author=author)
                    book_created = False
                except Book.DoesNotExist:
                    book = Book.objects.create(
                        title=title,
                        author=author,
                        normalized_title=normalized_title,
                        page_count=int(p) if (p := row.get('Number of Pages', '')) and p.isdigit() else None,
                        average_rating=float(r) if (r := row.get('Average Rating', '')) and r else None,
                        publish_year=int(y) if (y := row.get('Original Publication Year', '')) and y.isdigit() else None,
                        isbn13=row.get('ISBN13', '').replace('="', '').replace('"', '').strip() or None,
                    )
                    book_created = True
                
                # Publisher
                pub_name = row.get('Publisher', '').strip()
                if pub_name and not book.publisher:
                    try:
                        publisher, _ = Publisher.objects.get_or_create(name=pub_name)
                        book.publisher = publisher
                        book.save()
                    except Exception as e:
                        self.stdout.write(f"Error creating publisher {pub_name}: {e}")
                        pass
                
                count += 1
                if count % 10 == 0:
                    self.stdout.write(f"Processed {count} books...")
        
        self.stdout.write(self.style.SUCCESS(f"\nProcessed {count} books from CSV"))
        self.stdout.write(f"Total books in database: {Book.objects.count()}")

