"""Tests for seed-user handles and the migration that renames existing seed users."""

import importlib

from django.apps import apps
from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase

from core.seed_names import MAX_USERNAME_LENGTH, seed_identity

rename_migration = importlib.import_module("core.migrations.0031_rename_seed_users")


class SeedIdentityTests(SimpleTestCase):
    def test_deterministic_and_signup_shaped(self):
        first_a, handle_a = seed_identity("seed_001__x@seed.bibliotype.invalid", lambda name: False)
        first_b, handle_b = seed_identity("seed_001__x@seed.bibliotype.invalid", lambda name: False)
        self.assertEqual((first_a, handle_a), (first_b, handle_b))
        self.assertLessEqual(len(handle_a), MAX_USERNAME_LENGTH)
        self.assertEqual(handle_a, handle_a.lower())
        self.assertTrue(handle_a.startswith(first_a.lower()))

    def test_skips_taken_handles(self):
        key = "seed_002__y@seed.bibliotype.invalid"
        _, first_choice = seed_identity(key, lambda name: False)
        _, second_choice = seed_identity(key, lambda name: name == first_choice)
        self.assertNotEqual(first_choice, second_choice)

    def test_corpus_sized_pool_gets_unique_handles(self):
        taken = set()
        for i in range(250):
            _, handle = seed_identity(f"seed_{i:03d}__u@seed.bibliotype.invalid", taken.__contains__)
            taken.add(handle)
        self.assertEqual(len(taken), 250)


class RenameSeedUsersMigrationTests(TestCase):
    def test_renames_seed_users_and_patches_stored_recommendations(self):
        seed = User.objects.create_user(
            username="seed_012__someone_repo", email="seed_012__someone_repo@seed.bibliotype.invalid"
        )
        organic = User.objects.create_user(username="seed_lookalike", email="real@person.com", password="x")
        viewer = User.objects.create_user(username="viewer", email="viewer@example.com", password="x")

        source = {"type": "similar_user", "user_id": seed.id, "username": seed.username, "similarity_score": 0.34}
        viewer.userprofile.recommendations_data = [{"sources": [source], "primary_source_user": dict(source)}]
        viewer.userprofile.recommendations_meta = {"closest_match_username": seed.username}
        viewer.userprofile.save()

        rename_migration.rename_seed_users(apps, None)

        seed.refresh_from_db()
        organic.refresh_from_db()
        viewer.userprofile.refresh_from_db()
        self.assertFalse(seed.username.startswith("seed_"))
        self.assertTrue(seed.first_name)
        self.assertEqual(organic.username, "seed_lookalike")

        rec = viewer.userprofile.recommendations_data[0]
        self.assertEqual(rec["sources"][0]["username"], seed.username)
        self.assertEqual(rec["primary_source_user"]["username"], seed.username)
        self.assertEqual(viewer.userprofile.recommendations_meta["closest_match_username"], seed.username)

        # Re-running is a no-op once seed users have handles.
        rename_migration.rename_seed_users(apps, None)
        self.assertEqual(User.objects.get(pk=seed.pk).username, seed.username)
