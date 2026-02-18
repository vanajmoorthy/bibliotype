"""
Tests for the book recommendation system
"""
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import TestCase
from core.models import Book, Author, Genre, Publisher, UserBook, UserProfile
from core.services.user_similarity_service import (
    calculate_user_similarity,
    find_similar_users
)
from core.services.recommendation_service import (
    get_recommendations_for_user,
)
from core.services.top_books_service import calculate_and_store_top_books


class RecommendationTestCase(TestCase):
    """Test suite for the recommendation system"""
    
    def setUp(self):
        """Set up test data"""
        # Create authors
        self.author1 = Author.objects.create(name="J.R.R. Tolkien", normalized_name="tolkien, j.r.r.")
        self.author2 = Author.objects.create(name="George R.R. Martin", normalized_name="martin, george r.r.")
        self.author3 = Author.objects.create(name="Ursula K. Le Guin", normalized_name="guin, ursula k. le")
        
        # Create genres
        self.genre_fantasy = Genre.objects.create(name="fantasy")
        self.genre_sci_fi = Genre.objects.create(name="science fiction")
        self.genre_fiction = Genre.objects.create(name="literature")
        
        # Create publishers
        self.publisher1 = Publisher.objects.create(name="HarperCollins")
        self.publisher2 = Publisher.objects.create(name="Tor Books")
        
        # Create books
        self.book1 = Book.objects.create(
            title="The Fellowship of the Ring",
            author=self.author1,
            publisher=self.publisher1,
            page_count=423,
            publish_year=1954,
            average_rating=4.40
        )
        self.book1.genres.add(self.genre_fantasy)
        
        self.book2 = Book.objects.create(
            title="A Game of Thrones",
            author=self.author2,
            publisher=self.publisher2,
            page_count=694,
            publish_year=1996,
            average_rating=4.45
        )
        self.book2.genres.add(self.genre_fantasy)
        
        self.book3 = Book.objects.create(
            title="The Left Hand of Darkness",
            author=self.author3,
            publisher=self.publisher2,
            page_count=304,
            publish_year=1969,
            average_rating=4.08
        )
        self.book3.genres.add(self.genre_sci_fi)
        
        self.book4 = Book.objects.create(
            title="The Two Towers",
            author=self.author1,
            publisher=self.publisher1,
            page_count=352,
            publish_year=1954,
            average_rating=4.44
        )
        self.book4.genres.add(self.genre_fantasy)
        
        self.book5 = Book.objects.create(
            title="The Return of the King",
            author=self.author1,
            publisher=self.publisher1,
            page_count=416,
            publish_year=1955,
            average_rating=4.55
        )
        self.book5.genres.add(self.genre_fantasy)
        
        # Create users
        self.user1 = User.objects.create_user(username="testuser1", email="user1@test.com", password="test123")
        self.user2 = User.objects.create_user(username="testuser2", email="user2@test.com", password="test123")
        self.user3 = User.objects.create_user(username="testuser3", email="user3@test.com", password="test123")
        
        # Make user profiles public and add DNA data
        self.user1.userprofile.is_public = True
        self.user1.userprofile.visible_in_recommendations = True
        self.user1.userprofile.dna_data = {
            'top_genres': [('fantasy', 10), ('science fiction', 5)],
            'top_authors': [(self.author1.normalized_name, 5), (self.author2.normalized_name, 3)]
        }
        self.user1.userprofile.save()
        
        self.user2.userprofile.is_public = True
        self.user2.userprofile.visible_in_recommendations = True
        self.user2.userprofile.dna_data = {
            'top_genres': [('fantasy', 10), ('science fiction', 5)],
            'top_authors': [(self.author1.normalized_name, 5), (self.author2.normalized_name, 3)]
        }
        self.user2.userprofile.save()
        
        self.user3.userprofile.is_public = True
        self.user3.userprofile.visible_in_recommendations = True
        self.user3.userprofile.dna_data = {
            'top_genres': [('fantasy', 10), ('science fiction', 5)],
            'top_authors': [(self.author1.normalized_name, 5), (self.author2.normalized_name, 3)]
        }
        self.user3.userprofile.save()
    
    def test_user_similarity_calculation(self):
        """Test that user similarity is calculated correctly"""
        # User1 reads book1 with rating 5
        UserBook.objects.create(user=self.user1, book=self.book1, user_rating=5)
        UserBook.objects.create(user=self.user1, book=self.book4, user_rating=4)
        
        # User2 reads book1 with rating 5
        UserBook.objects.create(user=self.user2, book=self.book1, user_rating=5)
        UserBook.objects.create(user=self.user2, book=self.book2, user_rating=4)
        
        # Calculate similarity
        similarity = calculate_user_similarity(self.user1, self.user2)
        
        self.assertGreater(similarity['similarity_score'], 0)
        self.assertEqual(similarity['shared_books_count'], 1)
    
    def test_find_similar_users(self):
        """Test finding similar users"""
        # Create a shared book
        UserBook.objects.create(user=self.user1, book=self.book1, user_rating=5)
        UserBook.objects.create(user=self.user2, book=self.book1, user_rating=5)
        
        # Create top books for user1
        ub = UserBook.objects.create(user=self.user1, book=self.book4, user_rating=5, is_top_book=True, top_book_position=1)
        
        # Find similar users
        similar_users = find_similar_users(self.user2, top_n=5, min_similarity=0.1)
        
        self.assertGreater(len(similar_users), 0)
        found_user1 = False
        for user, data in similar_users:
            if user == self.user1:
                found_user1 = True
                self.assertGreater(data['similarity_score'], 0.1)
        self.assertTrue(found_user1)
    
    def test_top_books_calculation(self):
        """Test that top books are calculated correctly"""
        # Create multiple user books with different ratings
        UserBook.objects.create(user=self.user1, book=self.book1, user_rating=5)
        UserBook.objects.create(user=self.user1, book=self.book2, user_rating=4)
        UserBook.objects.create(user=self.user1, book=self.book3, user_rating=3)
        
        # Calculate top books
        top_books = calculate_and_store_top_books(self.user1, limit=5)
        
        self.assertGreater(len(top_books), 0)
        
        # Verify top book flags
        top_books_queried = UserBook.objects.filter(user=self.user1, is_top_book=True).order_by('top_book_position')
        self.assertTrue(all(ub.is_top_book for ub in top_books_queried))
    
    def test_top_books_rating_priority(self):
        """Test that books with higher ratings are prioritized"""
        # Create books with different ratings
        UserBook.objects.create(user=self.user1, book=self.book1, user_rating=5)
        UserBook.objects.create(user=self.user1, book=self.book2, user_rating=3)
        UserBook.objects.create(user=self.user1, book=self.book3, user_rating=4)
        
        # Calculate top books
        calculate_and_store_top_books(self.user1, limit=3)
        
        # The highest rated book should be position 1
        top_book = UserBook.objects.get(user=self.user1, is_top_book=True, top_book_position=1)
        self.assertEqual(top_book.book, self.book1)
    
    def test_get_recommendations_for_user(self):
        """Test recommendation generation for a user"""
        # User1 reads book1 and book2
        UserBook.objects.create(user=self.user1, book=self.book1, user_rating=5, is_top_book=True, top_book_position=1)
        UserBook.objects.create(user=self.user1, book=self.book2, user_rating=4, is_top_book=True, top_book_position=2)
        
        # User2 reads book1 and book3
        UserBook.objects.create(user=self.user2, book=self.book1, user_rating=5, is_top_book=True, top_book_position=1)
        UserBook.objects.create(user=self.user2, book=self.book3, user_rating=4, is_top_book=True, top_book_position=2)
        
        # User3 reads book1 and book4
        UserBook.objects.create(user=self.user3, book=self.book1, user_rating=5, is_top_book=True, top_book_position=1)
        UserBook.objects.create(user=self.user3, book=self.book4, user_rating=4, is_top_book=True, top_book_position=2)
        
        # Get recommendations for user1
        recommendations = get_recommendations_for_user(self.user1, limit=10)
        
        self.assertGreater(len(recommendations), 0)
        
        # Verify that recommended books are not books user1 has read
        user1_books = set(UserBook.objects.filter(user=self.user1).values_list('book_id', flat=True))
        for rec in recommendations:
            self.assertNotIn(rec['book'].id, user1_books)
    
    def test_fallback_recommendations(self):
        """Test fallback recommendations when no similar users"""
        # Create DNA data for user1
        dna_data = {
            'top_genres': [('fantasy', 10), ('science fiction', 5)],
            'top_authors': [(self.author1.normalized_name, 5), (self.author2.normalized_name, 3)]
        }
        self.user1.userprofile.dna_data = dna_data
        self.user1.userprofile.save()
        
        # User1 reads book1 and book2
        UserBook.objects.create(user=self.user1, book=self.book1, user_rating=5)
        UserBook.objects.create(user=self.user1, book=self.book2, user_rating=4)
        
        # No similar users exist, so fallback should kick in
        # The recommendation engine will use fallback candidates internally
        fallback_recs = get_recommendations_for_user(self.user1, limit=3)
        
        # Should get recommendations from favorite authors/genres
        self.assertGreater(len(fallback_recs), 0)
        
        # Verify that recommended books are not books user1 has read
        user_books = set(UserBook.objects.filter(user=self.user1).values_list('book_id', flat=True))
        for rec in fallback_recs:
            self.assertNotIn(rec['book'].id, user_books)
    
    def test_recommendations_exclude_read_books(self):
        """Test that books already read are excluded from recommendations"""
        # User1 reads book1 and book2
        UserBook.objects.create(user=self.user1, book=self.book1, user_rating=5, is_top_book=True, top_book_position=1)
        UserBook.objects.create(user=self.user1, book=self.book2, user_rating=4)
        
        # User2 reads book1, book3, and book4
        UserBook.objects.create(user=self.user2, book=self.book1, user_rating=5)
        UserBook.objects.create(user=self.user2, book=self.book3, user_rating=4, is_top_book=True, top_book_position=1)
        UserBook.objects.create(user=self.user2, book=self.book4, user_rating=5, is_top_book=True, top_book_position=2)
        
        # Get recommendations for user1
        recommendations = get_recommendations_for_user(self.user1, limit=10)
        
        # None of the recommendations should be book1 or book2 (already read)
        user1_book_ids = {self.book1.id, self.book2.id}
        recommended_book_ids = {rec['book'].id for rec in recommendations}
        
        self.assertFalse(user1_book_ids & recommended_book_ids)
    
    def test_recommendations_include_sources(self):
        """Test that recommendations include source information"""
        # User1 reads book1
        UserBook.objects.create(user=self.user1, book=self.book1, user_rating=5, is_top_book=True, top_book_position=1)
        
        # User2 reads book1 and book2
        UserBook.objects.create(user=self.user2, book=self.book1, user_rating=5)
        UserBook.objects.create(user=self.user2, book=self.book2, user_rating=4, is_top_book=True, top_book_position=1)
        
        # Get recommendations for user1
        recommendations = get_recommendations_for_user(self.user1, limit=10)
        
        # Should have recommendations from user2
        found_user2_rec = False
        for rec in recommendations:
            if rec['book'] == self.book2:
                self.assertIn('sources', rec)
                if rec['sources']:
                    found_user2_rec = True
                    # Check that sources have username field
                    for source in rec['sources']:
                        if source.get('username') == self.user2.username:
                            self.assertEqual(source['username'], self.user2.username)
                            break
        
        self.assertTrue(found_user2_rec)
    
    def test_top_books_sentiment_analysis(self):
        """Test that sentiment in reviews affects top book calculation"""
        # Create books with reviews
        UserBook.objects.create(
            user=self.user1, 
            book=self.book1, 
            user_rating=4,
            user_review="Amazing book! Really loved the characters and world-building. Highly recommend!"
        )
        UserBook.objects.create(
            user=self.user1, 
            book=self.book2, 
            user_rating=4,
            user_review="It was okay."
        )
        
        # Calculate top books (should prioritize book1 due to positive sentiment)
        calculate_and_store_top_books(self.user1, limit=2)
        
        top_book = UserBook.objects.get(user=self.user1, is_top_book=True, top_book_position=1)
        self.assertEqual(top_book.book, self.book1)


