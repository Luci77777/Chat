from django.conf import settings
from django.db import models

# Shared with groupchat/models.py's GroupMessage — kept as a plain list
# rather than a shared import to avoid coupling the two apps together for
# what's just a handful of string constants.
EFFECT_CHOICES = [
    ('', 'None'),
    ('confetti', 'Confetti'),
    ('balloons', 'Balloons'),
    ('fireworks', 'Fireworks'),
    ('slam', 'Slam'),
    ('loud', 'Loud'),
]


class Message(models.Model):
    KIND_TEXT = 'text'
    KIND_GIF = 'gif'
    KIND_STICKER = 'sticker'
    KIND_VOICE = 'voice'
    KIND_FILE = 'file'
    KIND_CHOICES = [
        (KIND_TEXT, 'Text'),
        (KIND_GIF, 'GIF'),
        (KIND_STICKER, 'Sticker'),
        (KIND_VOICE, 'Voice message'),
        (KIND_FILE, 'File'),
    ]

    sender = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='sent_messages', on_delete=models.CASCADE)
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='received_messages', on_delete=models.CASCADE)
    # For text messages this is the message itself. For GIF/sticker/voice/file
    # messages it's optional caption text (usually empty) — the media_url
    # carries the content.
    body = models.CharField(max_length=2000, blank=True)
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default=KIND_TEXT)
    media_url = models.URLField(blank=True)
    media_public_id = models.CharField(max_length=255, blank=True)  # Cloudinary asset id, for cleanup on delete
    file_name = models.CharField(max_length=255, blank=True)  # original filename, for KIND_FILE
    file_size = models.PositiveIntegerField(null=True, blank=True)  # bytes, for KIND_FILE
    duration_seconds = models.PositiveIntegerField(null=True, blank=True)  # for KIND_VOICE
    # Full-screen effect (confetti/balloons/...) played client-side the
    # moment this message is delivered live — see static/js/effects.js.
    # Only applies to text messages; blank = no effect.
    effect = models.CharField(max_length=12, blank=True, choices=EFFECT_CHOICES)

    reply_to = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.SET_NULL, related_name='replies'
    )

    created_at = models.DateTimeField(auto_now_add=True)
    edited_at = models.DateTimeField(null=True, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)  # soft delete — body/media cleared, row kept for thread integrity
    is_read = models.BooleanField(default=False)

    class Meta:
        ordering = ['created_at']
        indexes = [
            # Speeds up "how many unread messages does this user have" and
            # "what's the latest message in this conversation" — both are
            # run on every notification poll and every inbox load.
            models.Index(fields=['recipient', 'is_read'], name='chat_msg_recipient_read_idx'),
            models.Index(fields=['sender', 'recipient', 'created_at'], name='chat_msg_convo_idx'),
        ]
        constraints = [
            # Every message needs either text or a media attachment — never
            # neither. Deleted messages are the one exception (both get
            # cleared on delete), so this only applies while not deleted.
            models.CheckConstraint(
                check=models.Q(deleted_at__isnull=False) | models.Q(body__gt='') | ~models.Q(media_url=''),
                name='chat_msg_has_content',
            ),
        ]

    def __str__(self):
        if self.deleted_at:
            return f'{self.sender} -> {self.recipient}: [deleted]'
        preview = self.body[:30] if self.body else f'[{self.kind}]'
        return f'{self.sender} -> {self.recipient}: {preview}'


class MessageReaction(models.Model):
    """One emoji reaction per user per message — reacting again with a different emoji replaces it."""
    message = models.ForeignKey(Message, related_name='reactions', on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='+', on_delete=models.CASCADE)
    emoji = models.CharField(max_length=8)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['message', 'user'], name='unique_message_reaction')
        ]

    def __str__(self):
        return f'{self.user} reacted {self.emoji} to message#{self.message_id}'


class TypingStatus(models.Model):
    """
    "user is typing to friend" — upserted on every keystroke (client-side
    debounced to ~2s) and read back with a freshness check (see
    TYPING_FRESH_SECONDS in chat/views.py) rather than deleted when typing
    stops, so there's no extra request needed just to clear it.
    """
    user = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='+', on_delete=models.CASCADE)
    friend = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='+', on_delete=models.CASCADE)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['user', 'friend'], name='unique_typing_status')
        ]

    def __str__(self):
        return f'{self.user} typing to {self.friend} @ {self.updated_at}'


# ---------------------------------------------------------------------------
# Per-conversation personalization: a shared theme for the pair (whoever
# sets it, both people see it), plus a private nickname (only the person who
# set it sees it — like WhatsApp/Discord contact nicknames, not a display
# name either party can force on the other).
# ---------------------------------------------------------------------------

THEME_PRESET_CHOICES = [
    ('default', 'Default'),
    ('sunset', 'Sunset'),
    ('ocean', 'Ocean'),
    ('forest', 'Forest'),
    ('berry', 'Berry'),
    ('midnight', 'Midnight'),
    ('gold', 'Gold'),
]


class ChatTheme(models.Model):
    """
    One row per friend pair, keyed the same "lower pk first" way
    friends.Friendship keys user_a/user_b, so there's exactly one shared
    theme for a conversation no matter who opens the settings page.
    """
    user_a = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='+', on_delete=models.CASCADE)
    user_b = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='+', on_delete=models.CASCADE)
    preset = models.CharField(max_length=20, choices=THEME_PRESET_CHOICES, default='default')
    # Optional custom wallpaper photo, uploaded via Cloudinary — takes over
    # the chat background regardless of preset when set.
    wallpaper_url = models.URLField(blank=True)
    wallpaper_public_id = models.CharField(max_length=255, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['user_a', 'user_b'], name='unique_chat_theme_pair')
        ]

    @staticmethod
    def for_pair(user1, user2):
        a, b = sorted([user1, user2], key=lambda u: u.pk)
        obj, _ = ChatTheme.objects.get_or_create(user_a=a, user_b=b)
        return obj

    def __str__(self):
        return f'theme({self.user_a}, {self.user_b}) = {self.preset}'


class ContactNickname(models.Model):
    """owner's private label for friend — never visible to friend."""
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='+', on_delete=models.CASCADE)
    friend = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='+', on_delete=models.CASCADE)
    nickname = models.CharField(max_length=40, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['owner', 'friend'], name='unique_contact_nickname')
        ]

    def __str__(self):
        return f'{self.owner} calls {self.friend} "{self.nickname}"'
