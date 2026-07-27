from django.conf import settings
from django.db import models
from django.db.models import Q


class FriendRequest(models.Model):
    from_user = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='sent_requests', on_delete=models.CASCADE)
    to_user = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='received_requests', on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['from_user', 'to_user'], name='unique_friend_request')
        ]

    def __str__(self):
        return f'{self.from_user} -> {self.to_user}'


class Friendship(models.Model):
    """
    A symmetric relationship. We always store the pair with the lower id
    first so (a, b) and (b, a) can never both exist.
    """
    user_a = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='friendships_a', on_delete=models.CASCADE)
    user_b = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='friendships_b', on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['user_a', 'user_b'], name='unique_friendship')
        ]

    @staticmethod
    def create(user1, user2):
        a, b = sorted([user1, user2], key=lambda u: u.pk)
        return Friendship.objects.get_or_create(user_a=a, user_b=b)

    @staticmethod
    def are_friends(user1, user2):
        a, b = sorted([user1, user2], key=lambda u: u.pk)
        return Friendship.objects.filter(user_a=a, user_b=b).exists()

    @staticmethod
    def friends_of(user):
        qs = Friendship.objects.filter(Q(user_a=user) | Q(user_b=user)).select_related('user_a', 'user_b')
        friends = []
        for f in qs:
            friends.append(f.user_b if f.user_a_id == user.pk else f.user_a)
        return friends

    def __str__(self):
        return f'{self.user_a} <-> {self.user_b}'


class Block(models.Model):
    """
    One-directional: if A blocks B, A stops seeing/being reachable by B, but
    B blocking A independently is a separate row. Blocking someone also
    deletes any existing Friendship/FriendRequest between them (see
    friends.views.block_user) — a block always wins over "still friends".
    """
    blocker = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='blocking', on_delete=models.CASCADE)
    blocked = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='blocked_by', on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['blocker', 'blocked'], name='unique_block')
        ]

    @staticmethod
    def blocks(user_a, user_b):
        """True if either direction has blocked the other — used to gate messaging/calls."""
        return Block.objects.filter(
            Q(blocker=user_a, blocked=user_b) | Q(blocker=user_b, blocked=user_a)
        ).exists()

    def __str__(self):
        return f'{self.blocker} blocked {self.blocked}'


class ConversationMute(models.Model):
    """Muting a 1:1 conversation: messages still arrive, they just don't count toward the unread badge."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='muted_conversations', on_delete=models.CASCADE)
    friend = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='+', on_delete=models.CASCADE)
    muted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['user', 'friend'], name='unique_conversation_mute')
        ]

    @staticmethod
    def is_muted(user, friend):
        return ConversationMute.objects.filter(user=user, friend=friend).exists()

    def __str__(self):
        return f'{self.user} muted {self.friend}'
