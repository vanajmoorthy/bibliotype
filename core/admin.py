from django.contrib import admin

from .models import AggregateAnalytics, Author, Book, Genre, Publisher, UserProfile


@admin.register(Author)
class AuthorAdmin(admin.ModelAdmin):
    """Customizes the display for the Author model in the admin."""

    # --- UPDATED: Added normalized_name ---
    list_display = ("name", "normalized_name", "is_mainstream", "popularity_score")
    list_editable = ("is_mainstream",)
    # --- UPDATED: Made normalized_name searchable ---
    search_fields = ("name", "normalized_name")
    ordering = ("-popularity_score", "name")


@admin.register(Publisher)
class PublisherAdmin(admin.ModelAdmin):
    list_display = ("name", "is_mainstream", "parent")
    list_editable = ("is_mainstream",)
    search_fields = ("name",)
    list_filter = ("is_mainstream", "parent")
    ordering = ("name",)


@admin.register(Book)
class BookAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "author",
        "publisher",
        "publish_year",
        "global_read_count",  # Your app's metric
        "isbn13",
        "normalized_title",
    )
    list_filter = ("publish_year", "genres")
    search_fields = ("title", "author__name", "isbn13")
    # This lets you see the read-only JSON data directly in the admin
    # This organizes the edit page into logical sections
    readonly_fields = ("google_books_average_rating", "google_books_ratings_count", "google_books_last_checked")
    fieldsets = (
        (None, {"fields": ("title", "author", "isbn13")}),
        ("Publication Info", {"fields": ("page_count", "publish_year", "publisher", "genres")}),
        ("App Metrics", {"fields": ("global_read_count",)}),  #
        (
            "Google Books Data",
            {"fields": ("google_books_average_rating", "google_books_ratings_count", "google_books_last_checked")},
        ),
    )


@admin.register(Genre)
class GenreAdmin(admin.ModelAdmin):
    search_fields = ("name",)


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    """Customizes the display for the UserProfile model."""

    list_display = ("user", "reader_type", "total_books_read", "last_updated")
    search_fields = ("user__username",)


# --- NEW: Register the AggregateAnalytics model ---
@admin.register(AggregateAnalytics)
class AggregateAnalyticsAdmin(admin.ModelAdmin):
    # Make all fields read-only because this model should only be updated by the code.
    # This prevents accidental edits in the admin panel.
    readonly_fields = [f.name for f in AggregateAnalytics._meta.fields]

    def has_add_permission(self, request):
        # Prevent anyone from adding a new row, as there should only ever be one.
        return False

    def has_delete_permission(self, request, obj=None):
        # Prevent anyone from deleting the single, critical analytics row.
        return False
