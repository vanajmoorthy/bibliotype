from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, TransactionTestCase, override_settings

from core.models import Author, Book, Genre
from core.services.dna_analyser import _build_cover_url


class BuildCoverUrlTests(TestCase):
    """Tests for the _build_cover_url() helper function."""

    def test_valid_isbn13(self):
        url = _build_cover_url("9780593099322")
        self.assertEqual(url, "https://covers.openlibrary.org/b/isbn/9780593099322-M.jpg")

    def test_none_isbn(self):
        self.assertIsNone(_build_cover_url(None))

    def test_empty_string(self):
        self.assertIsNone(_build_cover_url(""))

    def test_short_string(self):
        self.assertIsNone(_build_cover_url("123"))

    def test_goodreads_wrapped_isbn(self):
        """Goodreads wraps ISBNs in ="..." format."""
        url = _build_cover_url('="9780593099322"')
        self.assertEqual(url, "https://covers.openlibrary.org/b/isbn/9780593099322-M.jpg")

    def test_isbn_with_whitespace(self):
        url = _build_cover_url("  9780593099322  ")
        self.assertEqual(url, "https://covers.openlibrary.org/b/isbn/9780593099322-M.jpg")


CSV_HEADER = (
    "Title,Author,Exclusive Shelf,ISBN13,My Rating,Number of Pages,"
    "Average Rating,Date Read,Date Added,Original Publication Year,My Review"
)


def _csv(*rows):
    """Join header + data rows into a single CSV string."""
    return "\n".join([CSV_HEADER] + list(rows))


