"""Currently-reading shelf: extraction from the CSV and sync to the shared Book table."""

import logging

import pandas as pd
from django.db import IntegrityError

from ...models import Author, Book
from .utils import _build_cover_url, _cover_initial

logger = logging.getLogger(__name__)


def extract_currently_reading(df):
    """Return (currently_reading_books, currently_reading_count, custom_shelf_count) for the normalised CSV."""
    currently_reading_df = df[df["Exclusive Shelf"] == "currently-reading"].copy()
    currently_reading_count = int(len(currently_reading_df))

    standard_shelves = {"read", "currently-reading", "to-read"}
    custom_shelf_count = int(len(df[~df["Exclusive Shelf"].isin(standard_shelves)]))

    currently_reading_books = []
    if not currently_reading_df.empty:
        if "Date Added" in currently_reading_df.columns:
            currently_reading_df["Date Added"] = pd.to_datetime(currently_reading_df["Date Added"], errors="coerce")
            currently_reading_df = currently_reading_df.sort_values(
                by="Date Added", ascending=False, na_position="last"
            )

        for _, row in currently_reading_df.head(3).iterrows():
            isbn13_raw = row.get("ISBN13")
            title = str(row.get("Title", "")).strip()
            currently_reading_books.append(
                {
                    "title": title,
                    "author": str(row.get("Author", "")).strip(),
                    "cover_url": _build_cover_url(isbn13_raw if pd.notna(isbn13_raw) else None),
                    "page_count": int(row["Number of Pages"]) if pd.notna(row.get("Number of Pages")) else None,
                    "initial": _cover_initial(title),
                }
            )

        logger.info(f"Found {currently_reading_count} currently-reading books, {custom_shelf_count} on custom shelves")
    return currently_reading_books, currently_reading_count, custom_shelf_count


def sync_currently_reading_books(currently_reading_books, upload_user_id, upload_nonce, dispatch_opts):
    """Sync currently-reading books to DB (Book/Author only, no UserBook, no global_read_count)."""
    for cr_book in currently_reading_books:
        cr_author_name = cr_book.get("author", "").strip()
        cr_title = cr_book.get("title", "").strip()
        if not cr_author_name or not cr_title:
            continue

        normalized_author_name = Author._normalize(cr_author_name)
        author, created = Author.objects.get_or_create(
            normalized_name=normalized_author_name, defaults={"name": cr_author_name}
        )
        if created:
            from ...tasks import check_author_mainstream_status_task

            check_author_mainstream_status_task.apply_async(
                args=(author.id,),
                kwargs={"user_id": upload_user_id, "upload_nonce": upload_nonce},
                **dispatch_opts,
            )

        normalized_title = Book._normalize_title(cr_title)
        isbn13_value = None
        cover_url = cr_book.get("cover_url")
        if cover_url:
            # Extract ISBN from the cover URL we already built
            isbn13_value = cover_url.split("/isbn/")[1].split("-")[0] if "/isbn/" in cover_url else None

        cr_book_defaults = {"title": cr_title}
        if isbn13_value:
            cr_book_defaults["isbn13"] = isbn13_value

        # Title only on create: the shared Book row must not be renamed per reader.
        try:
            db_book, created = Book.objects.get_or_create(
                normalized_title=normalized_title, author=author, defaults=cr_book_defaults
            )
        except IntegrityError:
            cr_book_defaults.pop("isbn13", None)
            db_book, created = Book.objects.get_or_create(
                normalized_title=normalized_title, author=author, defaults=cr_book_defaults
            )
        if not created and isbn13_value and db_book.isbn13 is None:
            try:
                Book.objects.filter(pk=db_book.pk).update(isbn13=isbn13_value)
            except IntegrityError:
                logger.warning(f"IntegrityError filling ISBN {isbn13_value} on book {db_book.pk}, skipping")

        # Upgrade cover from enrichment data (verified cover_i or GB thumbnail)
        if db_book.cover_url:
            cr_book["cover_url"] = db_book.cover_url
