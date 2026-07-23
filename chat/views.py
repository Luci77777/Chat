from urllib.parse import urlparse

from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST

from friends.models import Friendship
from . import klipy
from .models import Message

User = get_user_model()

# Media messages must point at Klipy's own CDN — this keeps send_message from
# being usable as an open image-hotlinking proxy for arbitrary URLs.
ALLOWED_MEDIA_HOSTS_SUFFIX = '.klipy.com'


def _preview_text(message):
    if message is None:
        return None
    if message.kind == Message.KIND_GIF:
        return '🎬 GIF'
    if message.kind == Message.KIND_STICKER:
        return '🏷️ Sticker'
    return message.body


def _conversation_summaries(user, friends):
    """
    Builds {friend_id: {'last_msg': Message|None, 'unread': int}} for every
    friend using a constant number of queries (2), instead of looping and
    issuing 2 queries per friend. This is what keeps the inbox (and its
    background poll) fast even as someone's friend list grows.
    """
    if not friends:
        return {}

    friend_ids = [f.pk for f in friends]

    # 1) Unread counts, grouped by sender, in one aggregate query.
    unread_rows = (
        Message.objects.filter(recipient=user, sender_id__in=friend_ids, is_read=False)
        .values('sender_id')
        .annotate(cnt=Count('id'))
    )
    unread_map = {row['sender_id']: row['cnt'] for row in unread_rows}

    # 2) Latest message per conversation. Walk all relevant messages newest
    # -> oldest once and keep the first one seen per friend; this uses the
    # (sender, recipient, created_at) index and stops as soon as every
    # conversation has its most recent message.
    last_map = {}
    qs = (
        Message.objects.filter(
            Q(sender=user, recipient_id__in=friend_ids) | Q(sender_id__in=friend_ids, recipient=user)
        )
        .order_by('-created_at')
        .only('id', 'sender_id', 'recipient_id', 'body', 'kind', 'created_at')
    )
    for m in qs.iterator():
        other_id = m.recipient_id if m.sender_id == user.pk else m.sender_id
        if other_id not in last_map:
            last_map[other_id] = m
        if len(last_map) == len(friend_ids):
            break

    return {
        f.pk: {'last_msg': last_map.get(f.pk), 'unread': unread_map.get(f.pk, 0)}
        for f in friends
    }


@login_required
def inbox(request):
    friends = Friendship.friends_of(request.user)
    summaries = _conversation_summaries(request.user, friends)

    conversations = [
        {'friend': friend, 'last_msg': summaries[friend.pk]['last_msg'], 'unread': summaries[friend.pk]['unread']}
        for friend in friends
    ]
    conversations.sort(
        key=lambda c: c['last_msg'].created_at if c['last_msg'] else c['friend'].date_joined,
        reverse=True,
    )

    return render(request, 'chat/inbox.html', {'conversations': conversations})


@login_required
def room(request, username):
    friend = get_object_or_404(User, username=username)
    if not Friendship.are_friends(request.user, friend):
        return HttpResponseForbidden("You can only message people on your friends list.")

    Message.objects.filter(sender=friend, recipient=request.user, is_read=False).update(is_read=True)

    messages_qs = Message.objects.filter(
        Q(sender=request.user, recipient=friend) | Q(sender=friend, recipient=request.user)
    ).order_by('created_at')

    return render(request, 'chat/room.html', {
        'friend': friend,
        'chat_messages': messages_qs,
        'gif_search_enabled': klipy.is_configured(),
    })


@login_required
def notify_summary(request):
    """
    Tiny, cheap endpoint polled from every page (via the sidebar) so
    friend-request/unread-message badges and incoming calls surface on
    whatever page someone is on. Three indexed queries total — the
    has_incoming_call check is a plain .exists() so it's effectively free.
    """
    from friends.models import FriendRequest
    from calls.models import Call

    pending_requests = FriendRequest.objects.filter(to_user=request.user).count()
    unread_messages = Message.objects.filter(recipient=request.user, is_read=False).count()
    has_incoming_call = Call.objects.filter(callee=request.user, status=Call.STATUS_RINGING).exists()
    return JsonResponse({
        'pending_requests': pending_requests,
        'unread_messages': unread_messages,
        'has_incoming_call': has_incoming_call,
    })


