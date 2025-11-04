#!/usr/bin/env python
"""
Generate synthetic Goodreads CSV files for testing recommendation system
Creates 10-15 CSVs with intentional similarity patterns
"""
import csv
import random
from datetime import datetime, timedelta
from collections import Counter
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Django setup
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'bibliotype.settings')
django.setup()

from core.models import Book, Author, Publisher, Genre


def get_books_by_genres(genre_names, min_count=20):
    """Get books filtered by genre names (case-insensitive partial match)"""
    books = Book.objects.exclude(isbn13__isnull=True).exclude(isbn13='').prefetch_related('genres', 'author', 'publisher')
    
    if not genre_names:
        return list(books[:500])  # Get larger pool for eclectic readers
    
    # Filter books that have any of the specified genres
    filtered_books = []
    for book in books:
        book_genres = [g.name.lower() for g in book.genres.all()]
        if any(any(genre_name.lower() in bg for bg in book_genres) for genre_name in genre_names):
            filtered_books.append(book)
        if len(filtered_books) >= 500:
            break
    
    return filtered_books


def get_recent_books(year_threshold=2010):
    """Get books published after a certain year"""
    books = Book.objects.exclude(isbn13__isnull=True).exclude(isbn13='').filter(
        publish_year__gte=year_threshold
    ).prefetch_related('genres', 'author', 'publisher')
    return list(books[:500])


def generate_csv_row(book, rating_distribution, shelf_distribution, date_range_days, rating_bias=None):
    """Generate a single CSV row for a book"""
    # Determine rating based on distribution and bias
    if random.random() < 0.7:  # 70% chance of being rated
        if rating_bias == 'harsh':
            # Harsh rater: mostly 2-3 stars, fewer 4-5
            my_rating = random.choices([1, 2, 3, 4, 5], weights=[0.15, 0.35, 0.30, 0.15, 0.05])[0]
        elif rating_bias == 'generous':
            # Generous rater: mostly 4-5 stars
            my_rating = random.choices([1, 2, 3, 4, 5], weights=[0.02, 0.08, 0.20, 0.35, 0.35])[0]
        else:
            # Normal distribution
            my_rating = random.choices([1, 2, 3, 4, 5], weights=rating_distribution)[0]
    else:
        my_rating = 0
    
    # Random shelf based on distribution
    shelf = random.choices(['read', 'currently-reading', 'to-read'], weights=shelf_distribution)[0]
    
    # Date added within range
    start_date = datetime.now() - timedelta(days=date_range_days)
    date_added = start_date + timedelta(days=random.randint(0, date_range_days))
    
    # Date read (only if on 'read' shelf)
    date_read = ''
    if shelf == 'read' and random.random() < 0.8:
        read_date = date_added + timedelta(days=random.randint(0, 90))
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
    author_parts = book.author.name.split()
    if len(author_parts) > 1:
        author_lf = f"{author_parts[-1]}, {' '.join(author_parts[:-1])}"
    else:
        author_lf = author_name
    
    # Average rating from book or default
    avg_rating = book.average_rating or book.google_books_average_rating or 3.50
    avg_rating_str = str(round(avg_rating, 2))
    
    return [
        str(random.randint(100000, 999999)),  # Book Id
        book.title,  # Title
        author_name,  # Author
        author_lf,  # Author l-f
        '',  # Additional Authors
        isbn,  # ISBN
        isbn13,  # ISBN13
        str(my_rating),  # My Rating
        avg_rating_str,  # Average Rating
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
    ]


