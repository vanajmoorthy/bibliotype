"""Rec cards must only name other readers who are public at render time, by their current username."""

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse


def _rec(book_id, title, source_user, shared_public):
    source = {
        "type": "similar_user",
        "username": source_user.username,
        "user_id": source_user.id,
        "similarity_score": 0.5,
        "match_quality": "Kindred reader",
        "shared_books": 12,
    }
    return {
        "book_id": book_id,
        "book": {"id": book_id, "title": title, "author": {"name": "Some Author"}, "average_rating": 4.0},
        "confidence": 0.5,
        "confidence_pct": 50,
        "sources": [source],
        "primary_source_user": dict(source, badge_class="bg-badge-4"),
        "explanation_components": {"shared": "You share 12 books in common", "shared_public": shared_public},
    }


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
class RecSourcePrivacyTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.alice = User.objects.create_user(username="alicezq", email="alicezq@test.com", password="pw12345!")
        self.alice.userprofile.is_public = False
        self.alice.userprofile.save()

        self.carol = User.objects.create_user(username="oldcarolzq", email="carolzq@test.com", password="pw12345!")
        self.carol.userprofile.is_public = True
        self.carol.userprofile.save()

        self.bob = User.objects.create_user(username="bobzq", email="bobzq@test.com", password="pw12345!")
        profile = self.bob.userprofile
        profile.is_public = True
        profile.dna_data = {"reader_type": "Test Reader"}
        # Usernames snapshotted at generation time; carol has since renamed and alice gone private.
        profile.recommendations_data = [
            _rec(1, "Private Pick", self.alice, "bobzq and alicezq share 12 books in common"),
            _rec(2, "Public Pick", self.carol, "bobzq and oldcarolzq share 12 books in common"),
        ]
        profile.recommendations_meta = {
            "max_similarity_pct": 50,
            "similar_users_count": 2,
            "closest_match_username": "alicezq",
        }
        profile.save()

        self.carol.username = "carolzq"
        self.carol.save()

    def _assert_names_resolved(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Private Pick")
        self.assertNotContains(response, "alicezq")
        self.assertNotContains(response, "oldcarolzq")
        self.assertContains(response, 'href="/u/carolzq/"')
        self.assertContains(response, "a private reader")

    def test_dashboard_hides_private_source_reader(self):
        self.client.login(username="bobzq", password="pw12345!")
        self._assert_names_resolved(self.client.get(reverse("core:display_dna")))

    def test_public_profile_hides_private_source_reader(self):
        response = self.client.get(reverse("core:public_profile", kwargs={"username": "bobzq"}))
        self._assert_names_resolved(response)
        self.assertContains(response, "bobzq and this reader share 12 books in common")

    def test_resolves_all_source_names_in_one_query(self):
        from core.views._helpers import _resolve_rec_source_names

        recs = self.bob.userprofile.recommendations_data
        with self.assertNumQueries(1):
            meta = _resolve_rec_source_names(recs, self.bob.userprofile.recommendations_meta, owner_name="bobzq")
        self.assertEqual([r["primary_source_user"]["username"] for r in recs], [None, "carolzq"])
        self.assertIsNone(meta["closest_match_username"])
