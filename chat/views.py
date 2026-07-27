from urllib.parse import urlparse

from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts import cloudinary_client
from accounts.middleware import ONLINE_WINDOW_SECONDS
from friends.models import ConversationMute, Friendship
from . import klipy
from .models import Message, MessageReaction, TypingStatus

User = get_user_model()

ALLOWED_GIF_HOST_SUFFIX = '.klipy.com'
ALLOWED_CHAT_MEDIA_HOST_SUFFIX = '.cloudinary.com'

TYPING_FRESH_SECONDS = 6
MAX_VOICE_SECONDS = 300


def _preview_text(message):
    if message is None:
        return None
    if message.deleted_at:
        return 'Message deleted'
    if message.kind == Message.KIND_GIF:
        return '🎬 GIF'
    if message.kind == Message.KIND_STICKER:
        return '🏷️ Sticker'
    if message.kind == Message.KIND_VOICE:
        return '🎤 Voice message'
    if message.kind == Message.KIND_FILE:
        return f'📎 {message.file_name or "File"}'
    return message.body


def _conversation_summaries(user, friends):
    if not friends:
        return {}

    friend_ids = [f.pk for f in friends]

    unread_rows = (
        Message.objects.filter(recipient=user, sender_id__in=friend_ids, is_read=False, deleted_at__isnull=True)
        .values('sender_id')
        .annotate(cnt=Count('id'))
    )
    unread_map = {row['sender_id']: row['cnt'] for row in unread_rows}

    last_map = {}
    qs = (
        Message.objects.filter(
            Q(sender=user, recipient_id__in=friend_ids) | Q(sender_id__in=friend_ids, recipient=user)
        )
        .order_by('-created_at')
        .only('id', 'sender_id', 'recipient_id', 'body', 'kind', 'file_name', 'created_at', 'deleted_at')
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
    muted_ids = set(ConversationMute.objects.filter(user=request.user).values_list('friend_id', flat=True))
    now = timezone.now()

    conversations = [
        {
            'friend': friend,
            'last_msg': summaries[friend.pk]['last_msg'],
            'unread': summaries[friend.pk]['unread'],
            'muted': friend.pk in muted_ids,
            'online': bool(friend.last_seen_at and (now - friend.last_seen_at).total_seconds() < ONLINE_WINDOW_SECONDS),
        }
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

    now = timezone.now()
    return render(request, 'chat/room.html', {
        'friend': friend,
        'gif_search_enabled': klipy.is_configured(),
        'is_muted': ConversationMute.is_muted(request.user, friend),
        'friend_online': bool(friend.last_seen_at and (now - friend.last_seen_at).total_seconds() < ONLINE_WINDOW_SECONDS),
    })


@login_required
def notify_summary(request):
    from friends.models import FriendRequest
    from calls.models import Call

    pending_requests = FriendRequest.objects.filter(to_user=request.user).count()
    muted_ids = ConversationMute.objects.filter(user=request.user).values_list('friend_id', flat=True)
    unread_messages = Message.objects.filter(
        recipient=request.user, is_read=False, deleted_at__isnull=True
    ).exclude(sender_id__in=muted_ids).count()
    has_incoming_call = Call.objects.filter(callee=request.user, status=Call.STATUS_RINGING).exists()
    return JsonResponse({
        'pending_requests': pending_requests,
        'unread_messages': unread_messages,
        'has_incoming_call': has_incoming_call,
    })


@login_required
def inbox_data(request):
    friends = Friendship.friends_of(request.user)
    summaries = _conversation_summaries(request.user, friends)
    muted_ids = set(ConversationMute.objects.filter(user=request.user).values_list('friend_id', flat=True))
    now = timezone.now()

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
            'unread': 0 if friend.pk in muted_ids else s['unread'],
            'muted': friend.pk in muted_ids,
            'online': bool(friend.last_seen_at and (now - friend.last_seen_at).total_seconds() < ONLINE_WINDOW_SECONDS),
            'sort_key': last_msg.created_at.isoformat() if last_msg else friend.date_joined.isoformat(),
        })

    conversations.sort(key=lambda c: c['sort_key'], reverse=True)
    return JsonResponse({'conversations': conversations})


def _serialize_reactions(message, user):
    counts = {}
    mine = None
    for r in message.reactions.all():
        counts[r.emoji] = counts.get(r.emoji, 0) + 1
        if r.user_id == user.pk:
            mine = r.emoji
    return [{'emoji': e, 'count': c, 'mine': e == mine} for e, c in counts.items()]


def _serialize_reply(message):
    rt = message.reply_to
    if not rt:
        return None
    return {
        'id': rt.pk,
        'sender_username': rt.sender.username if rt.sender_id else None,
        'preview': (_preview_text(rt) or '')[:80],
    }