@override_settings(
    CELERY_TASK_ALWAYS_EAGER=True,
    CELERY_TASK_EAGER_PROPAGATES=True,
    CELERY_RESULT_BACKEND="django-db",
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "currently-reading-tests",
        }
    },
)
class CurrentlyReadingExtractionTests(TransactionTestCase):
    """Tests for currently-reading extraction in calculate_full_dna()."""

    def setUp(self):
        try:
            cache.clear()
        except Exception:
            pass

    @patch("core.tasks.generate_recommendations_task.delay")
    @patch("core.tasks.check_author_mainstream_status_task.delay")
    @patch("core.tasks.enrich_book_task.delay")
    @patch("core.services.dna_analyser.generate_vibe_with_llm")
    def test_currently_reading_books_extracted(self, mock_vibe, mock_enrich, mock_author_check, mock_rec_task):
        """Currently-reading books should be extracted with correct fields."""
        mock_vibe.return_value = ["vibe1", "vibe2", "vibe3", "vibe4"]
        user = User.objects.create_user(username="crtest", password="pw")

        csv = _csv(
            'Book A,Author X,read,="9780000000001",4,200,3.5,2024/01/15,2024/01/01,2020,',
            'Book B,Author Y,read,="9780000000002",5,300,4.0,2024/02/10,2024/01/05,2019,',
            'Book C,Author Z,currently-reading,="9780000000003",0,400,4.2,,2024/06/15,2021,',
            'Book D,Author W,currently-reading,="9780000000004",0,250,3.8,,2024/08/01,2022,',
            'Book E,Author V,to-read,="9780000000005",0,180,3.9,,2024/03/01,2023,',
        )

        from core.tasks import generate_reading_dna_task

        result = generate_reading_dna_task.delay(csv, user.id)

        user.userprofile.refresh_from_db()
        dna = user.userprofile.dna_data

        self.assertEqual(dna["currently_reading_count"], 2)
        self.assertEqual(len(dna["currently_reading_books"]), 2)

        # Should be sorted by Date Added descending (most recent first)
        self.assertEqual(dna["currently_reading_books"][0]["title"], "Book D")
        self.assertEqual(dna["currently_reading_books"][1]["title"], "Book C")

        # Each book should have cover_url, author, title, page_count
        for book in dna["currently_reading_books"]:
            self.assertIn("cover_url", book)
            self.assertIn("author", book)
            self.assertIn("title", book)
            self.assertIn("page_count", book)

    @patch("core.tasks.generate_recommendations_task.delay")
    @patch("core.tasks.check_author_mainstream_status_task.delay")
    @patch("core.tasks.enrich_book_task.delay")
    @patch("core.services.dna_analyser.generate_vibe_with_llm")
    def test_custom_shelf_count(self, mock_vibe, mock_enrich, mock_author_check, mock_rec_task):
        """Custom shelf count should exclude standard shelves (read, currently-reading, to-read)."""
        mock_vibe.return_value = ["vibe1", "vibe2", "vibe3", "vibe4"]
        user = User.objects.create_user(username="crshelf", password="pw")

        csv = _csv(
            'Book A,Author X,read,="9780000000001",4,200,3.5,2024/01/15,2024/01/01,2020,',
            'Book C,Author Z,currently-reading,="9780000000003",0,400,4.2,,2024/06/15,2021,',
            'Book E,Author V,to-read,="9780000000005",0,180,3.9,,2024/03/01,2023,',
            'Book F,Author U,favorites,="9780000000006",0,320,4.5,,2024/04/01,2018,',
        )

        from core.tasks import generate_reading_dna_task

        generate_reading_dna_task.delay(csv, user.id)

        user.userprofile.refresh_from_db()
        dna = user.userprofile.dna_data

        # Only "favorites" is a custom shelf
        self.assertEqual(dna["custom_shelf_count"], 1)

    @patch("core.tasks.generate_recommendations_task.delay")
    @patch("core.tasks.check_author_mainstream_status_task.delay")
    @patch("core.tasks.enrich_book_task.delay")
    @patch("core.services.dna_analyser.generate_vibe_with_llm")
    def test_no_currently_reading_books(self, mock_vibe, mock_enrich, mock_author_check, mock_rec_task):
        """When no currently-reading books exist, fields should be empty/zero."""
        mock_vibe.return_value = ["vibe1", "vibe2", "vibe3", "vibe4"]
        user = User.objects.create_user(username="crnoread", password="pw")

        csv = _csv(
            'Book A,Author X,read,="9780000000001",4,200,3.5,2024/01/15,2024/01/01,2020,',
        )

        from core.tasks import generate_reading_dna_task

        generate_reading_dna_task.delay(csv, user.id)

        user.userprofile.refresh_from_db()
        dna = user.userprofile.dna_data

        self.assertEqual(dna["currently_reading_count"], 0)
        self.assertEqual(dna["currently_reading_books"], [])
        self.assertEqual(dna["custom_shelf_count"], 0)

    @patch("core.tasks.generate_recommendations_task.delay")
    @patch("core.tasks.check_author_mainstream_status_task.delay")
    @patch("core.tasks.enrich_book_task.delay")
    @patch("core.services.dna_analyser.generate_vibe_with_llm")
    def test_niche_book_has_cover_url(self, mock_vibe, mock_enrich, mock_author_check, mock_rec_task):
        """The most_niche_book dict should include a cover_url field."""
        mock_vibe.return_value = ["vibe1", "vibe2", "vibe3", "vibe4"]
        user = User.objects.create_user(username="crniche", password="pw")

        csv = _csv(
            'Book A,Author X,read,="9780000000001",4,200,3.5,2024/01/15,2024/01/01,2020,',
            'Book B,Author Y,read,="9780000000002",5,300,4.0,2024/02/10,2024/01/05,2019,',
        )

        from core.tasks import generate_reading_dna_task

        generate_reading_dna_task.delay(csv, user.id)

        user.userprofile.refresh_from_db()
        dna = user.userprofile.dna_data

        if dna.get("most_niche_book"):
            self.assertIn("cover_url", dna["most_niche_book"])


