"""Tests for the reader-uniqueness badge + similar-readers stat rework.

Covers the new pure helpers (compute_uniqueness, _friendly_floor,
_get_recommendation_pool_display) and the waffle-gated rendering of the
recommendations grid partial.
"""

from unittest.mock import patch

from django.contrib.auth.models import User
from django.template import Context, Template
from django.test import TestCase, override_settings
from waffle.testutils import override_switch

from core.dna_constants import UNIQUENESS_WEAK_MATCH_THRESHOLD, compute_uniqueness
from core.views._helpers import (
    RECS_POOL_MIN_DISPLAY,
    _friendly_floor,
    _get_recommendation_pool_display,
)

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


class ComputeUniquenessTests(TestCase):
    def test_zero_matches_is_one_of_a_kind(self):
        label, color = compute_uniqueness(0, 0.0)
        self.assertEqual(label, "One of a kind")
        self.assertEqual(color, "bg-brand-pink")

    def test_weak_best_match_is_pretty_unique(self):
        # A match exists but below the weak threshold
        label, color = compute_uniqueness(3, UNIQUENESS_WEAK_MATCH_THRESHOLD - 0.01)
        self.assertEqual(label, "Pretty unique")
        self.assertEqual(color, "bg-brand-cyan")

    def test_strong_match_gets_no_badge(self):
        self.assertIsNone(compute_uniqueness(5, UNIQUENESS_WEAK_MATCH_THRESHOLD))
        self.assertIsNone(compute_uniqueness(5, 0.92))

    def test_zero_count_takes_priority_over_similarity(self):
        # Defensive: 0 users should be "One of a kind" even if a stray score sneaks in
        label, _ = compute_uniqueness(0, 0.99)
        self.assertEqual(label, "One of a kind")


class FriendlyFloorTests(TestCase):
    def test_rounds_down_to_ten_under_500(self):
        self.assertEqual(_friendly_floor(237), 230)
        self.assertEqual(_friendly_floor(100), 100)
        self.assertEqual(_friendly_floor(499), 490)

    def test_rounds_down_to_fifty_under_2000(self):
        self.assertEqual(_friendly_floor(500), 500)
        self.assertEqual(_friendly_floor(1234), 1200)
        self.assertEqual(_friendly_floor(1999), 1950)

    def test_rounds_down_to_hundred_at_2000_and_above(self):
        self.assertEqual(_friendly_floor(2000), 2000)
        self.assertEqual(_friendly_floor(5678), 5600)


@override_settings(CACHES=LOCMEM)
class PoolDisplayTests(TestCase):
    @patch("core.services.user_similarity_service.get_recommendation_pool_size")
    def test_hidden_below_minimum(self, mock_size):
        mock_size.return_value = RECS_POOL_MIN_DISPLAY - 1
        self.assertIsNone(_get_recommendation_pool_display())

    @patch("core.services.user_similarity_service.get_recommendation_pool_size")
    def test_shown_and_floored_at_minimum(self, mock_size):
        mock_size.return_value = 237
        self.assertEqual(_get_recommendation_pool_display(), 230)


@override_settings(CACHES=LOCMEM)
class PoolSizeQueryTests(TestCase):
    def test_counts_only_eligible_profiles(self):
        from core.services.user_similarity_service import get_recommendation_pool_size

        eligible = User.objects.create_user(username="eligible", email="e@e.com", password="x")
        eligible.userprofile.dna_data = {"foo": "bar"}
        eligible.userprofile.visible_in_recommendations = True
        eligible.userprofile.save()

        # No DNA -> excluded
        User.objects.create_user(username="nodna", email="n@n.com", password="x")

        # Has DNA but hidden -> excluded
        hidden = User.objects.create_user(username="hidden", email="h@h.com", password="x")
        hidden.userprofile.dna_data = {"foo": "bar"}
        hidden.userprofile.visible_in_recommendations = False
        hidden.userprofile.save()

        self.assertEqual(get_recommendation_pool_size(), 1)


def _render_grid(meta, pool_display=None, pronoun_pos="your", pronoun_sub="you"):
    template = Template("{% include 'core/partials/dna/recommendations_grid.html' %}")
    raw = template.render(
        Context(
            {
                "recommendations": [],
                "recommendations_meta": meta,
                "recommendations_pool_display": pool_display,
                "pronoun_pos": pronoun_pos,
                "pronoun_sub": pronoun_sub,
            }
        )
    )
    # Collapse template indentation/newlines so assertions match the visible copy
    return " ".join(raw.split())


