"""
PostHog Client Wrapper

Provides environment-aware PostHog client with helper functions for event tracking.
"""

import os
import logging
import posthog
from django.conf import settings

logger = logging.getLogger(__name__)

# Initialize PostHog
_posthog_initialized = False


def _initialize_posthog():
    """Initialize PostHog client if not already initialized."""
    global _posthog_initialized
    if not _posthog_initialized:
        api_key = os.environ.get("POSTHOG_API_KEY", "")
        if api_key:
            posthog.api_key = api_key
            posthog.host = "https://eu.i.posthog.com"
            _posthog_initialized = True
            logger.info("PostHog initialized successfully")
        else:
            logger.warning("POSTHOG_API_KEY not found. PostHog tracking will be disabled.")


def get_environment():
    """
    Get current environment string.
    
    Returns:
        "production" or "development"
    """
    django_env = os.environ.get("DJANGO_ENV", "").lower()
    if django_env == "production":
        return "production"
    elif django_env == "development":
        return "development"
    else:
        # Fall back to DEBUG setting
        return "production" if not settings.DEBUG else "development"


def get_distinct_id(request):
    """
    Get distinct ID for PostHog tracking.
    
    For authenticated users, returns user ID.
    For anonymous users, returns session key.
    
    Args:
        request: Django request object
        
    Returns:
        str: distinct_id for PostHog
    """
    if request.user.is_authenticated:
        return str(request.user.id)
    else:
        return request.session.session_key or "anonymous"


def capture_event(distinct_id, event_name, properties=None, environment=None):
    """
    Capture a PostHog event with environment tagging.
    
    Args:
        distinct_id: User ID or session key
        event_name: Name of the event
        properties: Dictionary of event properties
        environment: Optional environment override (defaults to current environment)
    """
    _initialize_posthog()
    
    if not posthog.api_key:
        return
    
    if properties is None:
        properties = {}
    
    # Add environment to all events
    if environment is None:
        environment = get_environment()
    
    properties["environment"] = environment
    
    try:
        posthog.capture(
            distinct_id=distinct_id,
            event=event_name,
            properties=properties,
        )
    except Exception as e:
        logger.error(f"Failed to capture PostHog event '{event_name}': {e}", exc_info=True)


def capture_exception(distinct_id, exception, context=None, environment=None):
    """
    Capture an exception event in PostHog with sanitized error information.
    
    Args:
        distinct_id: User ID or session key
        exception: Exception object
        context: Additional context dictionary
        environment: Optional environment override
    """
    _initialize_posthog()
    
    if not posthog.api_key:
        return
    
    if context is None:
        context = {}
    
    # Sanitize error information
    error_type = type(exception).__name__
    error_message = str(exception)
    
    # Truncate long error messages
    if len(error_message) > 500:
        error_message = error_message[:500] + "..."
    
    # Remove potentially sensitive patterns
    import re
    # Remove API keys, passwords, etc.
    error_message = re.sub(r'(api[_-]?key|password|secret|token)\s*[:=]\s*[\w-]+', r'\1=***', error_message, flags=re.IGNORECASE)
    
    properties = {
        "error_type": error_type,
        "error_message": error_message,
        **context,
    }
    
    if environment is None:
        environment = get_environment()
    
    properties["environment"] = environment
    
    try:
        posthog.capture(
            distinct_id=distinct_id,
            event="exception",
            properties=properties,
        )
    except Exception as e:
        logger.error(f"Failed to capture PostHog exception: {e}", exc_info=True)

