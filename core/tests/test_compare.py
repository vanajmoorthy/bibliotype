"""
Tests for the compare page: pairwise similarity between public readers.
"""

import itertools
import re

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from core.models import Author, Book, UserBook
from core.services.user_similarity_service import calculate_user_similarity, compare_readers
from core.views.compare import GRAPH_VIEWBOX, _build_graph


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "compare-tests",
        }
    },
)
class CompareBaseTestCase(TestCase):
    def setUp(self):
        self.client = Client()

        self.alice = self._make_user("alice", is_public=True, dna=True)
        self.bob = self._make_user("bob", is_public=True, dna=True)
        self.carol = self._make_user("carol", is_public=True, dna=True)
        self.dave = self._make_user("dave", is_public=False, dna=True)
        self.nodna = self._make_user("nodna", is_public=True, dna=False)
        self.emptylib = self._make_user("emptylib", is_public=True, dna=True)  # DNA but zero UserBooks
        # Admin-created users bypass the forms' lowercasing — must still resolve.
        self.mixed = self._make_user("MixedCase", is_public=True, dna=True)

        author = Author.objects.create(name="Test Author")
        other_author = Author.objects.create(name="Other Author")
        books = [Book.objects.create(title=f"Book {i}", author=author, publish_year=1990 + i) for i in range(8)]
        solo_books = [
            Book.objects.create(title=f"Solo {i}", author=other_author, publish_year=2000 + i) for i in range(4)
        ]

        # alice and bob share most of a library with agreeing ratings;
        # carol reads mostly her own books.
        for i, book in enumerate(books):
            UserBook.objects.create(user=self.alice, book=book, user_rating=(i % 5) + 1)
            UserBook.objects.create(user=self.bob, book=book, user_rating=(i % 5) + 1)
        for i, book in enumerate(solo_books):
            UserBook.objects.create(user=self.carol, book=book, user_rating=4)
        UserBook.objects.create(user=self.carol, book=books[0], user_rating=2)
        for book in books[:3]:
            UserBook.objects.create(user=self.mixed, book=book, user_rating=3)

    def _make_user(self, username, is_public, dna):
        user = User.objects.create_user(username=username, email=f"{username}@test.com", password="testpass123")
        user.userprofile.is_public = is_public
        user.userprofile.dna_data = {"reader_type": "Test Reader"} if dna else None
        user.userprofile.save()
        return user


class CompareServiceTests(CompareBaseTestCase):
    def test_pair_score_matches_one_to_one_function(self):
        result = compare_readers([self.alice, self.bob])
        expected = calculate_user_similarity(self.alice, self.bob)
        self.assertEqual(len(result["pairs"]), 1)
        self.assertAlmostEqual(result["pairs"][0]["score"], expected["similarity_score"])

    def test_order_does_not_change_result(self):
        forward = compare_readers([self.alice, self.bob])
        # Same group, either order, hits the same cache key — but also verify
        # the underlying computation is symmetric with a cold cache.
        backward = compare_readers([self.bob, self.alice])
        self.assertAlmostEqual(forward["pairs"][0]["score"], backward["pairs"][0]["score"])
        sym1 = calculate_user_similarity(self.alice, self.bob)
        sym2 = calculate_user_similarity(self.bob, self.alice)
        self.assertAlmostEqual(sym1["similarity_score"], sym2["similarity_score"])

    def test_three_users_yield_three_pairs(self):
        result = compare_readers([self.alice, self.bob, self.carol])
        self.assertEqual(len(result["pairs"]), 3)
        self.assertEqual(result["best_pair"]["user_ids"], sorted([self.alice.id, self.bob.id]))
        mean = sum(p["score"] for p in result["pairs"]) / 3
        self.assertAlmostEqual(result["mean_score"], mean)

    def test_fewer_than_two_distinct_users_raises(self):
        with self.assertRaises(ValueError):
            compare_readers([self.alice])
        with self.assertRaises(ValueError):
            compare_readers([self.alice, self.alice])  # duplicates collapse

    def test_empty_library_user_raises(self):
        with self.assertRaises(ValueError):
            compare_readers([self.alice, self.emptylib])

    def test_second_call_is_cached(self):
        compare_readers([self.alice, self.bob])
        with self.assertNumQueries(0):
            cached = compare_readers([self.alice, self.bob])
        self.assertEqual(len(cached["pairs"]), 1)


