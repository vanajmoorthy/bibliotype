"""Tests for the expanded canonical genre set and shared fiction/nonfiction classification.

Covers the genre-accuracy work: 8 new canonical genres, ambiguous sub-splits
with backward-compat aliases, EXCLUDED_GENRES regression guards, and the
context-dependent classifier in core/services/genre_classification.py.
"""

from django.test import TestCase

from core.dna_constants import (
    AMBIGUOUS_FICTION_GENRES,
    AMBIGUOUS_TO_NONFICTION,
    CANONICAL_GENRE_MAP,
    EXCLUDED_GENRES,
    FICTION_GENRES,
    GENRE_ALIASES,
    GENRE_PRIORITY,
    NONFICTION_GENRES,
)
from core.services.genre_classification import (
    canonicalize_genre_names,
    classify_genres,
    count_fiction_nonfiction,
)


class NewCanonicalGenreMappingTests(TestCase):
    """Each new canonical genre must map from its representative aliases."""

    def _assert_maps(self, expected_canonical, aliases):
        for alias in aliases:
            self.assertEqual(
                CANONICAL_GENRE_MAP.get(alias),
                expected_canonical,
                f"Alias '{alias}' should map to '{expected_canonical}', got '{CANONICAL_GENRE_MAP.get(alias)}'",
            )

    def test_memoir(self):
        self._assert_maps("memoir", ["memoir", "memoirs", "personal narratives", "autobiographies"])

    def test_true_crime(self):
        self._assert_maps("true crime", ["true crime", "crime nonfiction", "murder nonfiction"])

    def test_poetry(self):
        self._assert_maps("poetry", ["poetry", "poems", "verse", "collected poems"])

    def test_essays(self):
        self._assert_maps("essays", ["essays", "collected essays", "literary essays", "personal essays"])

    def test_literary_fiction(self):
        self._assert_maps("literary fiction", ["literary fiction", "contemporary literary fiction", "literary novels"])

    def test_dystopian(self):
        self._assert_maps("dystopian", ["dystopian", "dystopian fiction", "dystopia", "post-apocalyptic"])

    def test_mystery(self):
        self._assert_maps(
            "mystery",
            ["mystery", "mystery fiction", "detective and mystery stories", "whodunit", "cozy mystery", "detectives"],
        )

    def test_adventure(self):
        self._assert_maps(
            "adventure",
            ["adventure", "adventure fiction", "adventure stories", "adventure and adventurers"],
        )
        self._assert_maps("adventure", ["action and adventure"])

    def test_moved_aliases_left_their_old_genres(self):
        """Aliases moved to new genres must no longer sit under their old canonical genre."""
        self.assertNotIn("memoir", GENRE_ALIASES["biography"])
        self.assertNotIn("memoirs", GENRE_ALIASES["biography"])
        self.assertNotIn("mystery", GENRE_ALIASES["thriller"])
        self.assertNotIn("detective and mystery stories", GENRE_ALIASES["thriller"])
        self.assertNotIn("adventure fiction", GENRE_ALIASES["fantasy"])
        self.assertNotIn("adventure stories", GENRE_ALIASES["thriller"])
        self.assertNotIn("essays", GENRE_ALIASES["non-fiction"])

    def test_autobiographical_fiction_is_not_memoir(self):
        """'autobiographical fiction' is fiction and must NOT map to memoir."""
        self.assertNotEqual(CANONICAL_GENRE_MAP.get("autobiographical fiction"), "memoir")


class BackwardCompatAliasTests(TestCase):
    """Old canonical names must resolve to their fiction sub-categories — no data migration."""

    def test_classics_maps_to_classic_fiction(self):
        self.assertEqual(CANONICAL_GENRE_MAP["classics"], "classic fiction")

    def test_young_adult_maps_to_young_adult_fiction(self):
        self.assertEqual(CANONICAL_GENRE_MAP["young adult"], "young adult fiction")

    def test_childrens_literature_maps_to_childrens_fiction(self):
        self.assertEqual(CANONICAL_GENRE_MAP["children's literature"], "children's fiction")

    def test_old_names_are_no_longer_canonical(self):
        for old_name in ("classics", "young adult", "children's literature"):
            self.assertNotIn(old_name, GENRE_ALIASES)

    def test_ambiguous_sets_are_consistent(self):
        self.assertEqual(
            AMBIGUOUS_FICTION_GENRES, {"classic fiction", "young adult fiction", "children's fiction"}
        )
        self.assertEqual(set(AMBIGUOUS_TO_NONFICTION.keys()), AMBIGUOUS_FICTION_GENRES)
        for nonfiction_variant in AMBIGUOUS_TO_NONFICTION.values():
            self.assertIn(nonfiction_variant, NONFICTION_GENRES)


