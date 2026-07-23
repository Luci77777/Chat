from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Custom user so we can extend it later without a painful migration."""
    bio = models.CharField(max_length=160, blank=True)
    avatar_color = models.CharField(max_length=7, default='#6C63FF')  # used for the letter-avatar fallback
    # Profile photo, hosted on Cloudinary. Empty means "no photo yet" and the
    # UI falls back to the colored letter-avatar above.
    avatar_url = models.URLField(blank=True)
    avatar_public_id = models.CharField(max_length=255, blank=True)  # Cloudinary asset id, so we can delete/replace it later

    def __str__(self):
        return self.username
