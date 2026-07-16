from django.conf import settings
from django.db import models


class Message(models.Model):
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='sent_messages', on_delete=models.CASCADE)
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='received_messages', on_delete=models.CASCADE)
    body = models.CharField(max_length=2000)
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

    def __str__(self):
        return f'{self.sender} -> {self.recipient}: {self.body[:30]}'
