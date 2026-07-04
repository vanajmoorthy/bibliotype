"""Tasks package: per-domain task modules re-exported here for stable `core.tasks.*` import paths.

Every `@shared_task` in the submodules pins its original `core.tasks.<name>` wire name so
queued broker messages and the Celery beat schedule survive the package split.
"""

from .dna import (  # noqa: F401 — re-exported for stable import paths
    _create_userbooks_from_anonymous_session,
    _save_dna_to_profile,
    claim_anonymous_dna_task,
    generate_reading_dna_task,
)
from .enrichment import (  # noqa: F401 — re-exported for stable import paths
    PUBLISHER_CHECK_AGE_THRESHOLD_DAYS,
    PUBLISHER_CHECK_BATCH_LIMIT,
    check_author_mainstream_status_task,
    enrich_book_task,
    research_publisher_mainstream_task,
)
from .maintenance import (  # noqa: F401 — re-exported for stable import paths
    anonymize_expired_sessions_task,
    run_management_command_task,
)
from .recommendations import generate_recommendations_task  # noqa: F401 — re-exported for stable import paths
