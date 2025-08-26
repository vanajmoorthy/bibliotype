from django.contrib.auth.models import User
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver


class Genre(models.Model):
    name = models.CharField(max_length=100, unique=True, db_index=True)

    def __str__(self):
        return self.name


class Author(models.Model):
    name = models.CharField(max_length=255, unique=True, db_index=True)

    def __str__(self):
        return self.name


class Book(models.Model):
    # Core Details
    title = models.CharField(max_length=255)
    author = models.ForeignKey(Author, on_delete=models.CASCADE, related_name="books")
    page_count = models.PositiveIntegerField(null=True, blank=True)
    average_rating = models.FloatField(null=True, blank=True)  # The global average (Goodreads, etc.)
    publish_year = models.IntegerField(null=True, blank=True)
    publisher = models.CharField(max_length=255, null=True, blank=True)

    # Relationships
    genres = models.ManyToManyField(Genre, related_name="books")

    # --- THE MAGIC FIELD FOR YOUR COMMUNITY STATS ---
    # Every time a user's CSV contains this book, we will increment this counter.
    global_read_count = models.PositiveIntegerField(default=0, db_index=True)

    class Meta:
        # A book is defined as a unique combination of a title and an author
        unique_together = ("title", "author")

    def __str__(self):
        return f'"{self.title}" by {self.author.name}'


# This model is perfect for saving the generated DNA for authenticated users.
class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    dna_data = models.JSONField(null=True, blank=True)
    last_updated = models.DateTimeField(auto_now=True)
    is_public = models.BooleanField(default=False)

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
