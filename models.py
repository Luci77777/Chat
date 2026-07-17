from django.conf import settings
from django.db import models


class Message(models.Model):
    KIND_TEXT = 'text'
    KIND_GIF = 'gif'
    KIND_STICKER = 'sticker'
    KIND_CHOICES = [
        (KIND_TEXT, 'Text'),
        (KIND_GIF, 'GIF'),
        (KIND_STICKER, 'Sticker'),
    ]

    sender = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='sent_messages', on_delete=models.CASCADE)
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='received_messages', on_delete=models.CASCADE)
    # For text messages this is the message itself. For GIF/sticker messages
    # it's optional caption text (usually empty) — the media_url carries the content.
    body = models.CharField(max_length=2000, blank=True)
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default=KIND_TEXT)
    media_url = models.URLField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
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
            # Every message needs either text or a media attachment — never neither.
            models.CheckConstraint(
                condition=models.Q(body__gt='') | ~models.Q(media_url=''),
                name='chat_msg_has_content',
            ),
        ]

    def __str__(self):
        preview = self.body[:30] if self.body else f'[{self.kind}]'
        return f'{self.sender} -> {self.recipient}: {preview}'
