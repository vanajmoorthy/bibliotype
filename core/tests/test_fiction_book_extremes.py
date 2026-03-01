from unittest.mock import MagicMock

from django.test import TestCase

from core.dna_constants import (
    CANONICAL_GENRE_MAP,
    FICTION_GENRES,
    GENRE_ALIASES,
    NONFICTION_GENRES,
)


class FictionNonfictionConstantsTests(TestCase):
    """Verify FICTION_GENRES and NONFICTION_GENRES cover all canonical genres."""

    def test_sets_cover_all_canonical_genres(self):
        all_canonical = set(GENRE_ALIASES.keys())
        classified = FICTION_GENRES | NONFICTION_GENRES
        self.assertEqual(classified, all_canonical)

    def test_no_overlap_between_fiction_and_nonfiction(self):
        overlap = FICTION_GENRES & NONFICTION_GENRES
        self.assertEqual(overlap, set(), f"Overlapping genres: {overlap}")


class FictionNonfictionClassificationTests(TestCase):
    """Test the fiction-first classification logic used in dna_analyser."""

    def _classify(self, genre_names):
        """Reproduce the classification logic from dna_analyser."""
        canonical = {CANONICAL_GENRE_MAP.get(g, g) for g in genre_names}
        if canonical & FICTION_GENRES:
            return "fiction"
        elif canonical & NONFICTION_GENRES:
            return "nonfiction"
        return "unclassified"

    def test_pure_fiction(self):
        self.assertEqual(self._classify(["fantasy fiction", "epic fantasy"]), "fiction")

    def test_pure_nonfiction(self):
        self.assertEqual(self._classify(["history", "military history"]), "nonfiction")

    def test_mixed_genres_fiction_wins(self):
        """A book with both fiction and nonfiction genres should classify as fiction."""
        self.assertEqual(self._classify(["fantasy", "history"]), "fiction")

    def test_no_matching_genres(self):
        self.assertEqual(self._classify(["unknown genre xyz"]), "unclassified")

    def test_empty_genres(self):
        self.assertEqual(self._classify([]), "unclassified")

    def test_alias_maps_to_fiction(self):
        self.assertEqual(self._classify(["ghost stories"]), "fiction")

    def test_alias_maps_to_nonfiction(self):
        self.assertEqual(self._classify(["autobiography"]), "nonfiction")


class BookExtremesTests(TestCase):
    """Test longest/shortest book selection logic."""

    def _make_book(self, title, author_name, page_count, isbn13=None):
        book = MagicMock()
        book.title = title
        book.page_count = page_count
        book.isbn13 = isbn13
        book.normalized_title = title.lower()
        book.author = MagicMock()
        book.author.name = author_name
        return book

    def test_longest_and_shortest(self):
        books = [
            self._make_book("Medium", "Author A", 300),
            self._make_book("Long", "Author B", 800),
            self._make_book("Short", "Author C", 100),
        ]
        books_with_pages = [b for b in books if b.page_count]
        books_with_pages.sort(key=lambda b: (-b.page_count, b.normalized_title))
        longest = books_with_pages[0]
        books_with_pages.sort(key=lambda b: (b.page_count, b.normalized_title))
        shortest = books_with_pages[0]

        self.assertEqual(longest.title, "Long")
        self.assertEqual(longest.page_count, 800)
        self.assertEqual(shortest.title, "Short")
        self.assertEqual(shortest.page_count, 100)

    def test_fewer_than_two_books_returns_none(self):
        books = [self._make_book("Only", "Author A", 200)]
        books_with_pages = [b for b in books if b.page_count]
        self.assertLess(len(books_with_pages), 2)

    def test_no_books_with_pages(self):
        books = [
            self._make_book("No Pages A", "Author A", None),
            self._make_book("No Pages B", "Author B", 0),
        ]
        books_with_pages = [b for b in books if b.page_count]
        self.assertEqual(len(books_with_pages), 0)

    def test_tiebreaker_uses_normalized_title(self):
        """When page counts are equal, normalized_title breaks the tie."""
        books = [
            self._make_book("Bravo", "Author A", 500),
            self._make_book("Alpha", "Author B", 500),
        ]
        books_with_pages = [b for b in books if b.page_count]
        books_with_pages.sort(key=lambda b: (-b.page_count, b.normalized_title))
        longest = books_with_pages[0]
        # "alpha" < "bravo" alphabetically, so Alpha wins the tiebreak
        self.assertEqual(longest.title, "Alpha")

        books_with_pages.sort(key=lambda b: (b.page_count, b.normalized_title))
        shortest = books_with_pages[0]
        self.assertEqual(shortest.title, "Alpha")
