from types import SimpleNamespace
from unittest.mock import ANY, MagicMock, patch

from django.test import TestCase

from core.analytics import events
from core.analytics.middleware import PostHogExceptionMiddleware
from core.services.anonymization_service import _key_hash

RAW_KEY = "rawsessionkey1234567890abcdef"


def _anon_request():
    return SimpleNamespace(
        user=SimpleNamespace(is_authenticated=False, id=None),
        session=SimpleNamespace(session_key=RAW_KEY),
        path="/dashboard/",
        method="GET",
    )


@patch("core.analytics.posthog_client._initialize_posthog")
@patch("core.analytics.posthog_client.posthog")
class RawSessionKeyNeverSentTests(TestCase):
    """Session keys are bearer credentials: every anonymous event path sends only their hash."""

    def test_no_anonymous_event_sends_the_raw_session_key(self, mock_posthog, _init):
        mock_posthog.api_key = "test-key"

        events.track_file_upload_started(_anon_request(), 1024)
        events.track_dna_displayed(_anon_request(), is_authenticated=False)
        events.track_dna_generation_started("t-1", session_key=RAW_KEY, is_anonymous=True)
        events.track_dna_generation_completed("t-1", session_key=RAW_KEY, is_anonymous=True)
        events.track_dna_generation_failed("t-1", session_key=RAW_KEY, is_anonymous=True, error_type="E")
        events.track_anonymous_dna_generated("t-1", RAW_KEY)
        events.track_anonymous_dna_displayed(RAW_KEY)
        events.track_anonymous_dna_claimed(user_id=7, task_id="t-1", session_key=RAW_KEY)
        events.track_public_profile_viewed("someone", 3, False, False, viewer_session_id=RAW_KEY)
        events.track_recommendations_generated(session_key=RAW_KEY)
        events.track_recommendations_displayed(session_key=RAW_KEY)
        with patch("core.analytics.middleware.get_environment", return_value="production"):
            PostHogExceptionMiddleware(MagicMock()).process_exception(_anon_request(), ValueError("boom"))

        calls = mock_posthog.capture.call_args_list + mock_posthog.capture_exception.call_args_list
        self.assertEqual(len(calls), 12)
        for call in calls:
            self.assertNotIn(RAW_KEY, str(call))

        # One visitor still maps to one person in PostHog.
        anon_ids = {c.kwargs["distinct_id"] for c in calls if c.kwargs["distinct_id"] != "7"}
        self.assertEqual(anon_ids, {_key_hash(RAW_KEY)})
        claimed = next(c for c in calls if c.kwargs.get("event") == "anonymous_dna_claimed")
        # Dashboards depend on the exact event shape.
        self.assertEqual(claimed.kwargs["distinct_id"], "7")
        self.assertEqual(
            claimed.kwargs["properties"],
            {
                "user_id": 7,
                "task_id": "t-1",
                "session_key_hash": _key_hash(RAW_KEY),
                "environment": ANY,
                "server_hostname": ANY,
            },
        )

    def test_anonymous_recommendation_cache_key_is_hashed(self, mock_posthog, _init):
        # Cache keys are sent to PostHog in redis_cache_error events.
        from core.services.recommendation_service import get_recommendations_for_anonymous

        with patch("core.services.recommendation_service.safe_cache_get", return_value=[]) as mock_get:
            get_recommendations_for_anonymous(RAW_KEY)

        cache_key = mock_get.call_args.args[0]
        self.assertNotIn(RAW_KEY, cache_key)
        self.assertEqual(cache_key, f"anon_recommendations_{_key_hash(RAW_KEY)}")


class RawSessionKeyNeverLoggedTests(TestCase):
    """Server logs get the hash too: a leaked log line must not hand out a usable session."""

    def test_missing_session_log_lines_use_the_hash(self):
        from django.contrib.auth.models import User

        from core.services.recommendation_service import _get_recommendations_for_anonymous_uncached
        from core.tasks.dna import _create_userbooks_from_anonymous_session

        user = User.objects.create_user(username="logcheck", email="logcheck@example.com", password="pw-123456!")
        with self.assertLogs("core", level="WARNING") as logs:
            _get_recommendations_for_anonymous_uncached(RAW_KEY)
            _create_userbooks_from_anonymous_session(user, RAW_KEY)

        output = "\n".join(logs.output)
        self.assertNotIn(RAW_KEY, output)
        self.assertIn(_key_hash(RAW_KEY), output)

    def test_anonymous_session_str_hides_the_key(self):
        from core.models import AnonymousUserSession

        self.assertNotIn(RAW_KEY, str(AnonymousUserSession(pk=7, session_key=RAW_KEY)))
