# In core/management/commands/scrape_nyt.py

import os
import time
from datetime import date, timedelta

import requests
from django.core.management.base import BaseCommand
from django.db.models import F

# --- You need to import both Author and Book ---
from core.models import Author, Book


class Command(BaseCommand):
    help = "Scrapes the last year of NYT Bestseller data."

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.session = requests.Session()
        # It's good practice to set a custom User-Agent
        self.session.headers.update({"User-Agent": "BibliotypeApp/1.0 (YourContactEmail@example.com)"})

    def handle(self, *args, **options):
        self.stdout.write("📚 Fetching NYT Bestseller data (last year)...")
        api_key = os.getenv("NYT_API_KEY")

        if not api_key:
            self.stdout.write(self.style.WARNING("⚠️ NYT_API_KEY not found. Skipping."))
            return

        start_date = date.today() - timedelta(days=365)
        current_date = start_date
        list_names = [
            "hardcover-fiction",
            "trade-fiction-paperback",
            "combined-print-and-e-book-fiction",
        ]

        while current_date <= date.today():
            for list_name in list_names:
                url = f"https://api.nytimes.com/svc/books/v3/lists/{current_date.strftime('%Y-%m-%d')}/{list_name}.json?api-key={api_key}"
                try:
                    response = self.session.get(url, timeout=15)
                    if response.status_code == 429:
                        self.stdout.write(self.style.WARNING("Rate limit hit. Pausing for 60 seconds..."))
                        time.sleep(60)
                        continue
                    response.raise_for_status()
                    data = response.json()

                    for book_data in data.get("results", {}).get("books", []):
                        # --- START: CORRECTED LOGIC ---

                        # 1. Get the raw data from the API
                        title_from_api = book_data["title"].title()
                        author_name_from_api = book_data["author"]

                        # 2. Get or create the Author object first.
                        #    This uses the built-in normalization on the Author model.
                        author_obj, _ = Author.objects.get_or_create(name=author_name_from_api)

                        # 3. Use the existing model methods for normalization to find the book.
                        #    Your Book model already handles title normalization on save.
                        book_obj, created = Book.objects.get_or_create(
                            normalized_title=Book._normalize_title(title_from_api),
                            author=author_obj,  # Use the author object, not a string
                            defaults={
                                "title": title_from_api,
                                "isbn13": book_data.get("primary_isbn13"),
                            },
                        )

                        # 4. Atomically increment the bestseller weeks. This is idempotent.
                        #    This logic remains the same.
                        if book_obj:
                            book_obj.nyt_bestseller_weeks = F("nyt_bestseller_weeks") + 1
                            book_obj.save()

                        if created:
                            self.stdout.write(f"     + Created NYT entry: {book_obj.title}")

                        # --- END: CORRECTED LOGIC ---

                except requests.RequestException as e:
                    self.stdout.write(self.style.ERROR(f"NYT API Error: {e}"))

                # Be polite to the API (NYT has a 12s/request, 5 reqs/min limit)
                time.sleep(13)

            self.stdout.write(f"   -> Fetched bestsellers for week of {current_date}")
            current_date += timedelta(days=7)

        self.stdout.write(self.style.SUCCESS("✅ Finished fetching NYT Bestseller data."))
