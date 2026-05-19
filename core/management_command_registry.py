"""Single source of truth for admin-runnable management commands.

`ADMIN_COMMANDS` drives the admin Command Runner UI; `ALLOWED_COMMANDS` is
the whitelist used both by the HTTP API in `core/admin.py` and by the Celery
task `run_management_command_task` so the worker rejects unwhitelisted names
even if a caller publishes directly to the broker.
"""

ADMIN_COMMANDS = [
    {
        "name": "backfill_isbn",
        "description": "Backfill ISBN13 for books missing it by querying Open Library. Run before enrich_books for better results.",
        "arguments": [
            {
                "name": "--dry-run",
                "type": "flag",
                "label": "Dry run",
                "help": "Show what would be updated without saving",
            },
            {"name": "--limit", "type": "int", "label": "Limit", "help": "Max books to process"},
        ],
    },
    {
        "name": "enrich_books",
        "description": "Enrich books missing metadata. Default: async Celery tasks. Use --sync for direct API calls.",
        "arguments": [
            {"name": "--dry-run", "type": "flag", "label": "Dry run", "help": "Show counts without processing"},
            {"name": "--limit", "type": "int", "label": "Limit", "help": "Max books to process"},
            {
                "name": "--sync",
                "type": "flag",
                "label": "Sync mode",
                "help": "Run synchronously via APIs instead of Celery",
            },
            {"name": "--process-all", "type": "flag", "label": "Process all", "help": "Re-check all books"},
        ],
    },
    {
        "name": "research_publishers",
        "description": "AI-powered publisher research to determine mainstream status and parent companies.",
        "arguments": [
            {"name": "--recheck-all", "type": "flag", "label": "Recheck all", "help": "Re-research all publishers"},
            {"name": "--limit", "type": "int", "label": "Limit", "help": "Max publishers to check"},
        ],
    },
    {
        "name": "update_author_status",
        "description": "Check author mainstream status via Open Library and Wikipedia APIs.",
        "arguments": [
            {"name": "--recheck-all", "type": "flag", "label": "Recheck all", "help": "Re-check all authors"},
            {"name": "--age-days", "type": "int", "label": "Age (days)", "help": "Re-check authors older than N days"},
        ],
    },
    {
        "name": "analyze_genres",
        "description": "Audit genre mappings: shows unmapped genres and their frequencies. Read-only.",
        "arguments": [],
    },
    {
        "name": "rebuild_analytics",
        "description": "Rebuild the aggregate analytics singleton with current community data.",
        "arguments": [],
    },
    {
        "name": "review_publishers",
        "description": "List non-mainstream publishers for manual review. Read-only.",
        "arguments": [],
    },
    {
        "name": "backfill_subtitle_data",
        "description": "Backfill subtitle stats (contrariness, review counts, niche counts) into existing DNA data.",
        "arguments": [
            {"name": "--dry-run", "type": "flag", "label": "Dry run", "help": "Show changes without saving"},
            {"name": "--limit", "type": "int", "label": "Limit", "help": "Max profiles to process"},
            {"name": "--username", "type": "str", "label": "Username", "help": "Process a single user"},
            {"name": "--force", "type": "flag", "label": "Force", "help": "Overwrite existing subtitle fields"},
        ],
    },
    {
        "name": "backfill_covers",
        "description": "Populate cover URLs for books. Fast mode uses ISBN (no API calls). Use --with-api for books missing ISBN.",
        "arguments": [
            {
                "name": "--dry-run",
                "type": "flag",
                "label": "Dry run",
                "help": "Show what would be updated without saving",
            },
            {"name": "--limit", "type": "int", "label": "Limit", "help": "Max books to process"},
            {
                "name": "--with-api",
                "type": "flag",
                "label": "With API calls",
                "help": "Also fetch covers for books without ISBN via API",
            },
        ],
    },
    {
        "name": "regenerate_dna",
        "description": "Regenerate genre/reader-type DNA fields from current enriched Book data. Run after enrichment backfills.",
        "arguments": [
            {"name": "--dry-run", "type": "flag", "label": "Dry run", "help": "Show changes without saving"},
            {"name": "--limit", "type": "int", "label": "Limit", "help": "Max profiles to process"},
            {"name": "--username", "type": "str", "label": "Username", "help": "Process a single user"},
            {
                "name": "--with-recommendations",
                "type": "flag",
                "label": "With recommendations",
                "help": "Also regenerate recommendations after DNA update",
            },
        ],
    },
    {
        "name": "regenerate_recommendations",
        "description": "Regenerate recommendations for users with DNA data. Dispatches async Celery tasks.",
        "arguments": [
            {
                "name": "--dry-run",
                "type": "flag",
                "label": "Dry run",
                "help": "Show what would happen without dispatching",
            },
            {"name": "--limit", "type": "int", "label": "Limit", "help": "Max profiles to process"},
            {"name": "--username", "type": "str", "label": "Username", "help": "Process a single user"},
        ],
    },
]

# Whitelist of allowed command names for security. Frozen so accidental
# mutation elsewhere can't widen the allowlist at runtime.
ALLOWED_COMMANDS = frozenset(cmd["name"] for cmd in ADMIN_COMMANDS)
