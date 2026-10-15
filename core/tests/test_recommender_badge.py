"""A recommender who cleared MIN_SIMILARITY is never badged "Opposite tastes" on a rec card."""

from django.contrib.auth.models import User
from django.test import TestCase

from core.models import Author, Book, UserBook
from core.services.recommendation_service import MIN_SIMILARITY, _collect_candidates_from_similar_users
from core.services.user_similarity_service import get_match_quality_label


class RecommenderBadgeTests(TestCase):
    def test_low_similarity_recommender_badged_some_overlap(self):
        recommender = User.objects.create_user(username="weak_match", password="x")
        book = Book.objects.create(title="Some Book", author=Author.objects.create(name="Some Author"))
        UserBook.objects.create(user=recommender, book=book, user_rating=5)

        candidates = {}
        _collect_candidates_from_similar_users(
            [(recommender, {"similarity_score": MIN_SIMILARITY + 0.02})], set(), candidates
        )

        self.assertEqual(candidates[book.id]["sources"][0]["match_quality"], "Some overlap")
        # The compare page keeps the honest label for the same score.
        self.assertEqual(get_match_quality_label(MIN_SIMILARITY + 0.02), "Opposite tastes")
