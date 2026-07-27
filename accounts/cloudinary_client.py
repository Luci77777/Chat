"""
Thin wrapper around Cloudinary's upload API for profile photos.

Cloudinary is used as the storage bucket for user-uploaded avatars so we
don't need persistent local disk (most free hosts wipe it on every
redeploy) and get resizing/optimization for free.

Configure with three env vars — from your Cloudinary dashboard:
    CLOUDINARY_CLOUD_NAME
    CLOUDINARY_API_KEY
    CLOUDINARY_API_SECRET

Without them set, is_configured() returns False and the profile view shows
a friendly error instead of crashing, so the rest of the app keeps working.
"""
import uuid

import cloudinary
import cloudinary.uploader
from django.conf import settings

# Keep avatars small and square — this is a chat-avatar photo, not a
# full-resolution upload. Cloudinary does the resize/crop server-side so we
# never store (or re-download) a huge original.
AVATAR_TRANSFORM = [
    {'width': 512, 'height': 512, 'crop': 'fill', 'gravity': 'face'},
    {'quality': 'auto', 'fetch_format': 'auto'},
]


def is_configured():
    return bool(
        getattr(settings, 'CLOUDINARY_CLOUD_NAME', '')
        and getattr(settings, 'CLOUDINARY_API_KEY', '')
        and getattr(settings, 'CLOUDINARY_API_SECRET', '')
    )


class CloudinaryError(Exception):
    pass


def upload_avatar(file_obj, user):
    """
    Uploads an image file to Cloudinary under a per-user folder/public_id
    (so re-uploading overwrites the old photo instead of littering the
    account with orphaned assets). Returns (secure_url, public_id).
    """
    if not is_configured():
        raise CloudinaryError('Cloudinary is not configured on this server.')

    public_id = f'pingback/avatars/user_{user.pk}'
    try:
        result = cloudinary.uploader.upload(
            file_obj,
            public_id=public_id,
            overwrite=True,
            invalidate=True,
            transformation=AVATAR_TRANSFORM,
            folder=None,  # public_id already namespaces the asset
        )
    except Exception as exc:  # cloudinary raises its own Error subclasses
        raise CloudinaryError(str(exc)) from exc

    return result.get('secure_url', ''), result.get('public_id', public_id)


def delete_avatar(public_id):
    """Best-effort delete — never raises, since a failed cleanup shouldn't block the user."""
    if not public_id or not is_configured():
        return
    try:
        cloudinary.uploader.destroy(public_id, invalidate=True)
    except Exception:
        pass


def upload_group_photo(file_obj, group_id):
    """Same idea as upload_avatar, but keyed by group id so re-uploading replaces the old photo."""
    if not is_configured():
        raise CloudinaryError('Cloudinary is not configured on this server.')
    public_id = f'pingback/group_avatars/group_{group_id}'
    try:
        result = cloudinary.uploader.upload(
            file_obj, public_id=public_id, overwrite=True, invalidate=True,
            transformation=AVATAR_TRANSFORM, folder=None,
        )
    except Exception as exc:
        raise CloudinaryError(str(exc)) from exc
    return result.get('secure_url', ''), result.get('public_id', public_id)


# Chat media (voice notes, shared files) share a namespace and are named by
# a random token rather than by user id, since one person can send many of
# them and each needs its own asset (unlike the one-avatar-per-user case).
MAX_CHAT_FILE_BYTES = 25 * 1024 * 1024  # 25MB — generous for a free-tier Cloudinary account


def upload_chat_voice(file_obj, sender_id):
    """
    Uploads a recorded voice note. Cloudinary files audio under its 'video'
    resource type (there's no separate 'audio' type). Returns (secure_url, public_id).
    """
    if not is_configured():
        raise CloudinaryError('Cloudinary is not configured on this server.')
    public_id = f'pingback/voice/{sender_id}_{uuid.uuid4().hex[:12]}'
    try:
        result = cloudinary.uploader.upload(
            file_obj, public_id=public_id, resource_type='video', overwrite=False,
        )
    except Exception as exc:
        raise CloudinaryError(str(exc)) from exc
    return result.get('secure_url', ''), result.get('public_id', public_id)


def upload_chat_file(file_obj, sender_id):
    """
    Uploads an arbitrary shared file (pdf, zip, docx, ...). resource_type='auto'
    lets Cloudinary route images/video/raw appropriately. Returns (secure_url, public_id).
    """
    if not is_configured():
        raise CloudinaryError('Cloudinary is not configured on this server.')
    public_id = f'pingback/files/{sender_id}_{uuid.uuid4().hex[:12]}'
    try:
        result = cloudinary.uploader.upload(
            file_obj, public_id=public_id, resource_type='auto', overwrite=False,
        )
    except Exception as exc:
        raise CloudinaryError(str(exc)) from exc
    return result.get('secure_url', ''), result.get('public_id', public_id)


def delete_chat_media(public_id, resource_type='auto'):
    """Best-effort delete for a chat attachment (voice note or file) — never raises."""
    if not public_id or not is_configured():
        return
    try:
        cloudinary.uploader.destroy(public_id, resource_type=resource_type, invalidate=True)
    except Exception:
        pass
