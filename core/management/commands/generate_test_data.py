"""
Generate synthetic Goodreads CSV files for testing
"""
import csv
import random
from datetime import datetime, timedelta
from django.core.management.base import BaseCommand
from core.models import Book, Author, User
from django.contrib.auth.models import User as DjangoUser


class Command(BaseCommand):
    help = 'Generate synthetic Goodreads CSVs and test users'

    def add_arguments(self, parser):
        parser.add_argument(
            '--num-users',
            type=int,
            default=5,
            help='Number of test users to create'
        )
        parser.add_argument(
            '--books-per-user',
            type=int,
            default=30,
            help='Number of books per user'
        )

    def handle(self, *args, **options):
        num_users = options['num_users']
        books_per_user = options['books_per_user']
        
        # Get books from database - try with ISBN first, then without
        books = list(Book.objects.exclude(isbn13__isnull=True).exclude(isbn13=''))
        if len(books) < 20:
            books = list(Book.objects.all()[:200])
        
        if len(books) < 20:
            self.stdout.write(self.style.ERROR(f"Not enough books in database. Found {len(books)} books. Need at least 20."))
            self.stdout.write(self.style.WARNING("Please upload a CSV file first to populate the database with books."))
            return
        
        passwords = {}
        
        for i in range(1, num_users + 1):
            username = f'test_reader{i}'
            
            # Create or get user
            if DjangoUser.objects.filter(username=username).exists():
                user = DjangoUser.objects.get(username=username)
                self.stdout.write(f"User {username} already exists")
            else:
                password = 'testpass123'
                user = DjangoUser.objects.create_user(
                    username=username,
                    email=f'reader{i}@test.com',
                    password=password,
                    first_name=f'Test Reader {i}'
                )
                passwords[username] = password
                self.stdout.write(self.style.SUCCESS(f"Created user {username}"))
            
            # Generate CSV
            csv_file = self.generate_csv(username, books, books_per_user)
            self.stdout.write(f"Generated CSV: {csv_file}")
        
        self.stdout.write(self.style.SUCCESS(f"\nGenerated {num_users} test users and CSVs!"))
        self.stdout.write("\nGenerated files:")
        for i in range(1, num_users + 1):
            self.stdout.write(f"  - csv/goodreads_library_export test_reader{i}.csv")
        
        self.stdout.write(f"\nAll test users have password: testpass123")

    def generate_csv(self, username, books, num_books):
        """Generate a synthetic Goodreads CSV for a user"""
        
        selected_books = random.sample(books, min(num_books, len(books)))
        
        # Generate CSV data
        csv_rows = []
        
        # Add header
        csv_rows.append([
            'Book Id', 'Title', 'Author', 'Author l-f', 'Additional Authors',
            'ISBN', 'ISBN13', 'My Rating', 'Average Rating', 'Publisher',
            'Binding', 'Number of Pages', 'Year Published', 'Original Publication Year',
            'Date Read', 'Date Added', 'Bookshelves', 'Bookshelves with positions',
            'Exclusive Shelf', 'My Review', 'Spoiler', 'Private Notes', 'Read Count', 'Owned Copies'
        ])
        
        # Generate random dates
        start_date = datetime.now() - timedelta(days=365)
        
        for book in selected_books:
            # Random rating (1-5 stars, or 0 for unrated)
            if random.random() < 0.7:  # 70% chance of being rated
                my_rating = random.choices([1, 2, 3, 4, 5], weights=[0.05, 0.10, 0.20, 0.35, 0.30])[0]
            else:
                my_rating = 0
            
            # Random shelf (60% read, 10% currently-reading, 30% to-read)
            shelf = random.choices(['read', 'currently-reading', 'to-read'], weights=[0.6, 0.1, 0.3])[0]
            
            # Date added
            date_added = start_date + timedelta(days=random.randint(0, 365))
            
            # Date read (only if on 'read' shelf)
            date_read = ''
            if shelf == 'read' and random.random() < 0.8:
                read_date = date_added + timedelta(days=random.randint(0, 60))
                date_read = read_date.strftime('%Y/%m/%d')
            
            # Generate synthetic review
            review = ''
            if my_rating >= 4 and random.random() < 0.5:
                review = random.choice([
                    "Amazing book! Really enjoyed the writing style.",
                    "One of my favorites. Highly recommend!",
                    "Beautiful prose and engaging characters.",
                    "Captivating from start to finish.",
                    "Thought-provoking and well-written."
                ])
            elif my_rating <= 2 and random.random() < 0.5:
                review = random.choice([
                    "Not my cup of tea. Found it boring.",
                    "Didn't connect with the characters.",
                    "Too slow for my taste."
                ])
            
            # Format dates
            date_added_str = date_added.strftime('%Y/%m/%d')
            
            # Publisher name
            pub_name = book.publisher.name if book.publisher else ''
            
            # Binding
            binding = random.choice(['Paperback', 'Hardcover', 'ebook', 'Kindle Edition'])
            
            # ISBN - handle books without ISBN
            isbn = ''
            isbn13 = ''
            if book.isbn13:
                isbn = book.isbn13
                isbn13 = f'="{book.isbn13}"'
            elif book.title and book.author.name:
                # Generate fake ISBN for testing
                fake_isbn = f"{random.randint(1000000000, 9999999999)}"
                isbn = fake_isbn
                isbn13 = f'="{fake_isbn}"'
            
            # Author information
            author_name = book.author.name
            author_lf = f"{book.author.name.split()[-1]}, {' '.join(book.author.name.split()[:-1])}"
            
            csv_rows.append([
                str(random.randint(100000, 999999)),  # Book Id
                book.title,  # Title
                author_name,  # Author
                author_lf,  # Author l-f
                '',  # Additional Authors
                isbn,  # ISBN
                isbn13,  # ISBN13
                str(my_rating),  # My Rating
                str(round(book.average_rating or 3.5, 2) if book.average_rating else '3.50'),  # Average Rating
                pub_name,  # Publisher
                binding,  # Binding
                str(book.page_count or 300),  # Number of Pages
                str(book.publish_year or 2000) if book.publish_year else '',  # Year Published
                str(book.publish_year or 2000) if book.publish_year else '',  # Original Publication Year
                date_read,  # Date Read
                date_added_str,  # Date Added
                shelf,  # Bookshelves
                f'{shelf} (#1)',  # Bookshelves with positions
                shelf,  # Exclusive Shelf
                review,  # My Review
                '',  # Spoiler
                '',  # Private Notes
                str(random.randint(0, 2)),  # Read Count
                str(random.randint(0, 1)),  # Owned Copies
            ])
        
        # Write to CSV file
        filename = f'csv/goodreads_library_export {username}.csv'
        
        with open(filename, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerows(csv_rows)
        
        return filename

