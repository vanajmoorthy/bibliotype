"""
PostHog Middleware

Middleware for tracking pageviews and catching unhandled exceptions.
"""

import logging
from django.utils.deprecation import MiddlewareMixin
from .posthog_client import get_environment, get_distinct_id, capture_exception

logger = logging.getLogger(__name__)


class PostHogPageviewMiddleware(MiddlewareMixin):
    """
    Middleware to track pageviews in PostHog.
    
    Tracks $pageview events for all page requests, excluding:
    - /admin/*
    - /static/*
    - /api/*
    - /silk/* (dev only)
    """

    def process_view(self, request, view_func, view_args, view_kwargs):
        """Track pageview before processing the view."""
        path = request.path
        
        # Skip tracking for excluded paths
        excluded_prefixes = ["/admin/", "/static/", "/api/", "/silk/"]
        if any(path.startswith(prefix) for prefix in excluded_prefixes):
            return None
        
        # Skip if PostHog not configured
        try:
            import posthog
            if not posthog.api_key:
                return None
        except Exception:
            return None
        
        # Track pageview
        try:
            distinct_id = get_distinct_id(request)
            environment = get_environment()
            
            properties = {
                "path": path,
                "method": request.method,
                "referrer": request.META.get("HTTP_REFERER", ""),
                "user_agent": request.META.get("HTTP_USER_AGENT", ""),
            }
            
            if request.user.is_authenticated:
                properties["user_id"] = request.user.id
            else:
                properties["session_id"] = request.session.session_key
            
            posthog.capture(
                distinct_id=distinct_id,
                event="$pageview",
                properties={
                    **properties,
                    "environment": environment,
                },
            )
        except Exception as e:
            # Don't break the request if tracking fails
            logger.warning(f"Failed to track pageview: {e}")
        
        return None


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

