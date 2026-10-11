"""
PostHog Middleware

Middleware for catching unhandled exceptions.
"""

import logging

from django.utils.deprecation import MiddlewareMixin

from .posthog_client import capture_exception, get_distinct_id, get_environment

logger = logging.getLogger(__name__)


class PostHogExceptionMiddleware(MiddlewareMixin):
    """
    Middleware to catch unhandled exceptions and track them in PostHog.

    Only tracks in production environment by default.
    """

    def process_exception(self, request, exception):
        """Track unhandled exceptions in PostHog."""
        environment = get_environment()

        # Only track in production (or if explicitly enabled in dev)
        if environment != "production":
            return None

        try:
            distinct_id = get_distinct_id(request)

            context = {
                "request_path": request.path,
                "request_method": request.method,
                "user_authenticated": request.user.is_authenticated,
            }

            if request.user.is_authenticated:
                context["user_id"] = request.user.id
            else:
                context["session_id"] = request.session.session_key

            capture_exception(
                distinct_id=distinct_id,
                exception=exception,
                context=context,
                environment=environment,
            )
        except Exception as e:
            # Don't break error handling if tracking fails
            logger.warning(f"Failed to track exception in PostHog: {e}")

        return None
