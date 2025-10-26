#!/usr/bin/env python
"""
Generate synthetic Goodreads CSV files for testing
"""
import csv
import random
from datetime import datetime, timedelta
from django.core.management.base import BaseCommand
from core.models import Book, Author, Publisher
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Django setup
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'bibliotype.settings')
django.setup()

def generate_synthetic_csv(username, num_books=20, filename=None):
    """Generate a synthetic Goodreads CSV for a user"""
    
    # Get random books from database
    books = list(Book.objects.exclude(isbn13__isnull=True).exclude(isbn13='')[:100])
    
    if len(books) < 10:
        print(f"Not enough books in database. Found {len(books)} books.")
        return None
    
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
        
        # Random shelf
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
        
        # ISBN
        isbn = book.isbn13 if book.isbn13 else ''
        isbn13 = f'="{book.isbn13}"' if book.isbn13 else ''
        
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
    if not filename:
        filename = f'csv/goodreads_library_export {username}.csv'
    
    with open(filename, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerows(csv_rows)
    
    print(f"Generated {filename} with {len(selected_books)} books")
    return filename


def create_test_users():
    """Create test users and upload their CSVs"""
    from django.contrib.auth.models import User
    from django.test import Client
    import subprocess
    import sys
    
    # List of test users
    test_users = [
        ('test_reader1', 'reader1@test.com'),
        ('test_reader2', 'reader2@test.com'),
        ('test_reader3', 'reader3@test.com'),
        ('test_reader4', 'reader4@test.com'),
        ('test_reader5', 'reader5@test.com'),
    ]
    
    passwords = {}
    
    for username, email in test_users:
        # Check if user exists
        if User.objects.filter(username=username).exists():
            user = User.objects.get(username=username)
            print(f"User {username} already exists")
        else:
            # Create user
            password = 'testpass123'
            user = User.objects.create_user(
                username=username,
                email=email,
                password=password
            )
            passwords[username] = password
            print(f"Created user {username} with email {email}")
        
        # Generate CSV for this user
        csv_file = generate_synthetic_csv(username, num_books=30)
        
        if csv_file and user:
            print(f"Generated CSV for {username}: {csv_file}")
    
    return passwords


if __name__ == '__main__':
    passwords = create_test_users()
    print("\nGenerated test users and CSVs!")
    print("\nYou can now manually upload these CSVs to each user account for testing.")

