import json
import os
import re
import time
from collections import Counter, defaultdict
from datetime import date, timedelta

import requests
from bs4 import BeautifulSoup
from django.core.management.base import BaseCommand
from django.db.models import F
from dotenv import load_dotenv

from core.models import Author, PopularBook

load_dotenv()

SCORE_CONFIG = {
    "PULITZER_WINNER": 100,
    "NATIONAL_BOOK_AWARD_WINNER": 90,
    "NATIONAL_BOOK_AWARD_FINALIST": 35,
    "BOOKER_PRIZE_WINNER": 80,
    "BOOKER_PRIZE_SHORTLIST": 30,
    "WOMENS_PRIZE_WINNER": 70,
    "WOMENS_PRIZE_SHORTLIST": 25,
    "GOODREADS_CHOICE_WINNER": 60,
    "NEBULA_AWARD_WINNER": 55,
    "NEBULA_AWARD_NOMINEE": 20,
    "NYT_BESTSELLER_WEEK": 15,
    "HIGH_RATING_BONUS": 10,
}

CACHE_FILE = "api_cache.json"


class Command(BaseCommand):
    help = "Scrapes literary prize winners and NYT Bestsellers to seed the database."

    def add_arguments(self, parser):
        # --- NEW: Add a command-line flag to clear data before running ---
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Delete all existing PopularBook entries before seeding.",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        # Use a session for connection pooling and performance
        self.session = requests.Session()
        self.session.headers.update(self.headers)

    def _load_cache(self):
        if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, "r") as f:
                try:
                    return json.load(f)
                except json.JSONDecodeError:
                    return {}
        return {}

    def _save_cache(self, cache_data):
        with open(CACHE_FILE, "w") as f:
            json.dump(cache_data, f, indent=2)

    def handle(self, *args, **options):
        if options["clear"]:
            self.stdout.write(self.style.WARNING("Clearing existing PopularBook and Author score data..."))

            PopularBook.objects.all().delete()
            Author.objects.all().update(popularity_score=0)

            if os.path.exists(CACHE_FILE):
                os.remove(CACHE_FILE)

        self.stdout.write("🚀 Starting the popular books seeding process...")

        book_data = []
        self._save_scraped_data(self._scrape_pulitzer_winners())
        self._save_scraped_data(self._scrape_booker_prize_winners())
        self._save_scraped_data(self._scrape_national_book_award_winners())
        self._save_scraped_data(self._scrape_womens_prize_winners())
        self._save_scraped_data(self._scrape_nebula_award_winners())

        self._scrape_and_save_goodreads_choice_winners()
        self._fetch_nyt_bestsellers()

        # --- NEW: Enrich data with missing ISBNs ---
        self.stdout.write(f"📚 Found {len(book_data)} initial entries. Enriching with ISBN and ratings data...")

        self._enrich_and_calculate_final_scores()

        self._update_author_popularity()

    def _save_scraped_data(self, books):
        """
        Takes a list of book dicts from a scraper and saves them to the DB.
        """
        self.stdout.write(f"   -> Saving {len(books)} entries to the database...")
        for book_data in books:
            lookup_key = PopularBook.generate_lookup_key(book_data["title"], book_data["author"])

            book_obj, created = PopularBook.objects.get_or_create(
                lookup_key=lookup_key,
                defaults={
                    "title": book_data["title"],
                    "author": book_data["author"],
                },
            )

            # Add the award or shortlist to the JSON field
            source = book_data["source"]

            if "WINNER" in source:
                if source not in book_obj.awards_won:
                    book_obj.awards_won.append(source)
            elif "SHORTLIST" in source or "FINALIST" in source or "NOMINEE" in source:
                if source not in book_obj.shortlists:
                    book_obj.shortlists.append(source)

            book_obj.save()

    def _update_author_popularity(self):
        self.stdout.write("👤 Calculating popular author scores...")
        Author.objects.all().update(popularity_score=0)
        author_scores = defaultdict(int)
        for pop_book in PopularBook.objects.all():
            author_name = pop_book.author
            # Find or create author, then add score
            author, _ = Author.objects.get_or_create(name=author_name)
            Author.objects.filter(pk=author.pk).update(
                popularity_score=F("popularity_score") + pop_book.mainstream_score
            )

        self.stdout.write(self.style.SUCCESS(f"✅ Author scores updated."))

    def _fetch_nyt_bestsellers(self):
        self.stdout.write("📚 Fetching NYT Bestseller data (last 2 years)...")
        api_key = os.getenv("NYT_API_KEY")

        if not api_key:
            self.stdout.write(self.style.WARNING("⚠️ NYT_API_KEY not found. Skipping bestseller fetch."))
            return []

        # Limit to last 2 years to avoid excessive API calls and rate limits
        start_date = date.today() - timedelta(days=1 * 365)
        current_date = start_date
        list_names = [
            "hardcover-fiction",
            "trade-fiction-paperback",
            "combined-print-and-e-book-fiction",
            "combined-print-and-e-book-nonfiction",
        ]

        while current_date <= date.today():
            for list_name in list_names:
                url = f"https://api.nytimes.com/svc/books/v3/lists/{current_date.strftime('%Y-%m-%d')}/{list_name}.json?api-key={api_key}"
                try:
                    response = requests.get(url, timeout=15)

                    if response.status_code == 429:
                        self.stdout.write(self.style.WARNING("Rate limit hit. Pausing for 60 seconds..."))
                        time.sleep(60)
                        continue  # Retry the same request

                    response.raise_for_status()

                    data = response.json()

                    books_in_list = data.get("results", {}).get("books", [])
                    for book_data in books_in_list:
                        lookup_key = PopularBook.generate_lookup_key(book_data["title"], book_data["author"])

                        # Get or Create the book object first
                        book_obj, created = PopularBook.objects.get_or_create(
                            lookup_key=lookup_key,
                            defaults={
                                "title": book_data["title"].title(),
                                "author": book_data["author"],
                                "isbn13": book_data.get("primary_isbn13"),
                            },
                        )

                        # Now, atomically increment the bestseller weeks count
                        # This is safe to run multiple times.
                        book_obj.nyt_bestseller_weeks = F("nyt_bestseller_weeks") + 1
                        book_obj.save()

                        if created:
                            self.stdout.write(f"     + Created NYT entry: {book_obj.title}")
                except requests.RequestException as e:
                    self.stdout.write(self.style.ERROR(f"NYT API Error: {e}"))

                time.sleep(15)

            self.stdout.write(f"   -> Fetched bestsellers for {current_date.strftime('%Y-%m')}")
            current_date += timedelta(days=30)

    def _fetch_google_books_data(self, isbn13):
        """Fetches ratings data from the Google Books API, with retry logic for rate limiting."""
        api_key = os.getenv("GOOGLE_BOOKS_API_KEY")
        if not api_key:
            if not hasattr(self, "_google_api_key_warned"):
                self.stdout.write(self.style.WARNING("⚠️ GOOGLE_BOOKS_API_KEY not found. Skipping ratings fetch."))
                self._google_api_key_warned = True
            return None

        url = f"https://www.googleapis.com/books/v1/volumes?q=isbn:{isbn13}&key={api_key}"

        # --- NEW: Retry Logic ---
        max_retries = 3
        for attempt in range(max_retries):
            try:
                res = self.session.get(url, timeout=10)
                res.raise_for_status()  # This will raise an HTTPError for 4xx/5xx responses
                data = res.json()
                if data.get("totalItems", 0) > 0:
                    volume_info = data["items"][0].get("volumeInfo", {})
                    ratings_count = volume_info.get("ratingsCount", 0)
                    avg_rating = volume_info.get("averageRating", 0)
                    if ratings_count:
                        return {"ratings_count": ratings_count, "average_rating": avg_rating}
                return None  # Successfully got a response, but no data, so we stop.

            except requests.exceptions.RequestException as e:
                # Check if this is a rate limit error
                if hasattr(e, "response") and e.response is not None and e.response.status_code == 429:
                    self.stdout.write(
                        self.style.WARNING(
                            f"   - Rate limit hit. Pausing for 61 seconds (attempt {attempt + 1}/{max_retries})..."
                        )
                    )
                    time.sleep(61)  # Wait for the rate limit window to reset
                    continue  # Continue to the next attempt in the loop
                else:
                    self.stdout.write(self.style.WARNING(f"   - Google Books API Error for ISBN {isbn13}: {e}"))
                    return None  # For other errors, we just fail and move on.

        self.stdout.write(self.style.ERROR(f"   - Failed to fetch data for ISBN {isbn13} after {max_retries} retries."))
        return None

    def _fetch_isbn_from_open_library(self, title, author):
        """A simple helper to find an ISBN from the Open Library API."""
        try:
            url = f"https://openlibrary.org/search.json?title={requests.utils.quote(title)}&author={requests.utils.quote(author)}"
            res = self.session.get(url, timeout=10)
            res.raise_for_status()
            data = res.json()
            if data.get("docs"):
                for doc in data["docs"]:
                    if "isbn" in doc:
                        for isbn in doc["isbn"]:
                            if len(isbn) == 13 and isbn.isdigit():
                                return isbn  # Return the first valid ISBN
        except requests.RequestException as e:
            self.stdout.write(self.style.WARNING(f"   - Open Library API Error: {e}"))
        return None

    # Add this new method to your Command class
    def _fetch_google_books_data_by_title(self, title, author):
        """
        Searches Google Books by title/author and returns a full dict of data
        (isbn, ratings_count, average_rating), with retry logic.
        """
        api_key = os.getenv("GOOGLE_BOOKS_API_KEY")
        if not api_key:
            return None

        url = f"https://www.googleapis.com/books/v1/volumes?q=intitle:{requests.utils.quote(title)}+inauthor:{requests.utils.quote(author)}&key={api_key}"

        max_retries = 3
        for attempt in range(max_retries):
            try:
                res = self.session.get(url, timeout=10)
                res.raise_for_status()
                data = res.json()
                if data.get("totalItems", 0) > 0:
                    for item in data.get("items", []):
                        volume_info = item.get("volumeInfo", {})

                        # Find the first valid ISBN13
                        isbn13 = None
                        for identifier in volume_info.get("industryIdentifiers", []):
                            if identifier.get("type") == "ISBN_13":
                                isbn13 = identifier.get("identifier")
                                break

                        # If we find an ISBN, we can get ratings too
                        if isbn13:
                            return {
                                "isbn13": isbn13,
                                "ratings_count": volume_info.get("ratingsCount", 0),
                                "average_rating": volume_info.get("averageRating", 0),
                            }
                return None  # Success, but no results found

            except requests.exceptions.RequestException as e:
                if hasattr(e, "response") and e.response is not None and e.response.status_code == 429:
                    self.stdout.write(
                        self.style.WARNING(
                            f"   - Rate limit hit (title search). Pausing... (attempt {attempt + 1}/{max_retries})"
                        )
                    )
                    time.sleep(61)
                    continue
                else:
                    self.stdout.write(self.style.WARNING(f"   - Google Books API Error for '{title}': {e}"))
                    return None

        self.stdout.write(self.style.ERROR(f"   - Failed to fetch data for '{title}' after {max_retries} retries."))
        return None

    def _scrape_booker_prize_winners(self):
        url = "https://en.wikipedia.org/wiki/List_of_winners_and_nominated_authors_of_the_Booker_Prize"
        self.stdout.write(f"🏆 Scraping Booker Prize winners & shortlist from {url}...")
        books = []
        try:
            res = self.session.get(url, timeout=10)
            res.raise_for_status()
            soup = BeautifulSoup(res.content, "html.parser")
            table = soup.find("table", {"class": "wikitable"})
            if not table:
                self.stdout.write(self.style.ERROR("Could not find 'wikitable' for Booker Prize."))
                return []

            rows = table.find_all("tr")[1:]
            for row in rows:
                # Skip publisher heading rows
                if 'style="background-color: transparent"' in str(row):
                    continue

                cols = row.find_all("td")
                if len(cols) > 2 and cols[2].find("i"):
                    author = cols[1].text.strip()
                    title = cols[2].find("i").text.strip()

                    # Winner rows have a specific background style attribute
                    if row.get("style") and "background" in row.get("style"):
                        source = "BOOKER_PRIZE_WINNER"
                    else:
                        source = "BOOKER_PRIZE_SHORTLIST"

                    books.append(
                        {
                            "title": title,
                            "author": author,
                            "isbn13": None,
                            "source": source,
                        }
                    )
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Failed to scrape Booker Prize: {e}"))
        return books

    def _scrape_pulitzer_winners(self):
        # This one seems to be working, keeping it as is but returning the new dict structure
        url = "https://en.wikipedia.org/wiki/Pulitzer_Prize_for_Fiction"
        self.stdout.write(f"🏆 Scraping Pulitzer Prize winners from {url}...")
        books = []
        try:
            response = requests.get(url, headers=self.headers, timeout=10)
            response.raise_for_status()
            soup = BeautifulSoup(response.content, "html.parser")
            table = soup.find("table", {"class": "wikitable"})
            if not table:
                self.stdout.write(self.style.ERROR("Could not find 'wikitable' for Pulitzer winners."))
                return []
            rows = table.find_all("tr")[1:]
            for row in rows:
                cols = row.find_all(["td", "th"])
                if len(cols) > 2:
                    title_element = cols[2].find("i")
                    if title_element:
                        books.append(
                            {
                                "title": title_element.text.strip(),
                                "author": cols[1].text.strip(),
                                "isbn13": None,
                                "source": "PULITZER_WINNER",
                            }
                        )
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Failed to scrape Pulitzer winners: {e}"))
        return books

    def _scrape_national_book_award_winners(self):
        url = "https://en.wikipedia.org/wiki/National_Book_Award_for_Fiction"
        self.stdout.write(f"🏆 Scraping National Book Award winners & finalists from {url}...")
        books = []
        try:
            res = self.session.get(url, timeout=10)
            res.raise_for_status()
            soup = BeautifulSoup(res.content, "html.parser")
            tables = soup.find_all("table", {"class": "wikitable"})
            for table in tables:
                rows = table.find_all("tr")[1:]
                for row in rows:
                    cols = row.find_all("td")
                    if len(cols) > 2 and cols[2].find("i"):
                        author = cols[1].text.strip()
                        title = cols[2].find("i").text.strip()

                        # The winner is marked with a double dagger symbol (‡)
                        if "‡" in cols[0].text:
                            source = "NATIONAL_BOOK_AWARD_WINNER"
                        else:
                            source = "NATIONAL_BOOK_AWARD_FINALIST"

                        books.append(
                            {
                                "title": title,
                                "author": author,
                                "isbn13": None,
                                "source": source,
                            }
                        )
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Failed to scrape National Book Awards: {e}"))
        return books

    def _scrape_womens_prize_winners(self):
        # Use the main prize page which lists both winners and nominees
        url = "https://en.wikipedia.org/wiki/List_of_Women%27s_Prize_for_Fiction_winners"
        self.stdout.write(f"🏆 Scraping Women's Prize winners & shortlist from {url}...")
        books = []
        try:
            res = self.session.get(url, timeout=10)
            res.raise_for_status()
            soup = BeautifulSoup(res.content, "html.parser")

            # The page has multiple tables, one for each decade
            tables = soup.find_all("table", {"class": "wikitable"})
            if not tables:
                self.stdout.write(self.style.ERROR("Could not find any 'wikitable' for Women's Prize."))
                return []

            for table in tables:
                rows = table.find_all("tr")[1:]  # Skip header
                for row in rows:
                    cols = row.find_all("td")
                    # A valid data row has at least 4 columns (Year, Author, Title, Result)
                    if len(cols) < 4 or not cols[2].find("i"):
                        continue

                    author = cols[1].text.strip()
                    title = cols[2].find("i").text.strip()
                    result = cols[3].text.strip().lower()

                    source = None
                    if "winner" in result:
                        source = "WOMENS_PRIZE_WINNER"
                    elif "shortlist" in result:
                        source = "WOMENS_PRIZE_SHORTLIST"

                    if source:
                        books.append(
                            {
                                "title": title,
                                "author": author,
                                "isbn13": None,
                                "source": source,
                            }
                        )

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Failed to scrape Women's Prize: {e}"))
        return books

    def _scrape_and_save_goodreads_choice_winners(self):
        """
        Scrapes Goodreads Choice Award winners and saves them directly to the database
        to ensure data persistence and script resumability.
        """
        self.stdout.write("🏆 Scraping and saving Goodreads Choice Award winners...")
        base_url = "https://www.goodreads.com/choiceawards/best-books-"
        years = range(2011, date.today().year)
        category_ids = {
            1: "fiction",
            4: "fantasy",
            22: "mystery_thriller",
            6: "science_fiction",
            18: "historical_fiction",
            8: "romance",
            17: "horror",
            16: "young_adult_fiction",
            3: "nonfiction",
            2: "memoir_autobiography",
            9: "humor",
        }

        for year in years:
            url = f"{base_url}{year}"
            self.stdout.write(f"  -> Processing {year} winners...")

            try:
                res = self.session.get(url, timeout=15)
                res.raise_for_status()
                soup = BeautifulSoup(res.content, "html.parser")

                for cat_id in category_ids.keys():
                    winner_block = soup.find("div", {"id": f"winner-{cat_id}"})
                    if winner_block:
                        img_element = winner_block.find("img", class_="pollAnswer__bookImage")
                        if img_element and " by " in (alt_text := img_element.get("alt", "")):
                            title, author = alt_text.rsplit(" by ", 1)

                            # --- MODIFICATION: Save directly to the DB ---
                            lookup_key = PopularBook.generate_lookup_key(title.strip(), author.strip())

                            book_obj, created = PopularBook.objects.get_or_create(
                                lookup_key=lookup_key,
                                defaults={"title": title.strip(), "author": author.strip()},
                            )

                            award = "GOODREADS_CHOICE_WINNER"
                            if award not in book_obj.awards_won:
                                book_obj.awards_won.append(award)
                                book_obj.save()

            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Failed to scrape Goodreads {year}: {e}"))

            time.sleep(1)  # Be polite between yearly page requests

    def _scrape_nebula_award_winners(self):
        url = "https://en.wikipedia.org/wiki/Nebula_Award_for_Best_Novel"
        self.stdout.write(f"🏆 Scraping Nebula Award winners & nominees from {url}...")
        books = []
        try:
            res = self.session.get(url, timeout=10)
            res.raise_for_status()
            soup = BeautifulSoup(res.content, "html.parser")
            tables = soup.find_all("table", {"class": "wikitable"})
            for table in tables:
                rows = table.find_all("tr")[1:]
                for row in rows:
                    cols = row.find_all("td")
                    if not cols or len(cols) < 2 or not cols[1].find("i"):
                        continue

                    author = cols[0].text.strip()
                    title = cols[1].find("i").text.strip()

                    # Winner rows have a specific background color
                    if row.get("style") and "background" in row.get("style"):
                        source = "NEBULA_AWARD_WINNER"
                    else:
                        source = "NEBULA_AWARD_NOMINEE"

                    books.append({"title": title, "author": author, "isbn13": None, "source": source})
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Failed to scrape Nebula Awards: {e}"))
        return books

    def _enrich_and_calculate_final_scores(self):
        """
        Iterates over all PopularBook entries, enriches them with API data,
        and calculates their final mainstream_score.
        """
        self.stdout.write("\n" + "=" * 50)
        self.stdout.write("📈 Enriching all books and calculating final scores...")
        self.stdout.write("=" * 50)

        api_cache = self._load_cache()
        all_books = PopularBook.objects.all()
        total_books = all_books.count()

        for i, book in enumerate(all_books):
            self.stdout.write(f'  -> Processing: "{book.title}" ({i + 1}/{total_books})')

            # --- This is your existing, excellent enrichment logic ---
            cache_key = f"{book.title}_{book.author}".lower().replace(" ", "_")

            if cache_key in api_cache and book.ratings_count > 0:
                self.stdout.write(self.style.SUCCESS("     - Already enriched. Skipping API calls."))
            else:
                # (Your logic to call _fetch_google_books_data_by_title or by ISBN)
                google_data = None

                if not book.isbn13:
                    google_data = self._fetch_google_books_data_by_title(book.title, book.author)
                else:
                    google_data = self._fetch_google_books_data(book.isbn13)

                if google_data:
                    book.isbn13 = google_data.get("isbn13") or book.isbn13
                    book.ratings_count = google_data.get("ratings_count", 0)
                    book.average_rating = google_data.get("average_rating", 0)
                    api_cache[cache_key] = google_data
                    self.stdout.write(self.style.SUCCESS("     - Enriched with Google Books data!"))

                time.sleep(1.1)  # Pace your API calls

            # --- Final Score Calculation ---
            score_breakdown = defaultdict(int)
            total_score = 0

            # Score from awards and shortlists
            for award in book.awards_won:
                score_breakdown[award] = SCORE_CONFIG.get(award, 0)

            for shortlist in book.shortlists:
                score_breakdown[shortlist] = SCORE_CONFIG.get(shortlist, 0)

            # Score from NYT weeks
            if book.nyt_bestseller_weeks > 0:
                score_breakdown["NYT_BESTSELLER_WEEKS"] = book.nyt_bestseller_weeks

            # Score from ratings
            if book.ratings_count > 0:
                score_breakdown["RATINGS_SCORE"] = min(book.ratings_count // 500, 100)

            if book.average_rating and book.average_rating >= 4.0:
                score_breakdown["HIGH_RATING_BONUS"] = SCORE_CONFIG["HIGH_RATING_BONUS"]

            # Calculate total score from breakdown
            for key, val in score_breakdown.items():
                if key == "NYT_BESTSELLER_WEEKS":
                    total_score += val * SCORE_CONFIG["NYT_BESTSELLER_WEEK"]
                else:
                    total_score += val

            book.mainstream_score = total_score
            book.score_breakdown = dict(score_breakdown)

            book.save()

            if i % 20 == 0:
                self._save_cache(api_cache)

        self._save_cache(api_cache)
