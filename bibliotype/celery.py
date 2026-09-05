import os

from celery import Celery

# Set the default Django settings module for the 'celery' program.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "bibliotype.settings")

# Create the Celery application
app = Celery("bibliotype")

# Using a string here means the worker doesn't have to serialize
# the configuration object to child processes.
# - namespace='CELERY' means all celery-related configuration keys
#   should have a `CELERY_` prefix.
app.config_from_object("django.conf:settings", namespace="CELERY")

# Load task modules from all registered Django apps.
app.autodiscover_tasks()


from celery.signals import celeryd_after_setup, task_failure  # noqa: E402


@task_failure.connect
def _capture_task_failure(sender=None, task_id=None, exception=None, einfo=None, **kwargs):
    """Send unhandled Celery task exceptions to PostHog error tracking (production only).

    The web tier gets this from PostHogExceptionMiddleware; workers have no
    request cycle, so without this hook task crashes never reach PostHog.
    """
    # Imported here: this module loads before django.setup() finishes.
    from core.analytics.posthog_client import capture_exception, get_environment

    if get_environment() != "production":
        return
    try:
        capture_exception(
            distinct_id="system",
            exception=exception,
            context={"task_name": getattr(sender, "name", None), "task_id": task_id},
        )
    except Exception:
        # Tracking must never mask the original task failure.
        pass


@celeryd_after_setup.connect
def _assert_bulk_queue_consumed(sender, instance, **kwargs):
    """Hard-exit a worker that isn't consuming the bulk enrichment queue.

    A worker started without the right -Q (e.g. a compose revert) would strand
    enrichment_bulk messages in Redis silently forever — no error, no consumer.
    Failing loudly at startup turns that into an immediately visible crash loop.
    """
    # Imported here: this module loads before django.setup() finishes.
    from core.dna_constants import ENRICHMENT_BULK_QUEUE

    default_queue = instance.app.conf.task_default_queue  # "celery" unless overridden
    required = {default_queue, ENRICHMENT_BULK_QUEUE}
    names = {q.name for q in instance.app.amqp.queues.consume_from.values()}
    if not required <= names:
        raise SystemExit(
            f"Worker is not consuming {sorted(required - names)} (got: {sorted(names)}). "
            f"Fix the -Q flag: celery -A bibliotype worker -Q {default_queue},{ENRICHMENT_BULK_QUEUE}"
        )
