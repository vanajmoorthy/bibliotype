"""
PostHog Event Tracking Helpers

Helper functions for tracking specific events in the Bibliotype application.
"""

import logging
from django.core.cache import cache
from .posthog_client import get_environment, get_distinct_id, capture_event, capture_exception

logger = logging.getLogger(__name__)


def track_file_upload_started(request, file_size):
    """Track when a user starts uploading a CSV file."""
    distinct_id = get_distinct_id(request)
    environment = get_environment()
    
    capture_event(
        distinct_id=distinct_id,
        event_name="file_upload_started",
        properties={
            "file_size": file_size,
            "is_authenticated": request.user.is_authenticated,
            "user_id": request.user.id if request.user.is_authenticated else None,
            "session_id": request.session.session_key if not request.user.is_authenticated else None,
        },
        environment=environment,
    )


def track_dna_generation_started(task_id, user_id=None, session_key=None, is_anonymous=False):
    """Track when DNA generation task starts."""
    distinct_id = str(user_id) if user_id else (session_key or "anonymous")
    environment = get_environment()
    
    capture_event(
        distinct_id=distinct_id,
        event_name="dna_generation_started",
        properties={
            "task_id": task_id,
            "user_id": user_id,
            "session_key": session_key,
            "is_anonymous": is_anonymous,
        },
        environment=environment,
    )


def track_dna_generation_completed(task_id, user_id=None, session_key=None, is_anonymous=False, books_count=None, processing_time=None):
    """Track when DNA generation task completes successfully."""
    distinct_id = str(user_id) if user_id else (session_key or "anonymous")
    environment = get_environment()
    
    properties = {
        "task_id": task_id,
        "user_id": user_id,
        "session_key": session_key,
        "is_anonymous": is_anonymous,
    }
    
    if books_count is not None:
        properties["books_count"] = books_count
    if processing_time is not None:
        properties["processing_time_seconds"] = processing_time
    
    capture_event(
        distinct_id=distinct_id,
        event_name="dna_generation_completed",
        properties=properties,
        environment=environment,
    )


def track_anonymous_dna_generated(task_id, session_key, books_count=None, processing_time=None):
    """Track when anonymous user successfully generates DNA."""
    distinct_id = session_key or "anonymous"
    environment = get_environment()
    
    properties = {
        "task_id": task_id,
        "session_key": session_key,
    }
    
    if books_count is not None:
        properties["books_count"] = books_count
    if processing_time is not None:
        properties["processing_time_seconds"] = processing_time
    
    capture_event(
        distinct_id=distinct_id,
        event_name="anonymous_dna_generated",
        properties=properties,
        environment=environment,
    )


def track_anonymous_dna_displayed(session_key, has_recommendations=False):
    """Track when anonymous user views their generated DNA."""
    distinct_id = session_key or "anonymous"
    environment = get_environment()
    
    capture_event(
        distinct_id=distinct_id,
        event_name="anonymous_dna_displayed",
        properties={
            "session_key": session_key,
            "has_recommendations": has_recommendations,
        },
        environment=environment,
    )


def track_dna_generation_failed(task_id, user_id=None, session_key=None, is_anonymous=False, error_type=None, error_message=None):
    """Track when DNA generation task fails."""
    distinct_id = str(user_id) if user_id else (session_key or "anonymous")
    environment = get_environment()
    
    # Sanitize error message
    if error_message and len(error_message) > 500:
        error_message = error_message[:500] + "..."
    
    capture_event(
        distinct_id=distinct_id,
        event_name="dna_generation_failed",
        properties={
            "task_id": task_id,
            "user_id": user_id,
            "session_key": session_key,
            "is_anonymous": is_anonymous,
            "error_type": error_type,
            "error_message": error_message,
        },
        environment=environment,
    )


def track_dna_displayed(request, is_authenticated, has_recommendations=False):
    """Track when user views their DNA results."""
    distinct_id = get_distinct_id(request)
    environment = get_environment()
    
    properties = {
        "is_authenticated": is_authenticated,
        "has_recommendations": has_recommendations,
    }
    
    if is_authenticated:
        properties["user_id"] = request.user.id
    else:
        properties["session_id"] = request.session.session_key
    
    capture_event(
        distinct_id=distinct_id,
        event_name="dna_displayed",
        properties=properties,
        environment=environment,
    )


def track_user_signed_up(user_id, signup_source, task_id_to_claim=None, had_dna_in_session=False):
    """
    Track when a new user signs up.
    
    Args:
        user_id: New user's ID
        signup_source: "after_anonymous_dna", "with_task_claim", "with_session_dna", or "before_dna"
        task_id_to_claim: Optional task ID if claiming anonymous DNA
        had_dna_in_session: Whether user had DNA data in session
    """
    environment = get_environment()
    
    properties = {
        "signup_source": signup_source,
        "had_dna_in_session": had_dna_in_session,
    }
    
    if task_id_to_claim:
        properties["task_id_to_claim"] = task_id_to_claim
    
    capture_event(
        distinct_id=str(user_id),
        event_name="user_signed_up",
        properties=properties,
        environment=environment,
    )


