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


class Author(models.Model):
    name = models.CharField(max_length=255, unique=True, db_index=True)
    popularity_score = models.PositiveIntegerField(default=0, db_index=True)

    def __str__(self):
        return self.name


class Book(models.Model):
    title = models.CharField(max_length=255)
    author = models.ForeignKey(Author, on_delete=models.CASCADE, related_name="books")
    page_count = models.PositiveIntegerField(null=True, blank=True)
    average_rating = models.FloatField(null=True, blank=True)
    publish_year = models.IntegerField(null=True, blank=True)
    publisher = models.CharField(max_length=255, null=True, blank=True)
    genres = models.ManyToManyField(Genre, related_name="books")
    global_read_count = models.PositiveIntegerField(default=0, db_index=True)

    # --- NEW FIELD ---
    # The ISBN is the most reliable way to uniquely identify a book edition.
    isbn13 = models.CharField(max_length=13, null=True, blank=True, db_index=True)

    class Meta:
        unique_together = ("title", "author")

    def __str__(self):
        return f'"{self.title}" by {self.author.name}'


class PopularBook(models.Model):
    title = models.CharField(max_length=255)
    author = models.CharField(max_length=255)
    isbn13 = models.CharField(max_length=13, null=True, blank=True, unique=True)
    mainstream_score = models.IntegerField(default=0)

    # Google Books Data
    average_rating = models.FloatField(null=True, blank=True)
    ratings_count = models.PositiveIntegerField(default=0)

    # NYT Bestseller Data
    nyt_bestseller_weeks = models.PositiveIntegerField(default=0)

    # Awards Data (using JSONField to store a list of strings)
    awards_won = models.JSONField(default=list)
    shortlists = models.JSONField(default=list)

    score_breakdown = models.JSONField(default=dict)
    # A unique key to prevent duplicate book/author pairs
    lookup_key = models.CharField(max_length=512, unique=True, editable=False)

    def save(self, *args, **kwargs):
        self.lookup_key = self.generate_lookup_key(self.title, self.author)
        super().save(*args, **kwargs)

    @staticmethod
    def generate_lookup_key(title, author):
        return slugify(f"{title}-{author}")

    def __str__(self):
        return f'"{self.title}" by {self.author}'


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
