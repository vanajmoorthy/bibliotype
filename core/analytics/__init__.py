"""
PostHog Analytics Module

This module provides PostHog event tracking and analytics for the Bibliotype application.
"""

from .posthog_client import get_environment, get_distinct_id, capture_event, capture_exception
from .events import (
    track_file_upload_started,
    track_dna_generation_started,
    track_dna_generation_completed,
    track_anonymous_dna_generated,
    track_anonymous_dna_displayed,
    track_dna_generation_failed,
    track_dna_displayed,
    track_user_signed_up,
    track_anonymous_dna_claimed,
    track_user_logged_in,
    track_profile_made_public,
    track_public_profile_viewed,
    track_recommendations_generated,
    track_settings_updated,
    track_recommendation_error,
    track_redis_cache_error,
)

__all__ = [
    "get_environment",
    "get_distinct_id",
    "capture_event",
    "capture_exception",
    "track_file_upload_started",
    "track_dna_generation_started",
    "track_dna_generation_completed",
    "track_anonymous_dna_generated",
    "track_anonymous_dna_displayed",
    "track_dna_generation_failed",
    "track_dna_displayed",
    "track_user_signed_up",
    "track_anonymous_dna_claimed",
    "track_user_logged_in",
    "track_profile_made_public",
    "track_public_profile_viewed",
    "track_recommendations_generated",
    "track_settings_updated",
    "track_recommendation_error",
    "track_redis_cache_error",
]

