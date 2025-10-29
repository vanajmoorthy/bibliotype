import os
import re

import requests
from bs4 import BeautifulSoup
from django.core.management.base import BaseCommand

from core.models import Author, Book


def _clean_author_name(author_name):
    """A helper function to normalize author names for display."""
    name = re.sub(r"\s?\(.*\)", "", author_name)
    name = name.replace(". ", ".")
    return name.strip()


class Command(BaseCommand):
    help = 'Scrapes "Top 100" and "Canon" book lists.'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "Mozilla/5.0 ... Safari/537.36"})
        self.output_dir = "scraped_html"
        os.makedirs(self.output_dir, exist_ok=True)

    def _save_html_for_inspection(self, filename, content):
        filepath = os.path.join(self.output_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        self.stdout.write(self.style.SUCCESS(f"   -> Saved raw HTML to {filepath}"))

    def handle(self, *args, **options):
        self.stdout.write("🚀 Starting scraping of canon book lists...")
        self._scrape_and_save(self._scrape_goodreads_classics, "GOODREADS_CLASSICS")
        self._scrape_and_save(self._scrape_greatest_books, "THE_GREATEST_BOOKS")
        self._scrape_and_save(self._scrape_penguin_classics, "PENGUIN_100")
        self._scrape_and_save(self._scrape_oclc_top500, "OCLC_TOP_500")
        self.stdout.write(self.style.SUCCESS("✅ Finished scraping canon lists."))

    def _scrape_and_save(self, scraper_func, list_name):
        self.stdout.write(f"📚 Scraping {list_name}...")
        books = scraper_func()

        if not books:
            self.stdout.write(self.style.WARNING(f"   -> No books were found for {list_name}."))
            return

        created_count = 0
        updated_count = 0

        for book_data in books:
            title, author_name = book_data.get("title"), book_data.get("author")
            if not title or not author_name:
                continue

            display_name = _clean_author_name(author_name)

            # 2. Generate the normalized name for the lookup key
            normalized_key = Author._normalize(display_name)

            author_obj, created_author = Author.objects.get_or_create(
                normalized_name=normalized_key, defaults={"name": display_name}
            )

            normalized_book_title = Book._normalize_title(title.strip())

            book_obj, created_book = Book.objects.get_or_create(
                normalized_title=normalized_book_title,
                author=author_obj,
                defaults={"title": title.strip()},  # Provide original title for display
            )

            if created_book:
                created_count += 1

            if list_name not in book_obj.canon_lists:
                book_obj.canon_lists.append(list_name)
                book_obj.save()
                updated_count += 1
                if created_book:
                    self.stdout.write(self.style.SUCCESS(f'   [CREATED & ADDED] "{title}" to {list_name}'))
                else:
                    self.stdout.write(self.style.NOTICE(f'   [UPDATED] Added "{title}" to {list_name}'))

        self.stdout.write(f"   -> Processed list '{list_name}'. Created: {created_count}, Updated: {updated_count}.")

    def _scrape_goodreads_classics(self):
        # This function is working
        url = "https://www.goodreads.com/list/show/449.Must_Read_Classics"
        books = []
        try:
            res = self.session.get(url, timeout=15)
            res.raise_for_status()
            soup = BeautifulSoup(res.content, "html.parser")
            rows = soup.find_all("tr", itemtype="http://schema.org/Book")
            for row in rows:
                title = row.find("a", class_="bookTitle").get_text(strip=True)
                author = row.find("a", class_="authorName").get_text(strip=True)
                books.append({"title": title, "author": author})
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"   -> Failed: {e}"))
        return books

    def _scrape_greatest_books(self):
        # This function is working
        url = "https://thegreatestbooks.org/"
        books = []
        try:
            res = self.session.get(url, timeout=15)
            res.raise_for_status()
            soup = BeautifulSoup(res.content, "html.parser")
            list_items = soup.find_all("li", class_="list-group-item")
            for item in list_items:
                h4 = item.find("h4")
                if h4:
                    links = h4.find_all("a")
                    if len(links) == 2:
                        title = links[0].get_text(strip=True)
                        author = links[1].get_text(strip=True)
                        books.append({"title": title, "author": author})
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"   -> Failed: {e}"))
        return books

    # --- FIXED PENGUIN SCRAPER ---
    def _scrape_penguin_classics(self):
        url = "https://www.penguin.co.uk/discover/articles/100-must-read-classic-books"
        books = []
        try:
            res = self.session.get(url, timeout=15)
            res.raise_for_status()
            soup = BeautifulSoup(res.content, "html.parser")

            # The main content area
            article_body = soup.find("div", class_="ArticleLayout_entry__LNu7S")
            if not article_body:
                self.stdout.write(self.style.ERROR("   -> Could not find the article body container."))
                return []

            # Each book entry is a <noscript> tag, which contains the data
            entries = article_body.find_all("noscript")
            for entry in entries:
                # The first <p> tag inside the noscript's div has the title and author
                p_tag = entry.find("p")
                if p_tag:
                    title_tag = p_tag.find("i")
                    if title_tag:
                        title = title_tag.get_text(strip=True)
                        # The author's name comes after "by "
                        if " by " in p_tag.get_text():
                            author_part = p_tag.get_text().split(" by ")[1]
                            # Remove the year in parentheses if it exists
                            author = re.sub(r"\s*\(\d{4}\)", "", author_part).strip()
                            books.append({"title": title, "author": author})
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"   -> Failed: {e}"))
        return books

    # --- NEW OCLC SCRAPER ---
    def _scrape_oclc_top500(self):
        url = "https://www.oclc.org/en/worldcat/library100/top500.html"
        books = []
        try:
            res = self.session.get(url, timeout=15)
            res.raise_for_status()
            soup = BeautifulSoup(res.content, "html.parser")

            # Find the main table with all the books
            table = soup.find("table", id="lib500list")
            if not table:
                self.stdout.write(self.style.ERROR("   -> Could not find the OCLC book list table."))
                return []

            # Iterate through each row in the table's body
            for row in table.tbody.find_all("tr"):
                # Find the cells for title and author
                title_cell = row.find("td", class_="ti")
                author_cell = row.find("td", class_="au")

                if title_cell and author_cell:
                    # The title is inside an <a> tag within its cell
                    title = title_cell.a.get_text(strip=True)
                    author = author_cell.get_text(strip=True)
                    books.append({"title": title, "author": author})
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"   -> Failed: {e}"))

        return books