class PrivacyTestCase(TestCase):
    """Test privacy and visibility features"""
    
    def setUp(self):
        self.user = User.objects.create_user(username="privacy_test", password="test123")
    
    def test_public_users_appear_in_recommendations(self):
        """Test that only public users are used for recommendations"""
        from core.models import Author, Book, Genre, Publisher
        
        author1 = Author.objects.create(name="Test Author", normalized_name="author, test")
        genre1 = Genre.objects.create(name="fantasy")
        publisher1 = Publisher.objects.create(name="Test Publisher")
        
        # Create a shared book that both users will read
        shared_book = Book.objects.create(
            title="Shared Book",
            author=author1,
            publisher=publisher1,
            average_rating=4.5
        )
        shared_book.genres.add(genre1)
        
        # Create another user with DNA data
        other_user = User.objects.create_user(username="other_user", password="test123")
        other_user.userprofile.is_public = True
        other_user.userprofile.visible_in_recommendations = True
        other_user.userprofile.dna_data = {
            'top_genres': [('fantasy', 10), ('science fiction', 5)],
            'top_authors': [(author1.normalized_name, 5)]
        }
        other_user.userprofile.save()
        
        # Add shared book to other_user
        UserBook.objects.create(user=other_user, book=shared_book, user_rating=5)
        
        # Make main user public with DNA data
        self.user.userprofile.is_public = True
        self.user.userprofile.visible_in_recommendations = True
        self.user.userprofile.dna_data = {
            'top_genres': [('fantasy', 10), ('science fiction', 5)],
            'top_authors': [(author1.normalized_name, 5)]
        }
        self.user.userprofile.save()
        
        # Add shared book to main user (required for find_similar_users to work)
        UserBook.objects.create(user=self.user, book=shared_book, user_rating=5)
        
        # Check that other_user appears in similar users
        similar_users = find_similar_users(self.user, top_n=10, min_similarity=0.0)
        
        # Should find similar users since they share books and have similar DNA data
        self.assertGreater(len(similar_users), 0)


