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
