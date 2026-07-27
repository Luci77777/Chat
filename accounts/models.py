from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Custom user so we can extend it later without a painful migration."""
    THEME_LIGHT = 'light'
    THEME_DARK = 'dark'
    THEME_CHOICES = [(THEME_LIGHT, 'Light'), (THEME_DARK, 'Dark')]

    bio = models.CharField(max_length=160, blank=True)
    avatar_color = models.CharField(max_length=7, default='#6C63FF')  # used for the letter-avatar fallback
    # Profile photo, hosted on Cloudinary. Empty means "no photo yet" and the
    # UI falls back to the colored letter-avatar above.
    avatar_url = models.URLField(blank=True)
    avatar_public_id = models.CharField(max_length=255, blank=True)  # Cloudinary asset id, so we can delete/replace it later

    # Touched by accounts.middleware.LastSeenMiddleware on every authenticated
    # request (throttled — see that file), which is what "online" / "last
    # seen" is computed from. No separate presence system needed.
    last_seen_at = models.DateTimeField(null=True, blank=True)

    theme_preference = models.CharField(max_length=5, choices=THEME_CHOICES, default=THEME_LIGHT)

    def __str__(self):
        return self.username
