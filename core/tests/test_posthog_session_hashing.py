from types import SimpleNamespace
from unittest.mock import MagicMock, patch

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
        self.assertEqual(claimed.kwargs["properties"]["session_key_hash"], _key_hash(RAW_KEY))
