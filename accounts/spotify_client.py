"""
Spotify "now playing" integration — OAuth2 (authorization code flow) so we
can read what someone is currently listening to and show it next to their
presence dot.

Setup (three env vars, same pattern as Cloudinary/Klipy):
    SPOTIFY_CLIENT_ID
    SPOTIFY_CLIENT_SECRET
    SPOTIFY_REDIRECT_URI      e.g. https://yourapp.onrender.com/accounts/spotify/callback/

Register the app at https://developer.spotify.com/dashboard, add the exact
redirect URI there (Spotify rejects any mismatch), and request the
`user-read-currently-playing` + `user-read-playback-state` scopes.

Without these three env vars set, is_configured() is False and the profile
page just doesn't show the "Connect Spotify" option — nothing else breaks.
"""
import time
import urllib.parse

import requests
from django.conf import settings
from django.urls import reverse
from django.utils import timezone

AUTHORIZE_URL = 'https://accounts.spotify.com/authorize'
TOKEN_URL = 'https://accounts.spotify.com/api/token'
CURRENTLY_PLAYING_URL = 'https://api.spotify.com/v1/me/player/currently-playing'
SCOPES = 'user-read-currently-playing user-read-playback-state'
REQUEST_TIMEOUT = 5  # seconds — this feeds a small UI widget, fail fast rather than hang a page load

# How long a cached SpotifyNowPlaying row is trusted before we bother
# Spotify again. Keeps a friend's chat window open in the background from
# hammering the API — Spotify's own client only updates every few seconds
# anyway, so this is not a noticeable staleness trade.
NOW_PLAYING_CACHE_SECONDS = 20

# Refresh the access token this many seconds before it actually expires, so
# a request never gets a "just expired" 401 mid-flight.
TOKEN_REFRESH_SKEW_SECONDS = 60


class SpotifyError(Exception):
    pass


def get_redirect_uri(request=None):
    """
    Returns the redirect URI for Spotify OAuth.
    If a request is provided, automatically uses request.build_absolute_uri()
    whenever the configured SPOTIFY_REDIRECT_URI does not match the current request host
    (e.g., when testing locally on 127.0.0.1 while .env contains the Render URL).
    """
    if request:
        dynamic_uri = request.build_absolute_uri(reverse('accounts:spotify_callback'))
        configured_uri = getattr(settings, 'SPOTIFY_REDIRECT_URI', '').strip()
        if not configured_uri:
            return dynamic_uri

        parsed_configured = urllib.parse.urlparse(configured_uri)
        if parsed_configured.netloc and parsed_configured.netloc != request.get_host():
            return dynamic_uri

        return configured_uri

    return getattr(settings, 'SPOTIFY_REDIRECT_URI', '').strip()


def is_configured():
    client_id = getattr(settings, 'SPOTIFY_CLIENT_ID', '').strip()
    client_secret = getattr(settings, 'SPOTIFY_CLIENT_SECRET', '').strip()
    return bool(client_id and client_secret)


def build_authorize_url(state, request=None):
    redirect_uri = get_redirect_uri(request)
    params = {
        'client_id': settings.SPOTIFY_CLIENT_ID,
        'response_type': 'code',
        'redirect_uri': redirect_uri,
        'scope': SCOPES,
        'state': state,
        'show_dialog': 'false',
    }
    return f'{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}'


