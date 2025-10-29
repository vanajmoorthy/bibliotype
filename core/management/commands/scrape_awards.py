import os
import re

import requests
from bs4 import BeautifulSoup
from django.core.management.base import BaseCommand

# --- CHANGE 1: Import Author and Book ---
# We now need both models to create the relationship correctly.
from core.models import Author, Book


def _clean_author_name(author_name):
    """A 'light' cleaning function that only removes parenthetical dates for the display name."""
    name = re.sub(r"\s?\(.*\)", "", author_name)
    return name.strip()


class Command(BaseCommand):
    help = "Scrapes competitive literary prize winners (Pulitzer, Booker, etc.)"

    # __init__ and other helper methods remain the same...
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
            }
        )
        self.output_dir = "scraped_html"
        os.makedirs(self.output_dir, exist_ok=True)

    def _save_html_for_inspection(self, filename, content):
        filepath = os.path.join(self.output_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        self.stdout.write(self.style.SUCCESS(f"   -> Saved HTML to {filepath}"))

    def handle(self, *args, **options):
        self.stdout.write("🚀 Starting scraping of competitive awards...")
        self._save_scraped_data(self._scrape_pulitzer_winners())
        self._save_scraped_data(self._scrape_booker_prize_winners())
        self._save_scraped_data(self._scrape_national_book_award_winners())
        self._save_scraped_data(self._scrape_womens_prize_winners())
        self._save_scraped_data(self._scrape_nebula_award_winners())
        self.stdout.write(self.style.SUCCESS("✅ Finished scraping competitive awards."))

    # --- CHANGE 2: This entire function is replaced with the new logic ---
    def _save_scraped_data(self, books):
        if not books:
            self.stdout.write(self.style.WARNING("   -> No books found by the scraper."))
            return

        created_count, updated_count, existing_count = 0, 0, 0

        for book_data in books:
            title, author_name = book_data.get("title"), book_data.get("author")
            if not title or not author_name:
                continue

            # STEP 1: Get or create the Author first. This handles variations in author names
            # and ensures we always link to the same author entity.
            display_name = _clean_author_name(author_name)

            # 2. Generate the normalized name for the lookup key
            normalized_key = Author._normalize(display_name)

            # --- THE FIX: Use get_or_create on the UNIQUE normalized_name field ---
            author_obj, created_author = Author.objects.get_or_create(
                normalized_name=normalized_key,
                defaults={"name": display_name},  # Only set the display name when creating
            )

            normalized_book_title = Book._normalize_title(title.strip())
            # Now create or get the book
            book_obj, created_book = Book.objects.get_or_create(
                normalized_title=normalized_book_title, author=author_obj, defaults={"title": title.strip()}
            )

            if created_book:
                self.stdout.write(self.style.SUCCESS(f'   [CREATED] "{title}" by {author_obj.name}'))
                created_count += 1

            source = book_data.get("source", "")
            is_update_needed = False

            if "WINNER" in source and source not in book_obj.awards_won:
                book_obj.awards_won.append(source)
                is_update_needed = True
            elif (
                any(term in source for term in ["SHORTLIST", "FINALIST", "NOMINEE"])
                and source not in book_obj.shortlists
            ):
                book_obj.shortlists.append(source)
                is_update_needed = True

            if is_update_needed:
                book_obj.save()
                self.stdout.write(self.style.NOTICE(f'   [UPDATED] "{title}" with new award: {source}'))
                updated_count += 1
            elif not created_book:
                existing_count += 1

        if existing_count > 0:
            self.stdout.write(f"   -> {existing_count} books already existed and were up-to-date.")
        self.stdout.write(f"   -> Summary: {created_count} created, {updated_count} updated.")

    def _scrape_pulitzer_winners(self):
        url = "https://en.wikipedia.org/wiki/Pulitzer_Prize_for_Fiction"
        self.stdout.write(f"🏆 Scraping {url}...")
        books = []
        try:
            response = self.session.get(url, timeout=10)
            # The line to save the HTML is already in your code, which is great for debugging.
            # self._save_html_for_inspection("pulitzer.html", response.text)
            soup = BeautifulSoup(response.content, "html.parser")

            # Find all wikitable sortable tables on the page
            tables = soup.find_all("table", {"class": "wikitable sortable"})

            for table in tables:
                for row in table.find_all("tr")[1:]:  # Skip the header row
                    cols = row.find_all(["th", "td"])

                    # Check if the row has enough columns
                    if len(cols) >= 4:
                        # Column indices are different from your original code.
                        # Author is in column 2, Title is in column 3.
                        author_col = cols[2]
                        title_col = cols[3]

                        # Find the title within an <i> tag
                        title_element = title_col.find("i")

                        if title_element:
                            # Extract author and title text
                            author = author_col.get_text(strip=True)
                            title = title_element.get_text(strip=True)

                            # Every book listed in these tables is a winner
                            books.append({"title": title, "author": author, "source": "PULITZER_WINNER"})

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"   -> Failed: {e}"))
        return books

    def _scrape_booker_prize_winners(self):
        url = "https://en.wikipedia.org/wiki/List_of_winners_and_nominated_authors_of_the_Booker_Prize"
        self.stdout.write(f"🏆 Scraping {url}...")
        books = []
        try:
            response = self.session.get(url, timeout=10)
            self._save_html_for_inspection("booker.html", response.text)
            soup = BeautifulSoup(response.content, "html.parser")
            table = soup.find("table", {"class": "wikitable"})
            if not table:
                return []
            for row in table.find_all("tr")[1:]:
                if 'style="background-color: transparent"' in str(row):
                    continue
                cols = row.find_all("td")
                if len(cols) > 2 and (title_element := cols[2].find("i")):
                    author = cols[1].text.strip()
                    title = title_element.text.strip()
                    source = (
                        "BOOKER_PRIZE_WINNER"
                        if row.get("style") and "background" in row.get("style")
                        else "BOOKER_PRIZE_SHORTLIST"
                    )
                    books.append({"title": title, "author": author, "source": source})
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"   -> Failed: {e}"))
        return books

    def _scrape_national_book_award_winners(self):
        url = "https://en.wikipedia.org/wiki/National_Book_Award_for_Fiction"
        self.stdout.write(f"🏆 Scraping {url}...")
        books = []
        try:
            response = self.session.get(url, timeout=10)
            # self._save_html_for_inspection("national_book_award.html", response.text)
            soup = BeautifulSoup(response.content, "html.parser")

            # Select all tables with the 'wikitable' class.
            tables = soup.find_all("table", class_="wikitable")

            for table in tables:
                for row in table.find_all("tr")[1:]:  # Skip header rows
                    cols = row.find_all(["td", "th"])  # Include th for year column

                    # Determine the correct column indices based on table structure
                    # The first column might be a <th> with the year
                    offset = 1 if cols[0].name == "th" else 0

                    if len(cols) < (2 + offset):
                        continue

                    author_col = cols[0 + offset]
                    title_col = cols[1 + offset]

                    author_element = author_col.find("a")
                    title_element = title_col.find("i")

                    if author_element and title_element:
                        author = author_element.get_text(strip=True)
                        title = title_element.get_text(strip=True)

                        # Check for winner status by looking for a background style on the row
                        if row.get("style") and "background" in row.get("style"):
                            source = "NATIONAL_BOOK_AWARD_WINNER"
                        else:
                            source = "NATIONAL_BOOK_AWARD_FINALIST"

                        books.append({"title": title, "author": author, "source": source})

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"   -> Failed: {e}"))
        return books

    def _scrape_womens_prize_winners(self):
        url = "https://en.wikipedia.org/wiki/List_of_Women%27s_Prize_for_Fiction_winners"
        self.stdout.write(f"🏆 Scraping {url}...")
        books = []
        try:
            response = self.session.get(url, timeout=10)
            # self._save_html_for_inspection("womens_prize.html", response.text)
            soup = BeautifulSoup(response.content, "html.parser")
            tables = soup.find_all("table", {"class": "wikitable"})
            for table in tables:
                for row in table.find_all("tr")[1:]:  # Skip header row
                    cols = row.find_all("td")

                    # Check if the row has enough columns for author and title
                    if len(cols) >= 2:
                        author_col = cols[0]
                        title_col = cols[1]

                        title_element = title_col.find("i")

                        if title_element and author_col.find("a"):
                            author = author_col.get_text(strip=True)
                            title = title_element.get_text(strip=True)

                            # Your original logic was correct. Winners are marked with a background color.
                            source = (
                                "WOMENS_PRIZE_WINNER"
                                if row.get("style") and "background" in row.get("style")
                                else "WOMENS_PRIZE_SHORTLIST"
                            )
                            books.append({"title": title, "author": author, "source": source})
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"   -> Failed: {e}"))
        return books

    def _scrape_nebula_award_winners(self):
        url = "https://en.wikipedia.org/wiki/Nebula_Award_for_Best_Novel"
        self.stdout.write(f"🏆 Scraping {url}...")
        books = []
        try:
            response = self.session.get(url, timeout=10)
            self._save_html_for_inspection("nebula_award.html", response.text)
            soup = BeautifulSoup(response.content, "html.parser")
            table = soup.find("table", {"class": "wikitable"})
            if not table:
                return []
            for row in table.find_all("tr")[1:]:
                cols = row.find_all("td")
                if len(cols) > 1 and (title_element := cols[1].find("i")):
                    author = cols[0].text.strip()
                    title = title_element.text.strip()
                    source = (
                        "NEBULA_AWARD_WINNER"
                        if row.get("style") and "background" in row.get("style")
                        else "NEBULA_AWARD_NOMINEE"
                    )
                    books.append({"title": title, "author": author, "source": source})
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"   -> Failed: {e}"))
        return books
