"""Correctness tests for live-enrichment recompute + finalize.

Covers:
- M1: per-book shelf signals persisted at generation are used by the poll-time
  recompute, so the fiction/nonfiction split keeps generation quality; legacy
  profiles (no signals) are NOT degraded at finalize.
- M2: the reader-type explanation stays stable across recomputes when the
  winning type is unchanged.
- M3: finalize busts the 2s stats cache so it can't lock in stale numbers.
- L1: finalize skips its write when a newer generation superseded the request.
"""

from django.test import TestCase, override_settings

from core.cache_utils import safe_cache_delete
from core.services.dna.reader_type import recompute_reader_type_from_db
from core.views._helpers import _compute_enrichment_progress, _compute_enrichment_stats

from .test_live_enrichment import _add_book, _make_user

LOCMEM = override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})


def _clear_stats_cache(user):
    safe_cache_delete(f"enrichment_stats_{user.id}_rt")
    safe_cache_delete(f"enrichment_stats_{user.id}_nort")


# ---------------------------------------------------------------------------
# M1 — shelf signals persisted at generation drive the recompute split
# ---------------------------------------------------------------------------

@LOCMEM
class ShelfSignalRecomputeTests(TestCase):
    def setUp(self):
        self.user = _make_user("shelf_sig_user")
        self.profile = self.user.userprofile
        _clear_stats_cache(self.user)

    def test_recompute_without_signals_defaults_a_genreless_book(self):
        # A book with no API genres and no shelf signal can't be classified.
        _add_book(self.user, "No Genre", google_books_checked=True)
        stats = _compute_enrichment_stats(self.user, fresh=True)
        self.assertIsNone(stats["fiction_nonfiction_split"])

    def test_recompute_uses_persisted_shelf_signals(self):
        # Same genreless book, but a persisted shelf "nonfiction" signal classifies it.
        book = _add_book(self.user, "No Genre", google_books_checked=True)
        signals = {str(book.id): [False, True, []]}  # shelf_nonfiction = True
        stats = _compute_enrichment_stats(self.user, shelf_signals=signals, fresh=True)
        split = stats["fiction_nonfiction_split"]
        self.assertIsNotNone(split)
        self.assertEqual(split["nonfiction_count"], 1)
        self.assertEqual(split["fiction_count"], 0)

    def test_finalize_uses_shelf_signals_for_persisted_split(self):
        book = _add_book(self.user, "No Genre", google_books_checked=True)
        dna_data = {"shelf_signals": {str(book.id): [False, True, []]}, "user_stats": {}}
        self.profile.dna_data = dna_data
        self.profile.save()

        dna_copy = {"shelf_signals": {str(book.id): [False, True, []]}, "user_stats": {}}
        result = _compute_enrichment_progress(self.user, self.profile, dna_copy)
        self.assertFalse(result["pending"])

        self.profile.refresh_from_db()
        split = self.profile.dna_data["fiction_nonfiction_split"]
        self.assertEqual(split["nonfiction_count"], 1)
        self.assertEqual(split["fiction_count"], 0)

    def test_legacy_profile_split_not_degraded_on_finalize(self):
        # Legacy profile: no shelf_signals persisted. A shelf-less recompute of a
        # genreless book would wipe the split to None; the guard keeps the
        # generation-time value instead.
        _add_book(self.user, "No Genre", google_books_checked=True)
        original_split = {"fiction_count": 0, "nonfiction_count": 1, "defaulted_count": 0}
        self.profile.dna_data = {"fiction_nonfiction_split": dict(original_split), "user_stats": {}}
        self.profile.save()

        dna_copy = {"fiction_nonfiction_split": dict(original_split), "user_stats": {}}
        _compute_enrichment_progress(self.user, self.profile, dna_copy)

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.dna_data["fiction_nonfiction_split"], original_split)


# ---------------------------------------------------------------------------
# M2 — reader-type explanation stability across recomputes
# ---------------------------------------------------------------------------