class EnrichDnaBackwardCompatTests(TestCase):
    """Tests for backward compatibility in _enrich_dna_for_display()."""

    @override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
    def test_old_dna_gets_currently_reading_defaults(self):
        """Old DNA data missing currently-reading fields should get sensible defaults."""
        from core.views import _enrich_dna_for_display

        old_dna = {
            "user_stats": {
                "total_books_read": 50,
                "avg_book_length": 300,
                "avg_publish_year": 2015,
                "avg_books_per_year": 10,
                "num_reading_years": 5,
                "books_with_dates": 50,
                "total_pages_read": 15000,
            },
            "stats_by_year": [{"year": 2023, "count": 10, "avg_rating": 4.0}],
            "most_niche_book": {"title": "Test Book", "author": "Test Author", "read_count": 1},
        }

        _enrich_dna_for_display(old_dna)

        self.assertEqual(old_dna["currently_reading_books"], [])
        self.assertEqual(old_dna["currently_reading_count"], 0)
        self.assertEqual(old_dna["custom_shelf_count"], 0)
        self.assertIsNone(old_dna["most_niche_book"]["cover_url"])


class RecommendationCurrentlyReadingBoostTests(TestCase):
    """Tests for the currently-reading recommendation boost."""

    def setUp(self):
        self.author = Author.objects.create(name="Test Author")
        self.genre = Genre.objects.create(name="fantasy")
        self.book = Book.objects.create(title="Test Book", author=self.author)
        self.book.genres.add(self.genre)

    def test_matching_author_gives_boost(self):
        from core.services.recommendation_service import RecommendationEngine

        engine = RecommendationEngine()
        context = {
            "currently_reading_authors": {self.author.id},
            "currently_reading_genres": set(),
        }

        boost = engine._calculate_currently_reading_boost(self.book, context)
        self.assertGreater(boost, 0)
        self.assertGreaterEqual(boost, 0.10)

    def test_matching_genre_gives_boost(self):
        from core.services.recommendation_service import RecommendationEngine

        engine = RecommendationEngine()
        context = {
            "currently_reading_authors": set(),
            "currently_reading_genres": {"fantasy"},
        }

        boost = engine._calculate_currently_reading_boost(self.book, context)
        self.assertGreater(boost, 0)

    def test_no_match_gives_zero(self):
        from core.services.recommendation_service import RecommendationEngine

        engine = RecommendationEngine()
        context = {
            "currently_reading_authors": set(),
            "currently_reading_genres": set(),
        }

        boost = engine._calculate_currently_reading_boost(self.book, context)
        self.assertEqual(boost, 0.0)

    def test_boost_capped_at_015(self):
        from core.services.recommendation_service import RecommendationEngine

        engine = RecommendationEngine()
        # Both author and genre match
        context = {
            "currently_reading_authors": {self.author.id},
            "currently_reading_genres": {"fantasy"},
        }

        boost = engine._calculate_currently_reading_boost(self.book, context)
        self.assertLessEqual(boost, 0.15)

    def test_empty_context_keys_handled(self):
        """Context without currently_reading keys should return 0."""
        from core.services.recommendation_service import RecommendationEngine

        engine = RecommendationEngine()
        context = {}

        boost = engine._calculate_currently_reading_boost(self.book, context)
        self.assertEqual(boost, 0.0)


class CoverUrlPriorityTests(TestCase):
    """Tests for the book.cover_url or _build_cover_url(isbn13) pattern in DNA generation."""

    def setUp(self):
        self.author = Author.objects.create(name="Cover Test Author")

    def test_cover_url_prefers_book_cover_url_over_isbn(self):
        """Book with cover_url and isbn13 → DNA dict gets the cover_url, not the ISBN URL."""
        book = Book.objects.create(
            title="Cover Priority Book",
            author=self.author,
            isbn13="9780593099322",
            cover_url="https://covers.openlibrary.org/b/id/123-M.jpg",
        )
        result = book.cover_url or _build_cover_url(book.isbn13)
        self.assertEqual(result, "https://covers.openlibrary.org/b/id/123-M.jpg")

    def test_cover_url_falls_back_to_isbn_when_no_book_cover_url(self):
        """Book with cover_url=None and isbn13 → DNA dict gets the ISBN URL."""
        book = Book.objects.create(
            title="ISBN Fallback Book",
            author=self.author,
            isbn13="9780593099322",
            cover_url=None,
        )
        result = book.cover_url or _build_cover_url(book.isbn13)
        self.assertEqual(result, "https://covers.openlibrary.org/b/isbn/9780593099322-M.jpg")

    def test_cover_url_none_when_no_book_cover_url_and_no_isbn(self):
        """Book with cover_url=None and isbn13=None → DNA dict gets None."""
        book = Book.objects.create(
            title="No Cover Book",
            author=self.author,
            isbn13=None,
            cover_url=None,
        )
        result = book.cover_url or _build_cover_url(book.isbn13)
        self.assertIsNone(result)


