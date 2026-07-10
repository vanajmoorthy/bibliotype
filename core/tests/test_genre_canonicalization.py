"""Tests for the expanded canonical genre set and shared fiction/nonfiction classification.

Covers the genre-accuracy work: 8 new canonical genres, ambiguous sub-splits
with backward-compat aliases, EXCLUDED_GENRES regression guards, the
context-dependent classifier in core/services/genre_classification.py, and the
inline-enrichment wall-clock budget.
"""

import threading
from itertools import count
from unittest.mock import patch

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
    parse_shelf_signals,
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


class ShelfSignalTiebreakerTests(TestCase):
    """The plan's 6-case shelf matrix: shelf signals never override clear API genres."""

    def test_clear_nonfiction_api_signal_beats_fiction_shelf(self):
        self.assertEqual(classify_genres({"history", "biography"}, shelf_fiction=True), "nonfiction")

    def test_clear_fiction_api_signal_beats_nonfiction_shelf(self):
        self.assertEqual(classify_genres({"fantasy"}, shelf_nonfiction=True), "fiction")

    def test_no_api_signal_nonfiction_shelf_wins(self):
        self.assertEqual(classify_genres(set(), shelf_nonfiction=True), "nonfiction")

    def test_no_api_signal_fiction_shelf_wins(self):
        self.assertEqual(classify_genres(set(), shelf_fiction=True), "fiction")

    def test_nonfiction_shelf_disambiguates_ambiguous_only_genres(self):
        self.assertEqual(classify_genres({"classic fiction"}, shelf_nonfiction=True), "nonfiction")

    def test_no_api_signal_no_shelf_is_defaulted(self):
        self.assertIsNone(classify_genres(set()))

    def test_shelf_genres_supplement_empty_api_genres(self):
        self.assertEqual(classify_genres(set(), shelf_genres=frozenset({"history"})), "nonfiction")
        self.assertEqual(classify_genres(set(), shelf_genres=frozenset({"fantasy"})), "fiction")


class ParseShelfSignalsTests(TestCase):
    """Goodreads Bookshelves parsing: comma-split, special-cased fiction/nonfiction."""

    def test_fiction_shelf_sets_fiction_signal(self):
        self.assertEqual(parse_shelf_signals("read, fiction, favorites"), (True, False, frozenset()))

    def test_nonfiction_spellings_set_nonfiction_signal(self):
        for spelling in ("nonfiction", "non-fiction"):
            shelf_fiction, shelf_nonfiction, shelf_genres = parse_shelf_signals(f"read, {spelling}")
            self.assertFalse(shelf_fiction)
            self.assertTrue(shelf_nonfiction)
            self.assertEqual(shelf_genres, frozenset())

    def test_genre_shelves_canonicalize(self):
        shelf_fiction, shelf_nonfiction, shelf_genres = parse_shelf_signals("read, fantasy, memoirs")
        self.assertFalse(shelf_fiction)
        self.assertFalse(shelf_nonfiction)
        self.assertEqual(shelf_genres, frozenset({"fantasy", "memoir"}))

    def test_non_genre_shelves_are_ignored(self):
        self.assertEqual(parse_shelf_signals("read, to-read, owned, favorites"), (False, False, frozenset()))

    def test_empty_and_missing_values(self):
        self.assertEqual(parse_shelf_signals(""), (False, False, frozenset()))
        self.assertEqual(parse_shelf_signals(None), (False, False, frozenset()))

    def test_case_and_whitespace_insensitive(self):
        shelf_fiction, shelf_nonfiction, _ = parse_shelf_signals("  Fiction ,  NONFICTION  ")
        self.assertTrue(shelf_fiction)
        self.assertTrue(shelf_nonfiction)


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

    def test_shelf_signals_break_ties_without_overriding_api(self):
        genre_sets = [
            {"history", "biography"},  # nonfiction — fiction shelf can't override
            {"fantasy"},  # fiction — nonfiction shelf can't override
            set(),  # nonfiction via shelf tiebreaker
            set(),  # fiction via shelf tiebreaker
            {"classic fiction"},  # nonfiction — shelf disambiguates ambiguous-only
            set(),  # defaulted — no shelf signal
        ]
        shelf_signals = [
            (True, False, frozenset()),
            (False, True, frozenset()),
            (False, True, frozenset()),
            (True, False, frozenset()),
            (False, True, frozenset()),
            (False, False, frozenset()),
        ]
        fiction, nonfiction, defaulted = count_fiction_nonfiction(genre_sets, shelf_signals)
        self.assertEqual((fiction, nonfiction, defaulted), (2, 3, 1))


class EnrichmentBudgetTests(TestCase):
    """_EnrichmentBudget: lazy start, exhaustion, exactly-once start under threads."""

    def test_creation_does_not_start_the_clock(self):
        from core.services.dna.enrichment_budget import _EnrichmentBudget

        with patch("core.services.dna.enrichment_budget.time.monotonic") as mock_monotonic:
            budget = _EnrichmentBudget()

        mock_monotonic.assert_not_called()
        self.assertIsNone(budget._started_at)

    def test_first_call_starts_clock_and_returns_true(self):
        from core.services.dna.enrichment_budget import _EnrichmentBudget

        budget = _EnrichmentBudget(max_seconds=90)
        with patch("core.services.dna.enrichment_budget.time.monotonic", return_value=1000.0):
            self.assertTrue(budget.has_remaining())
        self.assertEqual(budget._started_at, 1000.0)

    def test_exhaustion_after_max_seconds(self):
        from core.services.dna.enrichment_budget import _EnrichmentBudget

        budget = _EnrichmentBudget(max_seconds=90)
        clock = iter([1000.0, 1000.0 + 89.0, 1000.0 + 91.0])
        with patch("core.services.dna.enrichment_budget.time.monotonic", side_effect=lambda: next(clock)):
            self.assertTrue(budget.has_remaining())  # starts the clock at t=1000
            self.assertTrue(budget.has_remaining())  # 89s elapsed — still within budget
            self.assertFalse(budget.has_remaining())  # 91s elapsed — exhausted

    def test_default_budget_is_90_seconds(self):
        from core.services.dna.enrichment_budget import INLINE_ENRICHMENT_BUDGET_SECONDS, _EnrichmentBudget

        self.assertEqual(INLINE_ENRICHMENT_BUDGET_SECONDS, 90)
        self.assertEqual(_EnrichmentBudget()._max, 90)

    def test_thread_safety_clock_starts_exactly_once(self):
        """8 racing workers: exactly one call may take the start path (returns True
        directly); every other concurrent call must observe the already-started
        clock. The fake clock returns 0.0 for the very first monotonic call (the
        start, made under the lock) and a huge value afterwards — so if a second
        thread also 'started' the clock, later calls would wrongly return True."""
        from core.services.dna.enrichment_budget import _EnrichmentBudget

        budget = _EnrichmentBudget(max_seconds=90)
        calls = count()

        def fake_monotonic():
            return 0.0 if next(calls) == 0 else 10_000.0

        results = []
        results_lock = threading.Lock()
        barrier = threading.Barrier(8)

        def worker():
            barrier.wait()
            result = budget.has_remaining()
            with results_lock:
                results.append(result)

        with patch("core.services.dna.enrichment_budget.time.monotonic", side_effect=fake_monotonic):
            threads = [threading.Thread(target=worker) for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            self.assertEqual(results.count(True), 1, "clock must start exactly once across racing threads")
            self.assertEqual(budget._started_at, 0.0)
            self.assertFalse(budget.has_remaining())
