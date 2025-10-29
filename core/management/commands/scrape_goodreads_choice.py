# core/management/commands/scrape_goodreads.py

import time
from datetime import date

import requests
from bs4 import BeautifulSoup
from django.core.management.base import BaseCommand

from core.models import Book


class Command(BaseCommand):
    help = "Scrapes Goodreads Choice Award winners."

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "Mozilla/5.0 ... Safari/537.36"})

    def handle(self, *args, **options):
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

                            lookup_key = Book.generate_lookup_key(title.strip(), author.strip())
                            book_obj, created = Book.objects.get_or_create(
                                lookup_key=lookup_key,
                                defaults={"title": title.strip(), "author": author.strip()},
                            )

                            award = "GOODREADS_CHOICE_WINNER"
                            if award not in book_obj.awards_won:
                                book_obj.awards_won.append(award)
                                book_obj.save()
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Failed to scrape Goodreads {year}: {e}"))

            time.sleep(2)  # Be polite

        self.stdout.write(self.style.SUCCESS("✅ Finished scraping Goodreads Choice winners."))
