"""Views package: per-domain modules re-exported here for stable `core.views.*` import paths."""

from ..services.user_similarity_service import BADGE_COLOR_MAP  # noqa: F401 — re-exported for stable import paths
from ._enrichment_stats import (  # noqa: F401 — re-exported for stable import paths
    ENRICHMENT_STATS_CACHE_TTL,
    _compute_enrichment_progress,
    _compute_enrichment_stats,
    _recalculate_enrichment_stats,
)
from ._helpers import _enrich_dna_for_display, _expand_book_dict  # noqa: F401 — re-exported for stable import paths
from .auth import (  # noqa: F401 — re-exported for stable import paths
    CustomPasswordResetView,
    _login_view_throttled,
    admin_login_view,
    handler404,
    login_view,
    logout_view,
    signup_view,
)
from .compare import compare_view  # noqa: F401 — re-exported for stable import paths
from .dashboard import display_dna_view, public_profile_view  # noqa: F401 — re-exported for stable import paths
from .pages import (  # noqa: F401 — re-exported for stable import paths
    about_view,
    home_view,
    methodology_view,
    privacy_view,
    terms_view,
)
from .profile import (  # noqa: F401 — re-exported for stable import paths
    _update_username_throttled,
    change_password_view,
    delete_account_view,
    update_privacy_view,
    update_recommendation_visibility_view,
    update_username_view,
)
from .seo import robots_txt_view, sitemap_xml_view  # noqa: F401 — re-exported for stable import paths
from .upload import (  # noqa: F401 — re-exported for stable import paths
    MAX_UPLOAD_COLUMNS,
    MAX_UPLOAD_ROWS,
    MAX_UPLOAD_SIZE_BYTES,
    get_task_result_view,
    status_view,
    task_status_view,
    upload_view,
)