def _serialize_message(m, request_user):
    return {
        'id': m.pk,
        'body': '' if m.deleted_at else m.body,
        'kind': m.kind,
        'media_url': '' if m.deleted_at else m.media_url,
        'file_name': m.file_name,
        'file_size': m.file_size,
        'duration_seconds': m.duration_seconds,
        'mine': m.sender_id == request_user.pk,
        'is_read': m.is_read,
        'edited': m.edited_at is not None,
        'deleted': m.deleted_at is not None,
        'reply_to': _serialize_reply(m),
        'reactions': _serialize_reactions(m, request_user),
        'created_at': m.created_at.strftime('%H:%M'),
    }


@login_required
def poll_messages(request, username):
    friend = get_object_or_404(User, username=username)
    if not Friendship.are_friends(request.user, friend):
        return JsonResponse({'error': 'not friends'}, status=403)

    after_id = int(request.GET.get('after', 0))
    qs = Message.objects.filter(
        Q(sender=request.user, recipient=friend) | Q(sender=friend, recipient=request.user),
        pk__gt=after_id,
    ).select_related('reply_to', 'reply_to__sender').prefetch_related('reactions').order_by('created_at')

    qs.filter(sender=friend, recipient=request.user).update(is_read=True)

    data = [_serialize_message(m, request.user) for m in qs]

    recent_mine = (
        Message.objects.filter(sender=request.user, recipient=friend)
        .order_by('-id')
        .values('id', 'is_read')[:30]
    )

    recent_ids = list(
        Message.objects.filter(
            Q(sender=request.user, recipient=friend) | Q(sender=friend, recipient=request.user)
        ).order_by('-id').values_list('id', flat=True)[:30]
    )
    recent_msgs = Message.objects.filter(pk__in=recent_ids).prefetch_related('reactions')
    reaction_updates = {m.pk: _serialize_reactions(m, request.user) for m in recent_msgs}
    edit_delete_updates = {
        m.pk: {'edited': m.edited_at is not None, 'deleted': m.deleted_at is not None, 'body': '' if m.deleted_at else m.body}
        for m in recent_msgs
    }

    friend_typing = TypingStatus.objects.filter(
        user=friend, friend=request.user, updated_at__gte=timezone.now() - timezone.timedelta(seconds=TYPING_FRESH_SECONDS)
    ).exists()

    return JsonResponse({
        'messages': data,
        'read_status': list(recent_mine),
        'reaction_updates': reaction_updates,
        'edit_delete_updates': edit_delete_updates,
        'friend_typing': friend_typing,
        'friend_online': bool(
            friend.last_seen_at and (timezone.now() - friend.last_seen_at).total_seconds() < ONLINE_WINDOW_SECONDS
        ),
    })


@login_required
@require_POST
def typing(request, username):
    friend = get_object_or_404(User, username=username)
    if not Friendship.are_friends(request.user, friend):
        return JsonResponse({'error': 'not friends'}, status=403)
    TypingStatus.objects.update_or_create(user=request.user, friend=friend, defaults={})
    return JsonResponse({'ok': True})


@login_required
@require_POST
def send_message(request, username):
    friend = get_object_or_404(User, username=username)
    if not Friendship.are_friends(request.user, friend):
        return JsonResponse({'error': 'not friends'}, status=403)

    body = request.POST.get('body', '').strip()
    media_url = request.POST.get('media_url', '').strip()
    kind = request.POST.get('kind', Message.KIND_TEXT)
    if kind not in (Message.KIND_GIF, Message.KIND_STICKER, Message.KIND_VOICE, Message.KIND_FILE):
        kind = Message.KIND_TEXT

    reply_to = None
    reply_to_id = request.POST.get('reply_to')
    if reply_to_id:
        reply_to = Message.objects.filter(
            pk=reply_to_id, deleted_at__isnull=True
        ).filter(Q(sender=request.user, recipient=friend) | Q(sender=friend, recipient=request.user)).first()

    file_name, file_size, duration_seconds = '', None, None

    if kind == Message.KIND_TEXT:
        media_url = ''
        if not body:
            return JsonResponse({'error': 'empty message'}, status=400)
    else:
        parsed = urlparse(media_url)
        host = (parsed.hostname or '')
        allowed_suffix = ALLOWED_GIF_HOST_SUFFIX if kind in (Message.KIND_GIF, Message.KIND_STICKER) else ALLOWED_CHAT_MEDIA_HOST_SUFFIX
        if parsed.scheme != 'https' or not host.endswith(allowed_suffix):
            return JsonResponse({'error': 'invalid media url'}, status=400)
        body = ''

        if kind == Message.KIND_FILE:
            file_name = request.POST.get('file_name', '')[:255]
            try:
                file_size = int(request.POST.get('file_size', 0)) or None
            except ValueError:
                file_size = None
        if kind == Message.KIND_VOICE:
            try:
                duration_seconds = min(int(request.POST.get('duration_seconds', 0)), MAX_VOICE_SECONDS) or None
            except ValueError:
                duration_seconds = None

    if len(body) > 2000:
        body = body[:2000]

    msg = Message.objects.create(
        sender=request.user, recipient=friend, body=body, kind=kind, media_url=media_url,
        file_name=file_name, file_size=file_size, duration_seconds=duration_seconds, reply_to=reply_to,
    )
    TypingStatus.objects.filter(user=request.user, friend=friend).delete()

    return JsonResponse(_serialize_message(msg, request.user))


