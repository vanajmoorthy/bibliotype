import json
import logging

from django.contrib import admin
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import path
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect

from .models import (
    AggregateAnalytics,
    AnonymizedReadingProfile,
    AnonymousUserSession,
    Author,
    Book,
    Genre,
    Publisher,
    UserBook,
    UserProfile,
)

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# User Admin (add ID column)
# ──────────────────────────────────────────────

admin.site.unregister(User)


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ("id", "username", "email", "is_staff")


# ──────────────────────────────────────────────
# Model Admins
# ──────────────────────────────────────────────


@admin.register(Genre)
class GenreAdmin(admin.ModelAdmin):
    list_display = ("name", "book_count")
    search_fields = ("name",)
    ordering = ("name",)

    @admin.display(description="Books")
    def book_count(self, obj):
        return obj.books.count()


@admin.register(Author)
class AuthorAdmin(admin.ModelAdmin):
    list_display = ("name", "normalized_name", "is_mainstream", "popularity_score", "mainstream_last_checked")
    list_editable = ("is_mainstream",)
    list_filter = ("is_mainstream",)
    search_fields = ("name", "normalized_name")
    readonly_fields = ("mainstream_last_checked",)
    ordering = ("-popularity_score", "name")


@admin.register(Publisher)
class PublisherAdmin(admin.ModelAdmin):
    list_display = ("name", "is_mainstream", "parent", "book_count", "mainstream_last_checked")
    list_editable = ("is_mainstream",)
    search_fields = ("name",)
    list_filter = ("is_mainstream", "parent")
    readonly_fields = ("mainstream_last_checked",)
    ordering = ("name",)

    @admin.display(description="Books")
    def book_count(self, obj):
        return obj.books.count()


@admin.register(Book)
class BookAdmin(admin.ModelAdmin):
    filter_horizontal = ("genres",)

    list_display = (
        "title",
        "author",
        "publisher",
        "publish_year",
        "page_count",
        "average_rating",
        "global_read_count",
        "isbn13",
    )
    list_filter = ("publish_year", "genres")
    search_fields = ("title", "author__name", "isbn13")
    readonly_fields = ("google_books_average_rating", "google_books_ratings_count", "google_books_last_checked")
    fieldsets = (
        (None, {"fields": ("title", "author", "isbn13")}),
        ("Publication Info", {"fields": ("page_count", "publish_year", "publisher", "genres")}),
        ("App Metrics", {"fields": ("global_read_count",)}),
        (
            "Google Books Data",
            {"fields": ("google_books_average_rating", "google_books_ratings_count", "google_books_last_checked")},
        ),
    )


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "reader_type", "total_books_read", "is_public", "visible_in_recommendations", "last_updated")
    list_filter = ("reader_type", "is_public", "visible_in_recommendations")
    search_fields = ("user__username",)


@admin.register(UserBook)
class UserBookAdmin(admin.ModelAdmin):
    list_display = ("user", "book", "user_rating", "is_top_book", "top_book_position")
    list_filter = ("is_top_book", "user_rating")
    search_fields = ("user__username", "book__title", "book__author__name")


@admin.register(AnonymousUserSession)
class AnonymousUserSessionAdmin(admin.ModelAdmin):
    list_display = ("session_key", "created_at", "expires_at", "anonymized")
    list_filter = ("anonymized", "expires_at")
    readonly_fields = ("created_at",)


@admin.register(AnonymizedReadingProfile)
class AnonymizedReadingProfileAdmin(admin.ModelAdmin):
    list_display = ("reader_type", "total_books_read", "genre_diversity_count", "source", "created_at")
    list_filter = ("reader_type", "source", "created_at")
    readonly_fields = ("created_at",)


