"""
Generate synthetic Goodreads CSV files for test users.

Ported from the legacy `csv/generate_test_data.py` standalone script as part of
US-009 so the workflow is discoverable via `manage.py help`.
"""

import csv
import os
import random
from datetime import datetime, timedelta

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from core.models import Book


GOODREADS_HEADER = [
    "Book Id",
    "Title",
    "Author",
    "Author l-f",
    "Additional Authors",
    "ISBN",
    "ISBN13",
    "My Rating",
    "Average Rating",
    "Publisher",
    "Binding",
    "Number of Pages",
    "Year Published",
    "Original Publication Year",
    "Date Read",
    "Date Added",
    "Bookshelves",
    "Bookshelves with positions",
    "Exclusive Shelf",
    "My Review",
    "Spoiler",
    "Private Notes",
    "Read Count",
    "Owned Copies",
]


class Command(BaseCommand):
    help = "Generate synthetic Goodreads CSVs and matching test users."

    def add_arguments(self, parser):
        parser.add_argument(
            "--num-users",
            type=int,
            default=5,
            help="Number of test_reader{N} users + CSVs to create.",
        )
        parser.add_argument(
            "--books-per-user",
            type=int,
            default=30,
            help="Number of books to include per generated CSV.",
        )
        parser.add_argument(
            "--output-dir",
            type=str,
            default="core/tests/fixtures/csv",
            help="Directory to write generated CSVs into.",
        )
        parser.add_argument(
            "--password",
            type=str,
            default="testpass123",
            help="Password for newly-created test users.",
        )

    def handle(self, *args, **options):
        num_users = options["num_users"]
        books_per_user = options["books_per_user"]
        output_dir = options["output_dir"]
        password = options["password"]

        os.makedirs(output_dir, exist_ok=True)

        books = list(Book.objects.exclude(isbn13__isnull=True).exclude(isbn13="")[:100])
        if len(books) < 10:
            self.stdout.write(
                self.style.ERROR(f"Not enough books in database. Found {len(books)}; need at least 10.")
            )
            return

        for i in range(1, num_users + 1):
            username = f"test_reader{i}"
            email = f"reader{i}@test.com"

            if User.objects.filter(username=username).exists():
                self.stdout.write(f"User {username} already exists")
            else:
                User.objects.create_user(username=username, email=email, password=password)
                self.stdout.write(self.style.SUCCESS(f"Created user {username} ({email})"))

            csv_path = self._generate_csv(username, books, books_per_user, output_dir)
            self.stdout.write(f"Generated CSV: {csv_path}")

        self.stdout.write(self.style.SUCCESS(f"\nGenerated {num_users} test users and CSVs."))
        self.stdout.write(f"All test users use password: {password}")

    def _generate_csv(self, username, books, num_books, output_dir):
        selected_books = random.sample(books, min(num_books, len(books)))
        start_date = datetime.now() - timedelta(days=365)

        csv_rows = [GOODREADS_HEADER]

        for book in selected_books:
            if random.random() < 0.7:
                my_rating = random.choices([1, 2, 3, 4, 5], weights=[0.05, 0.10, 0.20, 0.35, 0.30])[0]
            else:
                my_rating = 0

            shelf = random.choices(["read", "currently-reading", "to-read"], weights=[0.6, 0.1, 0.3])[0]
            date_added = start_date + timedelta(days=random.randint(0, 365))

            date_read = ""
            if shelf == "read" and random.random() < 0.8:
                read_date = date_added + timedelta(days=random.randint(0, 60))
                date_read = read_date.strftime("%Y/%m/%d")

            review = ""
            if my_rating >= 4 and random.random() < 0.5:
                review = random.choice(
                    [
                        "Amazing book! Really enjoyed the writing style.",
                        "One of my favorites. Highly recommend!",
                        "Beautiful prose and engaging characters.",
                        "Captivating from start to finish.",
                        "Thought-provoking and well-written.",
                    ]
                )
            elif my_rating <= 2 and random.random() < 0.5:
                review = random.choice(
                    [
                        "Not my cup of tea. Found it boring.",
                        "Didn't connect with the characters.",
                        "Too slow for my taste.",
                    ]
                )

            date_added_str = date_added.strftime("%Y/%m/%d")
            pub_name = book.publisher.name if book.publisher else ""
            binding = random.choice(["Paperback", "Hardcover", "ebook", "Kindle Edition"])
            isbn = book.isbn13 if book.isbn13 else ""
            isbn13 = f'="{book.isbn13}"' if book.isbn13 else ""

            author_name = book.author.name
            author_parts = author_name.split()
            if len(author_parts) > 1:
                author_lf = f"{author_parts[-1]}, {' '.join(author_parts[:-1])}"
            else:
                author_lf = author_name

            avg_rating = book.average_rating if book.average_rating else 3.50
            avg_rating_str = f"{round(avg_rating, 2)}"

            csv_rows.append(
                [
                    str(random.randint(100000, 999999)),
                    book.title,
                    author_name,
                    author_lf,
                    "",
                    isbn,
                    isbn13,
                    str(my_rating),
                    avg_rating_str,
                    pub_name,
                    binding,
                    str(book.page_count or 300),
                    str(book.publish_year or 2000) if book.publish_year else "",
                    str(book.publish_year or 2000) if book.publish_year else "",
                    date_read,
                    date_added_str,
                    shelf,
                    f"{shelf} (#1)",
                    shelf,
                    review,
                    "",
                    "",
                    str(random.randint(0, 2)),
                    str(random.randint(0, 1)),
                ]
            )

        filename = os.path.join(output_dir, f"goodreads_library_export {username}.csv")
        with open(filename, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerows(csv_rows)

        return filename
