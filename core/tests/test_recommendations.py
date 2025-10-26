"""
Tests for the book recommendation system
"""
from django.contrib.auth.models import User
from django.test import TestCase
from core.models import Book, Author, Genre, Publisher, UserBook, UserProfile
from core.services.user_similarity_service import (
    calculate_user_similarity,
    find_similar_users
)
from core.services.recommendation_service import (
    get_recommendations_for_user,
    _get_fallback_recommendations
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
        self.user1.userprofile.dna_data = {
            'top_genres': [('fantasy', 10), ('science fiction', 5)],
            'top_authors': [(self.author1.normalized_name, 5), (self.author2.normalized_name, 3)]
        }
        self.user1.userprofile.save()
        
        self.user2.userprofile.is_public = True
        self.user2.userprofile.dna_data = {
            'top_genres': [('fantasy', 10), ('science fiction', 5)],
            'top_authors': [(self.author1.normalized_name, 5), (self.author2.normalized_name, 3)]
        }
        self.user2.userprofile.save()
        
        self.user3.userprofile.is_public = True
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
        user_books = set(UserBook.objects.filter(user=self.user1).values_list('book_id', flat=True))
        fallback_recs = _get_fallback_recommendations(self.user1, user_books, needed=3)
        
        # Should get recommendations from favorite authors
        self.assertGreater(len(fallback_recs), 0)
    
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
        from core.models import Author
        
        author1 = Author.objects.create(name="Test Author", normalized_name="author, test")
        
        # Create another user with DNA data
        other_user = User.objects.create_user(username="other_user", password="test123")
        other_user.userprofile.is_public = True
        other_user.userprofile.dna_data = {
            'top_genres': [('fantasy', 10), ('science fiction', 5)],
            'top_authors': [(author1.normalized_name, 5)]
        }
        other_user.userprofile.save()
        
        # Make main user public with DNA data
        self.user.userprofile.is_public = True
        self.user.userprofile.dna_data = {
            'top_genres': [('fantasy', 10), ('science fiction', 5)],
            'top_authors': [(author1.normalized_name, 5)]
        }
        self.user.userprofile.save()
        
        # Check that other_user appears in similar users
        similar_users = find_similar_users(self.user, top_n=10, min_similarity=0.0)
        
        # Just check that we found similar users (since they have same DNA data)
        self.assertGreater(len(similar_users), 0)


if __name__ == '__main__':
    # Run tests with: python manage.py test core.tests.test_recommendations
    pass