class GlobalPopularityFallbackTestCase(TestCase):
    """Test the global popularity last-resort fallback in the recommendation engine."""

    def setUp(self):
        self.author1 = Author.objects.create(name="Popular Author 1")
        self.author2 = Author.objects.create(name="Popular Author 2")
        self.author3 = Author.objects.create(name="Popular Author 3")
        self.genre = Genre.objects.create(name="fiction")

        # Create globally popular books with high ratings and rating counts
        self.popular_book1 = Book.objects.create(
            title="Popular Book One",
            author=self.author1,
            average_rating=4.5,
            google_books_ratings_count=50000,
        )
        self.popular_book1.genres.add(self.genre)

        self.popular_book2 = Book.objects.create(
            title="Popular Book Two",
            author=self.author2,
            average_rating=4.3,
            google_books_ratings_count=40000,
        )
        self.popular_book2.genres.add(self.genre)

        self.popular_book3 = Book.objects.create(
            title="Popular Book Three",
            author=self.author3,
            average_rating=4.1,
            google_books_ratings_count=30000,
        )
        self.popular_book3.genres.add(self.genre)

        # Create an isolated user with no similar users
        self.user = User.objects.create_user(username="isolated_user", password="test123")
        self.user.userprofile.is_public = True
        self.user.userprofile.visible_in_recommendations = True
        self.user.userprofile.dna_data = {
            "top_genres": [("fiction", 3)],
            "top_authors": [],
        }
        self.user.userprofile.save()

    def test_global_fallback_provides_recommendations_when_no_similar_users(self):
        """User with no similar users still gets recommendations from global popularity."""
        recommendations = get_recommendations_for_user(self.user, limit=6)
        self.assertGreater(len(recommendations), 0)

    def test_global_fallback_excludes_already_read_books(self):
        """Global fallback skips books the user has already read."""
        UserBook.objects.create(user=self.user, book=self.popular_book1, user_rating=4)

        recommendations = get_recommendations_for_user(self.user, limit=6)
        recommended_ids = {rec["book"].id for rec in recommendations}
        self.assertNotIn(self.popular_book1.id, recommended_ids)

    def test_global_fallback_has_discovery_explanation(self):
        """Global fallback recommendations get a 'discovery' explanation component."""
        from core.services.recommendation_service import RecommendationEngine

        engine = RecommendationEngine()
        context = engine._build_user_context(self.user)
        fallback = engine._get_fallback_candidates(context, limit=6)

        # Verify fallback candidates have global_popularity source type
        for book_id, candidate in fallback.items():
            source_types = [s["type"] for s in candidate["sources"]]
            if "global_popularity" in source_types:
                self.assertGreater(candidate["max_similarity"], 0)
                return

        self.fail("No global_popularity candidates found in fallback")

    def test_global_fallback_limits_one_book_per_author(self):
        """Global fallback enforces author diversity (max 1 per author)."""
        # Create a second book by author1
        extra_book = Book.objects.create(
            title="Another Book By Author 1",
            author=self.author1,
            average_rating=4.4,
            google_books_ratings_count=45000,
        )
        extra_book.genres.add(self.genre)

        from core.services.recommendation_service import RecommendationEngine

        engine = RecommendationEngine()
        context = engine._build_user_context(self.user)
        fallback = engine._get_fallback_candidates(context, limit=10)

        # Count books per author in global_popularity candidates
        author_counts = {}
        for book_id, candidate in fallback.items():
            if any(s["type"] == "global_popularity" for s in candidate["sources"]):
                author_id = candidate["book"].author_id
                author_counts[author_id] = author_counts.get(author_id, 0) + 1

        for author_id, count in author_counts.items():
            self.assertLessEqual(count, 1, f"Author {author_id} has {count} books in global fallback")

    def test_global_fallback_only_includes_high_rated_books(self):
        """Global fallback only includes books with 4.0+ average rating."""
        # Create a low-rated book
        low_author = Author.objects.create(name="Low Rated Author")
        low_book = Book.objects.create(
            title="Low Rated Book",
            author=low_author,
            average_rating=3.0,
            google_books_ratings_count=100000,
        )

        from core.services.recommendation_service import RecommendationEngine

        engine = RecommendationEngine()
        context = engine._build_user_context(self.user)
        fallback = engine._get_fallback_candidates(context, limit=10)

        fallback_ids = set(fallback.keys())
        self.assertNotIn(low_book.id, fallback_ids)