def exchange_code_for_tokens(code, request=None):
    """Authorization code -> {access_token, refresh_token, expires_in}. Raises SpotifyError on failure."""
    redirect_uri = get_redirect_uri(request)
    try:
        res = requests.post(
            TOKEN_URL,
            data={
                'grant_type': 'authorization_code',
                'code': code,
                'redirect_uri': redirect_uri,
            },
            auth=(settings.SPOTIFY_CLIENT_ID, settings.SPOTIFY_CLIENT_SECRET),
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise SpotifyError(str(exc)) from exc
    if res.status_code != 200:
        raise SpotifyError(f'token exchange failed ({res.status_code}): {res.text[:200]}')
    return res.json()


def _refresh_tokens(refresh_token):
    """refresh_token -> {access_token, expires_in, (maybe) refresh_token}. Raises SpotifyError on failure."""
    try:
        res = requests.post(
            TOKEN_URL,
            data={'grant_type': 'refresh_token', 'refresh_token': refresh_token},
            auth=(settings.SPOTIFY_CLIENT_ID, settings.SPOTIFY_CLIENT_SECRET),
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise SpotifyError(str(exc)) from exc
    if res.status_code != 200:
        raise SpotifyError(f'token refresh failed ({res.status_code}): {res.text[:200]}')
    return res.json()


def get_valid_access_token(user):
    """
    Returns a currently-valid access token for `user`, transparently
    refreshing (and persisting the refresh) if the stored one has expired
    or is about to. Returns None if the user never connected Spotify, or if
    the refresh itself fails (e.g. they revoked access on Spotify's end) —
    callers should treat None the same as "nothing playing".
    """
    if not user.spotify_refresh_token:
        return None

    now = timezone.now()
    still_valid = (
        user.spotify_access_token
        and user.spotify_token_expires_at
        and (user.spotify_token_expires_at - now).total_seconds() > TOKEN_REFRESH_SKEW_SECONDS
    )
    if still_valid:
        return user.spotify_access_token

    try:
        data = _refresh_tokens(user.spotify_refresh_token)
    except SpotifyError:
        return None

    user.spotify_access_token = data['access_token']
    user.spotify_token_expires_at = now + timezone.timedelta(seconds=data.get('expires_in', 3600))
    # Spotify only sometimes rotates the refresh token on refresh — keep the old one if it didn't.
    if data.get('refresh_token'):
        user.spotify_refresh_token = data['refresh_token']
    user.save(update_fields=['spotify_access_token', 'spotify_token_expires_at', 'spotify_refresh_token'])

    return user.spotify_access_token


def fetch_currently_playing(access_token):
    """
    Returns a dict (track_name, artist_name, track_url, album_image_url,
    is_playing) or None if nothing is playing / the track is a local file
    Spotify won't give us a URL for. Raises SpotifyError on a genuine API
    failure (as opposed to "nothing playing", which isn't an error).
    """
    try:
        res = requests.get(
            CURRENTLY_PLAYING_URL,
            headers={'Authorization': f'Bearer {access_token}'},
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise SpotifyError(str(exc)) from exc

    if res.status_code == 204:  # nothing currently playing
        return None
    if res.status_code == 401:
        raise SpotifyError('access token rejected')
    if res.status_code != 200:
        raise SpotifyError(f'currently-playing failed ({res.status_code}): {res.text[:200]}')

    data = res.json()
    item = data.get('item')
    if not item:
        return None

    artists = ', '.join(a.get('name', '') for a in item.get('artists', []) if a.get('name'))
    images = (item.get('album') or {}).get('images') or []
    # Spotify lists images largest-first; the smallest is plenty for a tiny widget.
    album_image_url = images[-1]['url'] if images else ''

    return {
        'track_name': item.get('name', ''),
        'artist_name': artists,
        'track_url': (item.get('external_urls') or {}).get('spotify', ''),
        'album_image_url': album_image_url,
        'is_playing': bool(data.get('is_playing')),
    }


def get_now_playing_cached(user):
    """
    The function everything else should call. Returns the dict shape from
    fetch_currently_playing() (or None), using the SpotifyNowPlaying cache
    row to avoid hitting Spotify more than once every
    NOW_PLAYING_CACHE_SECONDS per user — see that constant's docstring.
    """
    from .models import SpotifyNowPlaying  # local import: avoids a models<->client import cycle

    if not user.spotify_connected:
        return None

    cache, created = SpotifyNowPlaying.objects.get_or_create(user=user)
    # A brand-new row's fetched_at is set by auto_now=True at creation time,
    # which would otherwise look "fresh" and short-circuit below without
    # ever actually asking Spotify — `created` is what tells them apart.
    if not created:
        age = (timezone.now() - cache.fetched_at).total_seconds()
        if age < NOW_PLAYING_CACHE_SECONDS:
            return _cache_row_to_dict(cache)

    token = get_valid_access_token(user)
    if not token:
        return _cache_row_to_dict(cache)  # stale but better than nothing if Spotify's down

    try:
        result = fetch_currently_playing(token)
    except SpotifyError:
        return _cache_row_to_dict(cache)

    if result:
        cache.track_name = result['track_name']
        cache.artist_name = result['artist_name']
        cache.track_url = result['track_url']
        cache.album_image_url = result['album_image_url']
        cache.is_playing = result['is_playing']
    else:
        cache.track_name = ''
        cache.artist_name = ''
        cache.track_url = ''
        cache.album_image_url = ''
        cache.is_playing = False
    cache.save()

    return _cache_row_to_dict(cache)


def _cache_row_to_dict(cache):
    if not cache.track_name or not cache.is_playing:
        return None
    return {
        'track_name': cache.track_name,
        'artist_name': cache.artist_name,
        'track_url': cache.track_url,
        'album_image_url': cache.album_image_url,
        'is_playing': cache.is_playing,
    }
