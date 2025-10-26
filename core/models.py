import re

from django.contrib.auth.models import User
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver


class Genre(models.Model):
    name = models.CharField(max_length=100, unique=True, db_index=True)

    def __str__(self):
        return self.name


class Publisher(models.Model):
    name = models.CharField(max_length=255, unique=True)
    normalized_name = models.CharField(max_length=255, unique=True, editable=False)
    is_mainstream = models.BooleanField(default=False, db_index=True)
    parent = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True, related_name="subsidiaries")
    mainstream_last_checked = models.DateTimeField(null=True, blank=True, default=None)

    def save(self, *args, **kwargs):
        self.normalized_name = Author._normalize(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ["name"]


class Author(models.Model):
    name = models.CharField(max_length=255, unique=True, db_index=True)
    normalized_name = models.CharField(max_length=255, unique=True, db_index=True, editable=False)

    popularity_score = models.PositiveIntegerField(default=0, db_index=True)
    is_mainstream = models.BooleanField(default=False, db_index=True)
    mainstream_last_checked = models.DateTimeField(null=True, blank=True, default=None)

    def save(self, *args, **kwargs):
        self.normalized_name = self._normalize(self.name)
        super().save(*args, **kwargs)

    @staticmethod
    def _normalize(name):
        name = name.lower()
        name = re.sub(r"[^\w\s]", "", name)
        name = re.sub(r"\s+", "", name)
        return name

    def __str__(self):
        return self.name


class Book(models.Model):
    title = models.CharField(max_length=255)
    author = models.ForeignKey(Author, on_delete=models.CASCADE, related_name="books")
    normalized_title = models.CharField(max_length=255, db_index=True, editable=False)

    average_rating = models.FloatField(null=True, blank=True)
    page_count = models.PositiveIntegerField(null=True, blank=True)
    publish_year = models.IntegerField(null=True, blank=True)
    publisher = models.ForeignKey(Publisher, on_delete=models.SET_NULL, null=True, blank=True, related_name="books")
    genres = models.ManyToManyField(Genre, related_name="books")

    global_read_count = models.PositiveIntegerField(default=0, db_index=True)

    isbn13 = models.CharField(max_length=13, null=True, blank=True, unique=True, db_index=True)

    google_books_average_rating = models.FloatField(null=True, blank=True)
    google_books_ratings_count = models.PositiveIntegerField(default=0)
    google_books_last_checked = models.DateTimeField(null=True, blank=True, default=None)

    class Meta:
        unique_together = ("normalized_title", "author")

    def save(self, *args, **kwargs):
        self.normalized_title = self._normalize_title(self.title)
        super().save(*args, **kwargs)

    @staticmethod
    def _normalize_title(title):
        title = title.lower()
        title = re.sub(r"[\(\[].*?[\)\]]", "", title)
        title = re.sub(r"[^\w\s]", "", title)
        title = re.sub(r"\s+", "", title)
        return title.strip()

    def __str__(self):
        return f'"{self.title}" by {self.author.name}'


class UserBook(models.Model):
    """Stores the relationship between users and books they've read"""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="user_books")
    book = models.ForeignKey(Book, on_delete=models.CASCADE, related_name="readers")
    
    # Data from CSV
    user_rating = models.IntegerField(null=True, blank=True)  # 1-5 stars
    user_review = models.TextField(blank=True, null=True)
    date_read = models.DateTimeField(null=True, blank=True)
    date_added = models.DateTimeField(auto_now_add=True)
    
    # Computed fields for top books
    is_top_book = models.BooleanField(default=False, db_index=True)
    top_book_position = models.IntegerField(null=True, blank=True)
    
    class Meta:
        unique_together = ("user", "book")
        indexes = [
            models.Index(fields=["user", "is_top_book"]),
            models.Index(fields=["book", "is_top_book"]),
        ]
    
    def __str__(self):
        return f"{self.user.username} - {self.book.title}"


class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    last_updated = models.DateTimeField(auto_now=True)
    is_public = models.BooleanField(default=False)

    dna_data = models.JSONField(null=True, blank=True)
    reader_type = models.CharField(max_length=100, blank=True, null=True, db_index=True)
    total_books_read = models.PositiveIntegerField(null=True, blank=True)
    reading_vibe = models.JSONField(null=True, blank=True)
    vibe_data_hash = models.CharField(max_length=64, blank=True, null=True)
    pending_dna_task_id = models.CharField(max_length=255, blank=True, null=True)
    
    # New field for privacy setting
    visible_in_recommendations = models.BooleanField(
        default=True, 
        help_text="Allow other users to see you as a recommendation source"
    )

    def get_top_books(self, limit=5):
        """Returns user's top books ordered by position"""
        return UserBook.objects.filter(
            user=self.user, 
            is_top_book=True
        ).select_related('book', 'book__author').order_by('top_book_position')[:limit]
    
    def __str__(self):
        return f"DNA for {self.user.username}"


class AnonymousUserSession(models.Model):
    """Temporary storage for active anonymous sessions"""
    session_key = models.CharField(max_length=40, unique=True, db_index=True)
    dna_data = models.JSONField()
    
    # Store book IDs and distributions
    books_data = models.JSONField(default=list)  # List of book IDs
    top_books_data = models.JSONField(default=list)  # Top 5 book IDs
    genre_distribution = models.JSONField(default=dict)  # {"genre": count}
    author_distribution = models.JSONField(default=dict)  # {"normalized_author": count}
    
    # Metadata
    anonymized = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)
    
    class Meta:
        indexes = [
            models.Index(fields=["session_key", "expires_at"]),
            models.Index(fields=["expires_at", "anonymized"]),
        ]
    
    def is_expired(self):
        from django.utils import timezone
        return timezone.now() > self.expires_at
    
    def __str__(self):
        return f"Anonymous session: {self.session_key}"


class AnonymizedReadingProfile(models.Model):
    """Permanently stored, anonymized reading profile for comparison"""
    
    # Reading patterns (no identifiers)
    total_books_read = models.PositiveIntegerField()
    reader_type = models.CharField(max_length=100, db_index=True)
    
    # Distributions
    genre_distribution = models.JSONField(default=dict)
    author_distribution = models.JSONField(default=dict)
    
    # Statistics
    average_rating = models.FloatField(null=True, blank=True)
    avg_book_length = models.IntegerField(null=True, blank=True)
    avg_publish_year = models.IntegerField(null=True, blank=True)
    mainstream_score = models.IntegerField(null=True, blank=True)
    genre_diversity_count = models.PositiveIntegerField(default=0)
    
    # Top books (just IDs)
    top_book_ids = models.JSONField(default=list)
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    source = models.CharField(max_length=20, default="anonymous")
    
    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['reader_type', 'created_at']),
            models.Index(fields=['total_books_read']),
        ]
    
    def __str__(self):
        return f"AnonymizedProfile: {self.reader_type} ({self.total_books_read} books)"


class AggregateAnalytics(models.Model):
    total_profiles_counted = models.PositiveIntegerField(default=0)
    avg_book_length_dist = models.JSONField(default=dict)
    avg_publish_year_dist = models.JSONField(default=dict)
    total_books_read_dist = models.JSONField(default=dict)

    def save(self, *args, **kwargs):
        self.pk = 1
        super(AggregateAnalytics, self).save(*args, **kwargs)

    @classmethod
    def get_instance(cls):
        obj, created = cls.objects.get_or_create(pk=1)
        return obj


@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.create(user=instance)


@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
    instance.userprofile.save()
