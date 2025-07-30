from django.contrib.auth.models import User
from django.db import models


# This model is perfect for saving the generated DNA for authenticated users.
class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    dna_data = models.JSONField(null=True, blank=True)
    last_updated = models.DateTimeField(auto_now=True)
    is_public = models.BooleanField(default=False)

    def __str__(self):
        return f"DNA for {self.user.username}"


# Optional but recommended: Signal to create a UserProfile when a new User is created.
from django.db.models.signals import post_save
from django.dispatch import receiver


@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.create(user=instance)


@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
    instance.userprofile.save()
