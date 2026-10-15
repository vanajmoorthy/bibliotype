import logging

from django.core.management.base import BaseCommand

from core.models import AggregateAnalytics, AnonymizedReadingProfile, AnonymousUserSession, UserProfile
from core.percentile_engine import update_analytics_from_stats

logger = logging.getLogger(__name__)


def _with_books_per_year(dna_data):
    """user_stats from a DNA dict, backfilling avg_books_per_year for DNA saved before it existed."""
    user_stats = dna_data.get("user_stats")
    if user_stats and "avg_books_per_year" not in user_stats:
        stats_by_year = dna_data.get("stats_by_year", [])
        if stats_by_year:
            total_books_with_dates = sum(y.get("count", 0) for y in stats_by_year)
            user_stats["avg_books_per_year"] = round(total_books_with_dates / len(stats_by_year), 1)
            user_stats["num_reading_years"] = len(stats_by_year)
    return user_stats


def _fingerprint(stats):
    # Profiles store no session key, so a claimed upload is only recognisable by matching stats.
    # shortcut: two different readers with identical stats collide and one is dropped; add a claim marker if that matters.
    return (stats.get("total_books_read"), stats.get("avg_book_length"), stats.get("avg_publish_year"))


class Command(BaseCommand):
    help = "Rebuilds AggregateAnalytics from UserProfiles, unclaimed anonymous sessions and anonymised profiles."

    def _log(self, msg):
        self.stdout.write(msg)
        logger.info(f"rebuild_analytics: {msg}")

    def handle(self, *args, **kwargs):
        self._log("Rebuilding aggregate analytics from scratch...")
        AggregateAnalytics.objects.all().delete()
        AggregateAnalytics.get_instance()

        profile_prints = set()
        profiles = skipped = 0
        for profile in UserProfile.objects.exclude(dna_data__isnull=True).iterator():
            user_stats = _with_books_per_year(profile.dna_data)
            if not user_stats:
                skipped += 1
                continue
            update_analytics_from_stats(user_stats)
            profile_prints.add(_fingerprint(user_stats))
            profiles += 1

        # Anonymised sessions are left out: they live on as AnonymizedReadingProfile rows (or were
        # dropped for having under 10 books), and the session row survives 30 days after that.
        sessions = 0
        for session in AnonymousUserSession.objects.filter(anonymized=False).iterator():
            user_stats = _with_books_per_year(session.dna_data or {})
            # Pre-#246 claims left the session behind with the same DNA the profile got
            if not user_stats or _fingerprint(user_stats) in profile_prints:
                skipped += 1
                continue
            update_analytics_from_stats(user_stats)
            sessions += 1

        anonymised = 0
        for anon in AnonymizedReadingProfile.objects.iterator():
            # None (missing) is skipped by update_analytics_from_stats, never counted as 0
            stats = {
                "total_books_read": anon.total_books_read,
                "avg_book_length": anon.avg_book_length,
                "avg_publish_year": anon.avg_publish_year,
            }
            # A pre-#246 claimed session that later expired was anonymised too
            if _fingerprint(stats) in profile_prints:
                skipped += 1
                continue
            update_analytics_from_stats(stats)
            anonymised += 1

        self._log(
            f"Rebuilt aggregate analytics from {profiles} profiles, {sessions} unclaimed anonymous sessions "
            f"and {anonymised} anonymised profiles ({skipped} skipped as empty or already counted)."
        )
