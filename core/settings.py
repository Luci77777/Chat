"""
Django settings for core project.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# --- Load .env or .env.example file automatically if present ----------------
for env_filename in ('.env', '.env.example', '.env.exmaple'):
    env_path = BASE_DIR / env_filename
    if env_path.exists():
        with open(env_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                key, val = line.split('=', 1)
                key = key.strip()
                val = val.strip().strip("'").strip('"')
                os.environ.setdefault(key, val)

# --- Security / environment -------------------------------------------------
# In production, set these as environment variables on your host.
SECRET_KEY = os.environ.get(
    'SECRET_KEY',
    'django-insecure-CHANGE-ME-before-deploying-to-production'
)

DEBUG = os.environ.get('DEBUG', 'True') == 'True'

# ALLOWED_HOSTS = os.environ.get('ALLOWED_HOSTS', '127.0.0.1,localhost').split(',')
ALLOWED_HOSTS = ['*']

SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
USE_X_FORWARDED_HOST = True

# Render.com sets this env var automatically to the app's external hostname.
RENDER_EXTERNAL_HOSTNAME = os.environ.get('RENDER_EXTERNAL_HOSTNAME')
CSRF_TRUSTED_ORIGINS = ['https://*.onrender.com']
if RENDER_EXTERNAL_HOSTNAME:
    ALLOWED_HOSTS.append(RENDER_EXTERNAL_HOSTNAME)
    CSRF_TRUSTED_ORIGINS.append(f'https://{RENDER_EXTERNAL_HOSTNAME}')

# --- Applications -------------------------------------------------------------
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    'accounts',
    'friends',
    'chat',
    'calls',
    'groupchat',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'accounts.middleware.LastSeenMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'core.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'friends.context_processors.pending_requests_count',
                'chat.context_processors.unread_messages_count',
                'groupchat.context_processors.unread_group_messages_count',
            ],
        },
    },
]

WSGI_APPLICATION = 'core.wsgi.application'

# --- Database -----------------------------------------------------------------
# Uses SQLite by default (fine for local dev / small free hosts).
# If a DATABASE_URL env var is provided (e.g. Render's free Postgres), use it
# instead, since most free web-service disks are wiped on every redeploy.
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

DATABASE_URL = os.environ.get('DATABASE_URL')
if DATABASE_URL:
    import dj_database_url
    DATABASES['default'] = dj_database_url.parse(DATABASE_URL, conn_max_age=600)

# --- Auth -----------------------------------------------------------------
AUTH_USER_MODEL = 'accounts.User'

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LOGIN_URL = 'accounts:login'
LOGIN_REDIRECT_URL = 'chat:inbox'
LOGOUT_REDIRECT_URL = 'accounts:login'

# --- I18N -----------------------------------------------------------------
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

# --- Static / media -----------------------------------------------------------
STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'
STORAGES = {
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

MEDIA_URL = 'media/'
MEDIA_ROOT = BASE_DIR / 'media'

# Free GIF/sticker search (see README for how to get a free key at klipy.com/developers).
# The app degrades gracefully — GIF/sticker search just won't return results — if this is unset.
KLIPY_API_KEY = os.environ.get('KLIPY_API_KEY', '')

# Cloudinary — storage bucket for user-uploaded profile photos (see
# accounts/cloudinary_client.py). Get free credentials at
# https://cloudinary.com/users/register/free — the free tier (25 credits/mo,
# roughly 25GB of storage+bandwidth) is plenty for avatars on a personal app.
# Without these set, avatar upload is disabled and the app just keeps using
# the colored letter-avatars — nothing else breaks.
CLOUDINARY_CLOUD_NAME = os.environ.get('CLOUDINARY_CLOUD_NAME', '')
CLOUDINARY_API_KEY = os.environ.get('CLOUDINARY_API_KEY', '')
CLOUDINARY_API_SECRET = os.environ.get('CLOUDINARY_API_SECRET', '')

if CLOUDINARY_CLOUD_NAME and CLOUDINARY_API_KEY and CLOUDINARY_API_SECRET:
    import cloudinary
    cloudinary.config(
        cloud_name=CLOUDINARY_CLOUD_NAME,
        api_key=CLOUDINARY_API_KEY,
        api_secret=CLOUDINARY_API_SECRET,
        secure=True,
    )

# Spotify "now playing" (see accounts/spotify_client.py) — register a free
# app at https://developer.spotify.com/dashboard and add the exact same
# redirect URI there as SPOTIFY_REDIRECT_URI below (Spotify rejects any
# mismatch, even trailing-slash differences). Without these set, the
# "Connect Spotify" option on the profile page just doesn't appear.
SPOTIFY_CLIENT_ID = os.environ.get('SPOTIFY_CLIENT_ID', '')
SPOTIFY_CLIENT_SECRET = os.environ.get('SPOTIFY_CLIENT_SECRET', '')
SPOTIFY_REDIRECT_URI = os.environ.get('SPOTIFY_REDIRECT_URI', '')

# Free TURN relay for voice/video calls, via Metered / Open Relay
# (see README "Voice & video calls"). Optional — without it, calls still
# work over plain STUN for most network setups, just not the toughest ones.
METERED_APP_DOMAIN = os.environ.get('METERED_APP_DOMAIN', '')  # e.g. "yourapp.metered.live"
METERED_API_KEY = os.environ.get('METERED_API_KEY', '')

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
