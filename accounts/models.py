from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Custom user so we can extend it later without a painful migration."""
    bio = models.CharField(max_length=160, blank=True)
    avatar_color = models.CharField(max_length=7, default='#6C63FF')  # used for the letter-avatar

    def __str__(self):
        return self.username
