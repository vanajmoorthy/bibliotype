from django.contrib import admin

from .models import AggregateAnalytics, Author, Book, Genre, UserProfile


@admin.register(Book)
class BookAdmin(admin.ModelAdmin):
    list_display = ("title", "author", "global_read_count", "page_count", "publish_year")
    search_fields = ("title", "author__name")
    list_filter = ("author", "genres")
    readonly_fields = ("global_read_count",)


@admin.register(Author)
class AuthorAdmin(admin.ModelAdmin):
    search_fields = ("name",)


@admin.register(Genre)
class GenreAdmin(admin.ModelAdmin):
    search_fields = ("name",)


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "is_public", "last_updated")


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