@admin.register(AggregateAnalytics)
class AggregateAnalyticsAdmin(admin.ModelAdmin):
    readonly_fields = [f.name for f in AggregateAnalytics._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


# ──────────────────────────────────────────────
# Admin Command Runner
# ──────────────────────────────────────────────

ADMIN_COMMANDS = [
    {
        "name": "backfill_enrichment",
        "description": "Dispatch background Celery tasks to enrich books missing publish_year, genres, or Google Books data.",
        "arguments": [
            {"name": "--dry-run", "type": "flag", "label": "Dry run", "help": "Show count without dispatching tasks"},
            {"name": "--limit", "type": "int", "label": "Limit", "help": "Max books to queue"},
        ],
    },
    {
        "name": "enrich_books",
        "description": "Enrich books synchronously via Open Library and Google Books APIs.",
        "arguments": [
            {"name": "--limit", "type": "int", "label": "API limit", "help": "Google Books API request limit"},
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
        "name": "regenerate_dna",
        "description": "Regenerate genre/reader-type DNA fields from current enriched Book data. Run after enrichment backfills.",
        "arguments": [
            {"name": "--dry-run", "type": "flag", "label": "Dry run", "help": "Show changes without saving"},
            {"name": "--limit", "type": "int", "label": "Limit", "help": "Max profiles to process"},
            {"name": "--username", "type": "str", "label": "Username", "help": "Process a single user"},
        ],
    },
]

# Whitelist of allowed command names for security
ALLOWED_COMMANDS = {cmd["name"] for cmd in ADMIN_COMMANDS}


@staff_member_required
def command_runner_view(request):
    """Render the command runner page."""
    context = {
        **admin.site.each_context(request),
        "title": "Command Runner",
        "commands": ADMIN_COMMANDS,
        "commands_json": ADMIN_COMMANDS,
    }
    return render(request, "admin/command_runner.html", context)


@staff_member_required
def command_run_api(request):
    """API endpoint to trigger a management command via Celery."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    command_name = body.get("command")
    if not command_name or command_name not in ALLOWED_COMMANDS:
        return JsonResponse({"error": "Invalid or disallowed command"}, status=400)

    # Parse arguments from the request
    raw_args = body.get("arguments", {})
    cmd_config = next((c for c in ADMIN_COMMANDS if c["name"] == command_name), None)
    if not cmd_config:
        return JsonResponse({"error": "Command config not found"}, status=400)

    kwargs = {}
    for arg_def in cmd_config["arguments"]:
        arg_name = arg_def["name"].lstrip("-").replace("-", "_")
        if arg_def["type"] == "flag":
            kwargs[arg_name] = bool(raw_args.get(arg_def["name"]))
        elif arg_def["type"] == "int":
            val = raw_args.get(arg_def["name"])
            if val is not None and val != "":
                try:
                    kwargs[arg_name] = int(val)
                except (ValueError, TypeError):
                    return JsonResponse({"error": f"Invalid integer for {arg_def['name']}"}, status=400)
        elif arg_def["type"] == "str":
            val = raw_args.get(arg_def["name"])
            if val is not None and val != "":
                kwargs[arg_name] = str(val)

    from .tasks import run_management_command_task

    task = run_management_command_task.delay(command_name, kwargs=kwargs)
    logger.info(f"Admin command runner: dispatched '{command_name}' as task {task.id} by {request.user.username}")

    return JsonResponse({"task_id": task.id})


@staff_member_required
def command_result_api(request, task_id):
    """API endpoint to poll for command result."""
    from celery.result import AsyncResult

    result = AsyncResult(task_id)

    if result.ready():
        if result.successful():
            data = result.get()
            return JsonResponse({"status": "complete", "result": data})
        else:
            return JsonResponse({"status": "error", "error": str(result.result)})
    else:
        return JsonResponse({"status": "pending"})


# Patch admin site URLs to include command runner
_original_get_urls = admin.AdminSite.get_urls


def _patched_get_urls(self):
    custom_urls = [
        path("command-runner/", self.admin_view(command_runner_view), name="command_runner"),
        path("api/command-run/", self.admin_view(command_run_api), name="command_run_api"),
        path("api/command-result/<str:task_id>/", self.admin_view(command_result_api), name="command_result_api"),
    ]
    return custom_urls + _original_get_urls(self)


admin.AdminSite.get_urls = _patched_get_urls

# Patch admin sidebar to include command runner link
_original_get_app_list = admin.AdminSite.get_app_list


def _patched_get_app_list(self, request, app_label=None):
    app_list = _original_get_app_list(self, request, app_label=app_label)
    if app_label is None:
        app_list.append({
            "name": "Tools",
            "app_label": "tools",
            "app_url": "#",
            "has_module_perms": True,
            "models": [
                {
                    "name": "Command Runner",
                    "object_name": "CommandRunner",
                    "admin_url": "/admin/command-runner/",
                    "view_only": True,
                },
            ],
        })
    return app_list


admin.AdminSite.get_app_list = _patched_get_app_list
