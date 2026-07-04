"""Django settings for bibliotype project."""

import os
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("SECRET_KEY")


def _env_bool(name, default=False):
    """Parse a boolean env var accepting common truthy/falsy spellings.

    Unrecognized values raise — for security-critical flags we'd rather fail
    loudly than silently coerce to False.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    if raw.strip().lower() in {"true", "1", "yes", "on"}:
        return True
    if raw.strip().lower() in {"false", "0", "no", "off", ""}:
        return False
    raise ImproperlyConfigured(f"Unrecognized boolean value for env var {name}: {raw!r}")


# DEBUG defaults to False (US-004) so a missing env var never silently leaves
# production in debug mode. Local dev and CI must set DEBUG=True explicitly.
DEBUG = _env_bool("DEBUG", False)
# Refuse to boot with DEBUG=True outside known-safe environments. Default
# DJANGO_ENV (unset) is treated as production-equivalent for this check —
# fail-safe. Whitelisted dev envs: "development", "test", "ci".
_django_env = os.environ.get("DJANGO_ENV", "")
if _django_env not in {"development", "test", "ci"} and DEBUG:
    raise ImproperlyConfigured(
        f"DEBUG must be False when DJANGO_ENV={_django_env!r}. "
        "Permitted DEBUG=True environments: development, test, ci."
    )
ENABLE_SILK = _env_bool("ENABLE_SILK", False)

# US-027: when True, skip the per-call `time.sleep(1.2)` throttle in
# `core/services/book_enrichment_service.py`. The Celery `rate_limit="30/m"` on
# `enrich_book_task` is the unconditional safety net. Flip to True only after
# confirming Open Library + Google Books rate-limit headroom — see AGENTS.md
# "Settings invariants".
ENABLE_PARALLEL_ENRICHMENT = _env_bool("ENABLE_PARALLEL_ENRICHMENT", False)

# US-037: single source of truth for the Gemini API key and model id.
# Consumers must import these from settings via `core/services/_gemini.py`,
# never read `os.environ` directly. Override `GEMINI_MODEL` to swap model
# versions across the whole app (vibe generation + publisher research).
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

allowed_hosts_str = os.environ.get("ALLOWED_HOSTS", "")
ALLOWED_HOSTS = [host.strip() for host in allowed_hosts_str.split(",") if host.strip()]


# Application definition

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "core",
    "django_celery_results",
]

# Django Silk for profiling - opt-in via ENABLE_SILK=True (local development only)
if ENABLE_SILK:
    INSTALLED_APPS.insert(0, "silk")  # Insert at beginning to ensure it's loaded first


MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "core.analytics.middleware.PostHogExceptionMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

# Django Silk middleware for profiling - opt-in via ENABLE_SILK=True (local development only)
if ENABLE_SILK:
    MIDDLEWARE.insert(0, "silk.middleware.SilkyMiddleware")


ROOT_URLCONF = "bibliotype.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.posthog_settings",
                "core.context_processors.turnstile_context",
            ],
        },
    },
]

WSGI_APPLICATION = "bibliotype.wsgi.application"


# Database
# https://docs.djangoproject.com/en/5.2/ref/settings/#databases

DATABASES = {"default": dj_database_url.config(default=f'sqlite:///{os.path.join(BASE_DIR, "db.sqlite3")}')}

# Cache configuration
# Uses localhost:6379 by default, which works for:
# - Local Redis server running on localhost
# - Docker Redis container exposed on port 6379 (via docker-compose port mapping)
# Override with REDIS_CACHE_URL environment variable if needed
REDIS_CACHE_URL = os.environ.get("REDIS_CACHE_URL", "redis://localhost:6379/1")
if REDIS_CACHE_URL == "locmem://":
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": REDIS_CACHE_URL,
        }
    }

# Email configuration (Brevo SMTP)
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = os.environ.get("EMAIL_HOST", "smtp-relay.brevo.com")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT") or 587)
EMAIL_USE_TLS = True
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "noreply@bibliotype.com")

# Cloudflare Turnstile CAPTCHA (disabled in development — widget won't render, verification is bypassed)
if os.environ.get("DJANGO_ENV") == "production":
    TURNSTILE_SITE_KEY = os.environ.get("TURNSTILE_SITE_KEY", "")
    TURNSTILE_SECRET_KEY = os.environ.get("TURNSTILE_SECRET_KEY", "")
else:
    TURNSTILE_SITE_KEY = ""
    TURNSTILE_SECRET_KEY = ""

# Password validation
# https://docs.djangoproject.com/en/5.2/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]


TEST_RUNNER = "bibliotype.runner.ForceDisconnectTestRunner"

# Internationalization
# https://docs.djangoproject.com/en/5.2/topics/i18n/

LANGUAGE_CODE = "en-us"

TIME_ZONE = "UTC"

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.2/howto/static-files/


STATIC_URL = "/static/"

STATIC_ROOT = BASE_DIR / "staticfiles"

STATICFILES_DIRS = [
    BASE_DIR / "static",
]

STATICFILES_STORAGE = "django.contrib.staticfiles.storage.ManifestStaticFilesStorage"

if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = "same-origin"
else:
    SECURE_SSL_REDIRECT = False
    SESSION_COOKIE_SECURE = False
    CSRF_COOKIE_SECURE = False

# Default primary key field type
# https://docs.djangoproject.com/en/5.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Celery configuration
# Defaults to localhost:6379, which works for:
# - Local Redis server running on localhost
# - Docker Redis container exposed on port 6379 (via docker-compose port mapping)
# When running inside Docker containers, docker-compose sets these to redis://redis:6379/0
# Override with CELERY_BROKER_URL and CELERY_RESULT_BACKEND environment variables if needed
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", "redis://localhost:6379/0")
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = "UTC"
# Prevent Celery from connecting to broker during Django startup
# This avoids hanging when Redis is temporarily unavailable
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = False
CELERY_BROKER_CONNECTION_RETRY = False
CELERY_BROKER_CONNECTION_TIMEOUT = 5

# Logging configuration
# Create logs directory if it doesn't exist
LOGS_DIR = BASE_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {module} {process:d} {thread:d} {message}",
            "style": "{",
        },
        "simple": {
            "format": "{levelname} {asctime} {message}",
            "style": "{",
        },
    },
    "filters": {
        "require_debug_true": {
            "()": "django.utils.log.RequireDebugTrue",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
        "file": {
            "class": "logging.FileHandler",
            "filename": BASE_DIR / "logs" / "bibliotype.log",
            "formatter": "verbose",
        },
    },
    "root": {
        "handlers": ["console", "file"],
        "level": "INFO" if not DEBUG else "DEBUG",
    },
    "loggers": {
        "django": {
            "handlers": ["console", "file"],
            "level": os.getenv("DJANGO_LOG_LEVEL", "INFO"),
            "propagate": False,
        },
        "core": {
            "handlers": ["console", "file"],
            "level": "DEBUG" if DEBUG else "INFO",
            "propagate": False,
        },
    },
}

# Celery Configuration
from celery.schedules import crontab

CELERY_BEAT_SCHEDULE = {
    'anonymize-expired-sessions': {
        'task': 'core.tasks.anonymize_expired_sessions_task',
        'schedule': crontab(hour=2, minute=0),  # Daily at 2 AM
    },
    'research-publisher-mainstream': {
        'task': 'core.tasks.research_publisher_mainstream_task',
        'schedule': crontab(hour=3, minute=0, day_of_week='sunday'),  # Weekly on Sundays at 3 AM
    },
}

# Custom 404 handler
handler404 = 'core.views.handler404'

# Django Silk configuration - opt-in via ENABLE_SILK=True (local development only)
if ENABLE_SILK:
    SILKY_PYTHON_PROFILER = True
    SILKY_PYTHON_PROFILER_BINARY = False
    SILKY_META = True
    # Limit the number of requests to store (prevents database bloat)
    SILKY_MAX_RECORDED_REQUESTS = 1000
    # Profile all requests when enabled locally
    SILKY_INTERCEPT_PERCENT = 100
