import re

from django.contrib.auth.models import User
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils.text import slugify


class Genre(models.Model):
    name = models.CharField(max_length=100, unique=True, db_index=True)

    def __str__(self):
        return self.name


class Publisher(models.Model):
    name = models.CharField(max_length=255, unique=True)
    normalized_name = models.CharField(max_length=255, unique=True, editable=False)
    is_mainstream = models.BooleanField(default=False, db_index=True)
    # Self-referencing key to link subsidiaries (e.g., "Viking Press") to a parent ("Penguin Random House")
    parent = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True, related_name="subsidiaries")
    mainstream_last_checked = models.DateTimeField(null=True, blank=True, default=None)

    def save(self, *args, **kwargs):
        # Use the same normalization logic as Author for consistency
        self.normalized_name = Author._normalize(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ["name"]


class Author(models.Model):
    # This field stores the clean, human-readable name for display
    name = models.CharField(max_length=255, unique=True, db_index=True)

    # --- NEW FIELD ---
    # This stores a machine-readable, normalized version for lookups.
    normalized_name = models.CharField(max_length=255, unique=True, db_index=True, editable=False)

    popularity_score = models.PositiveIntegerField(default=0, db_index=True)
    is_mainstream = models.BooleanField(default=False, db_index=True)
    mainstream_last_checked = models.DateTimeField(null=True, blank=True, default=None)

    def save(self, *args, **kwargs):
        # Automatically generate the normalized name whenever the author is saved
        self.normalized_name = self._normalize(self.name)
        super().save(*args, **kwargs)

    @staticmethod
    def _normalize(name):
        # Aggressive cleaning for the lookup key
        name = name.lower()
        # Remove all punctuation except spaces
        name = re.sub(r"[^\w\s]", "", name)
        # Remove all spaces
        name = re.sub(r"\s+", "", name)
        return name

    def __str__(self):
        return self.name


class Book(models.Model):
    # --- Existing Fields ---
    title = models.CharField(max_length=255)
    author = models.ForeignKey(Author, on_delete=models.CASCADE, related_name="books")
    normalized_title = models.CharField(max_length=255, db_index=True, editable=False)

    average_rating = models.FloatField(null=True, blank=True)
    page_count = models.PositiveIntegerField(null=True, blank=True)
    publish_year = models.IntegerField(null=True, blank=True)
    publisher = models.ForeignKey(Publisher, on_delete=models.SET_NULL, null=True, blank=True, related_name="books")
    genres = models.ManyToManyField(Genre, related_name="books")

    # --- Your App-Specific Metric ---
    global_read_count = models.PositiveIntegerField(default=0, db_index=True)

    # --- Fields Merged from PopularBook ---
    isbn13 = models.CharField(
        max_length=13, null=True, blank=True, unique=True, db_index=True
    )  # Making ISBN unique is good practice

    google_books_average_rating = models.FloatField(null=True, blank=True)
    google_books_ratings_count = models.PositiveIntegerField(default=0)
    google_books_last_checked = models.DateTimeField(null=True, blank=True, default=None)

    class Meta:
        unique_together = ("title", "author")

    def save(self, *args, **kwargs):
        # Automatically populate the normalized field whenever a book is saved
        self.normalized_title = self._normalize_title(self.title)
        super().save(*args, **kwargs)

    @staticmethod
    def _normalize_title(title):
        # This is an aggressive cleaning function for reliable lookups.
        title = title.lower()

        # Find any text inside parentheses or brackets and remove it.
        title = re.sub(r"[\(\[].*?[\)\]]", "", title)

        # These lines clean up what's left
        title = re.sub(r"[^\w\s]", "", title)  # Remove remaining punctuation
        title = re.sub(r"\s+", "", title)  # Collapse whitespace
        return title.strip()

    def __str__(self):
        return f'"{self.title}" by {self.author.name}'


# This model is perfect for saving the generated DNA for authenticated users.
class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    last_updated = models.DateTimeField(auto_now=True)
    is_public = models.BooleanField(default=False)

    # --- The "Junk Drawer" for all the detailed, non-critical stats ---
    # We keep this for charts, niche details, etc.
    dna_data = models.JSONField(null=True, blank=True)

    # --- "LABELED DRAWERS" for important, queryable data ---
    # Key Summary Stats
    reader_type = models.CharField(
        max_length=100, blank=True, null=True, db_index=True
    )  # db_index=True is for performance!
    total_books_read = models.PositiveIntegerField(null=True, blank=True)

    # The Vibe Feature Data
    reading_vibe = models.JSONField(null=True, blank=True)
    vibe_data_hash = models.CharField(max_length=64, blank=True, null=True)
    pending_dna_task_id = models.CharField(max_length=255, blank=True, null=True)

    def __str__(self):
        return f"DNA for {self.user.username}"


class AggregateAnalytics(models.Model):
    """
    A singleton model to store the aggregated distribution of all user stats.
    There should only ever be one instance of this model.
    """

    total_profiles_counted = models.PositiveIntegerField(default=0)

    # We store distributions as histograms in JSONFields.
    # The keys are the "buckets" (e.g., "300-349") and values are the counts.
    avg_book_length_dist = models.JSONField(default=dict)
    avg_publish_year_dist = models.JSONField(default=dict)
    total_books_read_dist = models.JSONField(default=dict)

    # This ensures we can only ever have one row in this table.
    def save(self, *args, **kwargs):
        self.pk = 1
        super(AggregateAnalytics, self).save(*args, **kwargs)

    @classmethod
    def get_instance(cls):
        # This is a convenient way to get the single analytics object.
        obj, created = cls.objects.get_or_create(pk=1)
        return obj


@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.create(user=instance)


@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
    instance.userprofile.save()
