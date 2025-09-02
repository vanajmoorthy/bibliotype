from django.contrib import admin

from .models import AggregateAnalytics, Author, Book, Genre, PopularBook, UserProfile


@admin.register(Book)
class BookAdmin(admin.ModelAdmin):
    list_display = ("title", "author", "global_read_count", "publish_year", "isbn13")
    search_fields = ("title", "author__name", "isbn13")
    list_filter = ("publish_year",)
    readonly_fields = ("global_read_count",)


@admin.register(Author)
class AuthorAdmin(admin.ModelAdmin):
    """Customizes the display for the Author model in the admin."""

    list_display = ("name", "popularity_score")  # Columns to show in the list view
    search_fields = ("name",)  # Adds a search bar for author names
    ordering = ("-popularity_score", "name")  # Default sort order


@admin.register(PopularBook)
class PopularBookAdmin(admin.ModelAdmin):
    """Customizes the display for the PopularBook model in the admin."""

    list_display = ("title", "author", "mainstream_score", "isbn13")
    search_fields = ("title", "author", "isbn13")
    list_filter = ("mainstream_score",)  # Adds a filter sidebar for the score
    ordering = ("-mainstream_score", "title")


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
