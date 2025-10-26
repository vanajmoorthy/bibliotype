"""
Populate DNA data for test users based on their UserBook entries
"""
import random
from collections import Counter
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from core.models import UserBook, UserProfile, AggregateAnalytics, Book, Genre
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer


class Command(BaseCommand):
    help = 'Generate DNA data for test users based on their UserBooks'

    def add_arguments(self, parser):
        parser.add_argument(
            '--overwrite',
            action='store_true',
            help='Overwrite existing DNA data'
        )

    def handle(self, *args, **options):
        overwrite = options.get('overwrite', False)
        test_users = User.objects.filter(username__startswith='test_')
        
        for user in test_users:
            self.stdout.write(f"\nProcessing {user.username}...")
            
            # Skip if already has DNA and not overwriting
            if user.userprofile.dna_data and not overwrite:
                self.stdout.write(f"  Skipping {user.username} - already has DNA data")
                continue
            
            user_books = UserBook.objects.filter(user=user).select_related('book', 'book__author', 'book__publisher')
            
            if not user_books.exists():
                # No UserBooks - generate DNA from available books in DB with random assignments
                self.stdout.write(f"  No UserBooks found for {user.username}, generating synthetic DNA...")
                dna = self.generate_synthetic_dna()
            else:
                # Generate DNA from UserBooks
                dna = self.generate_dna_from_userbooks(user_books, user)
            
            # Save to profile
            profile = user.userprofile
            profile.dna_data = dna
            profile.save()
            
            self.stdout.write(self.style.SUCCESS(f"  Generated DNA for {user.username}"))

    def generate_dna_from_userbooks(self, user_books, user):
        """Generate DNA data from UserBook entries"""
        
        analyzer = SentimentIntensityAnalyzer()
        
        # Extract genres
        genres = Counter()
        authors = Counter()
        
        # Collect stats
        ratings = []
        controversial_books = []
        positive_reviews = []
        negative_reviews = []
        book_lengths = []
        publish_years = []
        mainstream_count = 0
        total_books = user_books.count()
        
        for user_book in user_books:
            book = user_book.book
            
            # Genres
            for genre in book.genres.all():
                weight = user_book.user_rating if user_book.user_rating else 1
                genres[genre.name] += weight
            
            # Authors (normalized)
            weight = user_book.user_rating if user_book.user_rating else 1
            authors[book.author.normalized_name] += weight
            
            # Ratings
            if user_book.user_rating:
                ratings.append(user_book.user_rating)
                
                # Check for controversial ratings
                if book.average_rating:
                    diff = abs(user_book.user_rating - book.average_rating)
                    controversial_books.append({
                        'title': book.title,
                        'my_rating': user_book.user_rating,
                        'average_rating': book.average_rating,
                        'rating_difference': diff
                    })
            
            # Reviews
            if user_book.user_review:
                sentiment = analyzer.polarity_scores(user_book.user_review)["compound"]
                review_data = {
                    'book_title': book.title,
                    'review': user_book.user_review,
                    'sentiment': sentiment
                }
                
                if sentiment > 0.5:
                    positive_reviews.append(review_data)
                elif sentiment < -0.3:
                    negative_reviews.append(review_data)
            
            # Stats
            if book.page_count:
                book_lengths.append(book.page_count)
            if book.publish_year:
                publish_years.append(book.publish_year)
            if (book.author and book.author.is_mainstream) or \
               (book.publisher and book.publisher.is_mainstream):
                mainstream_count += 1
        
        # Sort genres and authors
        top_genres = genres.most_common(10)
        top_authors = authors.most_common(20)
        
        # Sort controversial books
        controversial_books.sort(key=lambda x: x['rating_difference'], reverse=True)
        controversial_books = controversial_books[:5]
        
        # Sort reviews
        positive_reviews.sort(key=lambda x: x['sentiment'], reverse=True)
        negative_reviews.sort(key=lambda x: x['sentiment'])
        
        # Calculate averages
        avg_rating = sum(ratings) / len(ratings) if ratings else None
        avg_length = sum(book_lengths) / len(book_lengths) if book_lengths else None
        avg_year = int(sum(publish_years) / len(publish_years)) if publish_years else None
        
        # Mainstream score
        mainstream_score = int((mainstream_count / total_books) * 100) if total_books > 0 else 0
        
        # Ratings distribution
        ratings_dist = Counter(ratings)
        ratings_dist_list = [{'rating': k, 'count': v} for k, v in sorted(ratings_dist.items())]
        
        # Reader type
        reader_type = self.determine_reader_type(top_genres, avg_rating, mainstream_score)
        
        # Build DNA
        dna = {
            'user_stats': {
                'total_books_read': total_books,
                'avg_book_length': int(avg_length) if avg_length else None,
                'avg_publish_year': avg_year,
            },
            'bibliotype_percentiles': {
                'total_books': 50,  # Placeholder
                'avg_rating': 50,  # Placeholder
                'genre_diversity': len(genres),
            },
            'global_averages': {
                'avg_books_per_user': 50,  # Placeholder
                'avg_rating': 3.5,  # Placeholder
            },
            'most_niche_book': {
                'title': controversial_books[0]['title'] if controversial_books else 'Unknown',
                'average_rating': controversial_books[0]['average_rating'] if controversial_books else 3.5,
            },
            'reader_type': reader_type,
            'reader_type_explanation': f"A {reader_type.lower()} who enjoys diverse reading",
            'top_reader_types': [
                {'type': reader_type, 'score': 100},
                {'type': 'Eclectic Reader', 'score': 70},
            ],
            'reader_type_scores': {
                reader_type: 100,
                'Eclectic Reader': 70,
            },
            'top_genres': [(name, count) for name, count in top_genres],
            'top_authors': [(name, count) for name, count in top_authors],
            'average_rating_overall': round(avg_rating, 2) if avg_rating else 3.5,
            'ratings_distribution': ratings_dist_list,
            'top_controversial_books': controversial_books[:3],
            'most_positive_review': positive_reviews[0] if positive_reviews else None,
            'most_negative_review': negative_reviews[0] if negative_reviews else None,
            'stats_by_year': [],  # Could add this if we have dates
            'mainstream_score_percent': mainstream_score,
            'reading_vibe': f"You're a {reader_type.lower()} with eclectic taste, drawn to {top_genres[0][0] if top_genres else 'various'} genres.",
            'vibe_data_hash': hash(str(top_genres + top_authors)),
        }
        
        return dna

    def determine_reader_type(self, top_genres, avg_rating, mainstream_score):
        """Determine reader type based on genres"""
        
        if not top_genres:
            return 'Eclectic Reader'
        
        primary_genre = top_genres[0][0]
        
        # Map genres to reader types
        type_mapping = {
            'fantasy': 'Fantasy Fan',
            'science fiction': 'Sci-Fi Enthusiast',
            'history': 'History Buff',
            'philosophy': 'Philosopher Reader',
            'literature': 'Literary Connoisseur',
            'classics': 'Classics Aficionado',
            'thriller': 'Thriller Lover',
            'romance': 'Romantic Reader',
        }
        
        reader_type = type_mapping.get(primary_genre, 'Eclectic Reader')
        
        # Modify based on patterns
        if mainstream_score < 30:
            reader_type = 'Niche Explorer'
        elif mainstream_score > 70:
            reader_type = 'Mainstream Reader'
        
        return reader_type

    def generate_synthetic_dna(self):
        """Generate synthetic DNA data for testing when user has no UserBooks"""
        from core.models import Book
        
        # Get random books from database
        all_books = list(Book.objects.all()[:50])
        
        if not all_books:
            # Minimal fallback
            return {
                'user_stats': {'total_books_read': 10, 'avg_book_length': 300, 'avg_publish_year': 2015},
                'bibliotype_percentiles': {'total_books': 50, 'avg_rating': 50, 'genre_diversity': 5},
                'global_averages': {'avg_books_per_user': 50, 'avg_rating': 3.5},
                'most_niche_book': {'title': 'Unknown', 'average_rating': 3.5},
                'reader_type': 'Eclectic Reader',
                'reader_type_explanation': 'A varied reader',
                'top_reader_types': [{'type': 'Eclectic Reader', 'score': 100}],
                'reader_type_scores': {'Eclectic Reader': 100},
                'top_genres': [('fantasy', 5), ('history', 3), ('fiction', 2)],
                'top_authors': [('Unknown Author', 3)],
                'average_rating_overall': 4.0,
                'ratings_distribution': [{'rating': 4, 'count': 5}, {'rating': 5, 'count': 3}],
                'top_controversial_books': [],
                'most_positive_review': None,
                'most_negative_review': None,
                'stats_by_year': [],
                'mainstream_score_percent': 40,
                'reading_vibe': 'An eclectic reader exploring various genres',
                'vibe_data_hash': hash('synthetic'),
            }
        
        import random
        
        # Random sample of books
        books = random.sample(all_books, min(20, len(all_books)))
        
        genres = Counter()
        authors = Counter()
        ratings = []
        controversial_books = []
        
        for book in books:
            # Genres
            for genre in book.genres.all():
                genres[genre.name] += random.randint(1, 5)
            
            # Authors
            weight = random.randint(1, 5)
            authors[book.author.normalized_name] += weight
            
            # Ratings
            rating = random.choices([1, 2, 3, 4, 5], weights=[0.05, 0.10, 0.20, 0.35, 0.30])[0]
            ratings.append(rating)
            
            # Controversial
            if book.average_rating:
                diff = abs(rating - book.average_rating)
                if diff > 1.0:
                    controversial_books.append({
                        'title': book.title,
                        'my_rating': rating,
                        'average_rating': book.average_rating,
                        'rating_difference': diff
                    })
        
        # Build DNA
        top_genres = genres.most_common(10)
        top_authors = authors.most_common(20)
        
        avg_rating = sum(ratings) / len(ratings) if ratings else 4.0
        
        ratings_dist = Counter(ratings)
        ratings_dist_list = [{'rating': k, 'count': v} for k, v in sorted(ratings_dist.items())]
        
        reader_type = self.determine_reader_type(top_genres, avg_rating, 40)
        
        dna = {
            'user_stats': {
                'total_books_read': len(books),
                'avg_book_length': 300,  # Synthetic
                'avg_publish_year': 2015,  # Synthetic
            },
            'bibliotype_percentiles': {
                'total_books': 50,
                'avg_rating': 50,
                'genre_diversity': len(genres),
            },
            'global_averages': {
                'avg_books_per_user': 50,
                'avg_rating': 3.5,
            },
            'most_niche_book': controversial_books[0] if controversial_books else {'title': 'Unknown', 'average_rating': 3.5},
            'reader_type': reader_type,
            'reader_type_explanation': f'A {reader_type.lower()} with diverse interests',
            'top_reader_types': [
                {'type': reader_type, 'score': 100},
                {'type': 'Eclectic Reader', 'score': 70},
            ],
            'reader_type_scores': {
                reader_type: 100,
                'Eclectic Reader': 70,
            },
            'top_genres': [(name, count) for name, count in top_genres],
            'top_authors': [(name, count) for name, count in top_authors],
            'average_rating_overall': round(avg_rating, 2),
            'ratings_distribution': ratings_dist_list,
            'top_controversial_books': controversial_books[:3] if controversial_books else [],
            'most_positive_review': None,
            'most_negative_review': None,
            'stats_by_year': [],
            'mainstream_score_percent': 40,
            'reading_vibe': f"You're a {reader_type.lower()} exploring {top_genres[0][0] if top_genres else 'various'} genres.",
            'vibe_data_hash': hash(str(top_genres + top_authors)),
        }
        
        return dna

