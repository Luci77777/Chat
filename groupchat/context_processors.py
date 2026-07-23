from .models import GroupMembership, GroupMessage


def unread_group_messages_count(request):
    if not request.user.is_authenticated:
        return {'unread_group_messages_count': 0}

    total = 0
    memberships = GroupMembership.objects.filter(user=request.user).only('group_id', 'last_read_at')
    for m in memberships:
        qs = GroupMessage.objects.filter(group_id=m.group_id).exclude(sender=request.user).exclude(
            kind=GroupMessage.KIND_SYSTEM
        )
        if m.last_read_at:
            qs = qs.filter(created_at__gt=m.last_read_at)
        total += qs.count()

    return {'unread_group_messages_count': total}