def generate_synthetic_csv(
    username,
    books,
    num_books=30,
    shared_books=None,
    rating_distribution=[0.05, 0.10, 0.20, 0.35, 0.30],
    shelf_distribution=[0.6, 0.1, 0.3],
    date_range_days=1095,  # 3 years
    rating_bias=None,
    filename=None
):
    """
    Generate a synthetic Goodreads CSV for a user
    
    Args:
        username: Username for the CSV
        books: List of Book objects to choose from
        num_books: Number of books to include
        shared_books: List of Book objects that must be included (for similarity)
        rating_distribution: Weights for ratings [1,2,3,4,5]
        shelf_distribution: Weights for shelves [read, currently-reading, to-read]
        date_range_days: How many days back to generate dates
        rating_bias: 'harsh' or 'generous' to override rating_distribution
        filename: Optional custom filename
    """
    if len(books) < num_books:
        print(f"Warning: Only {len(books)} books available, using all of them")
        num_books = len(books)
    
    # Start with shared books if provided
    selected_books = []
    if shared_books:
        selected_books = shared_books.copy()
        available_books = [b for b in books if b not in selected_books]
        remaining = num_books - len(selected_books)
        if remaining > 0 and len(available_books) > 0:
            selected_books.extend(random.sample(available_books, min(remaining, len(available_books))))
    else:
        selected_books = random.sample(books, num_books)
    
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
    
    for book in selected_books:
        csv_rows.append(generate_csv_row(
            book, rating_distribution, shelf_distribution, date_range_days, rating_bias
        ))
    
    # Write to CSV file
    if not filename:
        filename = f'csv/goodreads_library_export {username}.csv'
    
    with open(filename, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerows(csv_rows)
    
    print(f"Generated {filename} with {len(selected_books)} books")
    return filename, selected_books


def create_sci_fi_fantasy_group():
    """Create 3 sci-fi/fantasy readers with shared books"""
    print("\n=== Creating Sci-Fi/Fantasy Group ===")
    
    # Get sci-fi/fantasy books
    genre_keywords = ['science fiction', 'fantasy', 'sci-fi', 'sf', 'speculative']
    books = get_books_by_genres(genre_keywords)
    
    if len(books) < 30:
        print(f"Warning: Only {len(books)} sci-fi/fantasy books found. Using broader selection.")
        books = list(Book.objects.exclude(isbn13__isnull=True).exclude(isbn13='')[:200])
    
    # Create shared highly-rated books pool (books that appear in multiple CSVs)
    shared_pool = random.sample(books, min(15, len(books)))
    
    results = []
    
    # Fan 1: Generous rater, loves classics
    shared1 = random.sample(shared_pool, 8)
    filename1, books1 = generate_synthetic_csv(
        'synthetic_sf_fan1',
        books,
        num_books=35,
        shared_books=shared1,
        rating_distribution=[0.02, 0.08, 0.20, 0.35, 0.35],
        rating_bias='generous'
    )
    results.append(('synthetic_sf_fan1', books1))
    
    # Fan 2: Moderate rater, shares some books with fan1
    shared2 = shared1[:5] + random.sample([b for b in books if b not in shared1], 8)
    filename2, books2 = generate_synthetic_csv(
        'synthetic_sf_fan2',
        books,
        num_books=40,
        shared_books=shared2,
        rating_distribution=[0.05, 0.10, 0.20, 0.35, 0.30]
    )
    results.append(('synthetic_sf_fan2', books2))
    
    # Fan 3: Harsher rater, shares some books with fan1 and fan2
    shared3 = shared1[:3] + shared2[:3] + random.sample([b for b in books if b not in shared1 and b not in shared2], 6)
    filename3, books3 = generate_synthetic_csv(
        'synthetic_sf_fan3',
        books,
        num_books=32,
        shared_books=shared3,
        rating_distribution=[0.15, 0.35, 0.30, 0.15, 0.05],
        rating_bias='harsh'
    )
    results.append(('synthetic_sf_fan3', books3))
    
    return results


def create_literary_fiction_group():
    """Create 3 literary fiction readers with shared classics"""
    print("\n=== Creating Literary Fiction Group ===")
    
    # Get literary fiction books
    genre_keywords = ['literary fiction', 'fiction', 'literature', 'classic', 'contemporary fiction']
    books = get_books_by_genres(genre_keywords)
    
    if len(books) < 30:
        print(f"Warning: Only {len(books)} literary fiction books found. Using broader selection.")
        books = list(Book.objects.exclude(isbn13__isnull=True).exclude(isbn13='')[:200])
    
    # Shared literary classics
    shared_pool = random.sample(books, min(12, len(books)))
    
    results = []
    
    # Reader 1: Classic literature focus
    shared1 = random.sample(shared_pool, 10)
    filename1, books1 = generate_synthetic_csv(
        'synthetic_lit_fiction1',
        books,
        num_books=38,
        shared_books=shared1,
        rating_distribution=[0.03, 0.10, 0.25, 0.35, 0.27],
        date_range_days=1460  # 4 years
    )
    results.append(('synthetic_lit_fiction1', books1))
    
    # Reader 2: Shares classics with reader1
    shared2 = shared1[:7] + random.sample([b for b in books if b not in shared1], 9)
    filename2, books2 = generate_synthetic_csv(
        'synthetic_lit_fiction2',
        books,
        num_books=42,
        shared_books=shared2,
        rating_distribution=[0.04, 0.12, 0.23, 0.33, 0.28]
    )
    results.append(('synthetic_lit_fiction2', books2))
    
    # Reader 3: More eclectic literary taste
    shared3 = shared1[:5] + shared2[:4] + random.sample([b for b in books if b not in shared1 and b not in shared2], 8)
    filename3, books3 = generate_synthetic_csv(
        'synthetic_lit_fiction3',
        books,
        num_books=35,
        shared_books=shared3,
        rating_distribution=[0.05, 0.10, 0.20, 0.35, 0.30]
    )
    results.append(('synthetic_lit_fiction3', books3))
    
    return results


def create_contemporary_fiction_group():
    """Create 2 contemporary fiction readers"""
    print("\n=== Creating Contemporary Fiction Group ===")
    
    # Get contemporary books (recent publication years)
    books = get_recent_books(year_threshold=2010)
    
    if len(books) < 20:
        books = get_books_by_genres(['fiction', 'contemporary'])
    
    if len(books) < 20:
        books = list(Book.objects.exclude(isbn13__isnull=True).exclude(isbn13='')[:200])
    
    results = []
    
    # Reader 1: Modern fiction enthusiast
    shared1 = random.sample(books, min(6, len(books)))
    filename1, books1 = generate_synthetic_csv(
        'synthetic_contemporary1',
        books,
        num_books=28,
        shared_books=shared1,
        rating_distribution=[0.05, 0.10, 0.25, 0.30, 0.30],
        date_range_days=730  # 2 years
    )
    results.append(('synthetic_contemporary1', books1))
    
    # Reader 2: Shares some books with reader1
    shared2 = shared1[:4] + random.sample([b for b in books if b not in shared1], 7)
    filename2, books2 = generate_synthetic_csv(
        'synthetic_contemporary2',
        books,
        num_books=31,
        shared_books=shared2,
        rating_distribution=[0.04, 0.12, 0.28, 0.32, 0.24],
        date_range_days=730
    )
    results.append(('synthetic_contemporary2', books2))
    
    return results


def create_eclectic_readers():
    """Create 2-3 eclectic readers with diverse tastes"""
    print("\n=== Creating Eclectic Readers ===")
    
    # Get diverse pool of books
    books = list(Book.objects.exclude(isbn13__isnull=True).exclude(isbn13='').prefetch_related('genres', 'author', 'publisher')[:500])
    
    results = []
    
    # Eclectic 1: Wide variety, moderate ratings
    filename1, books1 = generate_synthetic_csv(
        'synthetic_eclectic1',
        books,
        num_books=45,
        rating_distribution=[0.05, 0.15, 0.30, 0.30, 0.20],
        date_range_days=1825  # 5 years
    )
    results.append(('synthetic_eclectic1', books1))
    
    # Eclectic 2: Different selection, less overlap
    filename2, books2 = generate_synthetic_csv(
        'synthetic_eclectic2',
        books,
        num_books=38,
        rating_distribution=[0.08, 0.18, 0.32, 0.28, 0.14],
        date_range_days=1460  # 4 years
    )
    results.append(('synthetic_eclectic2', books2))
    
    # Eclectic 3: Another diverse reader
    filename3, books3 = generate_synthetic_csv(
        'synthetic_eclectic3',
        books,
        num_books=42,
        rating_distribution=[0.06, 0.12, 0.26, 0.32, 0.24],
        date_range_days=1095  # 3 years
    )
    results.append(('synthetic_eclectic3', books3))
    
    return results


def create_special_test_cases(all_generated_books):
    """Create special test cases: high overlap, harsh rater, recent reader"""
    print("\n=== Creating Special Test Cases ===")
    
    books = list(Book.objects.exclude(isbn13__isnull=True).exclude(isbn13='').prefetch_related('genres', 'author', 'publisher')[:500])
    
    results = []
    
    # High overlap: Shares many books with another CSV
    if all_generated_books:
        # Take books from the first generated CSV
        source_csv_name, source_books = all_generated_books[0]
        shared_books = source_books[:20]  # Share 20 books
        filename1, books1 = generate_synthetic_csv(
            'synthetic_high_overlap',
            books,
            num_books=35,
            shared_books=shared_books,
            rating_distribution=[0.05, 0.10, 0.20, 0.35, 0.30]
        )
        results.append(('synthetic_high_overlap', books1))
    
    # Harsh rater: Lower average ratings
    filename2, books2 = generate_synthetic_csv(
        'synthetic_harsh_rater',
        books,
        num_books=30,
        rating_distribution=[0.20, 0.35, 0.30, 0.12, 0.03],
        rating_bias='harsh',
        shelf_distribution=[0.65, 0.05, 0.30]  # More read books
    )
    results.append(('synthetic_harsh_rater', books2))
    
    # Recent reader: Focuses on recent publication years
    recent_books = get_recent_books(year_threshold=2015)
    if len(recent_books) < 25:
        recent_books = books
    
    filename3, books3 = generate_synthetic_csv(
        'synthetic_recent_reader',
        recent_books,
        num_books=32,
        rating_distribution=[0.05, 0.10, 0.20, 0.35, 0.30],
        date_range_days=365  # Only last year
    )
    results.append(('synthetic_recent_reader', books3))
    
    return results


def main():
    """Generate all synthetic test CSVs"""
    print("Starting synthetic CSV generation for recommendation testing...")
    print(f"Total books in database: {Book.objects.exclude(isbn13__isnull=True).exclude(isbn13='').count()}")
    
    all_generated_books = []
    
    # Create similarity groups
    try:
        sf_group = create_sci_fi_fantasy_group()
        all_generated_books.extend(sf_group)
    except Exception as e:
        print(f"Error creating sci-fi/fantasy group: {e}")
    
    try:
        lit_group = create_literary_fiction_group()
        all_generated_books.extend(lit_group)
    except Exception as e:
        print(f"Error creating literary fiction group: {e}")
    
    try:
        cont_group = create_contemporary_fiction_group()
        all_generated_books.extend(cont_group)
    except Exception as e:
        print(f"Error creating contemporary fiction group: {e}")
    
    try:
        eclectic_group = create_eclectic_readers()
        all_generated_books.extend(eclectic_group)
    except Exception as e:
        print(f"Error creating eclectic readers: {e}")
    
    try:
        special_group = create_special_test_cases(all_generated_books)
        all_generated_books.extend(special_group)
    except Exception as e:
        print(f"Error creating special test cases: {e}")
    
    print("\n" + "="*60)
    print(f"Successfully generated {len(all_generated_books)} CSV files:")
    for name, _ in all_generated_books:
        print(f"  - goodreads_library_export {name}.csv")
    print("="*60)


if __name__ == '__main__':
    main()

