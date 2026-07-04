"""Views package: per-domain modules re-exported here for stable `core.views.*` import paths."""

from ._helpers import (  # noqa: F401 — re-exported for stable import paths
    BADGE_COLOR_MAP,
    ENRICHMENT_STATS_CACHE_TTL,
    _compute_enrichment_progress,
    _compute_enrichment_stats,
    _enrich_dna_for_display,
    _expand_book_dict,
    _recalculate_enrichment_stats,
)
from .auth import (  # noqa: F401 — re-exported for stable import paths
    CustomPasswordResetView,
    _login_view_throttled,
    handler404,
    login_view,
    logout_view,
    signup_view,
)
from .dashboard import display_dna_view, public_profile_view  # noqa: F401 — re-exported for stable import paths
from .pages import about_view, home_view, privacy_view, terms_view  # noqa: F401 — re-exported for stable import paths
from .profile import (  # noqa: F401 — re-exported for stable import paths
    _update_username_api_throttled,
    update_display_name_view,
    update_privacy_view,
    update_recommendation_visibility,
    update_username_api,
)
from .seo import robots_txt_view, sitemap_xml_view  # noqa: F401 — re-exported for stable import paths
from .upload import (  # noqa: F401 — re-exported for stable import paths
    MAX_UPLOAD_COLUMNS,
    MAX_UPLOAD_ROWS,
    MAX_UPLOAD_SIZE_BYTES,
    check_dna_status_view,
    check_recommendations_status_view,
    enrichment_status_view,
    get_task_result_view,
    task_status_view,
    upload_view,
)