@login_required
@require_POST
def upload_voice(request, username):
    friend = get_object_or_404(User, username=username)
    if not Friendship.are_friends(request.user, friend):
        return JsonResponse({'error': 'not friends'}, status=403)

    audio_file = request.FILES.get('audio')
    if not audio_file:
        return JsonResponse({'error': 'missing audio'}, status=400)
    if audio_file.size > cloudinary_client.MAX_CHAT_FILE_BYTES:
        return JsonResponse({'error': 'file too large'}, status=400)

    try:
        url, public_id = cloudinary_client.upload_chat_voice(audio_file, request.user.pk)
    except cloudinary_client.CloudinaryError as exc:
        return JsonResponse({'error': str(exc)}, status=502)

    return JsonResponse({'media_url': url, 'public_id': public_id})


@login_required
@require_POST
def upload_file(request, username):
    friend = get_object_or_404(User, username=username)
    if not Friendship.are_friends(request.user, friend):
        return JsonResponse({'error': 'not friends'}, status=403)

    up = request.FILES.get('file')
    if not up:
        return JsonResponse({'error': 'missing file'}, status=400)
    if up.size > cloudinary_client.MAX_CHAT_FILE_BYTES:
        return JsonResponse({'error': 'file too large (25MB max)'}, status=400)

    try:
        url, public_id = cloudinary_client.upload_chat_file(up, request.user.pk)
    except cloudinary_client.CloudinaryError as exc:
        return JsonResponse({'error': str(exc)}, status=502)

    return JsonResponse({'media_url': url, 'public_id': public_id, 'file_name': up.name, 'file_size': up.size})


@login_required
@require_POST
def edit_message(request, message_id):
    msg = get_object_or_404(Message, pk=message_id, sender=request.user)
    if msg.deleted_at:
        return JsonResponse({'error': 'message deleted'}, status=400)
    if msg.kind != Message.KIND_TEXT:
        return JsonResponse({'error': 'only text messages can be edited'}, status=400)

    body = request.POST.get('body', '').strip()
    if not body:
        return JsonResponse({'error': 'empty message'}, status=400)

    msg.body = body[:2000]
    msg.edited_at = timezone.now()
    msg.save(update_fields=['body', 'edited_at'])
    return JsonResponse(_serialize_message(msg, request.user))


@login_required
@require_POST
def delete_message(request, message_id):
    msg = get_object_or_404(Message, pk=message_id, sender=request.user)
    if not msg.deleted_at:
        if msg.media_public_id:
            resource_type = 'video' if msg.kind == Message.KIND_VOICE else 'auto'
            cloudinary_client.delete_chat_media(msg.media_public_id, resource_type=resource_type)
        msg.deleted_at = timezone.now()
        msg.body = ''
        msg.media_url = ''
        msg.save(update_fields=['deleted_at', 'body', 'media_url'])
    return JsonResponse(_serialize_message(msg, request.user))


@login_required
@require_POST
def react_message(request, message_id):
    msg = get_object_or_404(Message, pk=message_id)
    if request.user.pk not in (msg.sender_id, msg.recipient_id):
        return JsonResponse({'error': 'forbidden'}, status=403)
    other = msg.recipient if msg.sender_id == request.user.pk else msg.sender
    if not Friendship.are_friends(request.user, other):
        return JsonResponse({'error': 'forbidden'}, status=403)

    emoji = request.POST.get('emoji', '').strip()[:8]
    if not emoji:
        return JsonResponse({'error': 'missing emoji'}, status=400)

    existing = MessageReaction.objects.filter(message=msg, user=request.user).first()
    if existing and existing.emoji == emoji:
        existing.delete()
    else:
        MessageReaction.objects.update_or_create(message=msg, user=request.user, defaults={'emoji': emoji})

    return JsonResponse({'reactions': _serialize_reactions(msg, request.user)})


@login_required
def search_messages(request, username):
    friend = get_object_or_404(User, username=username)
    if not Friendship.are_friends(request.user, friend):
        return JsonResponse({'error': 'not friends'}, status=403)

    query = request.GET.get('q', '').strip()
    if not query:
        return JsonResponse({'results': []})

    qs = Message.objects.filter(
        Q(sender=request.user, recipient=friend) | Q(sender=friend, recipient=request.user),
        body__icontains=query, deleted_at__isnull=True, kind=Message.KIND_TEXT,
    ).order_by('-created_at')[:30]

    return JsonResponse({'results': [
        {'id': m.pk, 'body': m.body, 'mine': m.sender_id == request.user.pk, 'created_at': m.created_at.strftime('%b %d, %H:%M')}
        for m in qs
    ]})


@login_required
@require_POST
def toggle_mute(request, username):
    friend = get_object_or_404(User, username=username)
    mute, created = ConversationMute.objects.get_or_create(user=request.user, friend=friend)
    if not created:
        mute.delete()
        return JsonResponse({'muted': False})
    return JsonResponse({'muted': True})


@login_required
def gif_search(request):
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