@login_required
def inbox_data(request):
    """
    JSON version of the inbox, polled only while the person is actually on
    the inbox page (triggered by notify.js noticing unread count changed),
    so the list/order/preview update live without a page refresh.
    """
    friends = Friendship.friends_of(request.user)
    summaries = _conversation_summaries(request.user, friends)

    conversations = []
    for friend in friends:
        s = summaries[friend.pk]
        last_msg = s['last_msg']
        conversations.append({
            'username': friend.username,
            'avatar_color': friend.avatar_color,
            'avatar_url': friend.avatar_url,
            'initial': friend.username[0].upper(),
            'last_message': _preview_text(last_msg),
            'mine': last_msg.sender_id == request.user.pk if last_msg else False,
            'unread': s['unread'],
            'sort_key': last_msg.created_at.isoformat() if last_msg else friend.date_joined.isoformat(),
        })

    conversations.sort(key=lambda c: c['sort_key'], reverse=True)
    return JsonResponse({'conversations': conversations})


@login_required
def poll_messages(request, username):
    """Returns messages newer than ?after=<id> as JSON. Used for auto-refresh."""
    friend = get_object_or_404(User, username=username)
    if not Friendship.are_friends(request.user, friend):
        return JsonResponse({'error': 'not friends'}, status=403)

    after_id = int(request.GET.get('after', 0))
    qs = Message.objects.filter(
        Q(sender=request.user, recipient=friend) | Q(sender=friend, recipient=request.user),
        pk__gt=after_id,
    ).order_by('created_at')

    qs.filter(sender=friend, recipient=request.user).update(is_read=True)

    data = [
        {
            'id': m.pk,
            'body': m.body,
            'kind': m.kind,
            'media_url': m.media_url,
            'mine': m.sender_id == request.user.pk,
            'created_at': m.created_at.strftime('%H:%M'),
        }
        for m in qs
    ]
    return JsonResponse({'messages': data})


@login_required
@require_POST
def send_message(request, username):
    friend = get_object_or_404(User, username=username)
    if not Friendship.are_friends(request.user, friend):
        return JsonResponse({'error': 'not friends'}, status=403)

    body = request.POST.get('body', '').strip()
    media_url = request.POST.get('media_url', '').strip()
    kind = request.POST.get('kind', Message.KIND_TEXT)
    if kind not in (Message.KIND_GIF, Message.KIND_STICKER):
        kind = Message.KIND_TEXT

    if kind == Message.KIND_TEXT:
        media_url = ''
        if not body:
            return JsonResponse({'error': 'empty message'}, status=400)
    else:
        parsed = urlparse(media_url)
        host = (parsed.hostname or '')
        if parsed.scheme != 'https' or not host.endswith(ALLOWED_MEDIA_HOSTS_SUFFIX):
            return JsonResponse({'error': 'invalid media url'}, status=400)
        body = ''  # GIF/sticker messages don't carry caption text in this UI

    if len(body) > 2000:
        body = body[:2000]

    msg = Message.objects.create(sender=request.user, recipient=friend, body=body, kind=kind, media_url=media_url)
    return JsonResponse({
        'id': msg.pk,
        'body': msg.body,
        'kind': msg.kind,
        'media_url': msg.media_url,
        'mine': True,
        'created_at': msg.created_at.strftime('%H:%M'),
    })


@login_required
def gif_search(request):
    """
    Server-side proxy to the Klipy API (see chat/klipy.py). Kept behind our
    own login-required endpoint so the Klipy API key never reaches the
    browser, and so a friend-only app doesn't expose an open search proxy
    to anonymous callers.
    """
    content_type = 'sticker' if request.GET.get('type') == 'sticker' else 'gif'
    query = request.GET.get('q', '').strip()[:100]
    try:
        page = max(1, int(request.GET.get('page', 1)))
    except ValueError:
        page = 1

    if not klipy.is_configured():
        return JsonResponse({'results': [], 'has_next': False, 'error': 'not_configured'})

    try:
        results, has_next = klipy.search(
            content_type=content_type,
            query=query,
            page=page,
            customer_id=f'pingback-user-{request.user.pk}',
        )
    except klipy.KlipyError:
        return JsonResponse({'results': [], 'has_next': False, 'error': 'search_failed'})

    return JsonResponse({'results': results, 'has_next': has_next})