class ExcludedGenresRegressionTests(TestCase):
    """The exclusion check runs BEFORE alias matching — collisions silently kill genres."""

    def test_no_canonical_genre_is_excluded(self):
        collisions = {name for name in GENRE_ALIASES if name in EXCLUDED_GENRES}
        self.assertEqual(collisions, set(), f"Canonical genres blocked by EXCLUDED_GENRES: {collisions}")

    def test_no_alias_is_excluded(self):
        collisions = {alias for aliases in GENRE_ALIASES.values() for alias in aliases if alias in EXCLUDED_GENRES}
        self.assertEqual(collisions, set(), f"Aliases blocked by EXCLUDED_GENRES: {collisions}")

    def test_generic_terms_remain_excluded(self):
        """'literary' and 'fiction' must stay excluded to prevent API false positives."""
        self.assertIn("literary", EXCLUDED_GENRES)
        self.assertIn("fiction", EXCLUDED_GENRES)

    def test_genre_priority_covers_all_canonical_genres(self):
        """Nothing may sort to the 999 fallback in enrichment priority sorting."""
        self.assertEqual(set(GENRE_PRIORITY), set(GENRE_ALIASES.keys()))

    def test_fiction_nonfiction_sets_partition_canonical_genres(self):
        self.assertEqual(FICTION_GENRES | NONFICTION_GENRES, set(GENRE_ALIASES.keys()))
        self.assertEqual(FICTION_GENRES & NONFICTION_GENRES, set())


class ClassifyGenresMatrixTests(TestCase):
    """The plan's classification matrix for context-dependent resolution."""

    def test_ambiguous_plus_fiction_is_fiction(self):
        self.assertEqual(classify_genres({"classic fiction", "fantasy"}), "fiction")

    def test_ambiguous_plus_nonfiction_is_nonfiction(self):
        self.assertEqual(classify_genres({"classic fiction", "history"}), "nonfiction")

    def test_ambiguous_alone_defaults_to_fiction(self):
        self.assertEqual(classify_genres({"classic fiction"}), "fiction")

    def test_ambiguous_plus_both_signals_is_fiction(self):
        self.assertEqual(classify_genres({"classic fiction", "history", "fantasy"}), "fiction")

    def test_empty_set_is_none(self):
        self.assertIsNone(classify_genres(set()))

    def test_unmatched_genres_are_none(self):
        self.assertIsNone(classify_genres({"totally unknown genre"}))

    def test_pure_nonfiction(self):
        self.assertEqual(classify_genres({"history", "biography"}), "nonfiction")

    def test_all_ambiguous_variants_resolve_against_nonfiction(self):
        for ambiguous in AMBIGUOUS_FICTION_GENRES:
            self.assertEqual(classify_genres({ambiguous, "history"}), "nonfiction")
            self.assertEqual(classify_genres({ambiguous}), "fiction")

    def test_canonicalize_genre_names_resolves_aliases(self):
        self.assertEqual(canonicalize_genre_names(["classics", "poems"]), {"classic fiction", "poetry"})

    def test_db_row_with_old_classics_name_classifies_as_fiction(self):
        """A Genre DB row named 'classics' must still classify via the backward-compat alias."""
        self.assertEqual(classify_genres(canonicalize_genre_names(["classics"])), "fiction")


class CountFictionNonfictionTests(TestCase):
    """Counter independence: defaulted books are never added to fiction."""

    def test_counters_sum_to_total(self):
        genre_sets = [
            {"fantasy"},  # fiction
            {"history"},  # nonfiction
            {"classic fiction", "history"},  # nonfiction (context)
            {"classic fiction"},  # fiction (default)
            set(),  # defaulted
            {"unmatched genre"},  # defaulted
        ]
        fiction, nonfiction, defaulted = count_fiction_nonfiction(genre_sets)
        self.assertEqual((fiction, nonfiction, defaulted), (2, 2, 2))
        self.assertEqual(fiction + nonfiction + defaulted, len(genre_sets))

    def test_defaulted_not_counted_as_fiction(self):
        fiction, nonfiction, defaulted = count_fiction_nonfiction([set(), set(), set()])
        self.assertEqual(fiction, 0)
        self.assertEqual(nonfiction, 0)
        self.assertEqual(defaulted, 3)

    def test_empty_iterable(self):
        self.assertEqual(count_fiction_nonfiction([]), (0, 0, 0))