class EmptyStateRecommendationsViewTestCase(TestCase):
    """Test that the empty-state UI shows when there are no recommendations."""

    def setUp(self):
        self.user = User.objects.create_user(username="emptyrecuser", email="empty@test.com", password="test123")
        self.user.userprofile.dna_data = {
            "reader_type": "Fantasy Fanatic",
            "reader_type_explanation": "Test",
            "top_reader_types": [{"type": "Fantasy Fanatic", "score": 10}],
            "reader_type_scores": {"Fantasy Fanatic": 10},
            "top_genres": [["fantasy", 5]],
            "top_authors": [["Test Author", 3]],
            "average_rating_overall": 4.0,
            "ratings_distribution": {"1": 0, "2": 0, "3": 1, "4": 2, "5": 1},
            "top_controversial_books": [],
            "most_positive_review": None,
            "most_negative_review": None,
            "stats_by_year": [],
            "mainstream_score_percent": 50,
            "reading_vibe": ["Test vibe"],
            "vibe_data_hash": "test",
            "user_stats": {"total_books_read": 4, "total_pages_read": 1200, "avg_book_length": 300, "avg_publish_year": 2020},
            "bibliotype_percentiles": {},
            "global_averages": {},
            "most_niche_book": None,
        }
        self.user.userprofile.reader_type = "Fantasy Fanatic"
        self.user.userprofile.recommendations_data = None
        self.user.userprofile.save()

    @patch("core.tasks.generate_recommendations_task")
    def test_dashboard_shows_empty_state_when_no_recommendations(self, mock_rec_task):
        """Dashboard shows fallback message when recommendations_data is None."""
        mock_rec_task.delay = MagicMock()
        self.client.login(username="emptyrecuser", password="test123")

        response = self.client.get("/dashboard/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No recommended books yet")
        self.assertContains(response, "upload an updated CSV")

    @patch("core.tasks.generate_recommendations_task")
    def test_dashboard_shows_recommendations_when_present(self, mock_rec_task):
        """Dashboard shows recommendations grid when data exists."""
        mock_rec_task.delay = MagicMock()

        self.user.userprofile.recommendations_data = [
            {
                "book_id": 1,
                "book_title": "Test Book",
                "book_author": "Test Author",
                "book_average_rating": 4.5,
                "confidence": 0.8,
                "confidence_pct": 80,
                "score": 1.0,
                "recommender_count": 2,
                "genre_alignment": 0.5,
                "sources": [{"type": "similar_user", "username": "other", "similarity_score": 0.8}],
                "explanation_components": {"rating": "Highly rated (4.5)"},
                "primary_source_user": {"username": "other", "match_quality": "Strong"},
            }
        ]
        self.user.userprofile.save()
        self.client.login(username="emptyrecuser", password="test123")

        response = self.client.get("/dashboard/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Test Book")
        self.assertNotContains(response, "No recommended books yet")

    def test_public_profile_shows_empty_state_when_no_recommendations(self):
        """Public profile shows fallback message when no recommendations."""
        self.user.userprofile.is_public = True
        self.user.userprofile.recommendations_data = None
        self.user.userprofile.save()

        response = self.client.get(f"/u/{self.user.username}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No recommended books yet")
        self.assertContains(response, "more readers join Bibliotype")


if __name__ == '__main__':
    # Run tests with: python manage.py test core.tests.test_recommendations
    pass