def track_anonymous_dna_claimed(user_id, task_id, session_key=None):
    """Track when user signs up and claims their anonymous DNA."""
    environment = get_environment()
    
    capture_event(
        distinct_id=str(user_id),
        event_name="anonymous_dna_claimed",
        properties={
            "user_id": user_id,
            "task_id": task_id,
            "session_key": session_key,
        },
        environment=environment,
    )


def track_user_logged_in(user_id, had_dna_in_session=False):
    """Track when user successfully logs in."""
    environment = get_environment()
    
    capture_event(
        distinct_id=str(user_id),
        event_name="user_logged_in",
        properties={
            "user_id": user_id,
            "had_dna_in_session": had_dna_in_session,
        },
        environment=environment,
    )


def track_profile_made_public(user_id):
    """Track when user makes their profile public."""
    environment = get_environment()
    
    capture_event(
        distinct_id=str(user_id),
        event_name="profile_made_public",
        properties={
            "user_id": user_id,
        },
        environment=environment,
    )


def track_public_profile_viewed(profile_username, profile_user_id, viewer_is_authenticated, viewer_is_owner, viewer_user_id=None, viewer_session_id=None):
    """Track when someone views a public profile."""
    # Use viewer's distinct_id if available, otherwise use profile owner's ID
    if viewer_is_authenticated and viewer_user_id:
        distinct_id = str(viewer_user_id)
    elif viewer_session_id:
        distinct_id = viewer_session_id
    else:
        distinct_id = str(profile_user_id)
    
    environment = get_environment()
    
    capture_event(
        distinct_id=distinct_id,
        event_name="public_profile_viewed",
        properties={
            "profile_username": profile_username,
            "profile_user_id": profile_user_id,
            "viewer_is_authenticated": viewer_is_authenticated,
            "viewer_is_owner": viewer_is_owner,
            "viewer_user_id": viewer_user_id,
            "viewer_session_id": viewer_session_id,
        },
        environment=environment,
    )


def track_recommendations_generated(user_id=None, recommendation_count=0, is_authenticated=False, session_key=None):
    """Track when recommendations are successfully generated."""
    distinct_id = str(user_id) if user_id else (session_key or "anonymous")
    environment = get_environment()
    
    properties = {
        "recommendation_count": recommendation_count,
        "is_authenticated": is_authenticated,
    }
    
    if user_id:
        properties["user_id"] = user_id
    if session_key:
        properties["session_id"] = session_key
    
    capture_event(
        distinct_id=distinct_id,
        event_name="recommendations_generated",
        properties=properties,
        environment=environment,
    )


def track_settings_updated(user_id, setting_type):
    """
    Track when user updates settings.
    
    Args:
        user_id: User ID
        setting_type: "display_name" or "recommendation_visibility"
    """
    environment = get_environment()
    
    capture_event(
        distinct_id=str(user_id),
        event_name="settings_updated",
        properties={
            "user_id": user_id,
            "setting_type": setting_type,
        },
        environment=environment,
    )


def track_recommendation_error(profile_user_id, error_type, error_message, context="public_profile_view"):
    """Track when recommendation generation fails."""
    environment = get_environment()
    
    # Sanitize error message
    if error_message and len(error_message) > 500:
        error_message = error_message[:500] + "..."
    
    capture_event(
        distinct_id=str(profile_user_id),
        event_name="recommendation_error",
        properties={
            "profile_user_id": profile_user_id,
            "error_type": error_type,
            "error_message": error_message,
            "context": context,
        },
        environment=environment,
    )


def track_redis_cache_error(operation, key, error_type, error_message):
    """
    Track Redis cache errors (only in production).
    
    Args:
        operation: "get" or "set"
        key: Cache key (will be sanitized if sensitive)
        error_type: Exception class name
        error_message: Sanitized error message
    """
    environment = get_environment()
    
    # Only track in production
    if environment != "production":
        return
    
    # Sanitize cache key if it might contain sensitive data
    sanitized_key = key
    if key and len(key) > 100:
        sanitized_key = key[:50] + "..." + key[-50:]
    
    # Sanitize error message
    if error_message and len(error_message) > 500:
        error_message = error_message[:500] + "..."
    
    # Use a system distinct_id for infrastructure errors
    capture_event(
        distinct_id="system",
        event_name="redis_cache_error",
        properties={
            "operation": operation,
            "key": sanitized_key,
            "error_type": error_type,
            "error_message": error_message,
        },
        environment=environment,
    )

