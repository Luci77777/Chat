import random

from django.conf import settings
from django.db import models

# Same warm palette accounts/forms.py uses for user letter-avatars, so group
# avatars feel consistent with the rest of the app.
PALETTE = ['#6C63FF', '#FF6584', '#2EC4B6', '#FF9F1C', '#3A86FF', '#8338EC']


class ChatGroup(models.Model):
    name = models.CharField(max_length=80)
    avatar_color = models.CharField(max_length=7, default=PALETTE[0])
    avatar_url = models.URLField(blank=True)  # optional group photo, uploaded via Cloudinary
    avatar_public_id = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name='created_groups', on_delete=models.CASCADE
    )
    created_at = models.DateTimeField(auto_now_add=True)
    members = models.ManyToManyField(
        settings.AUTH_USER_MODEL, through='GroupMembership', related_name='chat_groups'
    )

    def __str__(self):
        return self.name

    @staticmethod
    def random_color():
        return random.choice(PALETTE)

    def member_count(self):
        return self.memberships.count()


class GroupMembership(models.Model):
    group = models.ForeignKey(ChatGroup, related_name='memberships', on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='group_memberships', on_delete=models.CASCADE)
    is_admin = models.BooleanField(default=False)
    joined_at = models.DateTimeField(auto_now_add=True)
    # Rather than a per-message read-receipt row (which would mean N rows
    # per message per member), unread counts are computed as "messages in
    # this group created after last_read_at, not sent by me" — one indexed
    # query per group, same trick used for 1:1 unread counts elsewhere.
    last_read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['group', 'user'], name='unique_group_membership')
        ]

    def __str__(self):
        return f'{self.user} in {self.group}'


class GroupMessage(models.Model):
    KIND_TEXT = 'text'
    KIND_GIF = 'gif'
    KIND_STICKER = 'sticker'
    KIND_SYSTEM = 'system'  # "Alice added Bob" style notices
    KIND_CHOICES = [
        (KIND_TEXT, 'Text'),
        (KIND_GIF, 'GIF'),
        (KIND_STICKER, 'Sticker'),
        (KIND_SYSTEM, 'System'),
    ]

    group = models.ForeignKey(ChatGroup, related_name='messages', on_delete=models.CASCADE)
    # Null sender = system message (e.g. "X created the group").
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name='sent_group_messages',
        null=True, blank=True, on_delete=models.SET_NULL,
    )
    body = models.CharField(max_length=2000, blank=True)
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default=KIND_TEXT)
    media_url = models.URLField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['group', 'created_at'], name='groupchat_msg_grp_created_idx'),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(body__gt='') | ~models.Q(media_url=''),
                name='groupchat_msg_has_content',
            ),
        ]

    def __str__(self):
        preview = self.body[:30] if self.body else f'[{self.kind}]'
        who = self.sender or 'system'
        return f'{who} @ {self.group}: {preview}'