@LOCMEM
class ReaderTypeExplanationStabilityTests(TestCase):
    def setUp(self):
        self.user = _make_user("rt_stable_user")
        _clear_stats_cache(self.user)
        for i in range(10):
            _add_book(self.user, f"Fantasy {i}", genres=["fantasy"])
        self.ctx = {"reread_count": 0, "books_per_year_avg": 0.0}

    def test_same_type_keeps_explanation(self):
        r1 = recompute_reader_type_from_db(self.user, self.ctx)
        r2 = recompute_reader_type_from_db(
            self.user,
            self.ctx,
            current_reader_type=r1["reader_type"],
            current_explanation=r1["reader_type_explanation"],
        )
        self.assertEqual(r2["reader_type"], r1["reader_type"])
        self.assertEqual(r2["reader_type_explanation"], r1["reader_type_explanation"])

    def test_type_change_rerolls_explanation(self):
        # A mismatched current type must not force-reuse a stale blurb.
        r = recompute_reader_type_from_db(
            self.user,
            self.ctx,
            current_reader_type="Some Other Type",
            current_explanation="STALE BLURB",
        )
        self.assertNotEqual(r["reader_type_explanation"], "STALE BLURB")


# ---------------------------------------------------------------------------
# M3 — finalize busts the 2s stats cache
# ---------------------------------------------------------------------------

@LOCMEM
class FinalizeBustsStatsCacheTests(TestCase):
    def setUp(self):
        self.user = _make_user("finalize_cache_user")
        self.profile = self.user.userprofile
        _clear_stats_cache(self.user)

    def test_finalize_recomputes_fresh_stats(self):
        _add_book(self.user, "One", page_count=100, google_books_checked=True)
        # Prime the cache with single-book stats (total_pages_read == 100).
        primed = _compute_enrichment_stats(self.user)
        self.assertEqual(primed["total_pages_read"], 100)

        # A second book lands within the 2s cache window.
        _add_book(self.user, "Two", page_count=300, google_books_checked=True)

        self.profile.dna_data = {"user_stats": {}}
        self.profile.save()
        dna_copy = {"user_stats": {}}
        _compute_enrichment_progress(self.user, self.profile, dna_copy)

        self.profile.refresh_from_db()
        # Finalize must reflect BOTH books, not the stale cached single-book total.
        self.assertEqual(self.profile.dna_data["user_stats"]["total_pages_read"], 400)


# ---------------------------------------------------------------------------
# L1 — finalize does not clobber a superseding re-upload
# ---------------------------------------------------------------------------

@LOCMEM
class FinalizeSupersedeGuardTests(TestCase):
    def setUp(self):
        self.user = _make_user("supersede_user")
        self.profile = self.user.userprofile
        _clear_stats_cache(self.user)
        _add_book(self.user, "Book", page_count=100, google_books_checked=True)

    def test_finalize_skips_when_vibe_hash_differs(self):
        # DB row is a newer generation (different vibe hash).
        self.profile.dna_data = {
            "vibe_data_hash": "NEW",
            "user_stats": {"total_pages_read": 999},
        }
        self.profile.save()

        # Caller holds a stale pre-lock snapshot from the old generation.
        stale_request_dna = {"vibe_data_hash": "OLD", "user_stats": {"total_pages_read": 100}}
        _compute_enrichment_progress(self.user, self.profile, stale_request_dna)

        self.profile.refresh_from_db()
        # DB must still hold the newer generation, not the stale snapshot.
        self.assertEqual(self.profile.dna_data["vibe_data_hash"], "NEW")
        self.assertEqual(self.profile.dna_data["user_stats"]["total_pages_read"], 999)
        self.assertNotIn("enrichment_finalized", self.profile.dna_data)
        # The caller's dict is refreshed to the locked (newer) data.
        self.assertEqual(stale_request_dna["vibe_data_hash"], "NEW")

    def test_finalize_skips_when_dna_task_pending(self):
        # A re-upload is in flight (pending_dna_task_id set on the row).
        self.profile.pending_dna_task_id = "task-123"
        self.profile.dna_data = {"vibe_data_hash": "SAME", "user_stats": {"total_pages_read": 777}}
        self.profile.save()

        request_dna = {"vibe_data_hash": "SAME", "user_stats": {"total_pages_read": 100}}
        _compute_enrichment_progress(self.user, self.profile, request_dna)

        self.profile.refresh_from_db()
        self.assertNotIn("enrichment_finalized", self.profile.dna_data)
        self.assertEqual(self.profile.dna_data["user_stats"]["total_pages_read"], 777)
