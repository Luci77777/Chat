from friends.models import ConversationMute
from .models import Message


def unread_messages_count(request):
    if not request.user.is_authenticated:
        return {'unread_messages_count': 0}

    muted_friend_ids = ConversationMute.objects.filter(user=request.user).values_list('friend_id', flat=True)
    count = (
        Message.objects.filter(recipient=request.user, is_read=False, deleted_at__isnull=True)
        .exclude(sender_id__in=muted_friend_ids)
        .count()
    )
    return {'unread_messages_count': count}
