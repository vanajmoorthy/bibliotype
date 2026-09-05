"""
PostHog Analytics Module

This module provides PostHog event tracking and analytics for the Bibliotype application.
"""

from .events import (
    track_account_deleted,
    track_anonymous_dna_claimed,
    track_anonymous_dna_displayed,
    track_anonymous_dna_generated,
    track_dna_displayed,
    track_dna_generation_completed,
    track_dna_generation_failed,
    track_dna_generation_started,
    track_external_api_call,
    track_file_upload_started,
    track_profile_made_public,
    track_public_profile_viewed,
    track_recommendation_error,
    track_recommendations_displayed,
    track_recommendations_generated,
    track_redis_cache_error,
    track_settings_updated,
    track_user_logged_in,
    track_user_signed_up,
    track_vibe_generation_completed,
    track_vibe_generation_failed,
    track_vibe_requested,
)
from .posthog_client import capture_event, capture_exception, get_distinct_id, get_environment

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
    "track_recommendations_displayed",
    "track_settings_updated",
    "track_account_deleted",
    "track_recommendation_error",
    "track_redis_cache_error",
    "track_external_api_call",
    "track_vibe_requested",
    "track_vibe_generation_completed",
    "track_vibe_generation_failed",
]
