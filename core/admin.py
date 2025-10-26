from django.contrib import admin
from django import forms
from django.forms import ModelMultipleChoiceField

from .models import AggregateAnalytics, Author, Book, Genre, Publisher, UserProfile


class BookAdminForm(forms.ModelForm):
    """Custom form for managing book genres"""
    
    class Meta:
        model = Book
        fields = '__all__'
        
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Make the genres field readonly (display-only) initially
        # Users will see their current genres but can't edit them in the standard way
        # They can still use the admin interface's built-in add/delete buttons
        pass


@admin.register(Author)
class AuthorAdmin(admin.ModelAdmin):
    list_display = ("name", "normalized_name", "is_mainstream", "popularity_score")
    list_editable = ("is_mainstream",)
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
    form = BookAdminForm
    filter_horizontal = ("genres",)  # This provides a better UI for ManyToMany fields
    
    list_display = (
        "title",
        "author",
        "publisher",
        "publish_year",
        "global_read_count",
        "isbn13",
        "normalized_title",
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


@admin.register(Genre)
class GenreAdmin(admin.ModelAdmin):
    search_fields = ("name",)


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "reader_type", "total_books_read", "last_updated")
    search_fields = ("user__username",)


@admin.register(AggregateAnalytics)
class AggregateAnalyticsAdmin(admin.ModelAdmin):
    readonly_fields = [f.name for f in AggregateAnalytics._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