class RecommendationsGridRenderTests(TestCase):
    def test_closest_match_headline(self):
        html = _render_grid({"max_similarity_pct": 78, "similar_users_count": 15})
        self.assertIn("closest match", html)
        self.assertIn("78% similar taste", html)
        self.assertIn("14 other readers", html)
        self.assertNotIn("overlap", html)

    def test_single_match_omits_others_clause(self):
        html = _render_grid({"max_similarity_pct": 60, "similar_users_count": 1})
        self.assertIn("60% similar taste", html)
        self.assertNotIn("other reader", html)

    def test_pool_display_included_when_present(self):
        html = _render_grid(
            {"max_similarity_pct": 78, "similar_users_count": 15}, pool_display=230
        )
        self.assertIn("over 230 readers on Bibliotype", html)

    def test_stale_meta_falls_back_to_count_copy(self):
        # Old meta with no max_similarity_pct
        html = _render_grid({"similar_users_count": 12})
        self.assertIn("12 readers with taste similar", html)
        self.assertNotIn("overlap", html)

    @override_switch("uniqueness-badge", active=True)
    def test_badge_rendered_when_switch_on(self):
        html = _render_grid(
            {
                "max_similarity_pct": 30,
                "similar_users_count": 3,
                "uniqueness_label": "Pretty unique",
                "uniqueness_color": "bg-brand-cyan",
            }
        )
        self.assertIn("Pretty unique", html)
        self.assertIn("bg-brand-cyan", html)

    @override_switch("uniqueness-badge", active=False)
    def test_badge_hidden_when_switch_off(self):
        html = _render_grid(
            {
                "max_similarity_pct": 30,
                "similar_users_count": 3,
                "uniqueness_label": "Pretty unique",
                "uniqueness_color": "bg-brand-cyan",
            }
        )
        self.assertNotIn("Pretty unique", html)

    @override_switch("uniqueness-badge", active=True)
    def test_one_of_a_kind_zero_match_branch(self):
        # 0 similar users -> max_similarity_pct is 0/falsy, must still show unique copy + badge
        html = _render_grid(
            {
                "max_similarity_pct": 0,
                "similar_users_count": 0,
                "uniqueness_label": "One of a kind",
                "uniqueness_color": "bg-brand-pink",
            }
        )
        self.assertIn("Hardly anyone reads like you", html)
        self.assertIn("One of a kind", html)

    def test_public_profile_pronouns(self):
        html = _render_grid(
            {"max_similarity_pct": 55, "similar_users_count": 2},
            pronoun_pos="their",
        )
        self.assertIn("Their closest match", html)


@override_settings(CACHES=LOCMEM)
class TaskMetaTests(TestCase):
    """The recommendations task must persist max_similarity_pct + uniqueness fields."""

    def _make_book(self):
        from core.models import Author, Book

        author = Author.objects.create(name="Test Author", normalized_name="author, test")
        return Book.objects.create(title="Test Book", author=author, average_rating=4.0)

    def _rec_with_sources(self, book, sources):
        return {
            "book": book,
            "confidence": 0.5,
            "score": 1.0,
            "sources": sources,
            "explanation_components": {},
        }

    @patch("core.services.recommendation_service.get_recommendations_for_user")
    def test_meta_captures_max_similarity_and_no_badge_for_strong_match(self, mock_recs):
        from core.tasks import generate_recommendations_task

        user = User.objects.create_user(username="reader", email="r@r.com", password="x")
        user.userprofile.dna_data = {"foo": "bar"}
        user.userprofile.save()

        book = self._make_book()
        mock_recs.return_value = [
            self._rec_with_sources(
                book,
                [
                    {"type": "similar_user", "user_id": 2, "similarity_score": 0.8},
                    {"type": "similar_user", "user_id": 3, "similarity_score": 0.3},
                ],
            )
        ]

        generate_recommendations_task(user.id)
        user.userprofile.refresh_from_db()
        meta = user.userprofile.recommendations_meta

        self.assertEqual(meta["similar_users_count"], 2)
        self.assertEqual(meta["max_similarity_pct"], 80)
        self.assertEqual(meta["min_overlap_pct"], 30)
        # Strong best match -> no badge fields
        self.assertNotIn("uniqueness_label", meta)

    @patch("core.services.recommendation_service.get_recommendations_for_user")
    def test_meta_sets_pretty_unique_for_weak_match(self, mock_recs):
        from core.tasks import generate_recommendations_task

        user = User.objects.create_user(username="weakreader", email="w@w.com", password="x")
        user.userprofile.dna_data = {"foo": "bar"}
        user.userprofile.save()

        book = self._make_book()
        mock_recs.return_value = [
            self._rec_with_sources(
                book, [{"type": "similar_user", "user_id": 2, "similarity_score": 0.25}]
            )
        ]

        generate_recommendations_task(user.id)
        user.userprofile.refresh_from_db()
        meta = user.userprofile.recommendations_meta

        self.assertEqual(meta["max_similarity_pct"], 25)
        self.assertEqual(meta["uniqueness_label"], "Pretty unique")
        self.assertEqual(meta["uniqueness_color"], "bg-brand-cyan")
