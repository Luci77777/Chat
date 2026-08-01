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

    # --- Spotify "now playing" (see accounts/spotify_client.py) ---
    # Standard OAuth2 authorization-code tokens. Access tokens are short-
    # lived (~1hr per Spotify's contract) and refreshed on demand using
    # spotify_refresh_token — see accounts.spotify_client.get_valid_access_token.
    spotify_access_token = models.CharField(max_length=255, blank=True)
    spotify_refresh_token = models.CharField(max_length=255, blank=True)
    spotify_token_expires_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.username

    @property
    def spotify_connected(self):
        return bool(self.spotify_refresh_token)


class SpotifyNowPlaying(models.Model):
    """
    A short-lived cache of what a user is currently playing, so friends
    viewing their chat/profile don't each trigger a fresh Spotify API call —
    see NOW_PLAYING_CACHE_SECONDS in spotify_client.py. One row per user;
    empty track_name means "checked recently, nothing is playing".
    """
    user = models.OneToOneField('accounts.User', related_name='now_playing', on_delete=models.CASCADE)
    track_name = models.CharField(max_length=255, blank=True)
    artist_name = models.CharField(max_length=255, blank=True)
    track_url = models.URLField(blank=True)
    album_image_url = models.URLField(blank=True)
    is_playing = models.BooleanField(default=False)
    fetched_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.user}: {self.track_name or "(nothing)"}'