class CompareViewTests(CompareBaseTestCase):
    def _get(self, usernames):
        return self.client.get(reverse("core:compare", kwargs={"usernames": usernames}))

    def test_two_public_users_render_for_logged_out_visitor(self):
        response = self._get("alice,bob")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "alice")
        self.assertContains(response, "bob")
        self.assertContains(response, "%")

    def test_noindex_meta_present(self):
        response = self._get("alice,bob")
        self.assertContains(response, "noindex")

    def test_unknown_username_is_generic_404(self):
        response = self._get("alice,ghost")
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "Profile Not Available", status_code=404)

    def test_private_user_is_indistinguishable_from_unknown(self):
        private = self._get("alice,dave")
        unknown = self._get("alice,ghost")
        self.assertEqual(private.status_code, 404)
        self.assertEqual(private.content, unknown.content)
        self.assertNotContains(private, "dave", status_code=404)

    def test_owner_of_private_profile_also_gets_404(self):
        self.client.login(username="dave", password="testpass123")
        response = self._get("alice,dave")
        self.assertEqual(response.status_code, 404)

    def test_user_without_dna_is_404(self):
        response = self._get("alice,nodna")
        self.assertEqual(response.status_code, 404)

    def test_user_with_empty_library_is_404(self):
        response = self._get("alice,emptylib")
        self.assertEqual(response.status_code, 404)

    def test_uppercase_stored_username_is_reachable(self):
        response = self._get("alice,mixedcase")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "MixedCase")

    def test_single_username_is_404(self):
        response = self._get("alice")
        self.assertEqual(response.status_code, 404)

    def test_duplicate_usernames_collapse_and_404(self):
        response = self._get("alice,alice")
        self.assertEqual(response.status_code, 404)

    def test_three_users_render_graph_and_callouts(self):
        response = self._get("alice,bob,carol")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "The Web of Taste")
        self.assertContains(response, "Closest Pair")
        self.assertContains(response, "alice + bob")  # tightest fixture pair
        self.assertContains(response, "Odd One Out")
        self.assertContains(response, "carol")  # reads her own books
        self.assertContains(response, "Similarity graph of 3 readers")
        self.assertContains(response, "Group Score")

    def test_six_users_is_the_cap(self):
        # Cap is enforced before any lookups, so the usernames need not exist.
        response = self._get("a,b,c,d,e,f,g")
        self.assertEqual(response.status_code, 404)

    def test_two_users_have_no_graph(self):
        response = self._get("alice,bob")
        self.assertNotContains(response, "The Web of Taste")
        self.assertNotContains(response, "Odd One Out")

    def _visible_text(self, response):
        text = re.sub(r"<[^>]+>", "", response.content.decode())
        return re.sub(r"\s+", " ", text)

    def test_name_separators_have_no_stray_spaces(self):
        two = self._visible_text(self._get("alice,bob"))
        self.assertIn("alice vs bob:", two)
        three = self._visible_text(self._get("alice,bob,carol"))
        self.assertIn("alice, bob vs carol:", three)
        for text in (two, three):
            self.assertNotIn(" ,", text)
            self.assertNotIn(" :", text)

    def test_every_rejection_is_the_same_generic_404(self):
        unknown = self._get("alice,ghost")
        for usernames in ("alice,dave", "alice,alice", "alice", "a,b,c,d,e,f,g", "alice,nodna", "alice,emptylib"):
            with self.subTest(usernames=usernames):
                response = self._get(usernames)
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.content, unknown.content)

    def test_unsorted_url_redirects_to_canonical(self):
        response = self._get("bob,alice")
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response.url, reverse("core:compare", kwargs={"usernames": "alice,bob"}))

    def test_uppercase_url_redirects_to_canonical(self):
        response = self._get("Alice,bob")
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response.url, reverse("core:compare", kwargs={"usernames": "alice,bob"}))


class CompareGraphGeometryTests(TestCase):
    """Node boxes and % chips must stay inside the viewBox and never overlap."""

    @staticmethod
    def _overlap(a, b):
        return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]

    def test_layout_is_clear_for_three_to_six_readers(self):
        width, height = GRAPH_VIEWBOX
        for n in range(3, 7):
            for name_len in (1, 8, 15):  # 15 is the signup form's max
                with self.subTest(n=n, name_len=name_len):
                    names = [chr(ord("a") + i) * name_len for i in range(n)]
                    pairs = [
                        {"index": k, "names": list(pair), "pct": 50}
                        for k, pair in enumerate(itertools.combinations(names, 2))
                    ]
                    graph = _build_graph(names, pairs)
                    self.assertEqual(len(graph["edges"]), n * (n - 1) // 2)
                    # Footprints include the 4px offset shadow.
                    nodes = [
                        (d["rect_x"], d["rect_y"], d["rect_x"] + d["w"] + 4, d["rect_y"] + 50) for d in graph["nodes"]
                    ]
                    chips = [(e["chip_x"], e["chip_y"], e["chip_x"] + 56, e["chip_y"] + 34) for e in graph["edges"]]
                    for box in nodes + chips:
                        self.assertTrue(0 <= box[0] and 0 <= box[1] and box[2] <= width and box[3] <= height, box)
                    for a, b in itertools.combinations(nodes + chips, 2):
                        self.assertFalse(self._overlap(a, b), (a, b))
