"""
Generate synthetic Goodreads CSV files for recommendation-system testing.

Creates ~10–15 CSVs with intentional similarity patterns across groups
(sci-fi/fantasy fans, literary fiction readers, contemporary fans, eclectic
readers, plus special cases). Ported from the legacy
`csv/generate_synthetic_test_data.py` standalone script as part of US-009 so the
workflow is discoverable via `manage.py help`.
"""

import csv
import os
import random
from datetime import datetime, timedelta

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
    help = "Generate synthetic Goodreads CSVs with intentional similarity patterns for recommendation testing."

    def add_arguments(self, parser):
        parser.add_argument(
            "--output-dir",
            type=str,
            default="core/tests/fixtures/csv",
            help="Directory to write generated CSVs into.",
        )

    def handle(self, *args, **options):
        self.output_dir = options["output_dir"]
        os.makedirs(self.output_dir, exist_ok=True)

        self.stdout.write("Starting synthetic CSV generation for recommendation testing...")
        total_books = Book.objects.exclude(isbn13__isnull=True).exclude(isbn13="").count()
        self.stdout.write(f"Total books in database: {total_books}")

        all_generated_books = []

        for label, runner in (
            ("sci-fi/fantasy group", self._create_sci_fi_fantasy_group),
            ("literary fiction group", self._create_literary_fiction_group),
            ("contemporary fiction group", self._create_contemporary_fiction_group),
            ("eclectic readers", self._create_eclectic_readers),
        ):
            try:
                all_generated_books.extend(runner())
            except Exception as exc:  # noqa: BLE001 — surface any group failure but keep going
                self.stdout.write(self.style.WARNING(f"Error creating {label}: {exc}"))

        try:
            all_generated_books.extend(self._create_special_test_cases(all_generated_books))
        except Exception as exc:  # noqa: BLE001
            self.stdout.write(self.style.WARNING(f"Error creating special test cases: {exc}"))

        self.stdout.write("\n" + "=" * 60)
        self.stdout.write(self.style.SUCCESS(f"Successfully generated {len(all_generated_books)} CSV files:"))
        for name, _ in all_generated_books:
            self.stdout.write(f"  - goodreads_library_export {name}.csv")
        self.stdout.write("=" * 60)

    # --- book pool helpers ------------------------------------------------

    def _get_books_by_genres(self, genre_names, min_count=20):
        qs = Book.objects.exclude(isbn13__isnull=True).exclude(isbn13="").prefetch_related("genres", "author", "publisher")
        if not genre_names:
            return list(qs[:500])

        filtered_books = []
        for book in qs:
            book_genres = [g.name.lower() for g in book.genres.all()]
            if any(any(genre_name.lower() in bg for bg in book_genres) for genre_name in genre_names):
                filtered_books.append(book)
            if len(filtered_books) >= 500:
                break
        return filtered_books

    def _get_recent_books(self, year_threshold=2010):
        qs = (
            Book.objects.exclude(isbn13__isnull=True)
            .exclude(isbn13="")
            .filter(publish_year__gte=year_threshold)
            .prefetch_related("genres", "author", "publisher")
        )
        return list(qs[:500])

    # --- CSV row + file generation ---------------------------------------

    def _generate_csv_row(self, book, rating_distribution, shelf_distribution, date_range_days, rating_bias=None):
        if random.random() < 0.7:
            if rating_bias == "harsh":
                my_rating = random.choices([1, 2, 3, 4, 5], weights=[0.15, 0.35, 0.30, 0.15, 0.05])[0]
            elif rating_bias == "generous":
                my_rating = random.choices([1, 2, 3, 4, 5], weights=[0.02, 0.08, 0.20, 0.35, 0.35])[0]
            else:
                my_rating = random.choices([1, 2, 3, 4, 5], weights=rating_distribution)[0]
        else:
            my_rating = 0

        shelf = random.choices(["read", "currently-reading", "to-read"], weights=shelf_distribution)[0]
        start_date = datetime.now() - timedelta(days=date_range_days)
        date_added = start_date + timedelta(days=random.randint(0, date_range_days))

        date_read = ""
        if shelf == "read" and random.random() < 0.8:
            read_date = date_added + timedelta(days=random.randint(0, 90))
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

        avg_rating = book.average_rating or book.google_books_average_rating or 3.50
        avg_rating_str = f"{round(avg_rating, 2)}"

        return [
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

    def _generate_synthetic_csv(
        self,
        username,
        books,
        num_books=30,
        shared_books=None,
        rating_distribution=None,
        shelf_distribution=None,
        date_range_days=1095,
        rating_bias=None,
    ):
        if rating_distribution is None:
            rating_distribution = [0.05, 0.10, 0.20, 0.35, 0.30]
        if shelf_distribution is None:
            shelf_distribution = [0.6, 0.1, 0.3]

        if len(books) < num_books:
            self.stdout.write(self.style.WARNING(f"Only {len(books)} books available, using all of them"))
            num_books = len(books)

        if shared_books:
            selected_books = list(shared_books)
            available_books = [b for b in books if b not in selected_books]
            remaining = num_books - len(selected_books)
            if remaining > 0 and available_books:
                selected_books.extend(random.sample(available_books, min(remaining, len(available_books))))
        else:
            selected_books = random.sample(books, num_books)

        csv_rows = [GOODREADS_HEADER]
        for book in selected_books:
            csv_rows.append(
                self._generate_csv_row(book, rating_distribution, shelf_distribution, date_range_days, rating_bias)
            )

        filename = os.path.join(self.output_dir, f"goodreads_library_export {username}.csv")
        with open(filename, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerows(csv_rows)

        self.stdout.write(f"Generated {filename} with {len(selected_books)} books")
        return filename, selected_books

    # --- group builders ---------------------------------------------------

    def _create_sci_fi_fantasy_group(self):
        self.stdout.write("\n=== Creating Sci-Fi/Fantasy Group ===")
        genre_keywords = ["science fiction", "fantasy", "sci-fi", "sf", "speculative"]
        books = self._get_books_by_genres(genre_keywords)
        if len(books) < 30:
            self.stdout.write(self.style.WARNING(f"Only {len(books)} sci-fi/fantasy books found; widening pool."))
            books = list(Book.objects.exclude(isbn13__isnull=True).exclude(isbn13="")[:200])

        shared_pool = random.sample(books, min(15, len(books)))
        results = []

        shared1 = random.sample(shared_pool, 8)
        _, books1 = self._generate_synthetic_csv(
            "synthetic_sf_fan1",
            books,
            num_books=35,
            shared_books=shared1,
            rating_distribution=[0.02, 0.08, 0.20, 0.35, 0.35],
            rating_bias="generous",
        )
        results.append(("synthetic_sf_fan1", books1))

        shared2 = shared1[:5] + random.sample([b for b in books if b not in shared1], 8)
        _, books2 = self._generate_synthetic_csv(
            "synthetic_sf_fan2",
            books,
            num_books=40,
            shared_books=shared2,
            rating_distribution=[0.05, 0.10, 0.20, 0.35, 0.30],
        )
        results.append(("synthetic_sf_fan2", books2))

        shared3 = (
            shared1[:3]
            + shared2[:3]
            + random.sample([b for b in books if b not in shared1 and b not in shared2], 6)
        )
        _, books3 = self._generate_synthetic_csv(
            "synthetic_sf_fan3",
            books,
            num_books=32,
            shared_books=shared3,
            rating_distribution=[0.15, 0.35, 0.30, 0.15, 0.05],
            rating_bias="harsh",
        )
        results.append(("synthetic_sf_fan3", books3))
        return results

    def _create_literary_fiction_group(self):
        self.stdout.write("\n=== Creating Literary Fiction Group ===")
        genre_keywords = ["literary fiction", "fiction", "literature", "classic", "contemporary fiction"]
        books = self._get_books_by_genres(genre_keywords)
        if len(books) < 30:
            self.stdout.write(self.style.WARNING(f"Only {len(books)} literary fiction books found; widening pool."))
            books = list(Book.objects.exclude(isbn13__isnull=True).exclude(isbn13="")[:200])

        shared_pool = random.sample(books, min(12, len(books)))
        results = []

        shared1 = random.sample(shared_pool, 10)
        _, books1 = self._generate_synthetic_csv(
            "synthetic_lit_fiction1",
            books,
            num_books=38,
            shared_books=shared1,
            rating_distribution=[0.03, 0.10, 0.25, 0.35, 0.27],
            date_range_days=1460,
        )
        results.append(("synthetic_lit_fiction1", books1))

        shared2 = shared1[:7] + random.sample([b for b in books if b not in shared1], 9)
        _, books2 = self._generate_synthetic_csv(
            "synthetic_lit_fiction2",
            books,
            num_books=42,
            shared_books=shared2,
            rating_distribution=[0.04, 0.12, 0.23, 0.33, 0.28],
        )
        results.append(("synthetic_lit_fiction2", books2))

        shared3 = (
            shared1[:5]
            + shared2[:4]
            + random.sample([b for b in books if b not in shared1 and b not in shared2], 8)
        )
        _, books3 = self._generate_synthetic_csv(
            "synthetic_lit_fiction3",
            books,
            num_books=35,
            shared_books=shared3,
            rating_distribution=[0.05, 0.10, 0.20, 0.35, 0.30],
        )
        results.append(("synthetic_lit_fiction3", books3))
        return results

    def _create_contemporary_fiction_group(self):
        self.stdout.write("\n=== Creating Contemporary Fiction Group ===")
        books = self._get_recent_books(year_threshold=2010)
        if len(books) < 20:
            books = self._get_books_by_genres(["fiction", "contemporary"])
        if len(books) < 20:
            books = list(Book.objects.exclude(isbn13__isnull=True).exclude(isbn13="")[:200])

        results = []

        shared1 = random.sample(books, min(6, len(books)))
        _, books1 = self._generate_synthetic_csv(
            "synthetic_contemporary1",
            books,
            num_books=28,
            shared_books=shared1,
            rating_distribution=[0.05, 0.10, 0.25, 0.30, 0.30],
            date_range_days=730,
        )
        results.append(("synthetic_contemporary1", books1))

        shared2 = shared1[:4] + random.sample([b for b in books if b not in shared1], 7)
        _, books2 = self._generate_synthetic_csv(
            "synthetic_contemporary2",
            books,
            num_books=31,
            shared_books=shared2,
            rating_distribution=[0.04, 0.12, 0.28, 0.32, 0.24],
            date_range_days=730,
        )
        results.append(("synthetic_contemporary2", books2))
        return results

    def _create_eclectic_readers(self):
        self.stdout.write("\n=== Creating Eclectic Readers ===")
        books = list(
            Book.objects.exclude(isbn13__isnull=True)
            .exclude(isbn13="")
            .prefetch_related("genres", "author", "publisher")[:500]
        )

        results = []

        _, books1 = self._generate_synthetic_csv(
            "synthetic_eclectic1",
            books,
            num_books=45,
            rating_distribution=[0.05, 0.15, 0.30, 0.30, 0.20],
            date_range_days=1825,
        )
        results.append(("synthetic_eclectic1", books1))

        _, books2 = self._generate_synthetic_csv(
            "synthetic_eclectic2",
            books,
            num_books=38,
            rating_distribution=[0.08, 0.18, 0.32, 0.28, 0.14],
            date_range_days=1460,
        )
        results.append(("synthetic_eclectic2", books2))

        _, books3 = self._generate_synthetic_csv(
            "synthetic_eclectic3",
            books,
            num_books=42,
            rating_distribution=[0.06, 0.12, 0.26, 0.32, 0.24],
            date_range_days=1095,
        )
        results.append(("synthetic_eclectic3", books3))
        return results

    def _create_special_test_cases(self, all_generated_books):
        self.stdout.write("\n=== Creating Special Test Cases ===")
        books = list(
            Book.objects.exclude(isbn13__isnull=True)
            .exclude(isbn13="")
            .prefetch_related("genres", "author", "publisher")[:500]
        )

        results = []

        if all_generated_books:
            _, source_books = all_generated_books[0]
            shared_books = source_books[:20]
            _, books1 = self._generate_synthetic_csv(
                "synthetic_high_overlap",
                books,
                num_books=35,
                shared_books=shared_books,
                rating_distribution=[0.05, 0.10, 0.20, 0.35, 0.30],
            )
            results.append(("synthetic_high_overlap", books1))

        _, books2 = self._generate_synthetic_csv(
            "synthetic_harsh_rater",
            books,
            num_books=30,
            rating_distribution=[0.20, 0.35, 0.30, 0.12, 0.03],
            rating_bias="harsh",
            shelf_distribution=[0.65, 0.05, 0.30],
        )
        results.append(("synthetic_harsh_rater", books2))

        recent_books = self._get_recent_books(year_threshold=2015)
        if len(recent_books) < 25:
            recent_books = books

        _, books3 = self._generate_synthetic_csv(
            "synthetic_recent_reader",
            recent_books,
            num_books=32,
            rating_distribution=[0.05, 0.10, 0.20, 0.35, 0.30],
            date_range_days=365,
        )
        results.append(("synthetic_recent_reader", books3))
        return results
