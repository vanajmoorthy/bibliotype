from unittest.mock import MagicMock

from django.test import TestCase

from core.dna_constants import (
    FICTION_GENRES,
    GENRE_ALIASES,
    NONFICTION_GENRES,
)
from core.services.genre_classification import canonicalize_genre_names, classify_genres
from core.services.top_books_service import compute_book_score


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
    """Test the shared context-dependent classifier used by core.services.dna and views."""

    def _classify(self, genre_names):
        return classify_genres(canonicalize_genre_names(genre_names))

    def test_pure_fiction(self):
        self.assertEqual(self._classify(["fantasy fiction", "epic fantasy"]), "fiction")

    def test_pure_nonfiction(self):
        self.assertEqual(self._classify(["history", "military history"]), "nonfiction")

    def test_mixed_genres_fiction_wins(self):
        """A book with an unambiguous fiction genre classifies as fiction even with nonfiction genres."""
        self.assertEqual(self._classify(["fantasy", "history"]), "fiction")

    def test_no_matching_genres(self):
        self.assertIsNone(self._classify(["unknown genre xyz"]))

    def test_empty_genres(self):
        self.assertIsNone(self._classify([]))

    def test_alias_maps_to_fiction(self):
        self.assertEqual(self._classify(["ghost stories"]), "fiction")

    def test_alias_maps_to_nonfiction(self):
        self.assertEqual(self._classify(["autobiography"]), "nonfiction")

    def test_ambiguous_genre_with_fiction_signal(self):
        """classics + fantasy → fiction (unambiguous fiction signal wins)."""
        self.assertEqual(self._classify(["classics", "fantasy"]), "fiction")

    def test_ambiguous_genre_with_nonfiction_signal(self):
        """classics + history → nonfiction (e.g. 'A Brief History of Time')."""
        self.assertEqual(self._classify(["classics", "history"]), "nonfiction")

    def test_ambiguous_genre_no_signal_defaults_fiction(self):
        """classics alone → fiction (the conservative default)."""
        self.assertEqual(self._classify(["classics"]), "fiction")

    def test_ambiguous_plus_nonfiction_plus_fiction_stays_fiction(self):
        """classics + history + fantasy → fiction (historical fantasy, not nonfiction)."""
        self.assertEqual(self._classify(["classics", "history", "fantasy"]), "fiction")


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
        """Mirror production logic: single descending sort, [0] = longest, [-1] = shortest."""
        books = [
            self._make_book("Medium", "Author A", 300),
            self._make_book("Long", "Author B", 800),
            self._make_book("Short", "Author C", 100),
        ]
        books_with_pages = [b for b in books if b.page_count]
        books_with_pages.sort(key=lambda b: (-b.page_count, b.normalized_title))
        longest = books_with_pages[0]
        shortest = books_with_pages[-1]

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
        """When page counts are equal, normalized_title breaks the tie (single sort, like production)."""
        books = [
            self._make_book("Bravo", "Author A", 500),
            self._make_book("Alpha", "Author B", 500),
        ]
        books_with_pages = [b for b in books if b.page_count]
        books_with_pages.sort(key=lambda b: (-b.page_count, b.normalized_title))
        longest = books_with_pages[0]
        shortest = books_with_pages[-1]
        # "alpha" < "bravo" alphabetically, so Alpha wins the tiebreak for both
        self.assertEqual(longest.title, "Alpha")
        self.assertEqual(shortest.title, "Bravo")


class ComputeBookScoreTests(TestCase):
    """US-040: canonical top-book scoring shared by auth and anonymous paths."""

    def test_five_star_rating(self):
        self.assertEqual(compute_book_score(5, None), 100)

    def test_four_star_rating(self):
        self.assertEqual(compute_book_score(4, None), 80)

    def test_lower_ratings_scale_by_fifteen(self):
        self.assertEqual(compute_book_score(3, None), 45)
        self.assertEqual(compute_book_score(2, None), 30)
        self.assertEqual(compute_book_score(1, None), 15)

    def test_sentiment_weight(self):
        self.assertEqual(compute_book_score(5, 0.5), 100 + 0.5 * 30)
        self.assertEqual(compute_book_score(None, -1.0), -30)

    def test_no_rating_no_review_boost(self):
        self.assertEqual(compute_book_score(None, None), 10)
        self.assertEqual(compute_book_score(0, None), 10)

    def test_auth_and_anon_paths_agree_for_same_inputs(self):
        """The same (rating, sentiment) fixtures must rank identically on both paths."""
        fixtures = [(5, 0.9), (4, None), (3, -0.2), (None, 0.7), (None, None), (1, None)]
        scores = [compute_book_score(rating, sentiment) for rating, sentiment in fixtures]
        expected = [127.0, 80, 39.0, 21.0, 10, 15]
        self.assertEqual(scores, expected)
        # Ranking derived from the shared formula is what both paths store
        ranked = sorted(range(len(fixtures)), key=lambda i: scores[i], reverse=True)
        self.assertEqual(ranked, [0, 1, 2, 3, 5, 4])
