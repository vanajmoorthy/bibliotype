"""
PostHog Event Tracking Helpers

Helper functions for tracking specific events in the Bibliotype application.
"""

import logging

from .posthog_client import capture_event, capture_exception, get_distinct_id, get_environment

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


def track_dna_generation_completed(
    task_id,
    user_id=None,
    session_key=None,
    is_anonymous=False,
    books_count=None,
    processing_time=None,
    reader_type=None,
):
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
    if reader_type:
        properties["reader_type"] = reader_type

    capture_event(
        distinct_id=distinct_id,
        event_name="dna_generation_completed",
        properties=properties,
        environment=environment,
    )


def track_anonymous_dna_generated(task_id, session_key, books_count=None, processing_time=None, reader_type=None):
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
    if reader_type:
        properties["reader_type"] = reader_type

    capture_event(
        distinct_id=distinct_id,
        event_name="anonymous_dna_generated",
        properties=properties,
        environment=environment,
    )


def track_anonymous_dna_displayed(session_key, has_recommendations=False, reader_type=None):
    """Track when anonymous user views their generated DNA."""
    distinct_id = session_key or "anonymous"
    environment = get_environment()

    properties = {
        "session_key": session_key,
        "has_recommendations": has_recommendations,
    }
    if reader_type:
        properties["reader_type"] = reader_type

    capture_event(
        distinct_id=distinct_id,
        event_name="anonymous_dna_displayed",
        properties=properties,
        environment=environment,
    )


def track_dna_generation_failed(
    task_id, user_id=None, session_key=None, is_anonymous=False, error_type=None, error_message=None
):
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


def track_dna_displayed(request, is_authenticated, has_recommendations=False, reader_type=None):
    """Track when user views their DNA results."""
    distinct_id = get_distinct_id(request)
    environment = get_environment()

    properties = {
        "is_authenticated": is_authenticated,
        "has_recommendations": has_recommendations,
    }
    if reader_type:
        properties["reader_type"] = reader_type

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


def track_public_profile_viewed(
    profile_username,
    profile_user_id,
    viewer_is_authenticated,
    viewer_is_owner,
    viewer_user_id=None,
    viewer_session_id=None,
    profile_reader_type=None,
    profile_books_count=None,
):
    """Track when someone views a public profile."""
    # The distinct_id is the VIEWER, never the profile owner: attributing an
    # anonymous visit to the owner's person made owners look like they were
    # refreshing their own pages.
    if viewer_is_authenticated and viewer_user_id:
        distinct_id = str(viewer_user_id)
        viewer_type = "owner" if viewer_is_owner else "authenticated"
    elif viewer_session_id:
        distinct_id = viewer_session_id
        viewer_type = "anonymous"
    else:
        distinct_id = "anonymous"
        viewer_type = "anonymous"

    environment = get_environment()

    properties = {
        "profile_username": profile_username,
        "profile_user_id": profile_user_id,
        "viewer_type": viewer_type,
        "viewer_is_authenticated": viewer_is_authenticated,
        "viewer_is_owner": viewer_is_owner,
        "viewer_user_id": viewer_user_id,
        "viewer_session_id": viewer_session_id,
    }
    if profile_reader_type:
        properties["profile_reader_type"] = profile_reader_type
    if profile_books_count is not None:
        properties["profile_books_count"] = profile_books_count

    capture_event(
        distinct_id=distinct_id,
        event_name="public_profile_viewed",
        properties=properties,
        environment=environment,
    )


def track_recommendations_generated(
    user_id=None,
    recommendation_count=0,
    is_authenticated=False,
    session_key=None,
    reader_type=None,
    similar_users_count=None,
    max_similarity_pct=None,
    uniqueness_label=None,
    source=None,
):
    """Track when recommendations are actually generated (task for auth users, inline for anonymous)."""
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
    if reader_type:
        properties["reader_type"] = reader_type
    if similar_users_count is not None:
        properties["similar_users_count"] = similar_users_count
    if max_similarity_pct is not None:
        properties["max_similarity_pct"] = max_similarity_pct
    if uniqueness_label:
        properties["uniqueness_label"] = uniqueness_label
    if source:
        properties["source"] = source

    capture_event(
        distinct_id=distinct_id,
        event_name="recommendations_generated",
        properties=properties,
        environment=environment,
    )


def track_recommendations_displayed(
    user_id=None, recommendation_count=0, is_authenticated=False, session_key=None, reader_type=None
):
    """Track when stored recommendations are rendered on the dashboard (fires per view, unlike recommendations_generated)."""
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
    if reader_type:
        properties["reader_type"] = reader_type

    capture_event(
        distinct_id=distinct_id,
        event_name="recommendations_displayed",
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


def track_external_api_call(api_name, book_id, book_title, status, status_code=None, error_message=None):
    """Track an external API call (Open Library, Google Books) for usage monitoring."""
    environment = get_environment()

    properties = {
        "api_name": api_name,
        "book_id": book_id,
        "book_title": book_title,
        "status": status,
    }
    if status_code is not None:
        properties["status_code"] = status_code
    if error_message:
        properties["error_message"] = str(error_message)[:500]

    capture_event(
        distinct_id="system",
        event_name="external_api_call",
        properties=properties,
        environment=environment,
    )


def track_account_deleted(user_id):
    """Track when a user deletes their account."""
    environment = get_environment()

    capture_event(
        distinct_id=str(user_id),
        event_name="account_deleted",
        properties={
            "user_id": user_id,
        },
        environment=environment,
    )


def track_vibe_requested(cache_hit, user_id=None, is_anonymous=False):
    """
    Track a reading-vibe request and whether the DB-cached vibe was reused.

    Cache-hit rate is the headline LLM cost metric: a generation only happens
    on a miss. Production only. No vibe text or book data is captured.
    """
    environment = get_environment()
    if environment != "production":
        return

    capture_event(
        distinct_id=str(user_id) if user_id else "anonymous",
        event_name="vibe_requested",
        properties={
            "cache_hit": cache_hit,
            "is_anonymous": is_anonymous,
        },
        environment=environment,
    )


def track_vibe_generation_completed(model, latency_ms, prompt_chars, response_chars, provider="gemini"):
    """Track a successful LLM vibe generation. Production only. Lengths and counts only — never content."""
    environment = get_environment()
    if environment != "production":
        return

    capture_event(
        distinct_id="system",
        event_name="vibe_generation_completed",
        properties={
            "provider": provider,
            "model": model,
            "latency_ms": latency_ms,
            "prompt_chars": prompt_chars,
            "response_chars": response_chars,
        },
        environment=environment,
    )


def track_vibe_generation_failed(model, error_type, error_message=None, latency_ms=None, provider="gemini"):
    """Track a failed LLM vibe generation. Production only."""
    environment = get_environment()
    if environment != "production":
        return

    if error_message and len(error_message) > 500:
        error_message = error_message[:500] + "..."

    properties = {
        "provider": provider,
        "model": model,
        "error_type": error_type,
        "error_message": error_message,
    }
    if latency_ms is not None:
        properties["latency_ms"] = latency_ms

    capture_event(
        distinct_id="system",
        event_name="vibe_generation_failed",
        properties=properties,
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
