"""Maintenance tasks: expired-session anonymization and admin-triggered management commands."""

import logging

from celery import shared_task

from ..management_command_registry import ALLOWED_COMMANDS

logger = logging.getLogger(__name__)


# name pinned: wire name must survive the package split (queued msgs + beat)
@shared_task(name="core.tasks.run_management_command_task")
def run_management_command_task(command_name: str, args: list = None, kwargs: dict = None):
    """Run a Django management command and store the output in cache."""
    if command_name not in ALLOWED_COMMANDS:
        raise ValueError(f"command not allowed: {command_name}")

    import io
    from django.core.management import call_command
    from ..cache_utils import safe_cache_set

    args = args or []
    kwargs = kwargs or {}

    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()

    try:
        call_command(command_name, *args, stdout=stdout_buffer, stderr=stderr_buffer, **kwargs)
        stdout_output = stdout_buffer.getvalue()
        stderr_output = stderr_buffer.getvalue()

        if stdout_output:
            logger.info(f"[{command_name}] stdout:\n{stdout_output}")
        if stderr_output:
            logger.warning(f"[{command_name}] stderr:\n{stderr_output}")

        result = {
            "status": "success",
            "stdout": stdout_output,
            "stderr": stderr_output,
        }
    except Exception as e:
        stdout_output = stdout_buffer.getvalue()
        stderr_output = stderr_buffer.getvalue()

        if stdout_output:
            logger.info(f"[{command_name}] stdout:\n{stdout_output}")

        logger.error(f"Management command '{command_name}' failed: {e}", exc_info=True)
        result = {
            "status": "error",
            "stdout": stdout_output,
            "stderr": stderr_output,
            "error": str(e),
        }

    return result


# name pinned: wire name must survive the package split (queued msgs + beat)
@shared_task(name="core.tasks.anonymize_expired_sessions_task")
def anonymize_expired_sessions_task():
    """Periodic task to convert expired anonymous sessions to anonymized profiles"""
    from ..services.anonymization_service import batch_anonymize_expired_sessions

    count = batch_anonymize_expired_sessions()
    logger.info(f"Anonymized {count} expired sessions")
    return count