@override_settings(
    CELERY_TASK_ALWAYS_EAGER=True,
    CELERY_TASK_EAGER_PROPAGATES=True,
    CELERY_RESULT_BACKEND="django-db",
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "cover-upgrade-tests",
        }
    },
)
class CurrentlyReadingCoverUpgradeTests(TransactionTestCase):
    """Tests for currently-reading cover_url upgrade from DB."""

    def setUp(self):
        try:
            cache.clear()
        except Exception:
            pass

    @patch("core.tasks.generate_recommendations_task.delay")
    @patch("core.tasks.check_author_mainstream_status_task.delay")
    @patch("core.tasks.enrich_book_task.delay")
    @patch("core.services.dna_analyser.generate_vibe_with_llm")
    def test_currently_reading_cover_upgraded_from_db(self, mock_vibe, mock_enrich, mock_author_check, mock_rec_task):
        """Currently-reading book in DB with cover_url → gets the DB cover URL."""
        mock_vibe.return_value = ["vibe1", "vibe2", "vibe3", "vibe4"]

        # Pre-create the book in DB with a cover_url (as if previously enriched)
        author = Author.objects.create(name="Pre Author")
        Book.objects.create(
            title="Pre Book",
            author=author,
            isbn13="9781234567890",
            cover_url="https://covers.openlibrary.org/b/id/999-M.jpg",
        )

        user = User.objects.create_user(username="covertest", password="pw")

        csv = _csv(
            'Read Book,Other Author,read,="9780000000001",4,200,3.5,2024/01/15,2024/01/01,2020,',
            'Pre Book,Pre Author,currently-reading,="9781234567890",0,300,4.0,,2024/06/15,2021,',
        )

        from core.tasks import generate_reading_dna_task

        generate_reading_dna_task.delay(csv, user.id)

        user.userprofile.refresh_from_db()
        dna = user.userprofile.dna_data

        cr_books = dna.get("currently_reading_books", [])
        self.assertEqual(len(cr_books), 1)
        self.assertEqual(cr_books[0]["title"], "Pre Book")
        self.assertEqual(cr_books[0]["cover_url"], "https://covers.openlibrary.org/b/id/999-M.jpg")

    @patch("core.tasks.generate_recommendations_task.delay")
    @patch("core.tasks.check_author_mainstream_status_task.delay")
    @patch("core.tasks.enrich_book_task.delay")
    @patch("core.services.dna_analyser.generate_vibe_with_llm")
    def test_currently_reading_cover_keeps_csv_isbn_when_no_db_cover(
        self, mock_vibe, mock_enrich, mock_author_check, mock_rec_task
    ):
        """Currently-reading book in DB with cover_url=None → keeps the ISBN-constructed URL from CSV."""
        mock_vibe.return_value = ["vibe1", "vibe2", "vibe3", "vibe4"]
        user = User.objects.create_user(username="nodbcover", password="pw")

        csv = _csv(
            'Read Book,Other Author,read,="9780000000001",4,200,3.5,2024/01/15,2024/01/01,2020,',
            'New Book,New Author,currently-reading,="9780000000099",0,300,4.0,,2024/06/15,2021,',
        )

        from core.tasks import generate_reading_dna_task

        generate_reading_dna_task.delay(csv, user.id)

        user.userprofile.refresh_from_db()
        dna = user.userprofile.dna_data

        cr_books = dna.get("currently_reading_books", [])
        self.assertEqual(len(cr_books), 1)
        # Should still have the ISBN-constructed URL since DB book has no cover_url
        self.assertEqual(cr_books[0]["cover_url"], "https://covers.openlibrary.org/b/isbn/9780000000099-M.jpg")
